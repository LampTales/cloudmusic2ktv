import json
from unittest.mock import patch

from cloudmusic2ktv.sessions import FileSessionStore


def test_file_session_keeps_cookies_server_side_and_reloads_them(tmp_path):
    store = FileSessionStore(tmp_path, ttl_seconds=3600)
    with store.open(None, create=True) as session:
        assert session is not None
        token = session.token
        session.profile = {"userId": 1, "nickname": "测试用户", "avatarUrl": ""}
        session.client.session.cookies.set("MUSIC_U", "secret", domain=".music.163.com", path="/")

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert token not in files[0].name
    assert "secret" in files[0].read_text(encoding="utf-8")

    with store.open(token) as restored:
        assert restored is not None
        assert restored.profile["nickname"] == "测试用户"
        assert restored.client.session.cookies.get("MUSIC_U") == "secret"


def test_expired_session_is_rejected_lazily(tmp_path):
    store = FileSessionStore(tmp_path, ttl_seconds=60)
    with store.open(None, create=True) as session:
        assert session is not None
        token = session.token

    path = next(tmp_path.glob("*.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["last_used_at"] = 1
    path.write_text(json.dumps(value), encoding="utf-8")

    with store.open(token) as expired:
        assert expired is None
    assert not path.exists()


def test_read_only_session_does_not_write_back(tmp_path):
    store = FileSessionStore(tmp_path, ttl_seconds=3600)
    with store.open(None, create=True) as session:
        assert session is not None
        token = session.token

    with patch.object(store, "_write", wraps=store._write) as write:
        with store.open(token, touch=False, persist=False) as session:
            assert session is not None
        write.assert_not_called()


def test_cleanup_removes_invalid_and_expired_session_files(tmp_path):
    store = FileSessionStore(tmp_path, ttl_seconds=60)
    (tmp_path / "invalid.json").write_text("not json", encoding="utf-8")
    (tmp_path / "expired.json").write_text(
        json.dumps({"version": 1, "last_used_at": 1}), encoding="utf-8"
    )
    assert store.cleanup_expired() == 2
    assert list(tmp_path.glob("*.json")) == []
