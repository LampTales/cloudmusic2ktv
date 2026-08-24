import json

import pytest
from PIL import Image

from cloudmusic2ktv.service import (
    SongDownloadService,
    available_lyric_types,
    local_song_status,
    parse_song_id,
    safe_filename,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3346334398", 3346334398),
        ("https://music.163.com/#/song?id=642810", 642810),
        ("https://music.163.com/song?id=642810&userid=1", 642810),
        ("https://music.163.com/song/642810", 642810),
    ],
)
def test_parse_song_id(value, expected):
    assert parse_song_id(value) == expected


def test_parse_song_id_rejects_search_text():
    with pytest.raises(ValueError):
        parse_song_id("song name")


def test_safe_filename_removes_windows_reserved_characters():
    assert safe_filename('642810_a/b:c*?"d') == "642810_a_b_c___d"


def test_local_song_status_distinguishes_missing_partial_and_ready(tmp_path):
    assert local_song_status(tmp_path, 123)["status"] == "missing"
    directory = tmp_path / "123_artist_song"
    directory.mkdir()
    (directory / "metadata.json").write_text(json.dumps({"id": 123}), encoding="utf-8")
    assert local_song_status(tmp_path, 123)["status"] == "partial"
    (directory / "lyrics_timeline.json").write_text("[]", encoding="utf-8")
    (directory / "audio.mp3").write_bytes(b"ID3")
    Image.new("RGB", (10, 10)).save(directory / "cover.jpg")
    assert local_song_status(tmp_path, 123)["status"] == "ready"
    assert local_song_status(tmp_path, 123, downloading=True)["status"] == "downloading"


def test_inspect_prefers_ready_shared_metadata_without_network(tmp_path):
    class OfflineClient:
        def song_detail(self, song_id):
            raise AssertionError("ready shared songs should not require the network")

    directory = tmp_path / "123_artist_song"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "id": 123,
                "name": "共享歌曲",
                "artist": "歌手",
                "duration_ms": 10_000,
                "audio": {"url": "must-not-leak"},
            }
        ),
        encoding="utf-8",
    )
    (directory / "lyrics_timeline.json").write_text("[]", encoding="utf-8")
    (directory / "audio.mp3").write_bytes(b"ID3")
    Image.new("RGB", (10, 10)).save(directory / "cover.jpg")

    song = SongDownloadService(OfflineClient(), tmp_path).inspect(123)
    assert song["name"] == "共享歌曲"
    assert "audio" not in song


def test_available_lyric_types_reports_only_non_empty_sources():
    payload = {
        "lrc": {"lyric": "[00:00.00]原文"},
        "tlyric": {"lyric": "  "},
        "romalrc": {"lyric": "[00:00.00]romanized"},
        "klyric": {"lyric": "[0,1000](0,500,0)逐(500,500,0)字"},
    }

    assert available_lyric_types(payload) == ["original", "romanization", "karaoke"]
