from pathlib import Path
import json
import time

import pytest
from PIL import Image

from cloudmusic2ktv.video import (
    FrameRenderer,
    VideoError,
    VideoOptions,
    VideoProject,
    VideoJobManager,
    render_preview,
)
import cloudmusic2ktv.video as video_module


def make_project(tmp_path: Path, first_start: int = 1000) -> VideoProject:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (500, 500), (220, 84, 60)).save(cover)
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"ID3")
    return VideoProject(
        directory=tmp_path,
        song={
            "id": 1,
            "name": "测试歌曲",
            "artist": "测试歌手",
            "album": "测试专辑",
            "duration_ms": 40_000,
        },
        timeline=[
            {"start_ms": first_start, "end_ms": first_start + 20_000, "text": "第一句", "translation": "first", "romanization": "one"},
            {"start_ms": first_start + 20_000, "end_ms": first_start + 24_000, "text": "第二句", "translation": "second", "romanization": "two"},
        ],
        audio_path=audio,
        cover_path=cover,
        custom_background_path=tmp_path / "custom_background.png",
    )


def test_video_options_excludes_overloaded_three_language_mode():
    with pytest.raises(VideoError):
        VideoOptions.from_mapping({"lyric_mode": "translation_and_romanization"})


def test_opening_adds_preroll_only_when_lyrics_start_early(tmp_path):
    options = VideoOptions(spectrum=False)
    assert make_project(tmp_path / "early", 1000).pre_roll_ms(options) == 4000
    assert make_project(tmp_path / "late", 7000).pre_roll_ms(options) == 0


def test_long_gap_becomes_blank_then_four_second_cue(tmp_path):
    project = make_project(tmp_path)
    renderer = FrameRenderer(project, VideoOptions(spectrum=False))
    assert renderer._line_end_ms(0) == 9000
    assert renderer._lyric_state(10_000)["kind"] == "blank"
    cue = renderer._lyric_state(17_500)
    assert cue["kind"] == "cue"
    assert cue["index"] == 1
    assert cue["remaining"] == 3500


def test_preview_uses_same_frame_renderer(tmp_path):
    project = make_project(tmp_path)
    destination = tmp_path / "preview.png"
    result = render_preview(
        project,
        VideoOptions.from_mapping(
            {"resolution": "720p", "spectrum": False, "lyric_mode": "translation", "accent_mode": "cover"}
        ),
        destination,
    )
    with Image.open(destination) as preview:
        assert preview.size == (1280, 720)
    assert result["pre_roll_ms"] == 4000
    assert result["accent"].startswith("#")


def test_project_ignores_blank_and_music_marker_lines(tmp_path):
    directory = tmp_path / "123_test"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps({"id": 123, "name": "song", "artist": "artist", "duration_ms": 20_000}),
        encoding="utf-8",
    )
    (directory / "lyrics_timeline.json").write_text(
        json.dumps(
            [
                {"start_ms": 1000, "text": ""},
                {"start_ms": 4000, "text": "~music~"},
                {"start_ms": 9000, "text": "真正的歌词"},
            ]
        ),
        encoding="utf-8",
    )
    (directory / "audio.mp3").write_bytes(b"ID3")
    Image.new("RGB", (100, 100)).save(directory / "cover.jpg")
    project = VideoProject.load(tmp_path, 123)
    assert [line["text"] for line in project.timeline] == ["真正的歌词"]


def test_background_job_reports_completion(monkeypatch, tmp_path):
    project = make_project(tmp_path / "project")
    monkeypatch.setattr(VideoProject, "load", classmethod(lambda cls, root, song_id: project))

    def fake_render(project, options, destination, progress):
        progress(5, 10)
        destination.write_bytes(b"video")
        return {
            "path": str(destination),
            "size": 5,
            "duration_ms": 1000,
            "frames": 30,
            "resolution": options.resolution,
            "pre_roll_ms": 0,
        }

    monkeypatch.setattr(video_module, "render_video", fake_render)
    manager = VideoJobManager(tmp_path)
    job = manager.start(1, VideoOptions(spectrum=False))
    deadline = time.time() + 2
    while time.time() < deadline and manager.get(job["id"])["status"] not in {"done", "error"}:
        time.sleep(0.01)
    finished = manager.get(job["id"])
    manager.executor.shutdown(wait=True)
    assert finished["status"] == "done"
    assert finished["progress"] == 100
