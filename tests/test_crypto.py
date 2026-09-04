import base64
import json

from cloudmusic2ktv.netease import NeteaseClient, normalize_cookie_records, weapi_payload


def test_weapi_payload_is_stable_with_fixed_secret():
    result = weapi_payload({"hello": "世界"}, "1234567890abcdef")
    assert set(result) == {"params", "encSecKey"}
    assert len(result["encSecKey"]) == 256
    assert len(base64.b64decode(result["params"])) % 16 == 0


def test_netease_client_does_not_inherit_environment_proxies_by_default():
    assert NeteaseClient().session.trust_env is False
    assert NeteaseClient(trust_env_proxy=True).session.trust_env is True


def test_twice_used_phone_confirmation_uses_the_current_weapi_session(monkeypatch):
    client = NeteaseClient()
    calls = []

    def fake_weapi(path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return {"code": 200, "profile": {"userId": 101}}

    monkeypatch.setattr(client, "weapi", fake_weapi)
    result = client.confirm_twice_used_phone()

    assert result["code"] == 200
    assert calls == [("/weapi/login/cellphone/twice/used/confirm", {}, {})]


def test_imported_cookie_records_are_first_party_and_name_deduplicated():
    result = normalize_cookie_records(
        [
            {"name": "__csrf", "value": "host", "domain": "music.163.com", "path": "/"},
            {"name": "__csrf", "value": "parent", "domain": ".music.163.com", "path": "/"},
            {"name": "MUSIC_U", "value": "secret", "domain": ".music.163.com", "path": "/"},
            {"name": "TRACK", "value": "discarded", "domain": ".163.com", "path": "/"},
        ]
    )
    assert {item["name"] for item in result} == {"__csrf", "MUSIC_U"}
    csrf = next(item for item in result if item["name"] == "__csrf")
    assert csrf["domain"] == ".music.163.com"


def test_user_playlists_normalizes_web_playlist_response(monkeypatch):
    client = NeteaseClient()
    calls = []

    def fake_weapi(path, payload, **kwargs):
        calls.append((path, payload))
        return {
            "code": 200,
            "playlist": [{
                "id": 12,
                "name": "我的歌单",
                "coverImgUrl": "https://example.test/cover.jpg",
                "trackCount": 3,
                "creator": {"userId": 7, "nickname": "用户"},
            }],
        }

    monkeypatch.setattr(client, "weapi", fake_weapi)
    result = client.user_playlists(7)

    assert result[0]["id"] == 12
    assert result[0]["track_count"] == 3
    assert calls == [(
        "/weapi/user/playlist",
        {"uid": "7", "limit": "1000", "offset": "0", "includeVideo": True},
    )]


def test_playlist_tracks_resolves_ids_in_original_order(monkeypatch):
    client = NeteaseClient()
    calls = []

    def fake_post(path, **kwargs):
        calls.append((path, kwargs["data"]))
        if path.endswith("playlist/detail"):
            return {
                "code": 200,
                "playlist": {
                    "id": 12,
                    "name": "歌单",
                    "trackCount": 3,
                    "trackIds": [{"id": 101}, {"id": 102}, {"id": 103}],
                },
            }
        requested = json.loads(kwargs["data"]["c"])
        songs = [{
            "id": item["id"], "name": f"歌曲{item['id']}",
            "ar": [{"name": "歌手"}], "al": {"name": "专辑"},
        } for item in reversed(requested)]
        return {"code": 200, "songs": songs}

    monkeypatch.setattr(client, "_post_json", fake_post)
    result = client.playlist_tracks(12, offset=1, limit=2)

    assert [song["id"] for song in result["songs"]] == [102, 103]
    assert result["total"] == 3
    assert result["has_more"] is False
    assert calls[0][0] == "/api/v6/playlist/detail"
    assert json.loads(calls[1][1]["c"]) == [{"id": 102}, {"id": 103}]


def test_playlist_tracks_searches_the_complete_playlist_before_paginating(monkeypatch):
    client = NeteaseClient()

    def fake_post(path, **kwargs):
        if path.endswith("playlist/detail"):
            return {
                "code": 200,
                "playlist": {
                    "id": 12,
                    "name": "歌单",
                    "trackIds": [{"id": 101}, {"id": 102}, {"id": 103}],
                },
            }
        requested = json.loads(kwargs["data"]["c"])
        songs = {
            101: {"id": 101, "name": "夜曲", "ar": [{"name": "周杰伦"}], "al": {"name": "十一月的萧邦"}},
            102: {"id": 102, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}},
            103: {"id": 103, "name": "海阔天空", "ar": [{"name": "Beyond"}], "al": {"name": "乐与怒"}},
        }
        return {"code": 200, "songs": [songs[item["id"]] for item in requested]}

    monkeypatch.setattr(client, "_post_json", fake_post)
    result = client.playlist_tracks(12, offset=0, limit=50, query="beyond 乐与怒")

    assert [song["id"] for song in result["songs"]] == [103]
    assert result["total"] == 1
    assert result["query"] == "beyond 乐与怒"
    assert result["has_more"] is False
