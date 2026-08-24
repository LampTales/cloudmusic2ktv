from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from flask import Flask, jsonify, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException

from cloudmusic2ktv import NeteaseClient, NeteaseError, SongDownloadService
from cloudmusic2ktv.service import local_song_status, parse_song_id, safe_filename
from cloudmusic2ktv.sessions import FileSessionStore
from cloudmusic2ktv.video import (
    VideoError,
    VideoJobManager,
    VideoOptions,
    VideoProject,
    render_preview,
    save_custom_background,
    video_options_fingerprint,
)


ROOT = Path(__file__).resolve().parent
INSTANCE = ROOT / "instance"
OUTPUTS = ROOT / "outputs"
SESSION_COOKIE = "cloudmusic2ktv_session"
SESSION_TTL_SECONDS = int(os.environ.get("CLOUDMUSIC2KTV_SESSION_DAYS", "90")) * 24 * 60 * 60
VIDEO_ARTIFACT = re.compile(
    r"^(?:video_preview(?:_[0-9a-f]{12})?\.png|ktv_(?:1080p|720p)(?:_[0-9a-f]{12})?\.mp4)$"
)
VIDEO_FILE = re.compile(r"^ktv_(1080p|720p)(?:_[0-9a-f]{12})?\.mp4$")

app = Flask(__name__, instance_path=str(INSTANCE), instance_relative_config=True)
app.config.update(MAX_CONTENT_LENGTH=32 * 1024 * 1024)
app.json.ensure_ascii = False

auth_sessions = FileSessionStore(INSTANCE / "sessions", ttl_seconds=SESSION_TTL_SECONDS)
video_jobs = VideoJobManager(OUTPUTS)
download_state_lock = threading.Lock()
active_downloads: set[int] = set()
auth_sessions.cleanup_expired()


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/status")
def status() -> Any:
    with auth_sessions.open(auth_token(), touch=True) as session:
        if session is None:
            return jsonify({"ok": True, "logged_in": False, "profile": None})
        value = session.client.account_status()
        session.profile = public_profile(value.get("profile")) if value.get("logged_in") else None
        return jsonify({"ok": True, "logged_in": bool(session.profile), "profile": session.profile})


@app.post("/api/auth/captcha")
def send_captcha() -> Any:
    body = json_body()
    phone = required_string(body, "phone")
    country_code = clean_country_code(body.get("country_code", "86"))
    with auth_sessions.open(auth_token(), create=True, touch=True) as session:
        assert session is not None
        session.client.send_captcha(phone, country_code)
        response = jsonify({"ok": True, "message": "验证码已发送"})
        set_auth_cookie(response, session.token)
        return response


@app.post("/api/auth/login")
def login() -> Any:
    body = json_body()
    phone = required_string(body, "phone")
    captcha = required_string(body, "captcha")
    country_code = clean_country_code(body.get("country_code", "86"))
    with auth_sessions.open(auth_token(), create=True, touch=True) as session:
        assert session is not None
        result = session.client.login_with_captcha(phone, captcha, country_code)
        profile = result.get("profile") or session.client.account_status().get("profile")
        session.profile = public_profile(profile)
        previous_token = session.token
    token = auth_sessions.rotate(previous_token)
    auth_sessions.cleanup_expired()
    response = jsonify({"ok": True, "message": "登录成功", "profile": public_profile(profile)})
    set_auth_cookie(response, token)
    return response


@app.post("/api/auth/logout")
def logout() -> Any:
    token = auth_token()
    with auth_sessions.open(token, touch=False) as session:
        if session is not None:
            try:
                session.client.logout()
            except NeteaseError:
                pass
            session.profile = None
    auth_sessions.delete(token)
    response = jsonify({"ok": True, "message": "已退出登录并删除本地会话"})
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="Lax")
    return response


@app.get("/api/search")
def search() -> Any:
    query = (request.args.get("q") or "").strip()
    if not query:
        return error_response("请输入歌名或“歌名 歌手”", "invalid_query", 400)
    with current_netease_client() as client:
        songs = client.search_songs(query)
    return jsonify({"ok": True, "songs": songs})


@app.post("/api/song/inspect")
def inspect_song() -> Any:
    song_id = body_song_id()
    with current_netease_client() as client:
        song = SongDownloadService(client, OUTPUTS).inspect(song_id)
    return jsonify({"ok": True, "song": song, "local": song_local_status(song_id)})


@app.get("/api/song/local/<int:song_id>")
def song_local(song_id: int) -> Any:
    return jsonify({"ok": True, "local": song_local_status(song_id)})


@app.post("/api/song/download")
def download_song() -> Any:
    body = json_body()
    try:
        song_id = parse_song_id(body.get("song", ""))
    except ValueError as exc:
        return error_response(str(exc), "invalid_song_id", 400)
    level = str(body.get("level") or "exhigh")
    if level not in {"standard", "higher", "exhigh", "lossless", "hires"}:
        return error_response("不支持的音质", "invalid_quality", 400)
    if not begin_download(song_id):
        return error_response("这首歌的共享素材正在下载，请稍后再试", "download_in_progress", 409)
    try:
        with current_netease_client() as client:
            result = SongDownloadService(client, OUTPUTS).download(song_id, level)
        result["local"] = local_song_status(OUTPUTS, song_id)
        return jsonify({"ok": True, "result": result})
    finally:
        finish_download(song_id)


@app.post("/api/video/preview")
def video_preview() -> Any:
    body = json_body()
    song_id = video_song_id(body.get("song", ""))
    options = VideoOptions.from_mapping(body.get("options"))
    project = VideoProject.load(OUTPUTS, song_id)
    fingerprint = video_options_fingerprint(options)
    destination = project.directory / f"video_preview_{fingerprint}.png"
    result = render_preview(project, options, destination)
    result["url"] = url_for(
        "video_artifact", song_id=song_id, filename=destination.name, version=destination.stat().st_mtime_ns
    )
    return jsonify({"ok": True, "preview": result})


@app.post("/api/video/background")
def video_background() -> Any:
    song_id = video_song_id(request.form.get("song", ""))
    upload = request.files.get("background")
    if upload is None or not upload.filename:
        return error_response("请选择背景图片", "background_missing", 400)
    project = VideoProject.load(OUTPUTS, song_id)
    save_custom_background(upload, project.custom_background_path)
    return jsonify({"ok": True, "message": "自定义背景已保存"})


@app.post("/api/video/render")
def video_render() -> Any:
    if not auth_sessions.is_authenticated(auth_token()):
        return error_response("请先登录网易云账号，再提交视频任务", "login_required", 401)
    body = json_body()
    song_id = video_song_id(body.get("song", ""))
    options = VideoOptions.from_mapping(body.get("options"))
    # Fail early before accepting a background job.
    project = VideoProject.load(OUTPUTS, song_id)
    job = video_jobs.start(song_id, options, project.song)
    return jsonify({"ok": True, "job": job})


@app.get("/api/video/queue")
def video_queue() -> Any:
    queue = video_jobs.queue_status()
    if queue.get("recent"):
        queue["recent"] = job_with_artifact_url(queue["recent"])
    return jsonify({"ok": True, "queue": queue})


@app.get("/api/video/local/<int:song_id>")
def video_local(song_id: int) -> Any:
    videos = local_video_files(song_id)
    return jsonify(
        {
            "ok": True,
            "local": {
                "status": "ready" if videos else "missing",
                "ready": bool(videos),
                "message": f"找到 {len(videos)} 个本地视频" if videos else "本地还没有这首歌的完整视频",
                "videos": videos,
            },
        }
    )


@app.get("/api/video/jobs/<job_id>")
def video_job(job_id: str) -> Any:
    job = video_jobs.get(job_id)
    if job is None:
        return error_response("没有找到该视频任务", "job_not_found", 404)
    if job.get("status") == "done" and job.get("result"):
        job = job_with_artifact_url(job)
    return jsonify({"ok": True, "job": job})


@app.get("/api/video/artifact/<int:song_id>/<filename>")
def video_artifact(song_id: int, filename: str) -> Any:
    if not VIDEO_ARTIFACT.fullmatch(filename):
        return error_response("不允许访问该文件", "artifact_forbidden", 403)
    path = local_artifact_path(song_id, filename)
    if path is None:
        return error_response("文件尚未生成", "artifact_missing", 404)
    return send_file(
        path,
        conditional=True,
        as_attachment=request.args.get("download") == "1",
        download_name=video_download_name(song_id, filename),
    )


@app.errorhandler(NeteaseError)
def handle_netease_error(error: NeteaseError) -> Any:
    status_code = 401 if error.code in {301, -110, "audio_forbidden"} else 502
    return error_response(str(error), error.code, status_code)


@app.errorhandler(VideoError)
def handle_video_error(error: VideoError) -> Any:
    return error_response(str(error), "video_error", 400)


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception) -> Any:
    if isinstance(error, HTTPException):
        return error
    app.logger.exception("Unhandled error")
    return error_response("程序内部错误，请查看终端日志", "internal_error", 500)


def auth_token() -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def set_auth_cookie(response: Any, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.is_secure,
        samesite="Lax",
        path="/",
    )


def public_profile(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "userId": value.get("userId"),
        "nickname": str(value.get("nickname") or "网易云用户"),
        "avatarUrl": str(value.get("avatarUrl") or ""),
    }


@contextmanager
def current_netease_client() -> Iterator[NeteaseClient]:
    with auth_sessions.open(auth_token(), touch=True) as session:
        if session is not None:
            yield session.client
        else:
            yield NeteaseClient()


def song_local_status(song_id: int) -> dict[str, Any]:
    with download_state_lock:
        downloading = song_id in active_downloads
    return local_song_status(OUTPUTS, song_id, downloading=downloading)


def begin_download(song_id: int) -> bool:
    with download_state_lock:
        if song_id in active_downloads:
            return False
        active_downloads.add(song_id)
        return True


def finish_download(song_id: int) -> None:
    with download_state_lock:
        active_downloads.discard(song_id)


def job_with_artifact_url(value: dict[str, Any]) -> dict[str, Any]:
    job = dict(value)
    result = job.get("result")
    if not isinstance(result, dict) or not result.get("path"):
        return job
    result = dict(result)
    path = Path(result["path"])
    if path.exists():
        result["url"] = url_for(
            "video_artifact",
            song_id=job["song_id"],
            filename=path.name,
            version=path.stat().st_mtime_ns,
        )
    job["result"] = result
    return job


def local_video_files(song_id: int) -> list[dict[str, Any]]:
    directory = song_output_directory(song_id)
    if directory is None:
        return []
    videos: list[dict[str, Any]] = []
    for path in directory.iterdir():
        match = VIDEO_FILE.fullmatch(path.name)
        if match is None or not safe_output_file(directory, path):
            continue
        stat = path.stat()
        videos.append(
            {
                "filename": path.name,
                "download_name": video_download_name(song_id, path.name),
                "resolution": match.group(1),
                "size": stat.st_size,
                "updated_at": int(stat.st_mtime),
                "url": url_for(
                    "video_artifact",
                    song_id=song_id,
                    filename=path.name,
                    version=stat.st_mtime_ns,
                ),
            }
        )
    videos.sort(key=lambda item: (item["updated_at"], item["filename"]), reverse=True)
    return videos


def local_artifact_path(song_id: int, filename: str) -> Path | None:
    directory = song_output_directory(song_id)
    if directory is None:
        return None
    candidate = directory / filename
    return candidate if safe_output_file(directory, candidate) else None


def video_download_name(song_id: int, filename: str) -> str:
    """Return a user-facing attachment name without the internal option hash."""
    match = VIDEO_FILE.fullmatch(filename)
    if match is None:
        return filename

    directory = song_output_directory(song_id)
    if directory is None:
        return filename
    try:
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        artist = str(metadata.get("artist") or "").strip()
        title = str(metadata.get("name") or "").strip()
    except (OSError, ValueError, TypeError):
        return filename
    if not artist or not title:
        return filename
    stem = safe_filename(f"ktv_{match.group(1)}_{artist}_{title}", fallback=f"ktv_{match.group(1)}")
    return f"{stem}.mp4"


def song_output_directory(song_id: int) -> Path | None:
    output_root = OUTPUTS.resolve()
    for directory in sorted(OUTPUTS.glob(f"{song_id}_*")):
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if directory.is_dir() and resolved.parent == output_root:
            return resolved
    return None


def safe_output_file(directory: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    return candidate.is_file() and resolved.parent == directory.resolve()


def json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise NeteaseError("请求格式不正确", code="invalid_request")
    return value


def required_string(body: dict[str, Any], name: str) -> str:
    value = str(body.get(name) or "").strip()
    if not value:
        raise NeteaseError(f"缺少字段：{name}", code="invalid_request")
    return value


def clean_country_code(value: Any) -> str:
    result = str(value).strip().lstrip("+")
    if not result.isdigit():
        raise NeteaseError("国家/地区代码格式不正确", code="invalid_request")
    return result


def body_song_id() -> int:
    try:
        return parse_song_id(json_body().get("song", ""))
    except ValueError as exc:
        raise NeteaseError(str(exc), code="invalid_song_id") from exc


def video_song_id(value: Any) -> int:
    try:
        return parse_song_id(value)
    except ValueError as exc:
        raise VideoError(str(exc)) from exc


def error_response(message: str, code: Any, status_code: int) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": {"message": message, "code": code}}), status_code


if __name__ == "__main__":
    host = os.environ.get("CLOUDMUSIC2KTV_HOST", "0.0.0.0")
    port = int(os.environ.get("CLOUDMUSIC2KTV_PORT", "7860"))
    app.run(host=host, port=port, debug=False, threaded=True)
