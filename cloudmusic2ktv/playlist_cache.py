from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable


PlaylistLoader = Callable[[], list[dict[str, Any]]]
PlaylistDetailLoader = Callable[[], tuple[dict[str, Any], list[int]]]
SongLoader = Callable[[list[int]], list[dict[str, Any]]]


@dataclass
class _TimedValue:
    created_at: float
    value: list[dict[str, Any]]


@dataclass
class _PlaylistEntry:
    created_at: float
    playlist: dict[str, Any]
    track_ids: list[int]
    songs: dict[int, dict[str, Any]] = field(default_factory=dict)
    loaded_ids: set[int] = field(default_factory=set)
    full_index: bool = False


class PlaylistCache:
    """Thread-safe, process-local cache for playlist metadata and tracks.

    Entries use an absolute TTL: reading an entry updates only its LRU order,
    not its expiry time. Network loads for the same key are coalesced so the
    threaded Flask server does not repeat a large playlist index build.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 6 * 60 * 60,
        max_playlists: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_playlists <= 0:
            raise ValueError("max_playlists must be positive")
        self.ttl_seconds = float(ttl_seconds)
        self.max_playlists = int(max_playlists)
        self._clock = clock
        self._lock = threading.RLock()
        self._playlist_lists: dict[str, _TimedValue] = {}
        self._playlists: OrderedDict[tuple[str, int], _PlaylistEntry] = OrderedDict()
        self._list_loads: dict[str, threading.Event] = {}
        self._detail_loads: dict[tuple[str, int], threading.Event] = {}
        self._song_loads: dict[tuple[str, int], threading.Event] = {}
        self._generations: dict[str, int] = {}

    def get_playlists(self, user_id: str | int, loader: PlaylistLoader) -> list[dict[str, Any]]:
        user_key = str(user_id)
        while True:
            with self._lock:
                self._prune_expired_locked()
                cached = self._playlist_lists.get(user_key)
                if cached is not None:
                    return cached.value
                pending = self._list_loads.get(user_key)
                if pending is None:
                    pending = threading.Event()
                    self._list_loads[user_key] = pending
                    generation = self._generations.get(user_key, 0)
                    break
            pending.wait()

        try:
            value = loader()
        except BaseException:
            self._finish_load(self._list_loads, user_key, pending)
            raise
        with self._lock:
            if self._generations.get(user_key, 0) == generation:
                self._playlist_lists[user_key] = _TimedValue(self._clock(), value)
            self._finish_load_locked(self._list_loads, user_key, pending)
        return value

    def get_tracks(
        self,
        user_id: str | int,
        playlist_id: int,
        *,
        offset: int,
        limit: int,
        query: str,
        detail_loader: PlaylistDetailLoader,
        song_loader: SongLoader,
    ) -> dict[str, Any]:
        key = (str(user_id), int(playlist_id))
        entry = self._get_playlist_entry(key, detail_loader)
        query = str(query or "").strip()
        if query:
            self._ensure_songs(key, entry, entry.track_ids, song_loader, full_index=True)
            terms = [term.casefold() for term in query.split() if term]
            candidates = [entry.songs[song_id] for song_id in entry.track_ids if song_id in entry.songs]
            filtered = [song for song in candidates if self._matches(song, terms)]
            total = len(filtered)
            songs = filtered[offset : offset + limit]
        else:
            total = len(entry.track_ids)
            page_ids = entry.track_ids[offset : offset + limit]
            self._ensure_songs(key, entry, page_ids, song_loader)
            songs = [entry.songs[song_id] for song_id in page_ids if song_id in entry.songs]
        return {
            "playlist": entry.playlist,
            "songs": songs,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + limit < total,
            "query": query,
        }

    def invalidate_user(self, user_id: str | int) -> None:
        user_key = str(user_id)
        with self._lock:
            self._generations[user_key] = self._generations.get(user_key, 0) + 1
            self._playlist_lists.pop(user_key, None)
            for key in [key for key in self._playlists if key[0] == user_key]:
                self._playlists.pop(key, None)

    def _get_playlist_entry(
        self,
        key: tuple[str, int],
        loader: PlaylistDetailLoader,
    ) -> _PlaylistEntry:
        while True:
            with self._lock:
                self._prune_expired_locked()
                cached = self._playlists.get(key)
                if cached is not None:
                    self._playlists.move_to_end(key)
                    return cached
                pending = self._detail_loads.get(key)
                if pending is None:
                    pending = threading.Event()
                    self._detail_loads[key] = pending
                    generation = self._generations.get(key[0], 0)
                    break
            pending.wait()

        try:
            playlist, track_ids = loader()
            entry = _PlaylistEntry(self._clock(), playlist, track_ids)
        except BaseException:
            self._finish_load(self._detail_loads, key, pending)
            raise
        with self._lock:
            if self._generations.get(key[0], 0) == generation:
                self._playlists[key] = entry
                self._playlists.move_to_end(key)
                self._evict_lru_locked()
            self._finish_load_locked(self._detail_loads, key, pending)
        return entry

    def _ensure_songs(
        self,
        key: tuple[str, int],
        entry: _PlaylistEntry,
        song_ids: list[int],
        loader: SongLoader,
        *,
        full_index: bool = False,
    ) -> None:
        while True:
            with self._lock:
                if full_index and entry.full_index:
                    return
                missing = [song_id for song_id in song_ids if song_id not in entry.loaded_ids]
                if not missing:
                    if full_index:
                        entry.full_index = True
                    return
                pending = self._song_loads.get(key)
                if pending is None:
                    pending = threading.Event()
                    self._song_loads[key] = pending
                    break
            pending.wait()

        try:
            loaded = loader(missing)
            compact = {
                int(song["id"]): self._compact_song(song)
                for song in loaded
                if str(song.get("id") or "").isdigit()
            }
        except BaseException:
            self._finish_load(self._song_loads, key, pending)
            raise
        with self._lock:
            entry.songs.update(compact)
            entry.loaded_ids.update(missing)
            if full_index:
                entry.full_index = True
            if self._playlists.get(key) is entry:
                self._playlists.move_to_end(key)
            self._finish_load_locked(self._song_loads, key, pending)

    def _prune_expired_locked(self) -> None:
        cutoff = self._clock() - self.ttl_seconds
        for user_id, cached in list(self._playlist_lists.items()):
            if cached.created_at <= cutoff:
                self._playlist_lists.pop(user_id, None)
        for key, cached in list(self._playlists.items()):
            if cached.created_at <= cutoff:
                self._playlists.pop(key, None)

    def _evict_lru_locked(self) -> None:
        while len(self._playlists) > self.max_playlists:
            self._playlists.popitem(last=False)

    def _finish_load(self, loads: dict[Any, threading.Event], key: Any, event: threading.Event) -> None:
        with self._lock:
            self._finish_load_locked(loads, key, event)

    @staticmethod
    def _finish_load_locked(
        loads: dict[Any, threading.Event], key: Any, event: threading.Event
    ) -> None:
        if loads.get(key) is event:
            loads.pop(key, None)
        event.set()

    @staticmethod
    def _compact_song(song: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in song.items() if key != "source"}

    @staticmethod
    def _matches(song: dict[str, Any], terms: list[str]) -> bool:
        searchable = " ".join(
            str(song.get(field) or "") for field in ("name", "artist", "album")
        ).casefold()
        return all(term in searchable for term in terms)
