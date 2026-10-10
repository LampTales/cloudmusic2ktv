"""Per-directory last-access tracking and occasional LRU cleanup."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class SongAccessStore:
    """Store one small access record inside each song directory.

    There is intentionally no global song index.  Missing records fall back
    to the directory mtime until the first explicit touch, which keeps old
    directories compatible when this feature is introduced.
    """

    RECORD_FILENAME = "last_access.json"

    def __init__(self, output_root: Path):
        self.output_root = Path(output_root)
        self.lock = threading.RLock()

    def touch_directory(
        self,
        directory: Path,
        reason: str,
        *,
        min_interval_seconds: int | float = 0,
    ) -> bool:
        directory = Path(directory)
        if not directory.is_dir() or directory.parent != self.output_root:
            return False
        now = time.time_ns()
        with self.lock:
            current = self._read_record_locked(directory)
            previous = self._record_time(current)
            if min_interval_seconds and previous:
                if now - previous < int(float(min_interval_seconds) * 1_000_000_000):
                    return False
            self._write_record_locked(
                directory,
                {"last_access_at": now, "last_reason": str(reason)},
            )
            return True

    def touch_song(self, song_id: int, reason: str, **kwargs: Any) -> bool:
        directories = sorted(self.output_root.glob(f"{int(song_id)}_*"))
        for directory in directories:
            if directory.is_dir():
                return self.touch_directory(directory, reason, **kwargs)
        return False

    def cleanup(
        self,
        *,
        threshold: int,
        delete_count: int,
        protected_names: Iterable[str] = (),
    ) -> list[str]:
        """Delete the oldest directories when the top-level count is high.

        A candidate's timestamp is checked again immediately before deletion.
        If a request touched it after the initial list was made, it is skipped
        and the next oldest candidate is considered instead.
        """
        protected = {str(name) for name in protected_names}
        with self.lock:
            try:
                directories = [path for path in self.output_root.iterdir() if path.is_dir()]
            except OSError:
                return []
            if len(directories) <= int(threshold):
                return []
            snapshot = {
                path.name: self._effective_time_locked(path)
                for path in directories
                if path.name not in protected
            }
        candidates = sorted(snapshot.items(), key=lambda item: (item[1], item[0]))
        deleted: list[str] = []
        for name, listed_at in candidates:
            if len(deleted) >= int(delete_count):
                break
            with self.lock:
                path = self.output_root / name
                if name in protected or not path.is_dir():
                    continue
                # A touch during cleanup makes the directory ineligible.
                if self._effective_time_locked(path) != listed_at:
                    continue
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            deleted.append(name)
        return deleted

    def _effective_time_locked(self, directory: Path) -> int:
        record = self._read_record_locked(directory)
        value = self._record_time(record)
        if value:
            return value
        try:
            return directory.stat().st_mtime_ns
        except OSError:
            return 0

    @staticmethod
    def _record_time(record: Any) -> int:
        try:
            return int(record.get("last_access_at") or 0) if isinstance(record, dict) else 0
        except (TypeError, ValueError):
            return 0

    def _read_record_locked(self, directory: Path) -> dict[str, Any]:
        try:
            value = json.loads(
                (directory / self.RECORD_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_record_locked(self, directory: Path, value: dict[str, Any]) -> None:
        record_path = directory / self.RECORD_FILENAME
        temporary = directory / f".{self.RECORD_FILENAME}.tmp"
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(record_path)
        except (OSError, TypeError, ValueError):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
