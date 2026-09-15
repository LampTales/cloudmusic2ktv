"""Pixel regressions for local compositing and disposable render caches."""
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from cloudmusic2ktv import video
from tests.test_video import make_project


class FullFrameRenderer(video.FrameRenderer):
    """Reference full-canvas compositing, without memoized resources."""

    def _cached(self, name, key, create, *, limit=256):
        return create()

    def _active_text_layer(self, text, font, x, y):
        layer = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (x, y), text, font=font, fill=(*self.accent, 255),
            stroke_width=self._px(3), stroke_fill=(8, 10, 15, 255),
        )
        return layer, (0, 0)

    def _draw_spectrum(self, frame, song_time_ms):
        values = self.spectrum.at(song_time_ms) if self.spectrum else video._preview_spectrum(64)
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        left, right = self._px(785), self.width - self._px(120)
        bottom, max_height = self._px(606), self._px(270)
        gap = self._px(5)
        bar_width = max(self._px(5), (right - left - gap * (len(values) - 1)) // len(values))
        for index, value in enumerate(values):
            height = max(self._px(5), int(max_height * float(value)))
            x = left + index * (bar_width + gap)
            draw.rounded_rectangle(
                (x, bottom - height, x + bar_width, bottom),
                radius=max(2, bar_width // 2),
                fill=(*self.accent, int(255 * self.options.spectrum_opacity)),
            )
        frame.paste(Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB"))


def rich_project(tmp_path):
    project = make_project(tmp_path)
    lines = []
    for i, text in enumerate(("晴れた空 Music café!?", "中文、かな & jg", "長い歌詞の描画確認です" * 9, "終わり")):
        start = (1000, 6500, 11000, 30000)[i]
        units = [
            dict(text=char, start_ms=start + j * 80, end_ms=start + j * 80 + 60, reading="あめ", romaji="ame")
            for j, char in enumerate(text)
        ]
        lines.append(dict(
            text=text, start_ms=start, end_ms=start + 4500,
            singing_end_ms=start + 3500, display_units=units,
            surface_spans=[dict(surface_start=j, surface_end=j + 1, reading="あめ", romaji="ame", mora_indices=[j]) for j in range(len(text))],
            mora=units, translation="A sunny sky with music", romanization="hareta sora", source_index=i,
        ))
    return replace(project, timeline=lines, alignment={"lines": lines})


@pytest.mark.parametrize("resolution", ["720p", "1080p"])
@pytest.mark.parametrize("mode,highlight,pronunciation,secondary", [
    ("legacy", "line", "none", "original"),
    ("legacy", "sweep", "none", "translation"),
    ("model", "line", "kana", "original"),
    ("model", "sweep", "romaji", "romanization"),
    ("model", "smooth", "kana", "translation"),
])
def test_frames_match_full_canvas_and_survive_cache_rebuild(tmp_path, resolution, mode, highlight, pronunciation, secondary):
    project = rich_project(tmp_path)
    options = video.VideoOptions(
        resolution=resolution, alignment_mode=mode, lyric_highlight_mode=highlight,
        pronunciation_mode=pronunciation, lyric_mode=secondary, background_mode="gradient",
    )
    values = np.random.default_rng(4).random((1200, 64)).astype(np.float32)
    spectrum = video.SpectrumData(values)
    reference = FullFrameRenderer(project, options, spectrum=spectrum)
    renderer = video.FrameRenderer(project, options, spectrum=spectrum)
    # Seek straight into a lyric, then backwards into the opening; cover the
    # reveal, preroll, character gaps, both rows, overlong text and interludes.
    times = (5300, 5300, 0, 650, 2999, 3800, 3999, 4000, 5000, 5066, 9999, 11000, 16000, 17000, 30000, 34000)
    for timestamp in times:
        assert renderer.render(timestamp).tobytes() == reference.render(timestamp).tobytes(), timestamp
    rebuilt = video.FrameRenderer(project, options, spectrum=spectrum)
    assert rebuilt.render(5300).tobytes() == renderer.render(5300).tobytes()


@pytest.mark.parametrize("opacity", [0.1, 0.65, 1.0])
def test_spectrum_edges_and_blending_on_patterned_background(tmp_path, opacity):
    project = make_project(tmp_path)
    options = video.VideoOptions(resolution="720p", spectrum_opacity=opacity)
    spectrum = video.SpectrumData(np.array([[0, 1] * 32], dtype=np.float32))
    reference = FullFrameRenderer(project, options, spectrum=spectrum)
    renderer = video.FrameRenderer(project, options, spectrum=spectrum)
    pixels = np.random.default_rng(5).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    expected, actual = Image.fromarray(pixels), Image.fromarray(pixels)
    reference._draw_spectrum(expected, 0)
    renderer._draw_spectrum(actual, 0)
    assert actual.tobytes() == expected.tobytes()


def test_model_mora_fallback_stays_identical(tmp_path):
    project = rich_project(tmp_path)
    for line in project.alignment["lines"]:
        line.pop("display_units")
    options = video.VideoOptions(alignment_mode="model", lyric_highlight_mode="sweep", pronunciation_mode="romaji", spectrum=False)
    reference = FullFrameRenderer(project, options)
    renderer = video.FrameRenderer(project, options)
    for timestamp in (5066, 5300, 11000):
        assert renderer.render(timestamp).tobytes() == reference.render(timestamp).tobytes()


def test_text_cache_is_bounded_and_eviction_preserves_pixels(tmp_path):
    project = rich_project(tmp_path)
    renderer = video.FrameRenderer(project, video.VideoOptions(spectrum=False))
    expected = renderer.render(5300).tobytes()
    font = renderer._font(60)
    for index in range(30):
        renderer._active_text_layer(f"歌詞 {index}", font, 100, 750)
    layers = renderer._render_caches["active_text"].values()
    assert len(renderer._render_caches["active_text"]) <= 4
    # Four short lyric images should occupy far less than one full RGBA frame.
    assert sum(layer.width * layer.height * 4 for layer, _ in layers) < renderer.width * renderer.height * 4
    assert renderer.render(5300).tobytes() == expected


def test_warm_layout_avoids_font_loading_and_measurement(tmp_path, monkeypatch):
    renderer = video.FrameRenderer(rich_project(tmp_path), video.VideoOptions(
        alignment_mode="model", lyric_highlight_mode="smooth", pronunciation_mode="kana", spectrum=False,
    ))
    expected = renderer.render(5300).tobytes()

    def unexpected(*args, **kwargs):
        raise AssertionError("warm render repeated font loading or layout measurement")

    monkeypatch.setattr(video.ImageFont, "truetype", unexpected)
    monkeypatch.setattr(renderer._measure_draw, "textbbox", unexpected)
    monkeypatch.setattr(renderer._measure_draw, "textlength", unexpected)
    assert renderer.render(5300).tobytes() == expected
