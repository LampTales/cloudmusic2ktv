import base64

from cloudmusic2ktv.netease import weapi_payload


def test_weapi_payload_is_stable_with_fixed_secret():
    result = weapi_payload({"hello": "世界"}, "1234567890abcdef")
    assert set(result) == {"params", "encSecKey"}
    assert len(result["encSecKey"]) == 256
    assert len(base64.b64decode(result["params"])) % 16 == 0
