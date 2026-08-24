from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .lyrics import build_timeline
from .netease import NeteaseClient, NeteaseError


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SONG_ID_PATTERNS = (
    re.compile(r"^\s*(\d+)\s*$"),
    re.compile(r"(?:[?#&]|^)id=(\d+)(?:&|$)"),
    re.compile(r"/song/(\d+)(?:[/?#]|$)"),
)
PUBLIC_SONG_FIELDS = (
    "id",
    "name",
    "artists",
    "artist",
    "album",
    "cover_url",
    "duration_ms",
    "fee",
    "copyright",
)


def parse_song_id(value: str | int) -> int:
    if isinstance(value, int):
        return value
    for pattern in SONG_ID_PATTERNS:
        match = pattern.search(str(value))
        if match:
            return int(match.group(1))
    raise ValueError("请输入歌曲 ID，或包含 id=... 的网易云歌曲链接")


def safe_filename(value: str, fallback: str = "song") -> str:
    value = INVALID_FILENAME.sub("_", value).strip().rstrip(".")
    value = re.sub(r"\s+", " ", value)
    return (value[:100] or fallback)


class SongDownloadService:
    def __init__(self, client: NeteaseClient, output_root: Path):
        self.client = client
        self.output_root = output_root

    def inspect(self, song_id: int) -> dict[str, Any]:
        local = load_local_song(self.output_root, song_id)
        if local is not None:
            return local
        song = self.client.song_detail(song_id)
        return public_song(song)

    def local_status(self, song_id: int, *, downloading: bool = False) -> dict[str, Any]:
        return local_song_status(self.output_root, song_id, downloading=downloading)

    def download(self, song_id: int, level: str = "exhigh") -> dict[str, Any]:
        song = self.client.song_detail(song_id)
        lyrics = self.client.lyrics(song_id)
        audio = self.client.player_url(song_id, level=level)

        directory = self.output_root / safe_filename(
            f"{song_id}_{song['artist']}_{song['name']}", fallback=str(song_id)
        )
        directory.mkdir(parents=True, exist_ok=True)

        cover_path = self._download_cover(song, directory)
        audio_path = self._download_audio(audio, directory)
        timeline = build_timeline(lyrics)
        lyric_types = available_lyric_types(lyrics)

        self._write_json(directory / "metadata.json", {**public_song(song), "audio": audio})
        self._write_json(directory / "lyrics_raw.json", lyrics)
        self._write_json(directory / "lyrics_timeline.json", timeline)
        self._write_text(directory / "lyrics.lrc", (lyrics.get("lrc") or {}).get("lyric", ""))
        self._write_text(
            directory / "lyrics_translated.lrc", (lyrics.get("tlyric") or {}).get("lyric", "")
        )
        self._write_text(
            directory / "lyrics_romanized.lrc", (lyrics.get("romalrc") or {}).get("lyric", "")
        )
        self._write_text(
            directory / "lyrics_karaoke_raw.lrc", (lyrics.get("klyric") or {}).get("lyric", "")
        )

        return {
            "song": public_song(song),
            "directory": str(directory.resolve()),
            "audio": str(audio_path.resolve()),
            "cover": str(cover_path.resolve()),
            "timeline_lines": len(timeline),
            "lyric_types": lyric_types,
            "quality": audio.get("level"),
            "bitrate": audio.get("br"),
            "size": audio_path.stat().st_size,
        }

    def _download_cover(self, song: dict[str, Any], directory: Path) -> Path:
        url = song.get("cover_url")
        if not url:
            raise NeteaseError("歌曲没有封面地址", code="cover_missing")
        separator = "&" if "?" in url else "?"
        response = self.client.stream(f"{url}{separator}param=1200y1200")
        content_type = response.headers.get("Content-Type", "").lower()
        extension = ".png" if "png" in content_type else ".jpg"
        return self._stream_to_file(response, directory / f"cover{extension}")

    def _download_audio(self, audio: dict[str, Any], directory: Path) -> Path:
        url = audio["url"]
        extension = "." + safe_filename(str(audio.get("type") or "mp3"), "mp3").lower()
        if extension not in {".mp3", ".flac", ".m4a", ".aac", ".wav"}:
            suffix = Path(urlparse(url).path).suffix.lower()
            extension = suffix if suffix in {".mp3", ".flac", ".m4a", ".aac", ".wav"} else ".audio"
        target = directory / f"audio{extension}"
        response = self.client.stream(url, headers={"Referer": "https://music.163.com/"})
        target = self._stream_to_file(response, target)

        expected_size = int(audio.get("size") or 0)
        if expected_size and target.stat().st_size != expected_size:
            raise NeteaseError(
                f"音频大小校验失败：预期 {expected_size}，实际 {target.stat().st_size}",
                code="audio_size_mismatch",
            )
        expected_md5 = (audio.get("md5") or "").lower()
        if expected_md5 and _file_md5(target) != expected_md5:
            raise NeteaseError("音频 MD5 校验失败", code="audio_md5_mismatch")
        return target

    @staticmethod
    def _stream_to_file(response: Any, target: Path) -> Path:
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        handle.write(chunk)
            temporary.replace(target)
            return target
        finally:
            response.close()
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.write_text(value or "", encoding="utf-8")


def public_song(song: dict[str, Any]) -> dict[str, Any]:
    return {key: song.get(key) for key in PUBLIC_SONG_FIELDS if key in song}


def available_lyric_types(payload: dict[str, Any]) -> list[str]:
    mappings = (
        ("original", "lrc"),
        ("translation", "tlyric"),
        ("romanization", "romalrc"),
        ("karaoke", "klyric"),
    )
    return [
        name
        for name, key in mappings
        if str((payload.get(key) or {}).get("lyric") or "").strip()
    ]


def load_local_song(output_root: Path, song_id: int) -> dict[str, Any] | None:
    status = local_song_status(output_root, song_id)
    if not status["ready"]:
        return None
    directory = sorted(output_root.glob(f"{song_id}_*"))[0]
    try:
        value = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, dict) or int(value.get("id") or 0) != song_id:
        return None
    return public_song(value)


def local_song_status(
    output_root: Path, song_id: int, *, downloading: bool = False
) -> dict[str, Any]:
    if downloading:
        return {
            "status": "downloading",
            "ready": False,
            "message": "其他请求正在下载这首歌的共享素材",
        }

    directories = sorted(output_root.glob(f"{song_id}_*"))
    if not directories:
        return {
            "status": "missing",
            "ready": False,
            "message": "本地还没有这首歌的素材",
        }

    directory = directories[0]
    metadata = directory / "metadata.json"
    timeline = directory / "lyrics_timeline.json"
    audio = [path for path in directory.glob("audio.*") if not path.name.endswith(".part")]
    cover = [path for path in directory.glob("cover.*") if not path.name.endswith(".part")]
    ready = metadata.exists() and timeline.exists() and bool(audio) and bool(cover)
    if ready:
        updated_at = max(
            metadata.stat().st_mtime,
            timeline.stat().st_mtime,
            audio[0].stat().st_mtime,
            cover[0].stat().st_mtime,
        )
        return {
            "status": "ready",
            "ready": True,
            "message": "共享素材已经可以使用",
            "updated_at": int(updated_at),
        }
    return {
        "status": "partial",
        "ready": False,
        "message": "检测到不完整的本地素材，请重新下载",
    }


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
