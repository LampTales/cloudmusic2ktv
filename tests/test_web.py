import app as web_app
from cloudmusic2ktv.netease import NeteaseClient
from cloudmusic2ktv.sessions import FileSessionStore


def test_custom_background_upload_limit_is_32_mib():
    assert web_app.app.config["MAX_CONTENT_LENGTH"] == 32 * 1024 * 1024


def test_unknown_route_remains_404():
    response = web_app.app.test_client().get("/does-not-exist")
    assert response.status_code == 404


def test_video_submission_requires_a_logged_in_browser_session():
    response = web_app.app.test_client().post(
        "/api/video/render", json={"song": 123, "options": {}}
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "login_required"


def test_global_queue_status_is_available_without_login():
    response = web_app.app.test_client().get("/api/video/queue")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_local_video_status_lists_only_shareable_generated_mp4(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "OUTPUTS", tmp_path)
    directory = tmp_path / "123_artist_song"
    directory.mkdir()
    video = directory / "ktv_720p_012345abcdef.mp4"
    video.write_bytes(b"generated-video")
    (directory / "ktv_test_720p.mp4").write_bytes(b"test-video")

    response = web_app.app.test_client().get("/api/video/local/123")

    assert response.status_code == 200
    local = response.get_json()["local"]
    assert local["ready"] is True
    assert len(local["videos"]) == 1
    assert local["videos"][0]["filename"] == video.name
    assert local["videos"][0]["resolution"] == "720p"
    assert local["videos"][0]["size"] == len(b"generated-video")
    assert local["videos"][0]["url"].startswith(
        "/api/video/artifact/123/ktv_720p_012345abcdef.mp4?"
    )


def test_video_artifact_supports_head_and_byte_ranges_without_source_materials(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "OUTPUTS", tmp_path)
    directory = tmp_path / "123_artist_song"
    directory.mkdir()
    video = directory / "ktv_1080p.mp4"
    video.write_bytes(b"0123456789")
    client = web_app.app.test_client()

    head = client.head("/api/video/artifact/123/ktv_1080p.mp4")
    ranged = client.get(
        "/api/video/artifact/123/ktv_1080p.mp4", headers={"Range": "bytes=2-5"}
    )

    assert head.status_code == 200
    assert head.content_length == 10
    assert ranged.status_code == 206
    assert ranged.data == b"2345"
    assert ranged.headers["Accept-Ranges"] == "bytes"


def test_local_video_status_is_missing_when_song_has_no_generated_video(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "OUTPUTS", tmp_path)

    response = web_app.app.test_client().get("/api/video/local/456")

    assert response.status_code == 200
    assert response.get_json()["local"] == {
        "status": "missing",
        "ready": False,
        "message": "本地还没有这首歌的完整视频",
        "videos": [],
    }


def test_captcha_creates_an_http_only_server_side_browser_session(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "auth_sessions", FileSessionStore(tmp_path))

    def fake_send_captcha(self, phone, country_code="86"):
        self.session.cookies.set("MUSIC_U", "server-secret", domain=".music.163.com", path="/")
        return {"code": 200}

    monkeypatch.setattr(NeteaseClient, "send_captcha", fake_send_captcha)
    response = web_app.app.test_client().post(
        "/api/auth/captcha", json={"phone": "10000000000", "country_code": "86"}
    )
    cookie = response.headers["Set-Cookie"]
    assert response.status_code == 200
    assert "cloudmusic2ktv_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "server-secret" not in cookie
    assert "server-secret" in next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
