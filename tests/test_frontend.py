from pathlib import Path

import frontend_server


FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"


def test_standalone_frontend_serves_assets_and_runtime_config(monkeypatch):
    monkeypatch.setenv("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH", "/ktv")
    monkeypatch.setenv("CLOUDMUSIC2KTV_API_ORIGIN", "http://127.0.0.1:7860")
    client = frontend_server.create_frontend_app().test_client()

    root = client.get("/")
    assert root.status_code == 302
    assert root.headers["Location"] == "/ktv/"

    page = client.get("/ktv/")
    assert page.status_code == 200
    assert b"static/app.css" in page.data
    assert b"config.js" in page.data

    assert client.get("/ktv/static/app.css").status_code == 200

    config = client.get("/ktv/config.js")
    assert config.status_code == 200
    assert b"/ktv" in config.data
    assert b"http://127.0.0.1:7860" in config.data


def test_frontend_dev_proxy_forwards_api_requests(monkeypatch):
    class FakeResponse:
        status_code = 206
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": "4",
            "Content-Range": "bytes 0-3/4",
            "Set-Cookie": "cloudmusic2ktv_session=test; HttpOnly; Path=/ktv; SameSite=Lax",
        }

        def iter_content(self, chunk_size=0):
            yield b"test"

        def close(self):
            pass

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse()

    monkeypatch.setenv("CLOUDMUSIC2KTV_BACKEND_ORIGIN", "http://127.0.0.1:17860")
    monkeypatch.setenv("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH", "/ktv")
    monkeypatch.setattr(frontend_server.requests, "request", fake_request)
    client = frontend_server.create_frontend_app().test_client()
    response = client.get(
        "/ktv/api/video/artifact/1/test.mp4",
        headers={"Range": "bytes=0-3", "Host": "frontend.test:18080"},
    )

    assert response.status_code == 206
    assert response.data == b"test"
    assert "Path=/ktv" in response.headers["Set-Cookie"]
    assert calls[0][0:2] == (
        "GET",
        "http://127.0.0.1:17860/api/video/artifact/1/test.mp4",
    )
    assert calls[0][2]["headers"]["Range"] == "bytes=0-3"
    assert calls[0][2]["headers"]["X-Forwarded-Host"] == "frontend.test:18080"
    assert calls[0][2]["headers"]["X-Forwarded-Prefix"] == "/ktv"
    assert calls[0][2]["headers"]["X-Forwarded-Proto"] == "http"
    assert client.get("/api/status").status_code == 404


def test_standalone_frontend_keeps_root_routes_without_a_base_path(monkeypatch):
    monkeypatch.delenv("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH", raising=False)
    client = frontend_server.create_frontend_app().test_client()

    assert client.get("/").status_code == 200
    assert client.get("/config.js").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_login_uses_password_manager_form_semantics():
    page = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")

    assert '<form id="websiteLoginForm" autocomplete="on">' in page
    assert 'name="username" type="text"' in page
    assert 'autocomplete="section-login username"' in page
    assert 'name="password" type="password"' in page
    assert 'autocomplete="section-login current-password"' in page
    assert 'id="login" class="primary" type="submit"' in page
    assert 'id="searchInput" name="song-search" type="search"' in page


def test_system_share_payload_contains_only_the_video_url():
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const shareData = {url};" in script
    assert 'text: "CloudMusic2KTV 视频"' not in script
    assert "new URL(resolveBackendUrl(value), window.location.href).href" in script
