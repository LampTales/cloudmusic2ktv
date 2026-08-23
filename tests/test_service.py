import pytest

from cloudmusic2ktv.service import parse_song_id, safe_filename


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

