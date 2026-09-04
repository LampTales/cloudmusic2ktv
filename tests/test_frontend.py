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


def test_playlist_page_has_a_separate_navigation_view():
    page = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert page.count('<nav class="page-nav"') == 1
    nav = page.split('<nav class="page-nav"', 1)[1].split("</nav>", 1)[0]
    assert nav.count("<a ") == 4
    assert '<a href="#playlists">我的歌单</a>' in page
    assert nav.index('href="#videoBuilder"') < nav.index('href="#playlists"')
    assert 'id="playlistLayout" class="playlist-layout"' in page
    playlist_info = page.split('<div class="playlist-info">', 1)[1].split('</div>\n            </div>', 1)[0]
    assert 'id="playlistTrackSearch"' in playlist_info
    assert 'maxlength="100"' in playlist_info
    assert 'id="workbench"' in page
    assert 'api("/api/playlists"' in script
    assert 'api("/api/playlists/refresh", {method: "POST", body: "{}"})' in script
    assert 'window.scrollTo(0, 0)' in script
    assert 'document.body.classList.toggle("playlist-view", playlistView)' in script
    assert 'finally { busy(button, false); }' in script
    assert "setApplicationView(\"workbench\", \"songPreview\", false)" in script


def test_playlist_state_is_isolated_between_website_accounts():
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    reset = script.split("function resetPlaylistState", 1)[1].split(
        "function accountPlaylistKey", 1
    )[0]
    status = script.split("async function refreshStatus", 1)[1].split(
        "async function inspectSong", 1
    )[0]
    logout = script.split('$("#logout").addEventListener', 1)[1].split(
        '$("#inspect").addEventListener', 1
    )[0]

    assert "let playlistAccountKey = null" in script
    assert "let playlistListRequest = 0" in script
    assert "playlistListRequest += 1" in reset
    assert "playlistTrackRequest += 1" in reset
    assert "playlistsCache = []" in reset
    assert "selectedPlaylistId = null" in reset
    assert '$("#playlistDetailContent").classList.add("hidden")' in reset
    assert "nextPlaylistAccountKey !== playlistAccountKey" in status
    assert "if (requestNumber !== playlistListRequest) return" in script
    assert "playlistAccountKey = null" in logout
    assert "resetPlaylistState()" in logout
    assert script.count("await refreshAfterNeteaseReauthentication()") == 3


def test_playlist_images_use_small_netease_cdn_thumbnails():
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function neteaseThumbnailUrl(value, size)" in script
    assert 'url.hostname.endsWith(".music.126.net")' in script
    assert 'url.searchParams.set("param", `${pixels}y${pixels}`)' in script
    assert "setNeteaseThumbnail(image, playlist.cover_url, 48, 96)" in script
    assert 'setNeteaseThumbnail($("#playlistCover"), playlist.cover_url, 150, 300)' in script
    assert "setNeteaseThumbnail(image, song.cover_url, 48, 96)" in script
    assert "image.src = song.cover_url" not in script
    assert '$("#playlistCover").removeAttribute("srcset")' in script


def test_login_uses_password_manager_form_semantics():
    page = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")

    assert '<form id="websiteLoginForm" autocomplete="on">' in page
    assert 'name="username" type="text"' in page
    assert 'autocomplete="section-login username"' in page
    assert 'name="password" type="password"' in page
    assert 'autocomplete="section-login current-password"' in page
    assert 'id="login" class="primary" type="submit"' in page
    assert 'id="searchInput" name="song-search" type="search"' in page


def test_rare_netease_identity_confirmation_has_an_isolated_modal():
    page = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="identityConfirmationModal"' in page
    assert 'id="confirmIdentityConfirmation"' in page
    assert 'id="cancelIdentityConfirmation"' in page
    assert 'error.code === "netease_identity_confirmation_required"' in script
    assert 'payload.identity_confirmation = true' in script
    assert '"/api/auth/identity-confirmation/confirm"' in script
    assert '"/api/auth/identity-confirmation/cancel"' in script
    assert 'notify("网易云账号已重新验证")' in script
    assert 'notify("网易云绑定已更新")' not in script


def test_system_share_payload_contains_only_the_video_url():
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const shareData = {url};" in script
    assert 'text: "CloudMusic2KTV 视频"' not in script
    assert "new URL(resolveBackendUrl(value), window.location.href).href" in script


def test_highlight_mode_is_primary_and_resolution_is_advanced():
    page = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    highlight = page.index('id="lyricHighlightMode"')
    advanced = page.index('<details class="advanced-options">')
    resolution = page.index('id="videoResolution"')
    assert highlight < advanced < resolution
    assert '<div class="advanced-select-grid">' in page
    assert ".advanced-select-grid { display: grid; grid-template-columns: 1fr 1fr;" in (
        FRONTEND_ROOT / "static" / "app.css"
    ).read_text(encoding="utf-8")
    assert '<option value="line">整句点亮（不扫色）</option>' in page
    assert '<option value="sweep">匀速扫色</option>' in page
    assert 'const lyricHighlightMode = $("#lyricHighlightMode")?.value || "line"' in script
    assert "lyric_highlight_mode: lyricHighlightMode" in script


def test_mobile_controls_use_two_columns_and_keep_action_labels_single_line():
    css = (FRONTEND_ROOT / "static" / "app.css").read_text(encoding="utf-8")

    assert ".material-prep { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in css
    assert ".material-prep-copy { grid-column: 1 / -1; }" in css
    assert ".option-grid { grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert ".search-row button { white-space: nowrap; }" in css
    assert ".preview-actions button { flex: 0 0 auto; white-space: nowrap; }" in css
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'ready ? "重新下载" : "下载素材"' in script
    assert 'setResponsiveButtonLabel($("#refreshPreview"), "更新预览", "更新")' in script
    assert 'setResponsiveOptionLabel($("#lyricHighlightMode"), "line", "整句点亮（不扫色）", "整句点亮")' in script


def test_preview_media_cannot_expand_builder_columns():
    css = (FRONTEND_ROOT / "static" / "app.css").read_text(encoding="utf-8")

    assert ".builder-layout { min-width: 0;" in css
    assert ".preview-panel, .video-options { min-width: 0; }" in css
    assert ".preview-stage { position: relative; min-width: 0;" in css
    assert ".preview-stage img { display: none; width: 100%; max-width: 100%; min-width: 0;" in css
    assert "#previewPlaceholder { display: grid; min-width: 0; max-width: 100%;" in css
    assert ".preview-actions > span { min-width: 0;" in css
    assert "white-space: normal;" in css
    assert "-webkit-line-clamp: 2;" in css


def test_mobile_recent_queue_keeps_song_text_and_video_action_visible():
    css = (FRONTEND_ROOT / "static" / "app.css").read_text(encoding="utf-8")

    assert ".queue-recent { justify-content: space-between; }" in css
    assert ".queue-recent span { display: block; min-width: 0; flex: 1 1 auto; text-align: left; }" in css


def test_queue_completed_view_is_scrollable_and_selectable():
    css = (FRONTEND_ROOT / "static" / "app.css").read_text(encoding="utf-8")
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")

    assert "max-height: min(60vh, 520px)" in css
    assert "queueWaitingTab" in html and "queueCompletedTab" in html
    assert "completedTaskFilename" in script
    assert "已选中完成任务，可继续播放、投屏或下载" in script
    assert 'selectButton.textContent = "播放视频"' in script
    assert 'id="queueCount" class="queue-count" type="button" aria-haspopup="dialog">队列 0</button>' in html
    assert '$("#queueCount").textContent = `队列 ${queue.queued_count || 0}`' in script


def test_queue_polling_is_adaptive_and_pauses_when_hidden():
    script = (FRONTEND_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const QUEUE_ACTIVE_POLL_MS = 1500" in script
    assert "const QUEUE_IDLE_POLL_MS = 15000" in script
    assert "QUEUE_ERROR_MAX_POLL_MS = 30000" in script
    assert "document.addEventListener(\"visibilitychange\", queueVisibilityChanged)" in script
    assert "error?.status === 401 || error?.status === 403" in script
