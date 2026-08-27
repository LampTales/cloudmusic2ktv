"""Standalone development server for the CloudMusic2KTV frontend.

The backend in :mod:`app` owns all accounts, NetEase access and media files.
This process only serves the HTML/CSS/JavaScript assets and emits a tiny
runtime configuration script.  In production the same assets can be served by
Nginx; the `/api/` and `/api/video/artifact/` locations should then be proxied
to the backend over ZeroTier.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_file


ROOT = Path(__file__).resolve().parent
frontend_app = Flask(
    __name__,
    static_folder=str(ROOT / "static"),
    static_url_path="/static",
)


def backend_origin() -> str:
    return os.environ.get(
        "CLOUDMUSIC2KTV_BACKEND_ORIGIN", "http://127.0.0.1:7860"
    ).strip().rstrip("/")


@frontend_app.get("/")
def index():
    return send_file(ROOT / "templates" / "index.html")


@frontend_app.get("/config.js")
def config_js():
    base_path = os.environ.get("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH", "").strip().rstrip("/")
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
    response = frontend_app.response_class(payload, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    return response


@frontend_app.route("/api", defaults={"path": ""}, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@frontend_app.route("/api/<path:path>", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_api(path: str):
    """Development reverse proxy keeping browser requests same-origin."""
    target = f"{backend_origin()}/api/{path}" if path else f"{backend_origin()}/api"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    }
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


@frontend_app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "status": "healthy"})


if __name__ == "__main__":
    host = os.environ.get("CLOUDMUSIC2KTV_FRONTEND_HOST", "127.0.0.1")
    port = int(os.environ.get("CLOUDMUSIC2KTV_FRONTEND_PORT", "8080"))
    frontend_app.run(host=host, port=port, debug=False, threaded=True)
