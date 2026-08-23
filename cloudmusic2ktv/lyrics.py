from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


def _to_milliseconds(minutes: str, seconds: str, fraction: str | None) -> int:
    # LRC commonly uses either hundredths (.19) or milliseconds (.190).
    milliseconds = int(((fraction or "") + "000")[:3])
    return (int(minutes) * 60 + int(seconds)) * 1000 + milliseconds


def parse_lrc(text: str | None) -> list[dict[str, Any]]:
    """Parse standard/multi-timestamp LRC into a stable millisecond timeline."""
    result: list[dict[str, Any]] = []
    for source_index, raw_line in enumerate((text or "").splitlines()):
        timestamps = list(TIMESTAMP_RE.finditer(raw_line))
        if not timestamps:
            continue
        lyric = TIMESTAMP_RE.sub("", raw_line).strip()
        for timestamp in timestamps:
            result.append(
                {
                    "start_ms": _to_milliseconds(*timestamp.groups()),
                    "text": lyric,
                    "source_index": source_index,
                }
            )
    result.sort(key=lambda item: (item["start_ms"], item["source_index"]))
    return result


def build_timeline(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge original, translated and romanized lines on their timestamps."""
    original = parse_lrc((payload.get("lrc") or {}).get("lyric"))
    translated = _group_by_time(parse_lrc((payload.get("tlyric") or {}).get("lyric")))
    romanized = _group_by_time(parse_lrc((payload.get("romalrc") or {}).get("lyric")))

    timeline: list[dict[str, Any]] = []
    for index, line in enumerate(original):
        start_ms = line["start_ms"]
        next_start = original[index + 1]["start_ms"] if index + 1 < len(original) else start_ms + 5000
        timeline.append(
            {
                "start_ms": start_ms,
                "end_ms": max(start_ms, next_start),
                "text": line["text"],
                "translation": _joined(translated.get(start_ms)),
                "romanization": _joined(romanized.get(start_ms)),
            }
        )
    return timeline


def _group_by_time(lines: list[dict[str, Any]]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = defaultdict(list)
    for line in lines:
        if line["text"]:
            grouped[line["start_ms"]].append(line["text"])
    return grouped


def _joined(values: list[str] | None) -> str | None:
    return " / ".join(values) if values else None

