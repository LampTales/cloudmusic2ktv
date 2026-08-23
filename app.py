from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException

from cloudmusic2ktv import NeteaseClient, NeteaseError, SongDownloadService
from cloudmusic2ktv.service import parse_song_id
from cloudmusic2ktv.video import (
    VideoError,
    VideoJobManager,
    VideoOptions,
    VideoProject,
    render_preview,
    save_custom_background,
)


ROOT = Path(__file__).resolve().parent
INSTANCE = ROOT / "instance"
OUTPUTS = ROOT / "outputs"

app = Flask(__name__, instance_path=str(INSTANCE), instance_relative_config=True)
app.config.update(JSON_AS_ASCII=False, MAX_CONTENT_LENGTH=32 * 1024 * 1024)

client = NeteaseClient(INSTANCE / "netease_cookies.json")
downloads = SongDownloadService(client, OUTPUTS)
video_jobs = VideoJobManager(OUTPUTS)


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/status")
def status() -> Any:
    return jsonify({"ok": True, **client.account_status()})


@app.post("/api/auth/captcha")
def send_captcha() -> Any:
    body = json_body()
    phone = required_string(body, "phone")
    country_code = clean_country_code(body.get("country_code", "86"))
    client.send_captcha(phone, country_code)
    return jsonify({"ok": True, "message": "验证码已发送"})


@app.post("/api/auth/login")
def login() -> Any:
    body = json_body()
    phone = required_string(body, "phone")
    captcha = required_string(body, "captcha")
    country_code = clean_country_code(body.get("country_code", "86"))
    result = client.login_with_captcha(phone, captcha, country_code)
    profile = result.get("profile") or client.account_status().get("profile")
    return jsonify({"ok": True, "message": "登录成功", "profile": profile})


@app.post("/api/auth/logout")
def logout() -> Any:
    client.logout()
    return jsonify({"ok": True, "message": "已退出登录并删除本地会话"})


@app.get("/api/search")
def search() -> Any:
    query = (request.args.get("q") or "").strip()
    if not query:
        return error_response("请输入歌名或“歌名 歌手”", "invalid_query", 400)
    return jsonify({"ok": True, "songs": client.search_songs(query)})


@app.post("/api/song/inspect")
def inspect_song() -> Any:
    song_id = body_song_id()
    return jsonify({"ok": True, "song": downloads.inspect(song_id)})


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
    return jsonify({"ok": True, "result": downloads.download(song_id, level)})


@app.post("/api/video/preview")
def video_preview() -> Any:
    body = json_body()
    song_id = video_song_id(body.get("song", ""))
    options = VideoOptions.from_mapping(body.get("options"))
    project = VideoProject.load(OUTPUTS, song_id)
    destination = project.directory / "video_preview.png"
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
    body = json_body()
    song_id = video_song_id(body.get("song", ""))
    options = VideoOptions.from_mapping(body.get("options"))
    # Fail early before accepting a background job.
    VideoProject.load(OUTPUTS, song_id)
    job = video_jobs.start(song_id, options)
    return jsonify({"ok": True, "job": job})


@app.get("/api/video/jobs/<job_id>")
def video_job(job_id: str) -> Any:
    job = video_jobs.get(job_id)
    if job is None:
        return error_response("没有找到该视频任务", "job_not_found", 404)
    if job.get("status") == "done" and job.get("result"):
        job["result"] = dict(job["result"])
        job["result"]["url"] = url_for(
            "video_artifact",
            song_id=job["song_id"],
            filename=Path(job["result"]["path"]).name,
            version=Path(job["result"]["path"]).stat().st_mtime_ns,
        )
    return jsonify({"ok": True, "job": job})


@app.get("/api/video/artifact/<int:song_id>/<filename>")
def video_artifact(song_id: int, filename: str) -> Any:
    if filename not in {"video_preview.png", "ktv_1080p.mp4", "ktv_720p.mp4"}:
        return error_response("不允许访问该文件", "artifact_forbidden", 403)
    project = VideoProject.load(OUTPUTS, song_id)
    path = project.directory / filename
    if not path.exists():
        return error_response("文件尚未生成", "artifact_missing", 404)
    return send_file(path, conditional=True)


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
    host = os.environ.get("CLOUDMUSIC2KTV_HOST", "127.0.0.1")
    port = int(os.environ.get("CLOUDMUSIC2KTV_PORT", "7860"))
    app.run(host=host, port=port, debug=False, threaded=True)
