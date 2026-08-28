from pathlib import Path
import json
import threading
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
    video_options_fingerprint,
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


def test_lyric_highlight_defaults_to_whole_line_and_validates_modes():
    assert VideoOptions.from_mapping({}).lyric_highlight_mode == "line"
    assert VideoOptions.from_mapping({"lyric_highlight_mode": "sweep"}).lyric_highlight_mode == "sweep"
    with pytest.raises(VideoError):
        VideoOptions.from_mapping({"lyric_highlight_mode": "word"})


def test_lyric_highlight_mode_changes_option_fingerprint():
    whole_line = VideoOptions(spectrum=False)
    sweep = VideoOptions(spectrum=False, lyric_highlight_mode="sweep")

    assert video_options_fingerprint(whole_line) != video_options_fingerprint(sweep)


def test_opening_adds_preroll_only_when_lyrics_start_early(tmp_path):
    options = VideoOptions(spectrum=False)
    assert make_project(tmp_path / "early", 1000).pre_roll_ms(options) == 4000
    assert make_project(tmp_path / "late", 7000).pre_roll_ms(options) == 0


def test_opening_cover_keeps_fixed_size_while_moving(tmp_path):
    renderer = FrameRenderer(make_project(tmp_path), VideoOptions(spectrum=False))
    start_x, start_y, start_size = renderer._opening_cover_geometry(500)
    hold_x, hold_y, hold_size = renderer._opening_cover_geometry(2999)
    middle_x, middle_y, middle_size = renderer._opening_cover_geometry(3500)
    end_x, end_y, end_size = renderer._opening_cover_geometry(3999)
    assert start_x == hold_x > middle_x > end_x
    assert start_y == hold_y == middle_y == end_y == renderer._px(216)
    assert start_size == hold_size == middle_size == end_size == renderer._px(430)


def test_opening_animation_fills_the_four_second_intro(monkeypatch, tmp_path):
    renderer = FrameRenderer(make_project(tmp_path), VideoOptions(spectrum=False))
    opening = Image.new("RGB", renderer.options.size, (0, 0, 0))
    monkeypatch.setattr(renderer, "_render_opening", lambda video_time_ms: opening)
    monkeypatch.setattr(renderer, "_render_main", lambda song_time_ms: Image.new("RGB", renderer.options.size, (200, 200, 200)))

    assert renderer.render(3999) is opening
    assert renderer.render(4000).getpixel((0, 0)) == (200, 200, 200)


def test_fractional_animation_ranges_are_not_stretched():
    assert video_module._ratio(0.5, 0.0, 0.32) == 1.0
    assert video_module._ratio(0.16, 0.0, 0.32) == 0.5


def test_log_spectrum_bands_are_all_non_empty():
    frequencies = video_module.np.fft.rfftfreq(2048, 1 / 11025)
    bands = video_module._log_band_indices(
        frequencies, low_hz=45, high_hz=5200, bars=64
    )
    assert len(bands) == 64
    assert all(len(indices) >= 1 for indices in bands)
    assert bands[0].tolist() == [9]
    assert bands[1].tolist() == [10]


def test_long_gap_becomes_blank_then_four_second_cue(tmp_path):
    project = make_project(tmp_path)
    renderer = FrameRenderer(project, VideoOptions(spectrum=False))
    assert renderer._line_end_ms(0) == 9000
    assert renderer._lyric_state(10_000)["kind"] == "blank"
    cue = renderer._lyric_state(17_500)
    assert cue["kind"] == "cue"
    assert cue["index"] == 1
    assert cue["remaining"] == 3500


def test_interlude_hides_following_line_and_resets_to_left_row(tmp_path):
    project = make_project(tmp_path)
    renderer = FrameRenderer(project, VideoOptions(spectrum=False))
    captured = []

    renderer._draw_lyric_block = lambda frame, line, row, progress: captured.append(
        (line["text"], row, progress)
    )
    frame = Image.new("RGB", renderer.options.size, (0, 0, 0))

    renderer._draw_lyric_pair(frame, 0, 0.5)
    assert captured == [("第一句", 0, 0.5)]

    captured.clear()
    renderer._draw_lyric_pair(frame, 1, 0.5)
    assert captured[0] == ("第二句", 0, 0.5)


def test_active_lyric_is_whole_line_or_uniform_sweep(tmp_path):
    project = make_project(tmp_path)
    frame = Image.new("RGB", (1920, 1080), (0, 0, 0))

    whole_line = FrameRenderer(project, VideoOptions(spectrum=False))
    whole_line_progress = []
    whole_line._draw_lyric_pair = lambda rendered, index, progress: whole_line_progress.append(progress)
    whole_line._draw_lyrics(frame, 5_000)

    sweep = FrameRenderer(
        project, VideoOptions(spectrum=False, lyric_highlight_mode="sweep")
    )
    sweep_progress = []
    sweep._draw_lyric_pair = lambda rendered, index, progress: sweep_progress.append(progress)
    sweep._draw_lyrics(frame, 5_000)

    assert whole_line_progress == [1.0]
    assert sweep_progress == [0.5]


def test_countdown_is_above_left_top_lyric(tmp_path):
    project = make_project(tmp_path)
    renderer = FrameRenderer(project, VideoOptions(spectrum=False))
    frame = Image.new("RGB", renderer.options.size, (0, 0, 0))
    renderer._draw_countdown(frame, 2_000)
    # The bar is intentionally in the left lyric area, rather than centered
    # along the bottom edge.
    top = renderer._px(710)
    assert frame.getpixel((renderer._px(100), top + renderer._px(4)))[2] > 0
    assert frame.getpixel((renderer.width // 2, renderer._px(1002))) == (0, 0, 0)


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


def test_queue_deduplicates_active_options_and_reports_waiting_count(monkeypatch, tmp_path):
    project = make_project(tmp_path / "project")
    monkeypatch.setattr(VideoProject, "load", classmethod(lambda cls, root, song_id: project))
    started = threading.Event()
    release = threading.Event()

    def blocking_render(project, options, destination, progress):
        started.set()
        assert release.wait(2)
        destination.write_bytes(b"video")
        return {
            "path": str(destination),
            "size": 5,
            "duration_ms": 1000,
            "frames": 30,
            "resolution": options.resolution,
            "pre_roll_ms": 0,
        }

    monkeypatch.setattr(video_module, "render_video", blocking_render)
    manager = VideoJobManager(tmp_path)
    first = manager.start(1, VideoOptions(spectrum=False), project.song)
    assert started.wait(1)
    duplicate = manager.start(1, VideoOptions(spectrum=False), project.song)
    waiting = manager.start(
        1, VideoOptions(spectrum=False, resolution="720p"), project.song
    )
    assert duplicate["id"] == first["id"]
    assert duplicate["deduplicated"] is True
    assert waiting["position"] == 1
    queue = manager.queue_status()
    assert queue["queued_count"] == 1
    assert queue["queued"][0]["id"] == waiting["id"]
    assert queue["queued"][0]["position"] == 1
    assert queue["queued"][0]["song"]["name"] == project.song["name"]
    assert queue["queued"][0]["resolution"] == "720p"
    release.set()
    manager.executor.shutdown(wait=True)


def test_video_jobs_resume_queued_work_after_manager_restart(monkeypatch, tmp_path):
    project = make_project(tmp_path / "project")
    monkeypatch.setattr(VideoProject, "load", classmethod(lambda cls, root, song_id: project))

    def fake_render(project, options, destination, progress):
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
    state_path = tmp_path / "video_jobs.json"
    options = VideoOptions(spectrum=False)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": {
                    "recovered": {
                        "id": "recovered",
                        "song_id": 1,
                        "song": project.song,
                        "resolution": options.resolution,
                        "task_key": "1:recovered",
                        "fingerprint": "recovered",
                        "options": options.to_dict(),
                        "status": "running",
                        "progress": 42,
                        "message": "正在渲染",
                        "result": None,
                        "error": None,
                        "created_at": 1,
                        "started_at": 1,
                        "finished_at": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manager = VideoJobManager(tmp_path / "outputs", state_path=state_path)
    deadline = time.time() + 2
    while time.time() < deadline and manager.get("recovered")["status"] not in {"done", "error"}:
        time.sleep(0.01)
    assert manager.get("recovered")["status"] == "done"
    manager.executor.shutdown(wait=True)
