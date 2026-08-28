"""Standalone development server for the CloudMusic2KTV frontend.

The backend in :mod:`app` owns all accounts, NetEase access and media files.
This process only serves the HTML/CSS/JavaScript assets and emits a tiny
runtime configuration script.  In production the same assets can be served by
Nginx.  With an external prefix such as `/ktv`, this server mounts the assets
below that prefix and strips it while proxying API requests to the backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, redirect, request, send_file, send_from_directory


ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = ROOT / "frontend"
API_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def normalize_base_path(value: str | None) -> str:
    """Return a safe URL mount prefix such as ``/ktv`` or an empty string."""
    text = str(value or "").strip()
    if not text or text == "/":
        return ""
    if "?" in text or "#" in text or "\\" in text:
        raise RuntimeError("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH 只能包含 URL 路径")
    if not text.startswith("/"):
        text = "/" + text
    parts = [part for part in text.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise RuntimeError("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH 不能包含 . 或 ..")
    return "/" + "/".join(parts)


def backend_origin() -> str:
    return os.environ.get(
        "CLOUDMUSIC2KTV_BACKEND_ORIGIN", "http://127.0.0.1:7860"
    ).strip().rstrip("/")


def runtime_config(base_path: str) -> Response:
    # Empty means same-origin, which is the production reverse-proxy setup.
    # Local split testing sets this explicitly to the backend port.
    api_origin = os.environ.get("CLOUDMUSIC2KTV_API_ORIGIN", "").strip().rstrip("/")
    payload = (
        "window.CLOUDMUSIC2KTV_BASE_PATH = "
        + json.dumps(base_path, ensure_ascii=True)
        + ";\n"
        + "window.CLOUDMUSIC2KTV_API_ORIGIN = "
        + json.dumps(api_origin, ensure_ascii=True)
        + ";\n"
    )
    response = Response(payload, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    return response


def proxy_api(path: str, base_path: str) -> Response | tuple[Response, int]:
    """Development reverse proxy keeping browser requests same-origin."""
    target = f"{backend_origin()}/api/{path}" if path else f"{backend_origin()}/api"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower()
        not in {
            "host",
            "content-length",
            "connection",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-prefix",
            "x-forwarded-proto",
        }
    }
    headers["X-Forwarded-For"] = request.remote_addr or ""
    headers["X-Forwarded-Host"] = request.host
    headers["X-Forwarded-Proto"] = request.scheme
    if base_path:
        headers["X-Forwarded-Prefix"] = base_path
    try:
        upstream = requests.request(
            request.method,
            target,
            params=request.args,
            headers=headers,
            data=request.get_data(cache=True),
            stream=True,
            timeout=(10, 3600),
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": {"message": f"后端连接失败：{exc}", "code": "backend_unavailable"}}), 502

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in {"connection", "keep-alive", "transfer-encoding", "content-encoding"}
    }

    if request.method == "HEAD":
        upstream.close()
        return Response(status=upstream.status_code, headers=response_headers)

    def stream_body():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    return Response(
        stream_body(),
        status=upstream.status_code,
        headers=response_headers,
        direct_passthrough=True,
    )


def create_frontend_app(base_path: str | None = None) -> Flask:
    """Build the development frontend at the configured external URL prefix."""
    configured_base = normalize_base_path(
        os.environ.get("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH")
        if base_path is None
        else base_path
    )
    app = Flask(__name__, static_folder=None)

    if configured_base:
        @app.get("/")
        def root_redirect() -> Response:
            return redirect(f"{configured_base}/", code=302)

        @app.get(configured_base)
        def base_redirect() -> Response:
            return redirect(f"{configured_base}/", code=308)

    @app.get(f"{configured_base}/")
    def index() -> Any:
        return send_file(FRONTEND_ROOT / "index.html")

    @app.get(f"{configured_base}/config.js")
    def config_js() -> Response:
        return runtime_config(configured_base)

    @app.get(f"{configured_base}/static/<path:filename>")
    def static_file(filename: str) -> Any:
        return send_from_directory(FRONTEND_ROOT / "static", filename)

    app.add_url_rule(
        f"{configured_base}/api",
        endpoint="proxy_api_root",
        view_func=lambda: proxy_api("", configured_base),
        methods=API_METHODS,
    )
    app.add_url_rule(
        f"{configured_base}/api/<path:path>",
        endpoint="proxy_api_path",
        view_func=lambda path: proxy_api(path, configured_base),
        methods=API_METHODS,
    )

    @app.get("/healthz")
    def healthz() -> Any:
        return jsonify({"ok": True, "status": "healthy"})

    return app


frontend_app = create_frontend_app()


if __name__ == "__main__":
    host = os.environ.get("CLOUDMUSIC2KTV_FRONTEND_HOST", "127.0.0.1")
    port = int(os.environ.get("CLOUDMUSIC2KTV_FRONTEND_PORT", "8080"))
    frontend_app.run(host=host, port=port, debug=False, threaded=True)
