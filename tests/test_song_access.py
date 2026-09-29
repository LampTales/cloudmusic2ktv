import os
import json
import shutil
import time
from pathlib import Path

from cloudmusic2ktv.song_access import SongAccessStore


def test_old_directories_use_mtime_and_cleanup_deletes_oldest(tmp_path):
    old = tmp_path / "1_old"
    new = tmp_path / "2_new"
    old.mkdir()
    new.mkdir()
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    store = SongAccessStore(tmp_path)
    deleted = store.cleanup(threshold=1, delete_count=1)

    assert deleted == ["1_old"]
    assert not old.exists()
    assert new.exists()


def test_touch_is_throttled_for_repeated_media_requests(tmp_path):
    directory = tmp_path / "1_song"
    directory.mkdir()
    store = SongAccessStore(tmp_path)

    assert store.touch_directory(directory, "video_play", min_interval_seconds=600)
    first = json.loads((directory / "last_access.json").read_text())["last_access_at"]
    assert not store.touch_directory(directory, "video_play", min_interval_seconds=600)
    current = json.loads((directory / "last_access.json").read_text())["last_access_at"]
    assert current == first


def test_cleanup_rechecks_a_directory_touched_after_snapshot(monkeypatch, tmp_path):
    old = tmp_path / "1_old"
    touched = tmp_path / "2_touched"
    keep = tmp_path / "3_keep"
    old.mkdir()
    touched.mkdir()
    keep.mkdir()
    store = SongAccessStore(tmp_path)
    for directory, timestamp in ((old, 1), (touched, 2), (keep, 3)):
        (directory / "last_access.json").write_text(
            f'{{"last_access_at": {timestamp}}}', encoding="utf-8"
        )
    original_rmtree = shutil.rmtree

    def deleting(path):
        if Path(path).name == "1_old":
            store.touch_directory(touched, "video_play")
        return original_rmtree(path)

    monkeypatch.setattr("cloudmusic2ktv.song_access.shutil.rmtree", deleting)
    deleted = store.cleanup(threshold=2, delete_count=2)

    assert deleted == ["1_old", "3_keep"]
    assert touched.exists()
