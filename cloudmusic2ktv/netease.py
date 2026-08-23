from __future__ import annotations

import base64
import json
import secrets
import string
from pathlib import Path
from typing import Any, Iterable

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


BASE_URL = "https://music.163.com"
NONCE = b"0CoJUm6Qyw8W8jud"
IV = b"0102030405060708"
PUBLIC_EXPONENT = 0x10001
# This is the public modulus embedded in the current music.163.com web client.
MODULUS = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b7251"
    "52b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ec"
    "bda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d8"
    "13cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
    16,
)


class NeteaseError(RuntimeError):
    def __init__(self, message: str, *, code: int | str | None = None, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


class NeteaseClient:
    """Small, local-only client for the endpoints used by the NetEase web player."""

    def __init__(self, cookie_file: Path | None = None, timeout: int = 25):
        self.timeout = timeout
        self.cookie_file = cookie_file
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
                "Referer": f"{BASE_URL}/",
                "Origin": BASE_URL,
                "Accept": "application/json, text/plain, */*",
            }
        )
        self._load_cookies()

    def send_captcha(self, phone: str, country_code: str = "86") -> dict[str, Any]:
        data = self._post_json(
            "/api/sms/captcha/sent",
            data={
                "cellphone": phone,
                "ctcode": country_code,
                "secrete": "music_user_login",
                "source": "webMainStation",
            },
        )
        self._require_code(data)
        self._save_cookies()
        return data

    def login_with_captcha(
        self, phone: str, captcha: str, country_code: str = "86"
    ) -> dict[str, Any]:
        data = self.weapi(
            "/weapi/login/cellphone",
            {
                "countrycode": country_code,
                "phone": phone,
                "captcha": captcha,
                "rememberLogin": "true",
                "ydDeviceToken": "",
            },
            extra_headers={"X-Login-Method": "cellphone"},
        )
        self._require_code(data)
        self._save_cookies()
        return data

    def account_status(self) -> dict[str, Any]:
        try:
            data = self._get_json("/api/nuser/account/get")
        except NeteaseError:
            return {"logged_in": False, "profile": None}
        profile = data.get("profile")
        return {
            "logged_in": bool(profile),
            "profile": profile,
            "account": data.get("account"),
        }

    def logout(self) -> None:
        try:
            self.weapi("/weapi/logout", {})
        finally:
            self.session.cookies.clear()
            if self.cookie_file and self.cookie_file.exists():
                self.cookie_file.unlink()

    def search_songs(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        data = self.weapi(
            "/weapi/search/get",
            {"s": query, "type": "1", "offset": "0", "total": "true", "limit": str(limit)},
        )
        self._require_code(data)
        songs = ((data.get("result") or {}).get("songs") or [])
        return [normalize_song(song) for song in songs]

    def song_detail(self, song_id: int) -> dict[str, Any]:
        data = self._get_json(
            "/api/song/detail/",
            params={"id": str(song_id), "ids": f"[{song_id}]"},
        )
        songs = data.get("songs") or []
        if not songs:
            raise NeteaseError("没有找到这首歌", code="song_not_found", detail=data)
        return normalize_song(songs[0])

    def lyrics(self, song_id: int) -> dict[str, Any]:
        data = self._get_json(
            "/api/song/lyric/v1",
            params={
                "cp": "false",
                "id": str(song_id),
                "lv": "0",
                "kv": "0",
                "tv": "0",
                "rv": "0",
                "yv": "0",
                "ytv": "0",
                "yrv": "0",
            },
        )
        self._require_code(data)
        if not ((data.get("lrc") or {}).get("lyric")):
            raise NeteaseError("这首歌没有可用的带时间歌词", code="lyrics_missing", detail=data)
        return data

    def player_url(self, song_id: int, level: str = "exhigh") -> dict[str, Any]:
        data = self.weapi(
            "/weapi/song/enhance/player/url/v1",
            {"ids": json.dumps([song_id]), "level": level, "encodeType": "mp3"},
        )
        self._require_code(data)
        rows = data.get("data") or []
        if not rows:
            raise NeteaseError("网易云没有返回音频信息", code="audio_missing", detail=data)
        row = rows[0]
        if not row.get("url"):
            reason = "当前账号没有取得完整音频地址"
            if not self.account_status()["logged_in"]:
                reason += "；请先登录后重试"
            elif row.get("fee") == 1:
                reason += "；请确认该账号的 VIP 权益有效"
            raise NeteaseError(reason, code=row.get("code") or "audio_forbidden", detail=row)
        return row

    def weapi(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload)
        payload.setdefault("csrf_token", self.session.cookies.get("__csrf", ""))
        body = weapi_payload(payload)
        return self._post_json(path, data=body, headers=extra_headers)

    def stream(self, url: str, *, headers: dict[str, str] | None = None) -> requests.Response:
        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=(10, 60),
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise NeteaseError(f"下载资源失败：{exc}", code="download_failed") from exc

    def _get_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_json("GET", path, **kwargs)

    def _post_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_json("POST", path, **kwargs)

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise NeteaseError(f"连接网易云失败：{exc}", code="network_error") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise NeteaseError("网易云返回了无法解析的数据", code="invalid_response") from exc

    @staticmethod
    def _require_code(data: dict[str, Any]) -> None:
        code = data.get("code")
        if code not in (None, 200):
            message = data.get("message") or data.get("msg") or f"网易云请求失败（{code}）"
            raise NeteaseError(str(message), code=code, detail=data)

    def _load_cookies(self) -> None:
        if not self.cookie_file or not self.cookie_file.exists():
            return
        try:
            cookies = json.loads(self.cookie_file.read_text(encoding="utf-8"))
            for item in cookies:
                self.session.cookies.set(
                    item["name"],
                    item["value"],
                    domain=item.get("domain") or ".music.163.com",
                    path=item.get("path") or "/",
                )
        except (OSError, ValueError, KeyError, TypeError):
            # A corrupt local session should not prevent the UI from starting.
            self.session.cookies.clear()

    def _save_cookies(self) -> None:
        if not self.cookie_file:
            return
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
            for cookie in self.session.cookies
            if cookie.domain.endswith("music.163.com") or not cookie.domain
        ]
        self.cookie_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def weapi_payload(payload: dict[str, Any], secret_key: str | None = None) -> dict[str, str]:
    secret_key = secret_key or "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(16)
    )
    if len(secret_key.encode("ascii")) != 16:
        raise ValueError("secret_key must contain exactly 16 ASCII bytes")
    first = _aes_encrypt(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), NONCE
    )
    params = _aes_encrypt(first, secret_key.encode("ascii")).decode("ascii")
    reversed_hex = int(secret_key[::-1].encode("ascii").hex(), 16)
    encrypted_key = format(pow(reversed_hex, PUBLIC_EXPONENT, MODULUS), "x").zfill(256)
    return {"params": params, "encSecKey": encrypted_key}


def _aes_encrypt(value: bytes, key: bytes) -> bytes:
    padding_length = 16 - len(value) % 16
    padded = value + bytes([padding_length]) * padding_length
    encryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize())


def normalize_song(song: dict[str, Any]) -> dict[str, Any]:
    artists: Iterable[dict[str, Any]] = song.get("artists") or song.get("ar") or []
    album = song.get("album") or song.get("al") or {}
    privilege = song.get("privilege") or {}
    return {
        "id": int(song["id"]),
        "name": song.get("name") or "",
        "artists": [artist.get("name") or "" for artist in artists],
        "artist": " / ".join(artist.get("name") or "" for artist in artists),
        "album": album.get("name") or "",
        "cover_url": album.get("picUrl") or album.get("blurPicUrl") or "",
        "duration_ms": song.get("duration") or song.get("dt"),
        "fee": song.get("fee", privilege.get("fee")),
        "copyright": song.get("copyright"),
        "source": song,
    }

