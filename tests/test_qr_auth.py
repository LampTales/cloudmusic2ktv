import time

import pytest

import app as web_app
from cloudmusic2ktv.netease import NeteaseClient, NeteaseError
from cloudmusic2ktv.sessions import FileSessionStore
from tests.helpers import set_session_cookie


def test_qr_protocol_omits_unearned_captcha_and_preserves_challenges(monkeypatch):
    client = NeteaseClient()
    calls = []
    replies = iter([{"code": 200, "unikey": "key"}, {"code": 8821}, {"code": 803}, {"code": 8830}])

    def weapi(path, payload, **kwargs):
        calls.append((path, payload, kwargs.get("extra_headers", {})))
        return next(replies)

    monkeypatch.setattr(client, "weapi", weapi)
    client.qr_login_start(user_agent="browser")
    assert calls[-1][1] == {"type": 1, "noCheckToken": True}
    assert "x-loginmethod" not in calls[-1][2]
    assert client.qr_login_poll("key", "chain")["code"] == 8821
    assert "secureCaptcha" not in calls[-1][1]
    assert calls[-1][1]["type"] == 1
    assert calls[-1][1]["noCheckToken"] is True
    assert client.qr_login_poll("key", "chain", secure_captcha="validated")["code"] == 803
    assert calls[-1][1]["secureCaptcha"] == "validated"
    assert client.qr_login_poll("key", "chain")["code"] == 8830


def test_device_id_comes_from_netease_cookie_not_token(monkeypatch):
    client = NeteaseClient()

    def weapi(path, payload, **kwargs):
        assert path == "/weapi/middle/device-info/web/get"
        assert payload == {"ydDeviceType": "WebOnline", "ydDeviceToken": "fingerprint"}
        assert kwargs["extra_headers"]["User-Agent"] == "browser"
        client.session.cookies.set("sDeviceId", "YD-server-issued", domain=".music.163.com", path="/")
        return {"code": 200}

    monkeypatch.setattr(client, "weapi", weapi)
    assert client.prepare_qr_device("fingerprint", user_agent="browser") == "YD-server-issued"
    client.session.cookies.clear()
    monkeypatch.setattr(client, "weapi", lambda *a, **kw: {"code": 200, "sDeviceId": "not-a-cookie"})
    assert client.prepare_qr_device("fingerprint") == ""


def test_device_registration_failure_allows_official_fallback(monkeypatch):
    client = NeteaseClient()

    def fail(*args, **kwargs):
        raise NeteaseError("unavailable", code="network_error")

    monkeypatch.setattr(client, "weapi", fail)
    assert client.prepare_qr_device("token") == ""
    assert web_app._new_qr_chain_id().startswith("v1_unknown-")


@pytest.fixture
def qr_session(monkeypatch, tmp_path):
    sessions = FileSessionStore(tmp_path / "sessions")
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    with sessions.open(None, create=True) as session:
        token = session.token
        session.pending_qr = {
            "key": "key", "chain_id": "chain", "purpose": "register",
            "status": "waiting", "created_at": int(time.time()), "user_agent": "initial-browser",
        }
    browser = web_app.app.test_client()
    set_session_cookie(browser, token)
    return browser, sessions, token


def test_risk_verification_keeps_same_qr_and_can_finish(qr_session, monkeypatch):
    browser, sessions, token = qr_session
    attempts = []

    def poll(self, key, chain, **kwargs):
        attempts.append((key, chain, kwargs))
        if len(attempts) == 1:
            return {"code": 8821}
        self.session.cookies.set("MUSIC_U", "authenticated", domain=".music.163.com", path="/")
        return {"code": 803, "profile": {"userId": 101, "nickname": "test"}}

    monkeypatch.setattr(NeteaseClient, "qr_login_poll", poll)
    first = browser.post("/api/auth/qr/poll", json={})
    assert first.status_code == 200
    assert first.json["status"] == "verification_required"
    with sessions.open(token) as session:
        assert session.pending_qr["key"] == "key"
        assert session.pending_qr["status"] == "verification_required"
    second = browser.post("/api/auth/qr/poll", json={"secure_captcha": "proof", "browser_user_agent": "changed"})
    assert second.json["status"] == "verified"
    assert attempts[0][2]["secure_captcha"] is None
    assert attempts[1] == ("key", "chain", {
        "secure_captcha": "proof", "yd_device_token": "", "user_agent": "initial-browser",
    })
    with sessions.open(token) as session:
        assert session.pending_qr["status"] == "verified"
        assert session.client.session.cookies.get("MUSIC_U") == "authenticated"


@pytest.mark.parametrize("value", [True, False, {}, "", " " , "x" * 8193])
def test_captcha_must_be_a_nonempty_proof(qr_session, value):
    browser, _, _ = qr_session
    response = browser.post("/api/auth/qr/poll", json={"secure_captcha": value})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_captcha"


def test_qr_expiry_still_applies_after_risk_challenge(qr_session):
    browser, sessions, token = qr_session
    with sessions.open(token) as session:
        session.pending_qr.update(status="verification_required", created_at=int(time.time()) - 301)
    response = browser.post("/api/auth/qr/poll", json={"secure_captcha": "proof"})
    assert response.json["error"]["code"] == "qr_expired"


@pytest.mark.parametrize("device_id", ["YD-issued", "YD-example%2Fpart%2bvalue%3D"])
def test_start_registers_device_and_carries_cookie_between_requests(qr_session, monkeypatch, device_id):
    browser, sessions, token = qr_session
    calls = []

    def weapi(self, path, payload, **kwargs):
        calls.append(path)
        if path.endswith("device-info/web/get"):
            self.session.cookies.set("sDeviceId", device_id, domain=".music.163.com", path="/")
            return {"code": 200}
        assert self.session.cookies.get("sDeviceId") == device_id
        if path.endswith("qrcode/client/login"):
            assert kwargs["extra_headers"]["x-login-chain-id"] == chain_id
            return {"code": 801}
        return {"code": 200, "unikey": "new-key"}

    monkeypatch.setattr(NeteaseClient, "weapi", weapi)
    result = browser.post("/api/auth/qr/start", json={"yd_device_token": "token", "browser_user_agent": "browser"})
    assert result.status_code == 200
    assert f"chainId=v1_{device_id}_web_login_" in result.json["qr_url"]
    assert "%252" not in result.json["qr_url"]
    assert calls == ["/weapi/middle/device-info/web/get", "/weapi/login/qrcode/unikey"]
    with sessions.open(token) as session:
        assert session.client.session.cookies.get("sDeviceId") == device_id
        assert session.pending_qr["user_agent"] == "browser"
        chain_id = session.pending_qr["chain_id"]
        assert f"&chainId={chain_id}&hdw_device=" in result.json["qr_url"]
    assert browser.post("/api/auth/qr/poll", json={}).json["status"] == "waiting"


@pytest.mark.parametrize("device_id", [
    "", "YD-invalid%", "YD-invalid%2", "YD-invalid%GG", "YD-id&extra=1",
    "YD-id#fragment", "YD-id\r\nInjected: value", "x" * 129,
])
def test_malformed_device_cookie_still_falls_back(device_id):
    client = NeteaseClient()
    client.session.cookies.set("sDeviceId", device_id, domain=".music.163.com", path="/")
    assert client.prepare_qr_device("") == ""


@pytest.mark.parametrize("domain,expires", [("other.test", None), (".music.163.com", 1)])
def test_untrusted_or_expired_device_cookie_is_ignored(domain, expires):
    client = NeteaseClient()
    client.session.cookies.set("sDeviceId", "YD-example%2Fpart", domain=domain, path="/", expires=expires)
    assert client.prepare_qr_device("") == ""


@pytest.fixture
def account_flow(qr_session, monkeypatch, tmp_path):
    from cloudmusic2ktv.access import AllowlistStore
    from cloudmusic2ktv.accounts import NeteaseBindingStore, WebsiteAccountStore
    from cloudmusic2ktv.playlist_cache import PlaylistCache

    browser, sessions, token = qr_session
    users = AllowlistStore(tmp_path / "allowlist.json")
    accounts = WebsiteAccountStore(tmp_path / "accounts.json")
    bindings = NeteaseBindingStore(tmp_path / "bindings.json")
    cache = PlaylistCache()
    for name, value in [("allowlist", users), ("website_accounts", accounts),
                        ("netease_bindings", bindings), ("playlist_cache", cache)]:
        monkeypatch.setattr(web_app, name, value)

    def start(self, **kwargs):
        return {"unikey": "new-challenge"}

    def poll(self, *args, **kwargs):
        self.session.cookies.set("MUSIC_U", "renewed-cookie", domain=".music.163.com", path="/")
        return {"code": 803}  # Real web response needs the account lookup.

    monkeypatch.setattr(NeteaseClient, "prepare_qr_device", lambda *args, **kw: "YD-device%2Fvalue")
    monkeypatch.setattr(NeteaseClient, "qr_login_start", start)
    monkeypatch.setattr(NeteaseClient, "qr_login_poll", poll)
    monkeypatch.setattr(NeteaseClient, "account_status", lambda self: {
        "logged_in": True, "profile": {"userId": 2, "nickname": "qr-user", "avatarUrl": ""},
    })
    return browser, sessions, token, users, accounts, bindings, cache


def finish_qr(browser):
    assert browser.post("/api/auth/qr/start", json={}).status_code == 200
    result = browser.post("/api/auth/qr/poll", json={})
    assert result.status_code == 200
    assert result.json["status"] == "verified"


def test_qr_registration_bootstraps_root_and_rejects_duplicate_binding(account_flow):
    browser, sessions, token, users, accounts, bindings, _ = account_flow
    finish_qr(browser)
    result = browser.post("/api/auth/register", json={"username": "first", "password": "password", "qr": True})
    assert result.status_code == 200 and result.json["role"] == "root"
    assert users.role_for(2) == "root"
    assert accounts.authenticate("first", "password")["netease_user_id"] == "2"
    assert bindings.load(2)["cookies"][0]["value"] == "renewed-cookie"
    assert not sessions.is_authenticated(token)  # Session rotated after registration.
    other = web_app.app.test_client()
    finish_qr(other)
    duplicate = other.post("/api/auth/register", json={"username": "second", "password": "password", "qr": True})
    assert duplicate.status_code == 400
    assert accounts.authenticate("second", "password") is None
    assert accounts.by_netease_user(2)["username"] == "first"


def test_qr_registration_approval_login_and_access_revocation(account_flow):
    browser, sessions, token, users, accounts, bindings, _ = account_flow
    users.authorize_login({"userId": 1, "nickname": "root"})
    finish_qr(browser)
    registration = browser.post("/api/auth/register", json={"username": "newuser", "password": "password", "qr": True})
    assert registration.status_code == 202 and registration.json["status"] == "pending"
    assert users.role_for(2) is None and users.application_for(2)
    assert accounts.by_netease_user(2) and bindings.load(2)
    credentials = {"username": "newuser", "password": "password"}
    assert browser.post("/api/auth/login", json=credentials).json["error"]["code"] == "pending_approval"
    with sessions.open(None, create=True) as root:
        root.profile = {"username": "root", "netease_user_id": "1"}
        root_token = root.token
    admin = web_app.app.test_client()
    set_session_cookie(admin, root_token)
    assert admin.post("/api/admin/applications/2/approve").status_code == 200
    assert browser.post("/api/auth/login", json=credentials).status_code == 200
    assert browser.get("/api/status").json["profile"]["netease_user_id"] == "2"
    assert users.role_for(2) == "user"
    users.delete(2, actor_id="1")
    assert browser.post("/api/auth/login", json=credentials).json["error"]["code"] == "not_allowed"


def setup_member(flow):
    browser, sessions, token, users, accounts, bindings, cache = flow
    users.authorize_login({"userId": 1, "nickname": "root"})
    users.add({"userId": 2, "nickname": "member"}, "user", added_by="1")
    accounts.create("member", "password", netease_user_id="2", nickname="member")
    bindings.save(2, {"userId": 2, "nickname": "member"},
                  [{"name": "MUSIC_U", "value": "old-cookie", "domain": ".music.163.com", "path": "/"}])
    with sessions.open(token) as session:
        session.profile = {"username": "member", "netease_user_id": "2", "nickname": "member"}
        session.pending_identity_confirmation = {"purpose": "reauth", "status": "waiting"}
        return session.csrf_token


def test_qr_reauth_matches_sms_and_cookie_post_login_cleanup(account_flow):
    previous_csrf = setup_member(account_flow)
    browser, sessions, token, users, accounts, bindings, cache = account_flow
    cache.get_playlists(2, lambda: [{"id": "old"}])
    finish_qr(browser)
    assert bindings.load(2)["cookies"][0]["value"] == "renewed-cookie"
    assert cache.get_playlists(2, lambda: [{"id": "fresh"}]) == [{"id": "fresh"}]
    with sessions.open(token) as session:
        assert session.profile["netease_user_id"] == "2"
        assert session.pending_qr is None
        assert session.pending_identity_confirmation is None
        assert session.csrf_token != previous_csrf
        assert not session.client.export_cookies()
    assert users.role_for(2) == "user"
    assert accounts.authenticate("member", "password")


def test_qr_reauth_wrong_account_does_not_replace_binding(account_flow, monkeypatch):
    setup_member(account_flow)
    browser, sessions, token, _, _, bindings, _ = account_flow
    monkeypatch.setattr(NeteaseClient, "account_status", lambda self: {
        "logged_in": True, "profile": {"userId": 3, "nickname": "wrong-account"},
    })
    assert browser.post("/api/auth/qr/start", json={}).status_code == 200
    rejected = browser.post("/api/auth/qr/poll", json={})
    assert rejected.status_code == 403
    assert bindings.load(2)["cookies"][0]["value"] == "old-cookie"
    assert bindings.load(3) is None
    with sessions.open(token) as session:
        assert session.pending_qr is None
        assert session.pending_identity_confirmation is None
        assert not session.client.export_cookies()


def test_revoked_member_cannot_finish_old_reauth_challenge(account_flow, monkeypatch):
    setup_member(account_flow)
    browser, sessions, token, users, _, bindings, _ = account_flow
    assert browser.post("/api/auth/qr/start", json={}).status_code == 200
    users.delete(2, actor_id="1")

    def unexpected_poll(*args, **kwargs):
        raise AssertionError("revoked members must not continue reauth")

    monkeypatch.setattr(NeteaseClient, "qr_login_poll", unexpected_poll)
    assert browser.post("/api/auth/qr/poll", json={}).status_code == 403
    assert bindings.load(2)["cookies"][0]["value"] == "old-cookie"
    with sessions.open(token) as session:
        assert session is None
