import app as web_app

from cloudmusic2ktv.access import AllowlistStore
from cloudmusic2ktv.accounts import NeteaseBindingStore, WebsiteAccountStore
from cloudmusic2ktv.netease import NeteaseClient
from cloudmusic2ktv.sessions import FileSessionStore


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
    client.set_cookie("localhost", "cloudmusic2ktv_session", token)

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
    client.set_cookie("localhost", "cloudmusic2ktv_session", token)

    response = client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "admin_required"


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
