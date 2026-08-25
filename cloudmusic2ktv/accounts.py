from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any


ACCOUNTS_VERSION = 1
USERNAME_PATTERN = re.compile(r"^[^\s\\/:*?\"<>|]{2,40}$")


class AccountError(RuntimeError):
    pass


class AccountExists(AccountError):
    pass


class WebsiteAccountStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def create(
        self,
        username: str,
        password: str,
        *,
        netease_user_id: str,
        nickname: str,
        avatar_url: str = "",
    ) -> dict[str, Any]:
        key = self._username_key(username)
        if not USERNAME_PATTERN.fullmatch(username.strip()):
            raise AccountError("网站用户名需要为 2～40 个不含空格的字符")
        if len(password) < 6:
            raise AccountError("网站密码至少需要 6 个字符")
        user_id = self._normalize_id(netease_user_id)
        if user_id is None:
            raise AccountError("网易云用户 ID 无效")
        with self._lock:
            value = self._read()
            for entry in value["users"].values():
                if str(entry.get("username_key")) == key:
                    raise AccountExists("网站用户名已存在")
                if str(entry.get("netease_user_id")) == user_id:
                    raise AccountExists("该网易云账号已经绑定了网站用户")
            account = {
                "username": username.strip(),
                "username_key": key,
                "password_hash": hash_password(password),
                "netease_user_id": user_id,
                "nickname": str(nickname or "网易云用户"),
                "avatarUrl": str(avatar_url or ""),
                "created_at": int(time.time()),
            }
            value["users"][key] = account
            self._write(value)
            return self.public(account)

    def validate_new_account(self, username: str, password: str) -> None:
        """Validate local credentials before an external allowlist mutation."""
        key = self._username_key(username)
        if not USERNAME_PATTERN.fullmatch(username.strip()):
            raise AccountError("网站用户名需要为 2～40 个不含空格的字符")
        if len(password) < 6:
            raise AccountError("网站密码至少需要 6 个字符")
        with self._lock:
            value = self._read()
            if key in value["users"]:
                raise AccountExists("网站用户名已存在")

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        key = self._username_key(username)
        with self._lock:
            account = self._read()["users"].get(key)
            if not isinstance(account, dict):
                return None
            if not verify_password(password, str(account.get("password_hash") or "")):
                return None
            return self.public(account)

    @staticmethod
    def public(account: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": str(account.get("username") or ""),
            "netease_user_id": str(account.get("netease_user_id") or ""),
            "nickname": str(account.get("nickname") or "网易云用户"),
            "avatarUrl": str(account.get("avatarUrl") or ""),
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": ACCOUNTS_VERSION, "users": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise AccountError("网站账号文件无法读取") from exc
        if not isinstance(value, dict) or value.get("version") != ACCOUNTS_VERSION:
            raise AccountError("网站账号文件版本不受支持")
        if not isinstance(value.get("users"), dict):
            raise AccountError("网站账号文件格式不正确")
        return {"version": ACCOUNTS_VERSION, "users": value["users"]}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".part")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _username_key(value: str) -> str:
        return str(value or "").strip().casefold()

    @staticmethod
    def _normalize_id(value: Any) -> str | None:
        text = str(value or "").strip()
        return text if text.isdigit() and int(text) > 0 else None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64
    )
    return "scrypt$16384$8$1$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


class NeteaseBindingStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def save(self, user_id: Any, profile: dict[str, Any], cookies: list[dict[str, Any]]) -> None:
        normalized = str(user_id or "").strip()
        if not normalized.isdigit() or int(normalized) <= 0:
            raise AccountError("网易云用户 ID 无效")
        with self._lock:
            value = self._read()
            value["bindings"][normalized] = {
                "userId": normalized,
                "nickname": str(profile.get("nickname") or "网易云用户"),
                "avatarUrl": str(profile.get("avatarUrl") or ""),
                "cookies": cookies,
                "updated_at": int(time.time()),
            }
            self._write(value)

    def load(self, user_id: Any) -> dict[str, Any] | None:
        normalized = str(user_id or "").strip()
        with self._lock:
            value = self._read()
            entry = value["bindings"].get(normalized)
            return dict(entry) if isinstance(entry, dict) else None

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": ACCOUNTS_VERSION, "bindings": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise AccountError("网易云绑定文件无法读取") from exc
        if not isinstance(value, dict) or value.get("version") != ACCOUNTS_VERSION:
            raise AccountError("网易云绑定文件版本不受支持")
        if not isinstance(value.get("bindings"), dict):
            raise AccountError("网易云绑定文件格式不正确")
        return {"version": ACCOUNTS_VERSION, "bindings": value["bindings"]}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".part")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
