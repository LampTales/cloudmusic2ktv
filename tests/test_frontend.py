import frontend_server


def test_standalone_frontend_serves_assets_and_runtime_config(monkeypatch):
    monkeypatch.setenv("CLOUDMUSIC2KTV_FRONTEND_BASE_PATH", "/ktv")
    monkeypatch.setenv("CLOUDMUSIC2KTV_API_ORIGIN", "http://127.0.0.1:7860")
    client = frontend_server.frontend_app.test_client()

    page = client.get("/")
    assert page.status_code == 200
    assert b"static/app.css" in page.data
    assert b"config.js" in page.data

    config = client.get("/config.js")
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
    monkeypatch.setattr(frontend_server.requests, "request", fake_request)
    response = frontend_server.frontend_app.test_client().get(
        "/api/video/artifact/1/test.mp4", headers={"Range": "bytes=0-3"}
    )

    assert response.status_code == 206
    assert response.data == b"test"
    assert calls[0][0:2] == (
        "GET",
        "http://127.0.0.1:17860/api/video/artifact/1/test.mp4",
    )
    assert calls[0][2]["headers"]["Range"] == "bytes=0-3"
