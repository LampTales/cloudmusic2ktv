import pytest

from cloudmusic2ktv.netease import NeteaseClient, NeteaseError


def fake_player_client(monkeypatch, *, logged_in):
    client = object.__new__(NeteaseClient)
    monkeypatch.setattr(
        client,
        "weapi",
        lambda *args, **kwargs: {
            "data": [{"id": 642810, "url": None, "code": -110, "fee": 1}]
        },
    )
    monkeypatch.setattr(client, "account_status", lambda: {"logged_in": logged_in})
    return client


def test_player_url_distinguishes_vip_entitlement_from_expired_login(monkeypatch):
    client = fake_player_client(monkeypatch, logged_in=True)
    with pytest.raises(NeteaseError) as vip_error:
        client.player_url(642810)
    assert vip_error.value.code == "audio_vip_required"
    assert "VIP" in str(vip_error.value)

    client = fake_player_client(monkeypatch, logged_in=False)
    with pytest.raises(NeteaseError) as auth_error:
        client.player_url(642810)
    assert auth_error.value.code == "netease_auth_required"
    assert "登录" in str(auth_error.value)


def test_non_paid_audio_failure_keeps_fallback_without_status_request(monkeypatch):
    client = object.__new__(NeteaseClient)
    monkeypatch.setattr(
        client,
        "weapi",
        lambda *args, **kwargs: {
            "data": [{"id": 3406869488, "url": None, "code": -110, "fee": 0}]
        },
    )

    def unexpected_status():
        raise AssertionError("fee=0 失败时不应额外请求账号状态")

    monkeypatch.setattr(client, "account_status", unexpected_status)
    with pytest.raises(NeteaseError) as error:
        client.player_url(3406869488)
    assert error.value.code == -110
