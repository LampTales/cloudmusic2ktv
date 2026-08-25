from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


ALLOWLIST_VERSION = 1
VALID_ROLES = {"admin", "user"}


class AllowlistError(RuntimeError):
    """Raised when the local allowlist cannot be read or written safely."""


class UserNotAllowed(AllowlistError):
    """Raised when a successfully authenticated NetEase account is not listed."""


class AllowlistStore:
    """Small, atomic JSON allowlist for a single-process low-traffic service."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            value = self._read()
            users = value["users"]
            result = []
            for user_id, entry in users.items():
                item = dict(entry)
                item["userId"] = user_id
                result.append(item)
            result.sort(key=lambda item: (str(item.get("nickname") or ""), item["userId"]))
            return result

    def role_for(self, user_id: Any) -> str | None:
        normalized = self._normalize_user_id(user_id)
        if normalized is None:
            return None
        with self._lock:
            entry = self._read()["users"].get(normalized)
            role = entry.get("role") if isinstance(entry, dict) else None
            return role if role in VALID_ROLES else None

    def authorize_login(self, profile: dict[str, Any]) -> str:
        user_id = self._normalize_user_id(profile.get("userId"))
        if user_id is None:
            raise UserNotAllowed("网易云账号缺少有效的用户 ID")
        with self._lock:
            value = self._read()
            users = value["users"]
            existing = users.get(user_id)
            if isinstance(existing, dict) and existing.get("role") in VALID_ROLES:
                return str(existing["role"])
            if users:
                raise UserNotAllowed("该网易云账号不在允许名单中")
            users[user_id] = self._entry(profile, "admin", user_id)
            self._write(value)
            return "admin"

    def add(
        self,
        profile: dict[str, Any],
        role: str,
        *,
        added_by: str,
    ) -> dict[str, Any]:
        if role not in VALID_ROLES:
            raise AllowlistError("无效的权限等级")
        user_id = self._normalize_user_id(profile.get("userId"))
        if user_id is None:
            raise AllowlistError("用户 ID 无效")
        actor_id = self._normalize_user_id(added_by)
        if actor_id is None:
            raise AllowlistError("操作者 ID 无效")
        with self._lock:
            value = self._read()
            if user_id in value["users"]:
                raise AllowlistError("该用户已经在允许名单中")
            entry = self._entry(profile, role, actor_id)
            value["users"][user_id] = entry
            self._write(value)
            result = dict(entry)
            result["userId"] = user_id
            return result

    def delete(self, user_id: Any, *, actor_id: str) -> None:
        target = self._normalize_user_id(user_id)
        actor = self._normalize_user_id(actor_id)
        if target is None or actor is None:
            raise AllowlistError("用户 ID 无效")
        with self._lock:
            value = self._read()
            users = value["users"]
            entry = users.get(target)
            if not isinstance(entry, dict):
                raise AllowlistError("名单中没有该用户")
            if entry.get("role") == "admin":
                raise AllowlistError("不能删除管理员账号")
            del users[target]
            self._write(value)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": ALLOWLIST_VERSION, "users": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise AllowlistError("允许名单文件无法读取，请检查文件内容") from exc
        if not isinstance(value, dict) or value.get("version") != ALLOWLIST_VERSION:
            raise AllowlistError("允许名单文件版本不受支持")
        users = value.get("users")
        if not isinstance(users, dict):
            raise AllowlistError("允许名单文件格式不正确")
        return {"version": ALLOWLIST_VERSION, "users": users}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".part")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _normalize_user_id(value: Any) -> str | None:
        text = str(value or "").strip()
        return text if text.isdigit() and int(text) > 0 else None

    @staticmethod
    def _entry(profile: dict[str, Any], role: str, added_by: str) -> dict[str, Any]:
        return {
            "role": role,
            "nickname": str(profile.get("nickname") or "网易云用户"),
            "avatarUrl": str(profile.get("avatarUrl") or ""),
            "added_at": int(time.time()),
            "added_by": added_by,
        }
