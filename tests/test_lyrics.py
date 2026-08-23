from cloudmusic2ktv.lyrics import build_timeline, parse_lrc


def test_parse_lrc_supports_fraction_width_and_multiple_timestamps():
    lines = parse_lrc("[00:01.2]A\n[02:13.05][01:20.360]B\n[ar:artist]")
    assert [(line["start_ms"], line["text"]) for line in lines] == [
        (1200, "A"),
        (80360, "B"),
        (133050, "B"),
    ]


def test_build_timeline_merges_translation_and_romanization():
    payload = {
        "lrc": {"lyric": "[00:01.00]第一句\n[00:03.50]第二句"},
        "tlyric": {"lyric": "[00:01.00]first"},
        "romalrc": {"lyric": "[00:03.50]second roma"},
    }
    assert build_timeline(payload) == [
        {
            "start_ms": 1000,
            "end_ms": 3500,
            "text": "第一句",
            "translation": "first",
            "romanization": None,
        },
        {
            "start_ms": 3500,
            "end_ms": 8500,
            "text": "第二句",
            "translation": None,
            "romanization": "second roma",
        },
    ]

