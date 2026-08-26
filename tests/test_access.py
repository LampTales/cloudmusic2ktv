import app as web_app

from cloudmusic2ktv.access import AllowlistStore
from cloudmusic2ktv.accounts import NeteaseBindingStore, WebsiteAccountStore
from cloudmusic2ktv.netease import NeteaseClient, NeteaseError
from cloudmusic2ktv.sessions import FileSessionStore
from tests.helpers import set_session_cookie


def member_client(monkeypatch, tmp_path, *, user_id="2", role="user"):
    sessions = FileSessionStore(tmp_path / "sessions")
    users = AllowlistStore(tmp_path / "allowlist.json")
    users.authorize_login({"userId": 1, "nickname": "管理员", "avatarUrl": ""})
    profile = {
        "website_username": "test-user",
        "netease_user_id": str(user_id),
        "nickname": "测试用户",
        "avatarUrl": "",
    }
    if user_id != "1":
        users.add({"userId": int(user_id), "nickname": "测试用户", "avatarUrl": ""}, role, added_by="1")
    with sessions.open(None, create=True) as session:
        token = session.token
        session.profile = profile
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    monkeypatch.setattr(web_app, "allowlist", users)
    client = web_app.app.test_client()
    set_session_cookie(client, token)
    return client


def test_empty_allowlist_bootstraps_first_successful_login(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "auth_sessions", FileSessionStore(tmp_path / "sessions"))
    users = AllowlistStore(tmp_path / "allowlist.json")
    monkeypatch.setattr(web_app, "allowlist", users)
    monkeypatch.setattr(web_app, "website_accounts", WebsiteAccountStore(tmp_path / "accounts.json"))
    monkeypatch.setattr(web_app, "netease_bindings", NeteaseBindingStore(tmp_path / "bindings.json"))

    monkeypatch.setattr(
        NeteaseClient,
        "login_with_captcha",
        lambda self, phone, captcha, country_code="86": {
            "code": 200,
            "profile": {"userId": 101, "nickname": "首位用户", "avatarUrl": ""},
        },
    )
    response = web_app.app.test_client().post(
        "/api/auth/register",
        json={"username": "alice", "password": "password", "phone": "1", "captcha": "2", "country_code": "86"},
    )

    assert response.status_code == 200
    assert users.snapshot()[0]["userId"] == "101"
    assert users.snapshot()[0]["role"] == "admin"
    assert response.get_json()["role"] == "admin"


def test_non_listed_login_is_rejected_and_does_not_leave_a_session(monkeypatch, tmp_path):
    sessions = FileSessionStore(tmp_path / "sessions")
    users = AllowlistStore(tmp_path / "allowlist.json")
    users.authorize_login({"userId": 101, "nickname": "管理员", "avatarUrl": ""})
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    monkeypatch.setattr(web_app, "allowlist", users)
    monkeypatch.setattr(web_app, "website_accounts", WebsiteAccountStore(tmp_path / "accounts.json"))
    monkeypatch.setattr(web_app, "netease_bindings", NeteaseBindingStore(tmp_path / "bindings.json"))
    monkeypatch.setattr(
        NeteaseClient,
        "login_with_captcha",
        lambda self, phone, captcha, country_code="86": {
            "code": 200,
            "profile": {"userId": 202, "nickname": "陌生用户", "avatarUrl": ""},
        },
    )

    response = web_app.app.test_client().post(
        "/api/auth/register",
        json={"username": "alice", "password": "password", "phone": "1", "captcha": "2", "country_code": "86"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "not_allowed"
    assert [user["userId"] for user in users.snapshot()] == ["101"]
    assert list((tmp_path / "sessions").glob("*.json")) == []


def test_admin_can_list_and_delete_regular_users_but_not_admins(monkeypatch, tmp_path):
    sessions = FileSessionStore(tmp_path / "sessions")
    users = AllowlistStore(tmp_path / "allowlist.json")
    admin = {"userId": 101, "nickname": "管理员", "avatarUrl": ""}
    regular = {"userId": 202, "nickname": "普通用户", "avatarUrl": ""}
    users.authorize_login(admin)
    users.add(regular, "user", added_by="101")
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    monkeypatch.setattr(web_app, "allowlist", users)
    with sessions.open(None, create=True) as session:
        token = session.token
        session.profile = {
            "website_username": "admin",
            "netease_user_id": "101",
            "nickname": "管理员",
            "avatarUrl": "",
        }
    client = web_app.app.test_client()
    set_session_cookie(client, token)

    listed = client.get("/api/admin/users")
    assert listed.status_code == 200
    assert {item["userId"] for item in listed.get_json()["users"]} == {"101", "202"}

    deleted = client.delete("/api/admin/users/202")
    assert deleted.status_code == 200
    assert users.role_for(202) is None
    blocked = client.delete("/api/admin/users/101")
    assert blocked.status_code == 400


def test_regular_member_cannot_access_admin_routes(monkeypatch, tmp_path):
    sessions = FileSessionStore(tmp_path / "sessions")
    users = AllowlistStore(tmp_path / "allowlist.json")
    users.authorize_login({"userId": 101, "nickname": "管理员", "avatarUrl": ""})
    users.add({"userId": 202, "nickname": "普通用户", "avatarUrl": ""}, "user", added_by="101")
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    monkeypatch.setattr(web_app, "allowlist", users)
    with sessions.open(None, create=True) as session:
        token = session.token
        session.profile = {
            "website_username": "user",
            "netease_user_id": "202",
            "nickname": "普通用户",
            "avatarUrl": "",
        }
    client = web_app.app.test_client()
    set_session_cookie(client, token)

    response = client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "admin_required"


def test_admin_user_search_uses_the_bound_client(monkeypatch, tmp_path):
    client = member_client(monkeypatch, tmp_path, user_id="1", role="admin")

    def unexpected_anonymous_client():
        raise AssertionError("admin search should use the bound client")

    monkeypatch.setattr(web_app, "anonymous_netease_client", unexpected_anonymous_client)
    monkeypatch.setattr(
        NeteaseClient,
        "search_users",
        lambda self, query: [{"userId": 202, "nickname": query, "avatarUrl": ""}],
    )
    response = client.get("/api/admin/search-users?q=rin")

    assert response.status_code == 200
    assert response.get_json()["users"][0]["userId"] == 202


def test_member_can_check_bound_netease_cookie(monkeypatch, tmp_path):
    client = member_client(monkeypatch, tmp_path, user_id="2")
    bindings = NeteaseBindingStore(tmp_path / "bindings.json")
    bindings.save("2", {"nickname": "测试用户", "avatarUrl": ""}, [{"name": "MUSIC_U", "value": "secret"}])
    monkeypatch.setattr(web_app, "netease_bindings", bindings)
    monkeypatch.setattr(
        NeteaseClient,
        "account_status",
        lambda self: {
            "logged_in": True,
            "profile": {"userId": 2, "nickname": "测试用户", "avatarUrl": ""},
        },
    )

    response = client.get("/api/auth/netease-status")

    assert response.status_code == 200
    assert response.get_json()["valid"] is True


def test_cookie_check_reports_expired_bound_cookie(monkeypatch, tmp_path):
    client = member_client(monkeypatch, tmp_path, user_id="2")
    bindings = NeteaseBindingStore(tmp_path / "bindings.json")
    bindings.save("2", {"nickname": "测试用户", "avatarUrl": ""}, [{"name": "MUSIC_U", "value": "secret"}])
    monkeypatch.setattr(web_app, "netease_bindings", bindings)
    monkeypatch.setattr(
        NeteaseClient,
        "account_status",
        lambda self: (_ for _ in ()).throw(NeteaseError("登录已过期", code=301)),
    )

    response = client.get("/api/auth/netease-status")

    assert response.status_code == 200
    assert response.get_json()["valid"] is False
    assert response.get_json()["needs_reauth"] is True


def test_website_login_uses_local_credentials_without_netease_login(monkeypatch, tmp_path):
    sessions = FileSessionStore(tmp_path / "sessions")
    users = AllowlistStore(tmp_path / "allowlist.json")
    accounts = WebsiteAccountStore(tmp_path / "accounts.json")
    bindings = NeteaseBindingStore(tmp_path / "bindings.json")
    users.authorize_login({"userId": 101, "nickname": "管理员", "avatarUrl": ""})
    accounts.create(
        "alice",
        "password",
        netease_user_id="101",
        nickname="管理员",
    )
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    monkeypatch.setattr(web_app, "allowlist", users)
    monkeypatch.setattr(web_app, "website_accounts", accounts)
    monkeypatch.setattr(web_app, "netease_bindings", bindings)

    def unexpected_netease_login(*args, **kwargs):
        raise AssertionError("网站登录不应调用网易云登录接口")

    monkeypatch.setattr(NeteaseClient, "login_with_captcha", unexpected_netease_login)
    client = web_app.app.test_client()
    response = client.post("/api/auth/login", json={"username": "alice", "password": "password"})

    assert response.status_code == 200
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.get_json()["logged_in"] is True
    assert status.get_json()["profile"]["username"] == "alice"


def test_cookie_registration_verifies_account_before_binding(monkeypatch, tmp_path):
    sessions = FileSessionStore(tmp_path / "sessions")
    users = AllowlistStore(tmp_path / "allowlist.json")
    accounts = WebsiteAccountStore(tmp_path / "accounts.json")
    bindings = NeteaseBindingStore(tmp_path / "bindings.json")
    monkeypatch.setattr(web_app, "auth_sessions", sessions)
    monkeypatch.setattr(web_app, "allowlist", users)
    monkeypatch.setattr(web_app, "website_accounts", accounts)
    monkeypatch.setattr(web_app, "netease_bindings", bindings)
    monkeypatch.setenv("CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT", "1")
    monkeypatch.setattr(
        NeteaseClient,
        "account_status",
        lambda self: {
            "logged_in": True,
            "profile": {"userId": 101, "nickname": "首位用户", "avatarUrl": ""},
        },
    )
    client = web_app.app.test_client()
    csrf = client.get("/api/auth/csrf").get_json()["csrf_token"]
    cookies = [
        {"name": "MUSIC_U", "value": "server-secret", "domain": ".music.163.com", "path": "/"},
        {"name": "__csrf", "value": "csrf", "domain": ".music.163.com", "path": "/"},
    ]

    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password", "cookies": cookies, "csrf_token": csrf},
    )

    assert response.status_code == 200
    assert users.role_for(101) == "admin"
    assert accounts.authenticate("alice", "password")["netease_user_id"] == "101"
    stored = bindings.load("101")
    assert stored is not None
    assert {item["name"] for item in stored["cookies"]} == {"MUSIC_U", "__csrf"}


def test_cookie_registration_requires_csrf(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "auth_sessions", FileSessionStore(tmp_path / "sessions"))
    monkeypatch.setattr(web_app, "allowlist", AllowlistStore(tmp_path / "allowlist.json"))
    monkeypatch.setattr(web_app, "website_accounts", WebsiteAccountStore(tmp_path / "accounts.json"))
    monkeypatch.setattr(web_app, "netease_bindings", NeteaseBindingStore(tmp_path / "bindings.json"))
    monkeypatch.setattr(NeteaseClient, "account_status", lambda self: (_ for _ in ()).throw(AssertionError("should not call NetEase")))
    response = web_app.app.test_client().post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "password",
            "cookies": [{"name": "MUSIC_U", "value": "secret", "domain": ".music.163.com", "path": "/"}],
            "csrf_token": "wrong",
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "account_error"
