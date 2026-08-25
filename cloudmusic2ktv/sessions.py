from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .netease import NeteaseClient


TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,100}$")
SESSION_VERSION = 1


@dataclass
class AuthSession:
    token: str
    client: NeteaseClient
    created_at: int
    last_used_at: int
    profile: dict[str, Any] | None
    pending_qr: dict[str, Any] | None = None
    csrf_token: str = ""
    discard_cookies_on_error: bool = False


class FileSessionStore:
    """Small server-side session store for a trusted, low-traffic deployment."""

    def __init__(self, directory: Path, *, ttl_seconds: int = 90 * 24 * 60 * 60):
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._cleanup_lock = threading.Lock()

    @contextmanager
    def open(
        self, token: str | None, *, create: bool = False, touch: bool = True
    ) -> Iterator[AuthSession | None]:
        resolved = self._valid_token(token)
        record = None
        lock = None
        if resolved:
            lock = self._lock_for(resolved)
            lock.acquire()
            record = self._read(resolved)
            if record and self._expired(record):
                self._delete_file(resolved)
                record = None
            if record is None:
                lock.release()
                lock = None

        if record is None:
            if not create:
                yield None
                return
            resolved = self._new_token()
            lock = self._lock_for(resolved)
            lock.acquire()
            now = int(time.time())
            record = {
                "version": SESSION_VERSION,
                "created_at": now,
                "last_used_at": now,
                "profile": None,
                "pending_qr": None,
                "csrf_token": secrets.token_urlsafe(32),
                "cookies": [],
            }

        assert resolved is not None
        assert lock is not None
        try:
            client = NeteaseClient()
            try:
                client.load_cookies(record.get("cookies") or [])
            except (KeyError, TypeError, ValueError):
                client.session.cookies.clear()
            session = AuthSession(
                token=resolved,
                client=client,
                created_at=int(record.get("created_at") or time.time()),
                last_used_at=int(record.get("last_used_at") or time.time()),
                profile=record.get("profile") if isinstance(record.get("profile"), dict) else None,
                pending_qr=record.get("pending_qr") if isinstance(record.get("pending_qr"), dict) else None,
                csrf_token=str(record.get("csrf_token") or secrets.token_urlsafe(32)),
            )
            try:
                yield session
            except Exception:
                if session.discard_cookies_on_error:
                    client.session.cookies.clear()
                raise
            finally:
                now = int(time.time())
                self._write(
                    resolved,
                    {
                        "version": SESSION_VERSION,
                        "created_at": session.created_at,
                        "last_used_at": now if touch else session.last_used_at,
                        "profile": session.profile,
                        "pending_qr": session.pending_qr,
                        "csrf_token": session.csrf_token,
                        "cookies": client.export_cookies(),
                    },
                )
        finally:
            lock.release()

    def rotate(self, token: str) -> str:
        valid = self._valid_token(token)
        if not valid:
            raise ValueError("invalid session token")
        with self._lock_for(valid):
            record = self._read(valid)
            if record is None or self._expired(record):
                raise ValueError("session no longer exists")
            replacement = self._new_token()
            with self._lock_for(replacement):
                self._write(replacement, record)
            self._delete_file(valid)
        return replacement

    def is_authenticated(self, token: str | None) -> bool:
        with self.open(token, touch=True) as session:
            return bool(session and session.profile)

    def delete(self, token: str | None) -> None:
        valid = self._valid_token(token)
        if not valid:
            return
        with self._lock_for(valid):
            self._delete_file(valid)

    def cleanup_expired(self) -> int:
        removed = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._cleanup_lock:
            for path in self.directory.glob("*.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    expired = not isinstance(value, dict) or self._expired(value)
                except (OSError, ValueError, TypeError):
                    expired = True
                if expired:
                    try:
                        path.unlink()
                        removed += 1
                    except FileNotFoundError:
                        pass
        return removed

    def _read(self, token: str) -> dict[str, Any] | None:
        path = self._path(token)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != SESSION_VERSION:
                return None
            return value
        except (OSError, ValueError, TypeError):
            return None

    def _write(self, token: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(token)
        temporary = path.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _delete_file(self, token: str) -> None:
        try:
            self._path(token).unlink()
        except FileNotFoundError:
            pass

    def _expired(self, value: dict[str, Any]) -> bool:
        try:
            return int(value.get("last_used_at") or 0) < int(time.time()) - self.ttl_seconds
        except (TypeError, ValueError):
            return True

    def _path(self, token: str) -> Path:
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        return self.directory / f"{digest}.json"

    def _lock_for(self, token: str) -> threading.RLock:
        key = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._locks_guard:
            return self._locks.setdefault(key, threading.RLock())

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _valid_token(token: str | None) -> str | None:
        value = str(token or "")
        return value if TOKEN_PATTERN.fullmatch(value) else None
