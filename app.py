from __future__ import annotations

import json
import hmac
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from flask import Flask, g, jsonify, request, send_file
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

from cloudmusic2ktv import NeteaseClient, NeteaseError, SongDownloadService
from cloudmusic2ktv.accounts import (
    AccountError,
    AccountExists,
    NeteaseBindingStore,
    WebsiteAccountStore,
)
from cloudmusic2ktv.access import AllowlistError, AllowlistStore, UserNotAllowed
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


def normalize_base_path(value: str | None) -> str:
    """Normalize the URL prefix used by a trusted reverse proxy."""
    text = str(value or "").strip()
    if not text or text == "/":
        return ""
    if "?" in text or "#" in text or "\\" in text:
        raise RuntimeError("CLOUDMUSIC2KTV_BASE_PATH 只能包含 URL 路径")
    if not text.startswith("/"):
        text = "/" + text
    text = "/" + "/".join(part for part in text.split("/") if part)
    if any(part in {".", ".."} for part in text.split("/")):
        raise RuntimeError("CLOUDMUSIC2KTV_BASE_PATH 不能包含 . 或 ..")
    return text.rstrip("/")


BASE_PATH = normalize_base_path(os.environ.get("CLOUDMUSIC2KTV_BASE_PATH"))
SESSION_COOKIE_PATH = BASE_PATH or "/"
VIDEO_ARTIFACT = re.compile(
    r"^(?:video_preview(?:_[0-9a-f]{12})?\.png|ktv_(?:1080p|720p)(?:_[0-9a-f]{12})?\.mp4)$"
)
VIDEO_FILE = re.compile(r"^ktv_(1080p|720p)(?:_[0-9a-f]{12})?\.mp4$")

app = Flask(
    __name__,
    instance_path=str(INSTANCE),
    instance_relative_config=True,
    static_folder=None,
    template_folder=None,
)
app.config.update(
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    APPLICATION_ROOT=BASE_PATH or "/",
)
app.json.ensure_ascii = False


def configured_cors_origins() -> set[str]:
    """Return explicitly allowed frontend origins for local split testing.

    Production deployments should normally leave this empty and use a
    same-origin reverse proxy.  A comma-separated allowlist is useful when
    the standalone frontend runs on a different local port.
    """
    value = os.environ.get("CLOUDMUSIC2KTV_CORS_ORIGINS", "")
    return {item.strip().rstrip("/") for item in value.split(",") if item.strip()}


@app.after_request
def add_cors_headers(response: Any) -> Any:
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in configured_cors_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Expose-Headers"] = (
            "Accept-Ranges, Content-Length, Content-Range, Content-Disposition"
        )
        response.headers.add("Vary", "Origin")
    return response


if os.environ.get("CLOUDMUSIC2KTV_TRUST_PROXY") == "1":
    # Only enable this when the app is behind a controlled reverse proxy.
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_prefix=1,
    )

auth_sessions = FileSessionStore(INSTANCE / "sessions", ttl_seconds=SESSION_TTL_SECONDS)
allowlist = AllowlistStore(INSTANCE / "allowlist.json")
website_accounts = WebsiteAccountStore(INSTANCE / "accounts.json")
netease_bindings = NeteaseBindingStore(INSTANCE / "netease_bindings.json")
video_jobs = VideoJobManager(OUTPUTS, state_path=INSTANCE / "video_jobs.json")
download_state_lock = threading.Lock()
active_downloads: set[int] = set()
cookie_import_rate_lock = threading.Lock()
cookie_import_last_attempt: dict[str, float] = {}
auth_sessions.cleanup_expired()


def authorized_identity(*, admin: bool = False) -> tuple[dict[str, Any] | None, Any | None]:
    token = auth_token()
    with auth_sessions.open(token, touch=True) as session:
        if session is None or not session.profile:
            return None, error_response("请先登录网站账号", "login_required", 401)
        profile = dict(session.profile)
        role = allowlist.role_for(profile.get("netease_user_id"))
        session_token = session.token
    if role is None:
        auth_sessions.delete(session_token)
        response, status_code = error_response("该账号已不在允许名单中", "not_allowed", 403)
        response.delete_cookie(SESSION_COOKIE, path=SESSION_COOKIE_PATH, samesite="Lax")
        return None, (response, status_code)
    if admin and role != "admin":
        return None, error_response("只有管理员可以执行此操作", "admin_required", 403)
    profile["role"] = role
    return profile, None


def member_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        identity, failure = authorized_identity()
        if failure is not None:
            return failure
        g.current_user = identity
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        identity, failure = authorized_identity(admin=True)
        if failure is not None:
            return failure
        g.current_user = identity
        return view(*args, **kwargs)

    return wrapped


@app.get("/api/healthz")
def healthz() -> Any:
    """Unauthenticated liveness endpoint for Docker and reverse proxies."""
    return jsonify({"ok": True, "status": "healthy"})


@app.get("/api/status")
def status() -> Any:
    expired_token = None
    with auth_sessions.open(auth_token(), touch=True) as session:
        if session is None:
            return jsonify({"ok": True, "logged_in": False, "profile": None})
        role = None
        if not session.profile or not (session.profile.get("username") or session.profile.get("website_username")):
            expired_token = session.token
            session.profile = None
            result = {"ok": True, "logged_in": False, "profile": None, "access_denied": True}
        else:
            role = allowlist.role_for(session.profile.get("netease_user_id"))
            if role is None:
                expired_token = session.token
                session.profile = None
                result = {"ok": True, "logged_in": False, "profile": None, "access_denied": True}
            else:
                binding = netease_bindings.load(session.profile.get("netease_user_id"))
                site_profile = dict(session.profile)
                site_profile["username"] = site_profile.get("username") or site_profile.get("website_username")
                result = {
                    "ok": True,
                    "logged_in": True,
                    "profile": site_profile,
                    "role": role,
                    "netease_bound": binding is not None,
                }
    if expired_token is not None:
        auth_sessions.delete(expired_token)
        response = jsonify(result)
        response.delete_cookie(SESSION_COOKIE, path=SESSION_COOKIE_PATH, samesite="Lax")
        return response
    return jsonify(result)


@app.get("/api/auth/csrf")
def auth_csrf() -> Any:
    """Create or retrieve the short-lived CSRF token used by Cookie import."""
    with auth_sessions.open(auth_token(), create=True, touch=True) as session:
        assert session is not None
        response = jsonify({"ok": True, "csrf_token": session.csrf_token})
        response.headers["Cache-Control"] = "no-store"
        set_auth_cookie(response, session.token)
        return response


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


def _cookie_import_profile(session: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Load and validate an imported browser Cookie without persisting it yet."""
    if request.content_length and request.content_length > 128 * 1024:
        raise AccountError("Cookie 数据过大")
    if not request.is_secure:
        local_hosts = {"127.0.0.1", "::1", "localhost"}
        allow_insecure = os.environ.get("CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT") == "1"
        if request.remote_addr not in local_hosts and not allow_insecure:
            raise AccountError("Cookie 导入需要通过 HTTPS 进行")
    supplied_csrf = str(body.get("csrf_token") or "")
    if not supplied_csrf or not hmac.compare_digest(supplied_csrf, session.csrf_token):
        raise AccountError("Cookie 导入请求已失效，请重新打开导入窗口")
    now = time.monotonic()
    with cookie_import_rate_lock:
        for token, timestamp in list(cookie_import_last_attempt.items()):
            if now - timestamp > 600:
                cookie_import_last_attempt.pop(token, None)
        previous = cookie_import_last_attempt.get(session.token, 0.0)
        if now - previous < 3.0:
            raise AccountError("Cookie 导入操作过于频繁，请稍后再试")
        cookie_import_last_attempt[session.token] = now
    try:
        session.client.load_imported_cookies(body.get("cookies"))
        value = session.client.account_status()
    except NeteaseError as exc:
        session.client.session.cookies.clear()
        raise AccountError("Cookie 格式不正确或已失效") from exc
    profile = public_profile(value.get("profile"))
    if not value.get("logged_in") or not profile or profile.get("userId") is None:
        session.client.session.cookies.clear()
        raise AccountError("Cookie 无效或已过期")
    return profile


def _new_qr_chain_id() -> str:
    device_id = f"unknown-{secrets.randbelow(1_000_000)}"
    return f"v1_{device_id}_web_login_{int(time.time() * 1000)}"


@app.post("/api/auth/qr/start")
def start_qr_login() -> Any:
    """Start a current-web QR login challenge for registration or reauth."""
    body = json_body()
    with auth_sessions.open(auth_token(), create=True, touch=True) as session:
        assert session is not None
        # A logged-in site account may use this endpoint for reauthentication;
        # otherwise it is a pending registration verification.
        purpose = "reauth" if session.profile else "register"
        if purpose == "reauth":
            identity, failure = authorized_identity()
            if failure is not None:
                return failure
            if identity is None:
                purpose = "register"
        key_data = session.client.qr_login_start(
            user_agent=str(body.get("browser_user_agent") or "")
        )
        key = key_data["unikey"]
        chain_id = _new_qr_chain_id()
        session.pending_qr = {
            "key": key,
            "chain_id": chain_id,
            "purpose": purpose,
            "status": "waiting",
            "created_at": int(time.time()),
        }
        response = jsonify(
            {
                "ok": True,
                "qr_url": session.client.qr_login_url(key, chain_id),
                "expires_in": 300,
            }
        )
        set_auth_cookie(response, session.token)
        return response


@app.post("/api/auth/qr/poll")
def poll_qr_login() -> Any:
    body = json_body()
    with auth_sessions.open(auth_token(), touch=True) as session:
        if session is None or not session.pending_qr:
            return error_response("没有正在进行的扫码登录", "qr_not_started", 400)
        pending = session.pending_qr
        if int(pending.get("created_at") or 0) < int(time.time()) - 300:
            session.pending_qr = None
            return error_response("二维码已过期，请重新获取", "qr_expired", 400)
        result = session.client.qr_login_poll(
            str(pending.get("key") or ""),
            str(pending.get("chain_id") or ""),
            secure_captcha=True,
            yd_device_token=str(body.get("yd_device_token") or ""),
            user_agent=str(body.get("browser_user_agent") or ""),
        )
        code = result.get("code")
        if code == 801:
            return jsonify({"ok": True, "status": "waiting", "code": code})
        if code == 802:
            pending["status"] = "scanned"
            return jsonify({"ok": True, "status": "scanned", "code": code})
        if code in {800, 810, 811}:
            session.pending_qr = None
            return error_response("二维码已失效，请重新获取", "qr_expired", 400)
        if code in {8821, 8830}:
            session.pending_qr = None
            return error_response(
                "网易云拒绝了这次扫码验证（设备或登录链路未通过风控），请刷新二维码后重试",
                "qr_risk_rejected",
                401,
            )
        if code != 803:
            return error_response(
                result.get("message") or result.get("msg") or "扫码登录失败",
                "qr_login_failed",
                502,
            )
        profile = public_profile(
            result.get("profile") or session.client.account_status().get("profile")
        )
        if not profile or profile.get("userId") is None:
            return error_response("网易云没有返回有效的用户身份", "qr_profile_missing", 502)
        pending["status"] = "verified"
        pending["profile"] = profile
        if pending.get("purpose") == "reauth" and session.profile:
            identity = session.profile
            if str(profile.get("userId")) != str(identity.get("netease_user_id")):
                session.client.session.cookies.clear()
                session.pending_qr = None
                return error_response(
                    "只能重新验证当前网站账号绑定的网易云账号",
                    "not_allowed",
                    403,
                )
            netease_bindings.save(profile["userId"], profile, session.client.export_cookies())
            session.client.session.cookies.clear()
            session.pending_qr = None
            return jsonify({"ok": True, "status": "verified", "profile": profile})
        return jsonify({"ok": True, "status": "verified", "profile": profile})


@app.post("/api/auth/login")
def login() -> Any:
    body = json_body()
    username = required_string(body, "username")
    password = required_string(body, "password")
    account = website_accounts.authenticate(username, password)
    if account is None:
        return error_response("网站用户名或密码不正确", "invalid_credentials", 401)
    role = allowlist.role_for(account["netease_user_id"])
    if role is None:
        return error_response("该网站账号对应的网易云账号已不在允许名单中", "not_allowed", 403)
    previous_token = None
    with auth_sessions.open(auth_token(), create=True, touch=True) as session:
        assert session is not None
        session.profile = {
            "username": account["username"],
            "netease_user_id": account["netease_user_id"],
            "nickname": account["nickname"],
            "avatarUrl": account["avatarUrl"],
        }
        previous_token = session.token
    token = auth_sessions.rotate(previous_token)
    auth_sessions.cleanup_expired()
    response = jsonify({"ok": True, "message": "登录成功", "profile": account, "role": role})
    set_auth_cookie(response, token)
    return response


@app.post("/api/auth/register")
def register() -> Any:
    body = json_body()
    username = required_string(body, "username")
    password = required_string(body, "password")
    cookie_mode = "cookies" in body
    qr_mode = bool(body.get("qr"))
    phone = ""
    captcha = ""
    country_code = clean_country_code(body.get("country_code", "86"))
    if not qr_mode and not cookie_mode:
        phone = required_string(body, "phone")
        captcha = required_string(body, "captcha")
    previous_token = None
    profile = None
    rejected = False
    with auth_sessions.open(auth_token(), create=True, touch=True) as session:
        assert session is not None
        session.discard_cookies_on_error = cookie_mode
        if cookie_mode:
            profile = _cookie_import_profile(session, body)
        elif qr_mode:
            pending = session.pending_qr or {}
            if pending.get("purpose") != "register" or pending.get("status") != "verified":
                raise AccountError("请先完成网易云扫码验证")
            profile = public_profile(pending.get("profile") or {})
        else:
            result = session.client.login_with_captcha(phone, captcha, country_code)
            profile = public_profile(result.get("profile") or session.client.account_status().get("profile"))
        if not profile or profile.get("userId") is None:
            raise AccountError("网易云没有返回有效的用户身份")
        # Validate the local account before potentially bootstrapping or
        # changing the allowlist, so a duplicate username cannot leave an
        # orphaned first administrator entry.
        website_accounts.validate_new_account(username, password)
        try:
            role = allowlist.authorize_login(profile)
        except UserNotAllowed:
            session.client.session.cookies.clear()
            session.pending_qr = None
            session.profile = None
            rejected = True
            role = None
        if rejected:
            previous_token = session.token
        else:
            account = website_accounts.create(
                username,
                password,
                netease_user_id=str(profile["userId"]),
                nickname=profile["nickname"],
                avatar_url=profile["avatarUrl"],
            )
            netease_bindings.save(profile["userId"], profile, session.client.export_cookies())
            session.client.session.cookies.clear()
            session.pending_qr = None
            session.csrf_token = secrets.token_urlsafe(32)
            session.profile = {
                "username": account["username"],
                "netease_user_id": account["netease_user_id"],
                "nickname": account["nickname"],
                "avatarUrl": account["avatarUrl"],
            }
            previous_token = session.token
    if rejected:
        auth_sessions.delete(previous_token)
        response, status_code = error_response("该网易云账号不在允许名单中", "not_allowed", 403)
        response.delete_cookie(SESSION_COOKIE, path=SESSION_COOKIE_PATH, samesite="Lax")
        return response, status_code
    token = auth_sessions.rotate(previous_token)
    response = jsonify({"ok": True, "message": "网站账号创建成功", "profile": account, "role": role})
    set_auth_cookie(response, token)
    return response


@app.post("/api/auth/reauth")
@member_required
def reauthenticate_netease() -> Any:
    body = json_body()
    cookie_mode = "cookies" in body
    qr_mode = bool(body.get("qr"))
    phone = ""
    captcha = ""
    country_code = clean_country_code(body.get("country_code", "86"))
    if not qr_mode and not cookie_mode:
        phone = required_string(body, "phone")
        captcha = required_string(body, "captcha")
    identity = g.current_user
    with auth_sessions.open(auth_token(), touch=True) as session:
        assert session is not None
        session.discard_cookies_on_error = cookie_mode
        if cookie_mode:
            profile = _cookie_import_profile(session, body)
        elif qr_mode:
            pending = session.pending_qr or {}
            if pending.get("purpose") != "reauth" or pending.get("status") != "verified":
                raise AccountError("请先完成网易云扫码验证")
            profile = public_profile(pending.get("profile") or {})
        else:
            result = session.client.login_with_captcha(phone, captcha, country_code)
            profile = public_profile(result.get("profile") or session.client.account_status().get("profile"))
        if not profile or str(profile.get("userId")) != str(identity["netease_user_id"]):
            session.client.session.cookies.clear()
            session.pending_qr = None
            raise UserNotAllowed("只能重新验证当前网站账号绑定的网易云账号")
        netease_bindings.save(profile["userId"], profile, session.client.export_cookies())
        session.client.session.cookies.clear()
        session.pending_qr = None
        session.csrf_token = secrets.token_urlsafe(32)
    return jsonify({"ok": True, "message": "网易云账号已重新验证"})


@app.get("/api/auth/netease-status")
@member_required
def netease_binding_status() -> Any:
    """Check the current site's bound NetEase Cookie with an authenticated profile call."""
    identity = g.current_user
    user_id = str(identity.get("netease_user_id") or "").strip()
    binding = netease_bindings.load(user_id)
    if not binding:
        return jsonify(
            {
                "ok": True,
                "valid": False,
                "needs_reauth": True,
                "message": "当前网站账号尚未保存可用的网易云绑定",
            }
        )
    try:
        with current_netease_client() as client:
            value = client.account_status()
    except NeteaseError as exc:
        if is_netease_auth_failure(exc) or exc.code in {301, -110}:
            return jsonify(
                {
                    "ok": True,
                    "valid": False,
                    "needs_reauth": True,
                    "message": "网易云 Cookie 已失效，请重新验证原绑定账号",
                }
            )
        raise
    profile = public_profile(value.get("profile"))
    if not value.get("logged_in") or not profile or str(profile.get("userId")) != user_id:
        return jsonify(
            {
                "ok": True,
                "valid": False,
                "needs_reauth": True,
                "message": "网易云 Cookie 已失效，请重新验证原绑定账号",
            }
        )
    return jsonify(
        {
            "ok": True,
            "valid": True,
            "needs_reauth": False,
            "profile": public_profile(profile),
            "message": "网易云 Cookie 当前有效",
        }
    )


@app.post("/api/auth/logout")
def logout() -> Any:
    token = auth_token()
    with auth_sessions.open(token, touch=False) as session:
        if session is not None:
            session.profile = None
    auth_sessions.delete(token)
    response = jsonify({"ok": True, "message": "已退出登录并删除本地会话"})
    response.delete_cookie(SESSION_COOKIE, path=SESSION_COOKIE_PATH, samesite="Lax")
    return response


@app.get("/api/search")
@member_required
def search() -> Any:
    query = (request.args.get("q") or "").strip()
    if not query:
        return error_response("请输入歌名或“歌名 歌手”", "invalid_query", 400)
    with anonymous_netease_client() as client:
        songs = client.search_songs(query)
    return jsonify({"ok": True, "songs": songs})


@app.post("/api/song/inspect")
@member_required
def inspect_song() -> Any:
    song_id = body_song_id()
    with anonymous_netease_client() as client:
        song = SongDownloadService(client, OUTPUTS).inspect(song_id)
    return jsonify({"ok": True, "song": song, "local": song_local_status(song_id)})


@app.get("/api/song/local/<int:song_id>")
@member_required
def song_local(song_id: int) -> Any:
    return jsonify({"ok": True, "local": song_local_status(song_id)})


@app.post("/api/song/download")
@member_required
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
        try:
            with current_netease_client() as client:
                result = SongDownloadService(client, OUTPUTS).download(song_id, level)
        except NeteaseError as first_error:
            if not is_netease_auth_failure(first_error):
                raise
            try:
                with anonymous_netease_client() as client:
                    result = SongDownloadService(client, OUTPUTS).download(song_id, level)
            except NeteaseError as anonymous_error:
                if is_netease_auth_failure(anonymous_error):
                    raise NeteaseError(
                        "网易云绑定已失效；免费歌曲可匿名下载，付费歌曲请重新验证原绑定账号",
                        code="netease_reauth_required",
                    ) from anonymous_error
                raise
        result["local"] = local_song_status(OUTPUTS, song_id)
        return jsonify({"ok": True, "result": result})
    finally:
        finish_download(song_id)


@app.post("/api/video/preview")
@member_required
def video_preview() -> Any:
    body = json_body()
    song_id = video_song_id(body.get("song", ""))
    options = VideoOptions.from_mapping(body.get("options"))
    project = VideoProject.load(OUTPUTS, song_id)
    fingerprint = video_options_fingerprint(options)
    destination = project.directory / f"video_preview_{fingerprint}.png"
    result = render_preview(project, options, destination)
    result["url"] = artifact_url(song_id, destination.name, destination.stat().st_mtime_ns)
    return jsonify({"ok": True, "preview": result})


@app.post("/api/video/background")
@member_required
def video_background() -> Any:
    song_id = video_song_id(request.form.get("song", ""))
    upload = request.files.get("background")
    if upload is None or not upload.filename:
        return error_response("请选择背景图片", "background_missing", 400)
    project = VideoProject.load(OUTPUTS, song_id)
    save_custom_background(upload, project.custom_background_path)
    return jsonify({"ok": True, "message": "自定义背景已保存"})


@app.post("/api/video/render")
@member_required
def video_render() -> Any:
    body = json_body()
    song_id = video_song_id(body.get("song", ""))
    options = VideoOptions.from_mapping(body.get("options"))
    # Fail early before accepting a background job.
    project = VideoProject.load(OUTPUTS, song_id)
    job = video_jobs.start(song_id, options, project.song)
    return jsonify({"ok": True, "job": job})


@app.get("/api/video/queue")
@member_required
def video_queue() -> Any:
    queue = video_jobs.queue_status()
    if queue.get("recent"):
        queue["recent"] = job_with_artifact_url(queue["recent"])
    return jsonify({"ok": True, "queue": queue})


@app.get("/api/video/local/<int:song_id>")
@member_required
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
@member_required
def video_job(job_id: str) -> Any:
    job = video_jobs.get(job_id)
    if job is None:
        return error_response("没有找到该视频任务", "job_not_found", 404)
    if job.get("status") == "done" and job.get("result"):
        job = job_with_artifact_url(job)
    return jsonify({"ok": True, "job": job})


@app.get("/api/video/artifact/<int:song_id>/<filename>")
@member_required
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


@app.get("/api/admin/users")
@admin_required
def admin_users() -> Any:
    return jsonify({"ok": True, "users": allowlist.snapshot()})


@app.get("/api/admin/search-users")
@admin_required
def admin_search_users() -> Any:
    query = (request.args.get("q") or "").strip()
    if not query:
        return error_response("请输入网易云昵称或用户关键词", "invalid_query", 400)
    # User search is a logged-in web feature in the current NetEase API.  Use
    # the administrator's bound client when available instead of the
    # anonymous fallback used by public song search.
    with current_netease_client() as client:
        users = client.search_users(query)
    return jsonify({"ok": True, "users": users})


@app.post("/api/admin/users")
@admin_required
def admin_add_user() -> Any:
    body = json_body()
    user_id = str(body.get("userId") or body.get("user_id") or "").strip()
    role = str(body.get("role") or "user").strip()
    if not user_id.isdigit() or int(user_id) <= 0:
        return error_response("用户 ID 无效", "invalid_user_id", 400)
    # The search response already contains the profile fields needed by the
    # allowlist.  NetEase's legacy user-detail endpoint is no longer available,
    # so do not make a second upstream request here.
    submitted_profile = body.get("profile")
    if not isinstance(submitted_profile, dict):
        return error_response("搜索结果已失效，请重新搜索后添加", "profile_required", 400)
    if str(submitted_profile.get("userId") or submitted_profile.get("user_id") or "").strip() != user_id:
        return error_response("用户资料与用户 ID 不匹配，请重新搜索", "profile_mismatch", 400)
    profile = {
        "userId": user_id,
        "nickname": str(submitted_profile.get("nickname") or "网易云用户"),
        "avatarUrl": str(submitted_profile.get("avatarUrl") or ""),
    }
    entry = allowlist.add(profile, role, added_by=str(g.current_user["netease_user_id"]))
    return jsonify({"ok": True, "user": entry})


@app.delete("/api/admin/users/<user_id>")
@admin_required
def admin_delete_user(user_id: str) -> Any:
    allowlist.delete(user_id, actor_id=str(g.current_user["netease_user_id"]))
    return jsonify({"ok": True, "message": "已从允许名单删除"})


@app.errorhandler(NeteaseError)
def handle_netease_error(error: NeteaseError) -> Any:
    status_code = 401 if error.code in {301, -110, "audio_forbidden", "netease_reauth_required"} else 502
    return error_response(str(error), error.code, status_code)


@app.errorhandler(VideoError)
def handle_video_error(error: VideoError) -> Any:
    return error_response(str(error), "video_error", 400)


@app.errorhandler(AllowlistError)
def handle_allowlist_error(error: AllowlistError) -> Any:
    return error_response(str(error), "allowlist_error", 400)


@app.errorhandler(UserNotAllowed)
def handle_user_not_allowed(error: UserNotAllowed) -> Any:
    return error_response(str(error), "not_allowed", 403)


@app.errorhandler(AccountError)
def handle_account_error(error: AccountError) -> Any:
    return error_response(str(error), "account_error", 400)


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
        path=SESSION_COOKIE_PATH,
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
        profile = session.profile if session is not None else None
        client = NeteaseClient()
        if isinstance(profile, dict):
            binding = netease_bindings.load(profile.get("netease_user_id"))
            if binding:
                try:
                    client.load_cookies(binding.get("cookies") or [])
                except (KeyError, TypeError, ValueError):
                    client.session.cookies.clear()
        yield client


@contextmanager
def anonymous_netease_client() -> Iterator[NeteaseClient]:
    yield NeteaseClient()


def is_netease_auth_failure(error: NeteaseError) -> bool:
    return error.code in {301, -110, "audio_forbidden", "netease_reauth_required"}


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
        result["url"] = artifact_url(job["song_id"], path.name, path.stat().st_mtime_ns)
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
                "url": artifact_url(song_id, path.name, stat.st_mtime_ns),
            }
        )
    videos.sort(key=lambda item: (item["updated_at"], item["filename"]), reverse=True)
    return videos


def artifact_url(song_id: int, filename: str, version: int | None = None) -> str:
    """Build a proxy-friendly relative URL for a generated artifact."""
    path = f"{BASE_PATH}/api/video/artifact/{song_id}/{quote(filename, safe='')}"
    return f"{path}?version={version}" if version is not None else path


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


def tls_context_from_env() -> tuple[str, str] | None:
    """Return an optional certificate/key pair for local HTTPS testing."""
    certificate_value = os.environ.get("CLOUDMUSIC2KTV_TLS_CERT", "").strip()
    key_value = os.environ.get("CLOUDMUSIC2KTV_TLS_KEY", "").strip()
    if not certificate_value and not key_value:
        return None
    if not certificate_value or not key_value:
        raise RuntimeError(
            "CLOUDMUSIC2KTV_TLS_CERT 和 CLOUDMUSIC2KTV_TLS_KEY 必须同时设置"
        )

    def resolve(value: str) -> Path:
        candidate = Path(os.path.expandvars(value)).expanduser()
        return candidate if candidate.is_absolute() else ROOT / candidate

    certificate = resolve(certificate_value)
    key = resolve(key_value)
    if not certificate.is_file():
        raise RuntimeError(f"HTTPS 证书文件不存在：{certificate}")
    if not key.is_file():
        raise RuntimeError(f"HTTPS 私钥文件不存在：{key}")
    return str(certificate), str(key)


if __name__ == "__main__":
    host = os.environ.get("CLOUDMUSIC2KTV_HOST", "0.0.0.0")
    port = int(os.environ.get("CLOUDMUSIC2KTV_PORT", "7860"))
    ssl_context = tls_context_from_env()
    if ssl_context:
        print("HTTPS 已启用（证书由 CLOUDMUSIC2KTV_TLS_CERT 提供）")
    app.run(host=host, port=port, debug=False, threaded=True, ssl_context=ssl_context)
