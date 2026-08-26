from __future__ import annotations

import colorsys
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


FPS = 30
OPENING_SECONDS = 4.0
OPENING_HOLD_MS = 3_000
OPENING_TRANSITION_MS = int(OPENING_SECONDS * 1000) - OPENING_HOLD_MS
OPENING_COVER_MOVE_RATIO = 0.58
OPENING_DISC_START_RATIO = 0.62
INTERLUDE_THRESHOLD_MS = 15_000
INTERLUDE_COUNTDOWN_MS = 4_000
MAX_INTERLUDE_SWEEP_MS = 8_000
SPECTRUM_CACHE_VERSION = 2
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
RESOLUTIONS = {"1080p": (1920, 1080), "720p": (1280, 720)}
LYRIC_MODES = {"original", "translation", "romanization"}
BACKGROUND_MODES = {"blur", "gradient", "solid", "custom"}
ACCENT_MODES = {"blue", "cover", "custom"}
QUALITY_MAP = {
    "high": (17, "slow"),
    "balanced": (20, "medium"),
    "compact": (24, "fast"),
}


class VideoError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoOptions:
    lyric_mode: str = "original"
    background_mode: str = "blur"
    background_color: str = "#171b26"
    accent_mode: str = "blue"
    accent_color: str = "#4f8cff"
    spectrum: bool = True
    spectrum_opacity: float = 0.65
    resolution: str = "1080p"
    quality: str = "balanced"
    opening: bool = True
    interlude_cue: bool = True

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "VideoOptions":
        value = value or {}
        options = cls(
            lyric_mode=str(value.get("lyric_mode") or "original"),
            background_mode=str(value.get("background_mode") or "blur"),
            background_color=str(value.get("background_color") or "#171b26"),
            accent_mode=str(value.get("accent_mode") or "blue"),
            accent_color=str(value.get("accent_color") or "#4f8cff"),
            spectrum=_to_bool(value.get("spectrum"), True),
            spectrum_opacity=float(value.get("spectrum_opacity", 0.65)),
            resolution=str(value.get("resolution") or "1080p"),
            quality=str(value.get("quality") or "balanced"),
            opening=_to_bool(value.get("opening"), True),
            interlude_cue=_to_bool(value.get("interlude_cue"), True),
        )
        options.validate()
        return options

    def validate(self) -> None:
        if self.lyric_mode not in LYRIC_MODES:
            raise VideoError("不支持的歌词显示模式")
        if self.background_mode not in BACKGROUND_MODES:
            raise VideoError("不支持的背景模式")
        if self.accent_mode not in ACCENT_MODES:
            raise VideoError("不支持的扫色模式")
        if self.resolution not in RESOLUTIONS:
            raise VideoError("不支持的分辨率")
        if self.quality not in QUALITY_MAP:
            raise VideoError("不支持的视频画质")
        if not HEX_COLOR.fullmatch(self.background_color):
            raise VideoError("背景颜色格式不正确")
        if not HEX_COLOR.fullmatch(self.accent_color):
            raise VideoError("扫色颜色格式不正确")
        if not 0.1 <= self.spectrum_opacity <= 1:
            raise VideoError("频谱透明度必须在 10% 到 100% 之间")

    @property
    def size(self) -> tuple[int, int]:
        return RESOLUTIONS[self.resolution]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VideoProject:
    directory: Path
    song: dict[str, Any]
    timeline: list[dict[str, Any]]
    audio_path: Path
    cover_path: Path
    custom_background_path: Path

    @classmethod
    def load(cls, output_root: Path, song_id: int) -> "VideoProject":
        directories = sorted(output_root.glob(f"{song_id}_*"))
        if not directories:
            raise VideoError("本地还没有这首歌的素材，请先点击“下载全部素材”")
        directory = directories[0]
        metadata_path = directory / "metadata.json"
        timeline_path = directory / "lyrics_timeline.json"
        audio_paths = [p for p in directory.glob("audio.*") if not p.name.endswith(".part")]
        cover_paths = list(directory.glob("cover.*"))
        if not metadata_path.exists() or not timeline_path.exists() or not audio_paths or not cover_paths:
            raise VideoError("歌曲素材不完整，请重新下载全部素材")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        timeline = [
            line
            for line in json.loads(timeline_path.read_text(encoding="utf-8"))
            if _is_display_lyric(line)
        ]
        return cls(
            directory=directory,
            song=metadata,
            timeline=timeline,
            audio_path=audio_paths[0],
            cover_path=cover_paths[0],
            custom_background_path=directory / "custom_background.png",
        )

    @property
    def duration_ms(self) -> int:
        value = self.song.get("duration_ms")
        if value:
            return int(value)
        if self.timeline:
            return int(self.timeline[-1].get("end_ms") or self.timeline[-1]["start_ms"] + 5000)
        raise VideoError("无法确定歌曲时长")

    @property
    def first_lyric_ms(self) -> int:
        return int(self.timeline[0]["start_ms"]) if self.timeline else 0

    def pre_roll_ms(self, options: VideoOptions) -> int:
        if options.opening and self.first_lyric_ms < int(OPENING_SECONDS * 1000):
            return int(OPENING_SECONDS * 1000)
        return 0


class FrameRenderer:
    def __init__(
        self,
        project: VideoProject,
        options: VideoOptions,
        *,
        spectrum: "SpectrumData | None" = None,
    ):
        self.project = project
        self.options = options
        self.width, self.height = options.size
        self.scale = self.width / 1920
        self.cover = Image.open(project.cover_path).convert("RGB")
        self.accent = self._accent_color()
        self.background = self._make_background()
        self.static_main = self._make_static_main()
        self.spectrum = spectrum
        self.pre_roll_ms = project.pre_roll_ms(options)

    def representative_time_ms(self) -> int:
        if not self.project.timeline:
            return self.pre_roll_ms
        line = self.project.timeline[min(1, len(self.project.timeline) - 1)]
        end = self._line_end_ms(min(1, len(self.project.timeline) - 1))
        song_time = int(line["start_ms"] + max(600, (end - int(line["start_ms"])) * 0.52))
        return self.pre_roll_ms + song_time

    def render(self, video_time_ms: int) -> Image.Image:
        song_time_ms = video_time_ms - self.pre_roll_ms
        if self.options.opening and video_time_ms < int(OPENING_SECONDS * 1000):
            return self._render_opening(video_time_ms)

        return self._render_main(song_time_ms)

    def _render_main(self, song_time_ms: int) -> Image.Image:
        frame = self.static_main.copy()
        draw = ImageDraw.Draw(frame)
        self._draw_time(draw, max(0, song_time_ms))
        if self.options.spectrum:
            self._draw_spectrum(frame, song_time_ms)
        self._draw_lyrics(frame, song_time_ms)
        return frame

    def _render_opening(self, video_time_ms: int) -> Image.Image:
        frame = self.background.copy()
        transition = _ratio(video_time_ms, OPENING_HOLD_MS, int(OPENING_SECONDS * 1000))
        cover_x, cover_y, cover_size = self._opening_cover_geometry(video_time_ms)

        draw = ImageDraw.Draw(frame, "RGBA")
        # The disc is deliberately absent during the hold. It starts only after
        # the cover has nearly reached its main-layout position.
        disc_progress = _smoothstep(_ratio(transition, OPENING_DISC_START_RATIO, 0.92))
        if disc_progress > 0:
            # Keep the disc fixed at its final position and reveal it only
            # after the cover has settled, so it reads as a background layer.
            disc_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            disc_draw = ImageDraw.Draw(disc_layer, "RGBA")
            self._draw_disc(
                disc_draw,
                (self._px(116), self._px(238), self._px(566), self._px(688)),
                opacity=disc_progress,
            )
            frame = Image.alpha_composite(frame.convert("RGBA"), disc_layer).convert("RGB")

        cover = ImageOps.fit(self.cover, (cover_size, cover_size), method=_resampling())
        cover = _round_image(cover, self._px(18))
        shadow = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (
                cover_x + self._px(13), cover_y + self._px(18),
                cover_x + cover_size + self._px(13), cover_y + cover_size + self._px(18),
            ),
            radius=self._px(18), fill=(0, 0, 0, 135)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(self._px(18)))
        frame = Image.alpha_composite(frame.convert("RGBA"), shadow).convert("RGB")
        frame.paste(cover, (cover_x, cover_y), cover)

        text_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_layer, "RGBA")
        title = str(self.project.song.get("name") or "")
        artist = str(self.project.song.get("artist") or "")
        title_font = self._font(58, bold=True, text=title)
        artist_font = self._font(28, text=artist)
        title_font = self._fit_font(title, title_font, self._px(1000), 58, bold=True)
        text_alpha = round(255 * (1.0 - _smoothstep(_ratio(transition, 0.0, 0.32))))
        title_y = cover_y + cover_size + self._px(45)
        self._center_text(
            draw, title_y, title, title_font,
            fill=(247, 248, 250, text_alpha), stroke_width=self._px(2),
            stroke_fill=(0, 0, 0, text_alpha),
        )
        self._center_text(
            draw, title_y + self._px(76), artist, artist_font,
            fill=(196, 201, 211, text_alpha), stroke_width=self._px(1),
            stroke_fill=(0, 0, 0, text_alpha),
        )
        frame = Image.alpha_composite(frame.convert("RGBA"), text_layer).convert("RGB")

        self._draw_fade_components(frame, video_time_ms - self.pre_roll_ms, transition)

        fade = min(1.0, video_time_ms / 650)
        if fade < 1:
            overlay = Image.new("RGB", frame.size, (5, 7, 11))
            frame = Image.blend(overlay, frame, fade)
        return frame

    def _opening_cover_geometry(self, video_time_ms: int) -> tuple[int, int, int]:
        transition = _ratio(video_time_ms, OPENING_HOLD_MS, int(OPENING_SECONDS * 1000))
        motion = _smoothstep(_ratio(transition, 0.0, OPENING_COVER_MOVE_RATIO))
        cover_size = self._px(430)
        target_x, target_y = self._px(250), self._px(216)
        start_x = (self.width - cover_size) // 2
        cover_x = round(start_x + (target_x - start_x) * motion)
        return cover_x, target_y, cover_size

    def _draw_fade_components(self, frame: Image.Image, song_time_ms: int, progress: float) -> None:
        reveal = _smoothstep(_ratio(progress, 0.48, 0.92))
        if reveal <= 0:
            return
        source = self._render_main(song_time_ms)
        alpha = round(255 * reveal)
        boxes = (
            (self._px(72), self._px(52), self.width - self._px(72), self._px(162)),
            (self._px(760), self._px(290), self.width - self._px(96), self._px(635)),
            (self._px(52), self._px(680), self.width - self._px(52), self.height - self._px(18)),
        )
        for left, top, right, bottom in boxes:
            crop = source.crop((left, top, right, bottom)).convert("RGBA")
            crop.putalpha(Image.new("L", crop.size, alpha))
            frame.paste(crop, (left, top), crop)

    def _draw_disc(
        self,
        draw: ImageDraw.ImageDraw,
        disc_box: tuple[int, int, int, int],
        *,
        opacity: float = 1.0,
    ) -> None:
        opacity = max(0.0, min(1.0, opacity))
        disc_alpha = round(255 * opacity)
        outline_alpha = round(210 * opacity)
        draw.ellipse(
            disc_box,
            fill=(8, 9, 12, disc_alpha),
            outline=(74, 77, 83, disc_alpha),
            width=self._px(3),
        )
        for inset in range(22, 188, 18):
            d = self._px(inset)
            draw.ellipse(
                (disc_box[0] + d, disc_box[1] + d, disc_box[2] - d, disc_box[3] - d),
                outline=(49, 52, 58, outline_alpha), width=max(1, self._px(1)),
            )
        label_box = tuple(
            value + self._px(155 if index < 2 else -155)
            for index, value in enumerate(disc_box)
        )
        draw.ellipse(
            label_box,
            fill=(*self.accent, round(230 * opacity)),
            outline=(245, 245, 245, round(190 * opacity)),
            width=self._px(2),
        )

    def _make_background(self) -> Image.Image:
        size = (self.width, self.height)
        mode = self.options.background_mode
        if mode == "blur":
            background = ImageOps.fit(self.cover, size, method=_resampling())
            background = background.filter(ImageFilter.GaussianBlur(self._px(52)))
            background = ImageEnhance.Color(background).enhance(0.78)
            overlay = Image.new("RGB", size, (7, 10, 16))
            return Image.blend(background, overlay, 0.55)
        if mode == "custom":
            if not self.project.custom_background_path.exists():
                raise VideoError("尚未上传自定义背景图片")
            background = Image.open(self.project.custom_background_path).convert("RGB")
            background = ImageOps.fit(background, size, method=_resampling())
            return Image.blend(background, Image.new("RGB", size, (4, 6, 10)), 0.34)
        if mode == "solid":
            return Image.new("RGB", size, _hex_to_rgb(self.options.background_color))

        accent = np.array(self.accent, dtype=np.float32)
        dark = np.maximum(accent * 0.11, np.array([5, 7, 12], dtype=np.float32))
        secondary = np.maximum(accent * 0.28, np.array([16, 18, 27], dtype=np.float32))
        y = np.linspace(0, 1, self.height, dtype=np.float32)[:, None, None]
        x = np.linspace(0, 1, self.width, dtype=np.float32)[None, :, None]
        mix = np.clip(0.15 + 0.55 * x + 0.25 * (1 - y), 0, 1)
        array = dark[None, None, :] * (1 - mix) + secondary[None, None, :] * mix
        return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGB")

    def _make_static_main(self) -> Image.Image:
        frame = self.background.copy().convert("RGBA")
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")

        # Quiet header strip keeps mixed cover colors away from the metadata.
        draw.rounded_rectangle(
            (self._px(72), self._px(52), self.width - self._px(72), self._px(162)),
            radius=self._px(24), fill=(4, 6, 10, 88)
        )
        title = str(self.project.song.get("name") or "")
        artist = str(self.project.song.get("artist") or "")
        title_font = self._fit_font(title, self._font(50, bold=True), self._px(1040), 50, bold=True)
        draw.text(
            (self._px(108), self._px(67)), title, font=title_font, fill=(248, 248, 245, 255),
            stroke_width=self._px(2), stroke_fill=(0, 0, 0, 150)
        )
        draw.text(
            (self._px(110), self._px(124)), artist, font=self._font(23, text=artist), fill=(190, 197, 209, 255)
        )

        # Vinyl disc behind the cover.
        disc_box = (self._px(116), self._px(238), self._px(566), self._px(688))
        self._draw_disc(draw, disc_box)

        cover_size = self._px(430)
        cover = ImageOps.fit(self.cover, (cover_size, cover_size), method=_resampling())
        cover = _round_image(cover, self._px(18))
        shadow = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        cover_x, cover_y = self._px(250), self._px(216)
        shadow_draw.rounded_rectangle(
            (cover_x + self._px(13), cover_y + self._px(18), cover_x + cover_size + self._px(13), cover_y + cover_size + self._px(18)),
            radius=self._px(18), fill=(0, 0, 0, 135)
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(self._px(18)))
        frame = Image.alpha_composite(frame, shadow)
        frame = Image.alpha_composite(frame, overlay)
        frame.paste(cover, (cover_x, cover_y), cover)
        return frame.convert("RGB")

    def _draw_time(self, draw: ImageDraw.ImageDraw, song_time_ms: int) -> None:
        text = f"{_format_time(song_time_ms)} / {_format_time(self.project.duration_ms)}"
        font = self._font(25, bold=True, text=text)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (self.width - self._px(108) - (bbox[2] - bbox[0]), self._px(91)),
            text, font=font, fill=(222, 225, 231), stroke_width=self._px(1), stroke_fill=(0, 0, 0)
        )

    def _draw_spectrum(self, frame: Image.Image, song_time_ms: int) -> None:
        values = self.spectrum.at(song_time_ms) if self.spectrum else _preview_spectrum(64)
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        left, right = self._px(785), self.width - self._px(120)
        bottom, max_height = self._px(606), self._px(270)
        gap = self._px(5)
        bar_width = max(self._px(5), (right - left - gap * (len(values) - 1)) // len(values))
        alpha = int(255 * self.options.spectrum_opacity)
        for index, value in enumerate(values):
            height = max(self._px(5), int(max_height * float(value)))
            x = left + index * (bar_width + gap)
            draw.rounded_rectangle(
                (x, bottom - height, x + bar_width, bottom),
                radius=max(2, bar_width // 2), fill=(*self.accent, alpha)
            )
        frame.paste(Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB"))

    def _draw_lyrics(self, frame: Image.Image, song_time_ms: int) -> None:
        if not self.project.timeline or song_time_ms < 0:
            return
        state = self._lyric_state(song_time_ms)
        if state["kind"] == "blank":
            return
        if state["kind"] == "cue":
            self._draw_lyric_pair(frame, state["index"], None)
            self._draw_countdown(frame, state["remaining"])
            return
        self._draw_lyric_pair(frame, state["index"], state["progress"])

    def _lyric_state(self, song_time_ms: int) -> dict[str, Any]:
        starts = [int(line["start_ms"]) for line in self.project.timeline]
        if song_time_ms < starts[0]:
            if self.options.interlude_cue and starts[0] - song_time_ms <= INTERLUDE_COUNTDOWN_MS:
                return {"kind": "cue", "index": 0, "remaining": starts[0] - song_time_ms}
            return {"kind": "blank"}
        index = int(np.searchsorted(starts, song_time_ms, side="right") - 1)
        start = starts[index]
        end = self._line_end_ms(index)
        if index + 1 < len(starts) and starts[index + 1] - start >= INTERLUDE_THRESHOLD_MS:
            if song_time_ms <= end:
                return {"kind": "active", "index": index, "progress": _ratio(song_time_ms, start, end)}
            next_start = starts[index + 1]
            if self.options.interlude_cue and song_time_ms >= next_start - INTERLUDE_COUNTDOWN_MS:
                return {"kind": "cue", "index": index + 1, "remaining": next_start - song_time_ms}
            return {"kind": "blank"}
        return {"kind": "active", "index": index, "progress": _ratio(song_time_ms, start, end)}

    def _line_end_ms(self, index: int) -> int:
        line = self.project.timeline[index]
        start = int(line["start_ms"])
        if index + 1 >= len(self.project.timeline):
            return min(self.project.duration_ms, start + MAX_INTERLUDE_SWEEP_MS)
        next_start = int(self.project.timeline[index + 1]["start_ms"])
        if next_start - start >= INTERLUDE_THRESHOLD_MS:
            return min(next_start, start + MAX_INTERLUDE_SWEEP_MS)
        return max(start + 400, next_start)

    def _draw_lyric_pair(self, frame: Image.Image, index: int, progress: float | None) -> None:
        current = self.project.timeline[index]
        following = self.project.timeline[index + 1] if index + 1 < len(self.project.timeline) else None
        blocks = [(index, current, progress)]
        # Keep the next line hidden during an interlude.  It should first
        # appear as the cue near the end of the gap, giving each section a
        # clean visual reset instead of leaking the next line into the
        # previous one.
        if following and not self._is_interlude_after(index):
            blocks.append((index + 1, following, None))
        for line_index, line, line_progress in blocks:
            row = self._lyric_row(line_index)
            self._draw_lyric_block(frame, line, row, line_progress)

    def _is_interlude_after(self, index: int) -> bool:
        if index + 1 >= len(self.project.timeline):
            return False
        start = int(self.project.timeline[index]["start_ms"])
        next_start = int(self.project.timeline[index + 1]["start_ms"])
        return next_start - start >= INTERLUDE_THRESHOLD_MS

    def _lyric_row(self, index: int) -> int:
        """Return the alternating lyric row, resetting after each interlude."""
        row = 0
        for line_index in range(index):
            if self._is_interlude_after(line_index):
                row = 0
            else:
                row = 1 - row
        return row

    def _draw_lyric_block(
        self, frame: Image.Image, line: dict[str, Any], row: int, progress: float | None
    ) -> None:
        text = str(line.get("text") or "")
        if not text:
            return
        has_secondary = self.options.lyric_mode != "original"
        top_positions = (720, 875) if has_secondary else (765, 900)
        y = self._px(top_positions[row])
        max_width = self.width - self._px(150)
        font = self._fit_font(text, self._font(60, bold=True, text=text), max_width, 60, bold=True, minimum=36)
        inactive = (236, 238, 241)
        align_left = row == 0
        self._draw_wipe_text(frame, text, y, font, align_left, inactive, progress)

        secondary = None
        if self.options.lyric_mode == "translation":
            secondary = line.get("translation")
        elif self.options.lyric_mode == "romanization":
            secondary = line.get("romanization")
        if secondary:
            secondary = str(secondary)
            secondary_font = self._fit_font(
                secondary, self._font(29, text=secondary), max_width, 29, minimum=20
            )
            self._draw_plain_text(
                frame, secondary, y + self._px(67), secondary_font, align_left, (174, 181, 193)
            )

    def _draw_wipe_text(
        self,
        frame: Image.Image,
        text: str,
        y: int,
        font: ImageFont.FreeTypeFont,
        align_left: bool,
        inactive: tuple[int, int, int],
        progress: float | None,
    ) -> None:
        draw = ImageDraw.Draw(frame)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=self._px(3))
        width = bbox[2] - bbox[0]
        x = self._px(76) if align_left else self.width - self._px(76) - width
        draw.text(
            (x, y), text, font=font, fill=inactive, stroke_width=self._px(3), stroke_fill=(8, 10, 15)
        )
        if progress is None:
            return
        active_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        active_draw = ImageDraw.Draw(active_layer)
        active_draw.text(
            (x, y), text, font=font, fill=(*self.accent, 255),
            stroke_width=self._px(3), stroke_fill=(8, 10, 15, 255)
        )
        clip_right = x + int(width * max(0, min(1, progress)))
        if clip_right > x:
            cropped = active_layer.crop((x, 0, clip_right, self.height))
            frame.paste(cropped.convert("RGB"), (x, 0), cropped)

    def _draw_plain_text(
        self,
        frame: Image.Image,
        text: str,
        y: int,
        font: ImageFont.FreeTypeFont,
        align_left: bool,
        fill: tuple[int, int, int],
    ) -> None:
        draw = ImageDraw.Draw(frame)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        x = self._px(78) if align_left else self.width - self._px(78) - width
        draw.text((x, y), text, font=font, fill=fill, stroke_width=self._px(2), stroke_fill=(8, 10, 15))

    def _draw_countdown(self, frame: Image.Image, remaining_ms: int) -> None:
        ratio = max(0, min(1, remaining_ms / INTERLUDE_COUNTDOWN_MS))
        draw = ImageDraw.Draw(frame, "RGBA")
        width = self._px(420)
        height = self._px(8)
        # Place the cue above the left/top lyric block, matching the usual
        # KTV layout and keeping the bottom of the frame free for controls.
        left = self._px(76)
        top = self._px(665 if self.options.lyric_mode != "original" else 710)
        draw.rounded_rectangle((left, top, left + width, top + height), radius=height // 2, fill=(255, 255, 255, 55))
        draw.rounded_rectangle(
            (left, top, left + int(width * ratio), top + height), radius=height // 2,
            fill=(*self.accent, 230)
        )

    def _accent_color(self) -> tuple[int, int, int]:
        if self.options.accent_mode == "blue":
            return (79, 140, 255)
        if self.options.accent_mode == "custom":
            return _hex_to_rgb(self.options.accent_color)
        return _cover_accent(self.cover)

    def _font(
        self, size: int, bold: bool = False, text: str | None = None
    ) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(_font_path(bold, text)), self._px(size))

    def _fit_font(
        self,
        text: str,
        initial: ImageFont.FreeTypeFont,
        max_width: int,
        size: int,
        *,
        bold: bool = False,
        minimum: int = 18,
    ) -> ImageFont.FreeTypeFont:
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = self._font(size, bold=bold, text=text)
        while size > minimum and draw.textbbox((0, 0), text, font=font)[2] > max_width:
            size -= 2
            font = self._font(size, bold=bold, text=text)
        return font

    def _center_text(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        text: str,
        font: ImageFont.FreeTypeFont,
        *,
        fill: tuple[int, int, int, int],
        stroke_width: int,
        stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 150),
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (self.width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)

    def _px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))


class SpectrumData:
    def __init__(self, values: np.ndarray, fps: int = FPS):
        self.values = values
        self.fps = fps

    def at(self, song_time_ms: int) -> np.ndarray:
        if song_time_ms < 0 or not len(self.values):
            return np.zeros(self.values.shape[1] if self.values.ndim == 2 else 64, dtype=np.float32)
        index = min(len(self.values) - 1, int(song_time_ms / 1000 * self.fps))
        return self.values[index]

    @classmethod
    def load_or_create(cls, project: VideoProject, ffmpeg_executable: str) -> "SpectrumData":
        cache = project.directory / "spectrum_30fps.npz"
        if cache.exists() and cache.stat().st_mtime >= project.audio_path.stat().st_mtime:
            with np.load(cache) as value:
                if "version" in value.files and int(value["version"]) == SPECTRUM_CACHE_VERSION:
                    values = value["values"].astype(np.float32)
                    fps = int(value["fps"])
                    return cls(values, fps)
        values = _analyze_spectrum(project.audio_path, project.duration_ms, ffmpeg_executable)
        np.savez_compressed(
            cache,
            values=values.astype(np.float16),
            fps=FPS,
            version=SPECTRUM_CACHE_VERSION,
        )
        return cls(values)


def render_preview(project: VideoProject, options: VideoOptions, destination: Path) -> dict[str, Any]:
    spectrum = None
    if options.spectrum:
        try:
            spectrum = SpectrumData.load_or_create(project, get_ffmpeg_executable())
        except VideoError:
            # A still preview can use a representative spectrum before FFmpeg is installed.
            spectrum = None
    renderer = FrameRenderer(project, options, spectrum=spectrum)
    frame = renderer.render(renderer.representative_time_ms())
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.save(destination, format="PNG", optimize=True)
    return {
        "path": str(destination.resolve()),
        "width": renderer.width,
        "height": renderer.height,
        "accent": "#%02x%02x%02x" % renderer.accent,
        "pre_roll_ms": renderer.pre_roll_ms,
    }


def render_video(
    project: VideoProject,
    options: VideoOptions,
    destination: Path,
    progress: Callable[[int, int], None] | None = None,
    *,
    duration_limit_ms: int | None = None,
) -> dict[str, Any]:
    ffmpeg = get_ffmpeg_executable()
    spectrum = SpectrumData.load_or_create(project, ffmpeg) if options.spectrum else None
    renderer = FrameRenderer(project, options, spectrum=spectrum)
    total_ms = renderer.pre_roll_ms + project.duration_ms
    if duration_limit_ms is not None:
        total_ms = min(total_ms, duration_limit_ms)
    total_frames = max(1, math.ceil(total_ms / 1000 * FPS))
    temporary = destination.with_name(destination.stem + ".part" + destination.suffix)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    crf, preset = QUALITY_MAP[options.quality]
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{renderer.width}x{renderer.height}",
        "-r",
        str(FPS),
        "-i",
        "pipe:0",
    ]
    if renderer.pre_roll_ms:
        command.extend(["-itsoffset", f"{renderer.pre_roll_ms / 1000:.3f}"])
    command.extend(
        [
            "-i",
            str(project.audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-t",
            f"{total_ms / 1000:.3f}",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for frame_index in range(total_frames):
            frame_time_ms = int(frame_index * 1000 / FPS)
            frame = renderer.render(frame_time_ms).convert("RGB")
            process.stdin.write(frame.tobytes())
            if progress and (frame_index % FPS == 0 or frame_index + 1 == total_frames):
                progress(frame_index + 1, total_frames)
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait()
        if return_code:
            raise VideoError(f"FFmpeg 编码失败：{stderr[-1200:]}")
        temporary.replace(destination)
    except Exception:
        if process.poll() is None:
            process.kill()
        if temporary.exists():
            temporary.unlink()
        raise
    return {
        "path": str(destination.resolve()),
        "size": destination.stat().st_size,
        "duration_ms": total_ms,
        "frames": total_frames,
        "resolution": options.resolution,
        "pre_roll_ms": renderer.pre_roll_ms,
    }


class VideoJobManager:
    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-render")
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(
        self, song_id: int, options: VideoOptions, song: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        fingerprint = video_options_fingerprint(options)
        task_key = f"{song_id}:{fingerprint}"
        with self.lock:
            for existing in self.jobs.values():
                if existing.get("task_key") == task_key and existing["status"] in {"queued", "running"}:
                    result = self._public_job(existing)
                    result["deduplicated"] = True
                    result["position"] = self._position_locked(existing["id"])
                    return result
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "song_id": song_id,
                "song": {
                    "name": str((song or {}).get("name") or ""),
                    "artist": str((song or {}).get("artist") or ""),
                    "cover_url": str((song or {}).get("cover_url") or ""),
                },
                "resolution": options.resolution,
                "task_key": task_key,
                "fingerprint": fingerprint,
                "status": "queued",
                "progress": 0,
                "message": "等待渲染",
                "result": None,
                "error": None,
                "created_at": int(time.time()),
                "started_at": None,
                "finished_at": None,
            }
            self.jobs[job_id] = job
        self.executor.submit(self._run, job_id, song_id, options)
        with self.lock:
            result = self._public_job(self.jobs[job_id])
            result["deduplicated"] = False
            result["position"] = self._position_locked(job_id)
            return result

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return self._public_job(job) if job else None

    def queue_status(self) -> dict[str, Any]:
        with self.lock:
            active = [
                job for job in self.jobs.values() if job["status"] in {"queued", "running"}
            ]
            running = next((job for job in active if job["status"] == "running"), None)
            current = running or (active[0] if active else None)
            waiting_jobs = [job for job in active if job is not current]
            queued = []
            for position, job in enumerate(waiting_jobs, start=1):
                item = self._public_job(job)
                item["position"] = position
                queued.append(item)
            recent = next(
                (
                    job
                    for job in reversed(list(self.jobs.values()))
                    if job["status"] in {"done", "error"}
                ),
                None,
            )
            return {
                "current": self._public_job(current) if current else None,
                "queued_count": len(waiting_jobs),
                "queued": queued,
                "recent": self._public_job(recent) if recent else None,
            }

    def _run(self, job_id: str, song_id: int, options: VideoOptions) -> None:
        try:
            self._update(
                job_id,
                status="running",
                message="分析音频与准备画面",
                progress=1,
                started_at=int(time.time()),
            )
            project = VideoProject.load(self.output_root, song_id)
            fingerprint = video_options_fingerprint(options)
            destination = project.directory / f"ktv_{options.resolution}_{fingerprint}.mp4"

            def on_progress(done: int, total: int) -> None:
                percent = min(99, max(2, round(done / total * 100)))
                self._update(job_id, progress=percent, message=f"正在渲染 {done}/{total} 帧")

            result = render_video(project, options, destination, on_progress)
            self._update(
                job_id,
                status="done",
                progress=100,
                message="视频生成完成",
                result=result,
                finished_at=int(time.time()),
            )
        except Exception as exc:
            self._update(
                job_id,
                status="error",
                message="生成失败",
                error=str(exc),
                finished_at=int(time.time()),
            )

    def _update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            self.jobs[job_id].update(values)

    def _position_locked(self, job_id: str) -> int:
        position = 0
        for job in self.jobs.values():
            if job["status"] not in {"queued", "running"}:
                continue
            if job["id"] == job_id:
                return position
            position += 1
        return 0

    @staticmethod
    def _public_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        return {key: value for key, value in job.items() if key != "task_key"}


def video_options_fingerprint(options: VideoOptions) -> str:
    encoded = json.dumps(
        options.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()[:12]


def get_ffmpeg_executable() -> str:
    configured = os.environ.get("CLOUDMUSIC2KTV_FFMPEG", "").strip()
    if configured:
        executable = Path(configured).expanduser()
        if executable.is_file():
            return str(executable)
        raise VideoError(f"CLOUDMUSIC2KTV_FFMPEG 指向的文件不存在：{executable}")
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise VideoError("尚未安装视频编码依赖 imageio-ffmpeg") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def save_custom_background(upload: Any, destination: Path) -> None:
    try:
        image = Image.open(upload.stream).convert("RGB")
        image.thumbnail((3840, 2160), _resampling())
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "PNG", optimize=True)
    except Exception as exc:
        raise VideoError("无法读取上传的背景图片") from exc


def _analyze_spectrum(
    audio_path: Path, duration_ms: int, ffmpeg_executable: str, bars: int = 64
) -> np.ndarray:
    sample_rate = 11025
    command = [
        ffmpeg_executable,
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        raise VideoError("无法解码音频以生成频谱")
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    frame_count = max(1, math.ceil(duration_ms / 1000 * FPS))
    window_size = 2048
    window = np.hanning(window_size).astype(np.float32)
    frequencies = np.fft.rfftfreq(window_size, 1 / sample_rate)
    band_indices = _log_band_indices(
        frequencies,
        low_hz=45.0,
        high_hz=min(5200.0, sample_rate / 2),
        bars=bars,
    )
    values = np.zeros((frame_count, bars), dtype=np.float32)
    half = window_size // 2
    padded = np.pad(samples, (half, half))
    for frame_index in range(frame_count):
        center = int(frame_index / FPS * sample_rate) + half
        chunk = padded[center - half : center + half]
        if len(chunk) < window_size:
            chunk = np.pad(chunk, (0, window_size - len(chunk)))
        magnitude = np.abs(np.fft.rfft(chunk * window))
        for band, indices in enumerate(band_indices):
            if len(indices):
                values[frame_index, band] = float(np.mean(magnitude[indices]))
    values = np.log1p(values)
    normalizer = max(float(np.percentile(values, 98)), 1e-6)
    values = np.clip(values / normalizer, 0, 1)
    # Fast attack with a slower release produces a readable, stable spectrum.
    for index in range(1, frame_count):
        rising = values[index] >= values[index - 1]
        values[index] = np.where(
            rising,
            values[index - 1] * 0.30 + values[index] * 0.70,
            values[index - 1] * 0.76 + values[index] * 0.24,
        )
    return values.astype(np.float32)


def _log_band_indices(
    frequencies: np.ndarray, *, low_hz: float, high_hz: float, bars: int
) -> list[np.ndarray]:
    """Map logarithmic frequency bands onto non-empty FFT-bin ranges.

    Logarithmic edges do not generally land on FFT bins.  At low frequencies
    several adjacent edges can therefore round to the same bin, producing an
    empty band whose value would otherwise stay zero forever.  The boundaries
    below are snapped to the real FFT grid and made strictly increasing while
    preserving the requested low/high limits as closely as possible.
    """
    if bars <= 0:
        return []
    if frequencies.ndim != 1 or not len(frequencies):
        return [np.array([], dtype=np.intp) for _ in range(bars)]
    low_hz = max(float(frequencies[0]), float(low_hz))
    high_hz = min(float(frequencies[-1]), float(high_hz))
    if high_hz <= low_hz:
        return [np.array([], dtype=np.intp) for _ in range(bars)]

    edges = np.geomspace(low_hz, high_hz, bars + 1)
    boundaries = np.searchsorted(frequencies, edges, side="left").astype(np.intp)
    boundaries[0] = max(0, min(int(boundaries[0]), len(frequencies) - bars))
    boundaries[-1] = min(len(frequencies), max(int(boundaries[-1]), boundaries[0] + bars))

    # Keep enough bins for the remaining bands while forcing every interval
    # to contain at least one actual FFT bin.
    for index in range(1, len(boundaries)):
        minimum = int(boundaries[index - 1]) + 1
        maximum = len(frequencies) - (len(boundaries) - 1 - index)
        boundaries[index] = max(minimum, min(int(boundaries[index]), maximum))

    return [np.arange(boundaries[index], boundaries[index + 1], dtype=np.intp) for index in range(bars)]


def _cover_accent(image: Image.Image) -> tuple[int, int, int]:
    sample = image.convert("RGB").resize((64, 64), _resampling()).quantize(colors=12)
    palette = sample.getpalette() or []
    candidates: list[tuple[float, tuple[int, int, int]]] = []
    for count, palette_index in sample.getcolors() or []:
        offset = palette_index * 3
        rgb = tuple(palette[offset : offset + 3])
        if len(rgb) != 3:
            continue
        h, s, v = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
        if 0.25 <= v <= 0.95:
            candidates.append((count * (0.35 + s) * (0.5 + v), rgb))
    rgb = max(candidates, default=(1, (79, 140, 255)), key=lambda item: item[0])[1]
    h, s, v = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
    s = max(0.52, s)
    v = min(0.94, max(0.68, v))
    return tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(h, s, v))


def _font_path(bold: bool, text: str | None = None) -> Path:
    text = text or ""
    contains_kana = any("\u3040" <= char <= "\u30ff" for char in text)
    contains_cjk = any("\u3400" <= char <= "\u9fff" for char in text)

    configured_dir = os.environ.get("CLOUDMUSIC2KTV_FONT_DIR", "").strip()
    font_dirs = [Path(configured_dir).expanduser()] if configured_dir else []
    font_dirs.extend(
        [
            Path("/usr/share/fonts/opentype/noto"),
            Path("/usr/share/fonts/truetype/noto"),
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path(r"C:\Windows\Fonts"),
        ]
    )

    regular_names = [
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJKsc-Regular.otf",
        "NotoSansCJKjp-Regular.otf",
        "NotoSansSC-Regular.otf",
        "PingFang.ttc",
        "Hiragino Sans GB.ttc",
        "Hiragino Sans.ttc",
        "msyh.ttc",
        "YuGothM.ttc",
        "meiryo.ttc",
        "Arial Unicode.ttf",
        "arial.ttf",
        "DejaVuSans.ttf",
    ]
    bold_names = [
        "NotoSansCJK-Bold.ttc",
        "NotoSansCJKsc-Bold.otf",
        "NotoSansCJKjp-Bold.otf",
        "NotoSansSC-Bold.otf",
        "PingFang.ttc",
        "Hiragino Sans GB.ttc",
        "Hiragino Sans.ttc",
        "msyhbd.ttc",
        "YuGothB.ttc",
        "meiryob.ttc",
        "Arial Unicode.ttf",
        "arialbd.ttf",
        "DejaVuSans-Bold.ttf",
    ]
    names = bold_names if bold else regular_names

    def find(names_to_check: list[str]) -> list[Path]:
        return [directory / name for directory in font_dirs for name in names_to_check]

    candidates = []
    if contains_cjk and not contains_kana:
        candidates.extend(find(names))
    elif contains_kana:
        candidates.extend(find(names))
    candidates.extend(find(names))
    for path in candidates:
        if path.exists():
            return path
    configured_hint = f"，请检查 CLOUDMUSIC2KTV_FONT_DIR={configured_dir}" if configured_dir else ""
    raise VideoError(f"没有找到支持中日文的系统字体{configured_hint}")


def _round_image(image: Image.Image, radius: int) -> Image.Image:
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width, image.height), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def _preview_spectrum(bars: int) -> np.ndarray:
    x = np.linspace(0, math.pi * 5, bars)
    envelope = 0.42 + 0.42 * np.sin(np.linspace(0, math.pi, bars))
    return np.clip((0.32 + 0.23 * np.sin(x) + 0.15 * np.sin(x * 2.31)) * envelope + 0.12, 0.08, 0.9)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def _format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _ratio(value: float, start: float, end: float) -> float:
    return max(0.0, min(1.0, (value - start) / max(1e-9, end - start)))


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _is_display_lyric(line: dict[str, Any]) -> bool:
    text = str(line.get("text") or "").strip()
    if not text:
        return False
    normalized = text.casefold().replace(" ", "")
    if normalized in {"间奏", "間奏", "instrumental"}:
        return False
    if "music" in normalized and len(normalized) <= 24:
        return False
    return True


def _resampling() -> Any:
    return getattr(Image, "Resampling", Image).LANCZOS
