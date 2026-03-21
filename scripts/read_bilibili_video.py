#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover - exercised through CLI runtime
    YoutubeDL = None
    DownloadError = Exception

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised through CLI runtime
    OpenAI = None


TEXT_EXTENSIONS = {".vtt", ".srt", ".ass", ".ssa", ".json", ".json3"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".flv", ".avi", ".m4v"}
HTML_TAG_RE = re.compile(r"<[^>]+>")
TIMECODE_RE = re.compile(
    r"^\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s+-->\s+\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}"
)
DEFAULT_ANALYSIS_MODEL = "gpt-4.1-mini"
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_LOCAL_ASR_MODEL = "base"
DEFAULT_FOCUS_WINDOW_SECONDS = 45.0
SUBTITLE_EXTENSION_PRIORITY = {
    ".vtt": 0,
    ".srt": 1,
    ".ass": 2,
    ".ssa": 3,
    ".json3": 4,
    ".json": 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a Bilibili video URL into metadata, subtitles, transcript text, optional audio, key frames, and optional AI analysis."
    )
    parser.add_argument("url", help="Public Bilibili or b23.tv video URL")
    parser.add_argument(
        "--focus-seconds",
        type=float,
        help="Optional playback timestamp to focus on; defaults to parsing the URL parameter such as ?t=284",
    )
    parser.add_argument(
        "--focus-window-seconds",
        type=float,
        default=DEFAULT_FOCUS_WINDOW_SECONDS,
        help=f"Window size used for focused frames, danmaku, and local transcript snippets (default: {DEFAULT_FOCUS_WINDOW_SECONDS})",
    )
    parser.add_argument(
        "--out-dir",
        default="output/bilibili",
        help="Parent directory where run folders will be created",
    )
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        default=[],
        help="Subtitle language tag to request; repeat to request multiple tags",
    )
    parser.add_argument(
        "--auto-subtitles",
        action="store_true",
        help="Request automatic subtitles when the extractor exposes them",
    )
    parser.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip subtitle download and only fetch metadata or audio",
    )
    parser.add_argument(
        "--download-audio",
        action="store_true",
        help="Download the best available audio stream in addition to metadata",
    )
    parser.add_argument(
        "--download-video",
        action="store_true",
        help="Download the best available video so frames can be extracted",
    )
    parser.add_argument(
        "--cleanup-video",
        action="store_true",
        help="Delete downloaded video files after the needed artifacts have been generated",
    )
    parser.add_argument(
        "--keep-video",
        action="store_true",
        help="Keep downloaded video files even in balanced-read mode",
    )
    parser.add_argument(
        "--balanced-read",
        action="store_true",
        help="Preferred understanding mode: keep subtitles enabled and also extract representative video frames",
    )
    parser.add_argument(
        "--extract-frames",
        action="store_true",
        help="Extract representative frames from the downloaded video with ffmpeg",
    )
    parser.add_argument(
        "--frame-count",
        type=int,
        default=6,
        help="Maximum number of frames to extract per video item (default: 6)",
    )
    parser.add_argument(
        "--frame-width",
        type=int,
        default=960,
        help="Scale extracted frames to this width while preserving aspect ratio (default: 960)",
    )
    parser.add_argument(
        "--transcribe-audio",
        action="store_true",
        help="Transcribe downloaded audio when subtitles are missing or insufficient",
    )
    parser.add_argument(
        "--transcription-backend",
        choices=("auto", "local", "openai"),
        default="auto",
        help="Audio transcription backend: auto, local (faster-whisper), or openai (default: auto)",
    )
    parser.add_argument(
        "--transcribe-model",
        default=DEFAULT_TRANSCRIBE_MODEL,
        help=f"OpenAI transcription model (default: {DEFAULT_TRANSCRIBE_MODEL})",
    )
    parser.add_argument(
        "--local-asr-model",
        default=DEFAULT_LOCAL_ASR_MODEL,
        help=f"Local faster-whisper model size for offline transcription (default: {DEFAULT_LOCAL_ASR_MODEL})",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Use OpenAI to analyze transcript and extracted frames into a markdown report",
    )
    parser.add_argument(
        "--analysis-model",
        default=DEFAULT_ANALYSIS_MODEL,
        help=f"OpenAI multimodal analysis model (default: {DEFAULT_ANALYSIS_MODEL})",
    )
    parser.add_argument(
        "--analysis-focus",
        default="Summarize the video, explain the core points, call out on-screen visuals, and list any uncertainty clearly.",
        help="Extra analysis instructions passed to the model",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=6,
        help="Maximum number of extracted frames to send to the model (default: 6)",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print the final run summary JSON to stdout",
    )
    return parser.parse_args()


def require_yt_dlp() -> None:
    if YoutubeDL is None:
        raise SystemExit(
            "Missing dependency: yt-dlp\n"
            "Install it with: python -m pip install yt-dlp"
        )


def ensure_api_key(feature_name: str) -> None:
    if os.getenv("OPENAI_API_KEY"):
        return
    raise SystemExit(
        f"{feature_name} requires OPENAI_API_KEY to be set in the environment."
    )


def create_openai_client() -> Any:
    if OpenAI is None:
        raise SystemExit(
            "Missing dependency: openai\n"
            "Install it with: python -m pip install openai"
        )
    return OpenAI()


def ensure_bilibili_url(url: str) -> None:
    host = urlparse(url).netloc.lower()
    allowed = ("bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv")
    if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
        raise SystemExit(f"Expected a Bilibili or b23.tv URL, received host: {host}")


def parse_time_like(value: str) -> float | None:
    raw = value.strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    match = re.fullmatch(
        r"(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)(?:s)?)?",
        raw,
    )
    if not match:
        return None
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = int(match.group("s") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return float(total) if total > 0 else None


def focus_seconds_from_url(url: str) -> float | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("t", "start", "time"):
        values = query.get(key)
        if not values:
            continue
        parsed_value = parse_time_like(values[0])
        if parsed_value is not None:
            return parsed_value
    if parsed.fragment:
        return parse_time_like(parsed.fragment)
    return None


def slugify(text: str, *, limit: int = 72) -> str:
    normalized = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    normalized = re.sub(r"[-\s]+", "-", normalized)
    if not normalized:
        normalized = "video"
    return normalized[:limit].strip("-") or "video"


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def base_ydl_options() -> dict[str, object]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        "ignoreerrors": False,
    }


def extract_info(url: str, *, download: bool, options: dict[str, object] | None = None) -> dict:
    assert YoutubeDL is not None
    ydl_options = base_ydl_options()
    if options:
        ydl_options.update(options)
    with YoutubeDL(ydl_options) as ydl:
        info = ydl.extract_info(url, download=download)
        return ydl.sanitize_info(info)


def resolve_entries(root_info: dict) -> list[dict]:
    entries = root_info.get("entries")
    if isinstance(entries, list) and entries:
        return [entry for entry in entries if isinstance(entry, dict)]
    return [root_info]


def pick_item_url(entry: dict, fallback_url: str) -> str:
    candidates = (
        entry.get("webpage_url"),
        entry.get("original_url"),
        entry.get("url"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
    return fallback_url


def subtitle_languages(requested: list[str]) -> list[str]:
    if not requested:
        return ["all"]
    return requested


def find_generated_files(folder: Path) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    subtitle_files: list[Path] = []
    audio_files: list[Path] = []
    video_files: list[Path] = []
    danmaku_files: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        lower_suffix = path.suffix.lower()
        lower_name = path.name.lower()
        if lower_name.endswith(".danmaku.xml"):
            danmaku_files.append(path)
            continue
        if lower_suffix in TEXT_EXTENSIONS and lower_name not in {
            "metadata.json",
            "summary.json",
        } and not lower_name.endswith(".info.json"):
            subtitle_files.append(path)
        elif lower_suffix in {".m4a", ".mp3", ".aac", ".wav", ".flac", ".ogg", ".opus"}:
            audio_files.append(path)
        elif lower_suffix in VIDEO_EXTENSIONS:
            video_files.append(path)
    subtitle_files.sort()
    audio_files.sort()
    video_files.sort()
    danmaku_files.sort()
    return subtitle_files, audio_files, video_files, danmaku_files


def subtitle_sort_key(path: Path) -> tuple[int, str]:
    return (SUBTITLE_EXTENSION_PRIORITY.get(path.suffix.lower(), 99), path.name.lower())


def choose_preferred_subtitle_files(subtitle_files: list[Path]) -> list[Path]:
    """Prefer VTT when multiple formats exist for the same subtitle track."""
    grouped: dict[str, list[Path]] = {}
    for path in subtitle_files:
        key = str(path.with_suffix("")).lower()
        grouped.setdefault(key, []).append(path)

    selected: list[Path] = []
    for _, group in sorted(grouped.items(), key=lambda item: item[0]):
        group.sort(key=subtitle_sort_key)
        selected.append(group[0])
    return selected


def collapse_lines(lines: Iterable[str]) -> str:
    output: list[str] = []
    previous = ""
    for raw_line in lines:
        line = HTML_TAG_RE.sub("", raw_line).replace("&nbsp;", " ")
        line = line.replace("\\N", " ").replace("\\n", " ")
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "WEBVTT" or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if line.isdigit() or TIMECODE_RE.match(line):
            continue
        if line.startswith("NOTE"):
            continue
        if line == previous:
            continue
        output.append(line)
        previous = line
    return "\n".join(output).strip()


def parse_ass_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        if raw_line.startswith("Dialogue:"):
            parts = raw_line.split(",", 9)
            lines.append(parts[-1] if len(parts) == 10 else raw_line)
    return collapse_lines(lines)


def extract_json_fragments(payload: object) -> Iterable[str]:
    if isinstance(payload, dict):
        for key in ("content", "text", "utf8", "line"):
            value = payload.get(key)
            if isinstance(value, str):
                yield value
        for key in ("body", "events", "segments", "segs", "lines"):
            nested = payload.get(key)
            if isinstance(nested, list):
                for item in nested:
                    yield from extract_json_fragments(item)
    elif isinstance(payload, list):
        for item in payload:
            yield from extract_json_fragments(item)


def read_subtitle_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix in {".ass", ".ssa"}:
        return parse_ass_text(text)
    if suffix in {".json", ".json3"}:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return collapse_lines(text.splitlines())
        return collapse_lines(extract_json_fragments(payload))
    return collapse_lines(text.splitlines())


def parse_danmaku_file(path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    try:
        root = ElementTree.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except ElementTree.ParseError:
        return entries
    for node in root.findall("d"):
        meta = node.attrib.get("p", "")
        parts = meta.split(",")
        text = (node.text or "").strip()
        if not text:
            continue
        try:
            timestamp = float(parts[0]) if parts else 0.0
        except ValueError:
            timestamp = 0.0
        entries.append(
            {
                "timestamp_seconds": round(timestamp, 3),
                "timestamp_label": format_timestamp(timestamp),
                "text": text,
            }
        )
    return entries


def summarize_danmaku(
    entries: list[dict[str, object]],
    *,
    focus_seconds: float | None,
    focus_window_seconds: float,
) -> dict[str, object]:
    def is_in_focus(entry: dict[str, object]) -> bool:
        if focus_seconds is None:
            return True
        return abs(float(entry["timestamp_seconds"]) - focus_seconds) <= focus_window_seconds / 2

    focused_entries = [entry for entry in entries if is_in_focus(entry)]
    source_entries = focused_entries if focused_entries else entries

    counts: dict[str, int] = {}
    for entry in source_entries:
        text = str(entry["text"]).strip()
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1

    top_comments = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:15]
    return {
        "entry_count": len(entries),
        "focused_entry_count": len(focused_entries),
        "focus_seconds": focus_seconds,
        "top_comments": [{"text": text, "count": count} for text, count in top_comments],
        "focused_comments": focused_entries[:20],
    }


def build_transcript(subtitle_files: list[Path]) -> tuple[str, list[Path]]:
    selected_files = choose_preferred_subtitle_files(subtitle_files)
    sections: list[str] = []
    for subtitle_file in selected_files:
        transcript = read_subtitle_text(subtitle_file)
        if not transcript:
            continue
        heading = subtitle_file.name
        sections.append(f"[{heading}]\n{transcript}")
    return "\n\n".join(sections).strip(), selected_files


def build_run_directory(root: Path, root_info: dict) -> Path:
    root_id = root_info.get("id") or root_info.get("display_id") or "bilibili"
    title = root_info.get("title") or root_info.get("playlist_title") or "video"
    run_dir = root / f"{utc_timestamp()}-{slugify(str(root_id))}-{slugify(str(title))}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def item_folder_name(entry: dict, index: int, total: int) -> str:
    item_id = entry.get("id") or entry.get("display_id") or f"item-{index:02d}"
    title = entry.get("title") or f"item-{index:02d}"
    prefix = f"{index:02d}-" if total > 1 else ""
    return f"{prefix}{slugify(str(item_id), limit=24)}-{slugify(str(title), limit=48)}"


def get_ffmpeg_binary(name: str) -> str:
    from shutil import which

    binary = which(name)
    if not binary:
        raise SystemExit(
            f"Missing dependency: {name}\n"
            "Install ffmpeg and ensure ffmpeg/ffprobe are available on PATH."
        )
    return binary


def read_media_duration(video_path: Path) -> float | None:
    ffprobe = get_ffmpeg_binary("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def compute_frame_timestamps(
    duration: float | None,
    frame_count: int,
    *,
    focus_seconds: float | None = None,
    focus_window_seconds: float = DEFAULT_FOCUS_WINDOW_SECONDS,
) -> list[float]:
    if frame_count < 1:
        return []
    if duration is None or duration <= 0:
        return [float(index * 30) for index in range(frame_count)]
    if focus_seconds is not None:
        half_window = max(5.0, focus_window_seconds / 2)
        start = max(0.0, focus_seconds - half_window)
        end = min(duration, focus_seconds + half_window)
        if end > start:
            step = (end - start) / (frame_count + 1)
            timestamps = [round(start + step * index, 3) for index in range(1, frame_count + 1)]
            return [value for value in timestamps if 0 <= value < duration]
    step = duration / (frame_count + 1)
    timestamps = [round(step * index, 3) for index in range(1, frame_count + 1)]
    return [value for value in timestamps if value < duration]


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def extract_frames_from_video(
    video_path: Path,
    frames_dir: Path,
    frame_count: int,
    frame_width: int,
    *,
    focus_seconds: float | None = None,
    focus_window_seconds: float = DEFAULT_FOCUS_WINDOW_SECONDS,
) -> list[dict[str, object]]:
    ffmpeg = get_ffmpeg_binary("ffmpeg")
    duration = read_media_duration(video_path)
    timestamps = compute_frame_timestamps(
        duration,
        frame_count,
        focus_seconds=focus_seconds,
        focus_window_seconds=focus_window_seconds,
    )
    frames_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[dict[str, object]] = []
    for index, timestamp in enumerate(timestamps, start=1):
        output_path = frames_dir / f"frame-{index:02d}-{int(timestamp * 1000):09d}ms.jpg"
        command = [
            ffmpeg,
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={frame_width}:-2",
            "-q:v",
            "4",
            str(output_path),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode == 0 and output_path.exists():
            extracted.append(
                {
                    "path": str(output_path),
                    "timestamp_seconds": round(timestamp, 3),
                    "timestamp_label": format_timestamp(timestamp),
                }
            )
    return extracted


def guess_mime_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def encode_data_url(path: Path) -> str:
    mime_type = guess_mime_type(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def transcript_snippet(text: str, *, limit: int = 18000) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "\n\n[Transcript truncated for analysis]"


def create_local_asr_model(model_name: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "Missing dependency: faster-whisper\n"
            "Install it with: python -m pip install faster-whisper"
        )
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def transcribe_audio_file_local(
    audio_path: Path,
    *,
    model_name: str,
) -> dict[str, object]:
    model = create_local_asr_model(model_name)
    segments, info = model.transcribe(
        str(audio_path),
        language="zh",
        vad_filter=True,
        beam_size=5,
    )
    segment_rows: list[dict[str, object]] = []
    texts: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        segment_rows.append(
            {
                "start": round(float(segment.start), 2),
                "end": round(float(segment.end), 2),
                "text": text,
            }
        )
        texts.append(text)
    return {
        "backend": "local",
        "model": model_name,
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "text": "\n".join(texts).strip(),
        "segments": segment_rows,
    }


def transcribe_audio_file(
    audio_path: Path,
    *,
    backend: str,
    openai_client: Any | None,
    openai_model: str,
    local_model: str,
) -> dict[str, object]:
    selected_backend = backend
    if backend == "auto":
        try:
            return transcribe_audio_file_local(audio_path, model_name=local_model)
        except SystemExit:
            if openai_client is None:
                raise
            selected_backend = "openai"

    if selected_backend == "local":
        return transcribe_audio_file_local(audio_path, model_name=local_model)

    if openai_client is None:
        raise SystemExit(
            "OpenAI transcription requested but OPENAI_API_KEY is not set or the client was not initialized."
        )

    with audio_path.open("rb") as audio_file:
        result = openai_client.audio.transcriptions.create(
            file=audio_file,
            model=openai_model,
            response_format="text",
        )
    text = result if isinstance(result, str) else getattr(result, "text", str(result))
    return {
        "backend": "openai",
        "model": openai_model,
        "text": text.strip(),
        "segments": [],
    }


def maybe_extract_audio_from_video(video_path: Path, output_path: Path) -> Path | None:
    ffmpeg = get_ffmpeg_binary("ffmpeg")
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode == 0 and output_path.exists():
        return output_path
    return None


def cleanup_paths(paths: list[Path]) -> list[Path]:
    removed: list[Path] = []
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                removed.append(path)
        except OSError:
            continue
    return removed


def focused_transcript_lines(
    segments: list[dict[str, object]],
    *,
    focus_seconds: float | None,
    focus_window_seconds: float,
) -> list[str]:
    if focus_seconds is None:
        target_segments = segments[:20]
    else:
        half_window = focus_window_seconds / 2
        target_segments = [
            segment
            for segment in segments
            if abs(float(segment["start"]) - focus_seconds) <= half_window
            or abs(float(segment["end"]) - focus_seconds) <= half_window
            or (float(segment["start"]) <= focus_seconds <= float(segment["end"]))
        ]
    lines: list[str] = []
    for segment in target_segments:
        lines.append(
            f"[{format_timestamp(float(segment['start']))}-{format_timestamp(float(segment['end']))}] {segment['text']}"
        )
    return lines


def analyze_video_item(
    client: Any,
    *,
    item_title: str,
    source_url: str,
    transcript_text: str,
    frame_entries: list[dict[str, object]],
    danmaku_summary: dict[str, object] | None,
    focus_seconds: float | None,
    model: str,
    focus: str,
    max_images: int,
) -> dict[str, object]:
    selected_frames = frame_entries[: max(0, max_images)]
    content: list[dict[str, object]] = [
        {
            "type": "input_text",
            "text": (
                "You are analyzing a Bilibili video using transcript text and sampled frames.\n"
                f"Title: {item_title}\n"
                f"Source URL: {source_url}\n\n"
                f"Requested focus timestamp: {format_timestamp(focus_seconds) if focus_seconds is not None else 'none'}\n\n"
                "Instructions:\n"
                f"{focus}\n\n"
                "Return markdown with these sections:\n"
                "1. Summary\n"
                "2. Key Points\n"
                "3. Visual Observations\n"
                "4. Danmaku Signals\n"
                "5. Uncertainty / Missing Context\n\n"
                "Transcript:\n"
                f"{transcript_snippet(transcript_text)}"
            ),
        }
    ]
    if danmaku_summary:
        content.append(
            {
                "type": "input_text",
                "text": "Danmaku summary:\n" + json.dumps(danmaku_summary, ensure_ascii=False, indent=2),
            }
        )
    for frame in selected_frames:
        label = frame.get("timestamp_label") or "unknown"
        content.append(
            {
                "type": "input_text",
                "text": f"Frame at {label}",
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": encode_data_url(Path(str(frame["path"]))),
                "detail": "low",
            }
        )
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
        text={"format": {"type": "text"}},
    )
    markdown = getattr(response, "output_text", "").strip()
    raw_response = response.model_dump() if hasattr(response, "model_dump") else {}
    return {
        "model": model,
        "markdown": markdown,
        "raw_response": raw_response,
        "frame_count": len(selected_frames),
    }


def process_item(
    *,
    url: str,
    entry: dict,
    run_dir: Path,
    index: int,
    total: int,
    args: argparse.Namespace,
    openai_client: Any | None,
) -> dict[str, object]:
    item_dir = run_dir / item_folder_name(entry, index, total)
    item_dir.mkdir(parents=True, exist_ok=True)
    cleaned_video_files: list[Path] = []
    cleaned_audio_files: list[Path] = []

    request_subtitles = not args.no_subtitles
    fetch_options: dict[str, object] = {
        "noplaylist": True,
        "outtmpl": str(item_dir / "%(title).120B [%(id)s].%(ext)s"),
    }
    if request_subtitles:
        fetch_options["writesubtitles"] = True
        fetch_options["subtitleslangs"] = subtitle_languages(args.languages)
        fetch_options["subtitlesformat"] = "vtt/srt/ass/json3/best"
        if args.auto_subtitles:
            fetch_options["writeautomaticsub"] = True
    if args.download_video:
        fetch_options["format"] = "bv*+ba/b"
        fetch_options["merge_output_format"] = "mp4"
    elif args.download_audio:
        fetch_options["format"] = "bestaudio/best"
    else:
        fetch_options["skip_download"] = True

    info = extract_info(url, download=True, options=fetch_options)
    metadata_path = item_dir / "metadata.json"
    write_json(metadata_path, info)

    subtitle_files, audio_files, video_files, danmaku_files = find_generated_files(item_dir)
    transcript_text, transcript_source_files = build_transcript(subtitle_files)
    transcript_path = item_dir / "transcript.txt"
    if transcript_text:
        transcript_path.write_text(transcript_text + "\n", encoding="utf-8")
        transcript_value: str | None = str(transcript_path)
    else:
        transcript_value = None

    danmaku_summary: dict[str, object] | None = None
    danmaku_text_path: str | None = None
    if danmaku_files:
        danmaku_entries = parse_danmaku_file(danmaku_files[0])
        danmaku_summary = summarize_danmaku(
            danmaku_entries,
            focus_seconds=args.focus_seconds,
            focus_window_seconds=args.focus_window_seconds,
        )
        write_json(item_dir / "danmaku_summary.json", danmaku_summary)
        focused_comments = [
            f"[{entry['timestamp_label']}] {entry['text']}"
            for entry in danmaku_summary.get("focused_comments", [])
        ]
        if focused_comments:
            danmaku_text_file = item_dir / "danmaku_focus.txt"
            danmaku_text_file.write_text("\n".join(focused_comments) + "\n", encoding="utf-8")
            danmaku_text_path = str(danmaku_text_file)

    frame_entries: list[dict[str, object]] = []
    if args.extract_frames and video_files:
        frames_dir = item_dir / "frames"
        frame_entries = extract_frames_from_video(
            video_files[0],
            frames_dir,
            max(1, args.frame_count),
            max(160, args.frame_width),
            focus_seconds=args.focus_seconds,
            focus_window_seconds=args.focus_window_seconds,
        )
        write_json(item_dir / "frames.json", frame_entries)

    transcription_summary: dict[str, object] | None = None
    if args.transcribe_audio and not transcript_text:
        audio_source: Path | None = audio_files[0] if audio_files else None
        if audio_source is None and video_files:
            extracted_audio = maybe_extract_audio_from_video(video_files[0], item_dir / "extracted_audio.wav")
            if extracted_audio is not None:
                audio_source = extracted_audio
                audio_files = [extracted_audio] + audio_files
        if audio_source is None:
            raise SystemExit("No audio source was available for transcription.")
        transcription_summary = transcribe_audio_file(
            audio_source,
            backend=args.transcription_backend,
            openai_client=openai_client,
            openai_model=args.transcribe_model,
            local_model=args.local_asr_model,
        )
        transcript_text = str(transcription_summary["text"])
        transcript_path.write_text(transcript_text + "\n", encoding="utf-8")
        transcript_value = str(transcript_path)
        write_json(item_dir / "audio_transcript.json", transcription_summary)
        focused_lines = focused_transcript_lines(
            transcription_summary.get("segments") or [],
            focus_seconds=args.focus_seconds,
            focus_window_seconds=args.focus_window_seconds,
        )
        if focused_lines:
            focus_transcript_file = item_dir / "focus_transcript.txt"
            focus_transcript_file.write_text("\n".join(focused_lines) + "\n", encoding="utf-8")

    if args.cleanup_video and video_files:
        cleaned_video_files = cleanup_paths(video_files)
        video_files = [path for path in video_files if path.exists()]

    temp_audio_files = [path for path in audio_files if path.name == "extracted_audio.wav"]
    if temp_audio_files and transcript_value:
        cleaned_audio_files = cleanup_paths(temp_audio_files)
        audio_files = [path for path in audio_files if path.exists()]

    analysis_summary: dict[str, object] | None = None
    if args.analyze:
        analysis_summary = analyze_video_item(
            openai_client,
            item_title=str(info.get("title") or entry.get("title") or "Bilibili video"),
            source_url=url,
            transcript_text=transcript_text or "No transcript was available. Focus on the visual evidence in the sampled frames.",
            frame_entries=frame_entries,
            danmaku_summary=danmaku_summary,
            focus_seconds=args.focus_seconds,
            model=args.analysis_model,
            focus=args.analysis_focus,
            max_images=args.max_images,
        )
        analysis_markdown_path = item_dir / "analysis.md"
        analysis_markdown_path.write_text(
            str(analysis_summary["markdown"]).rstrip() + "\n",
            encoding="utf-8",
        )
        write_json(item_dir / "analysis.json", analysis_summary)

    item_summary = {
        "index": index,
        "source_url": url,
        "focus_seconds": args.focus_seconds,
        "focus_timestamp": format_timestamp(args.focus_seconds) if args.focus_seconds is not None else None,
        "item_id": info.get("id") or entry.get("id"),
        "title": info.get("title") or entry.get("title"),
        "output_dir": str(item_dir),
        "metadata_path": str(metadata_path),
        "subtitle_files": [str(path) for path in subtitle_files],
        "preferred_subtitle_path": str(transcript_source_files[0]) if transcript_source_files else None,
        "preferred_subtitle_format": transcript_source_files[0].suffix.lower() if transcript_source_files else None,
        "transcript_source_files": [str(path) for path in transcript_source_files],
        "transcript_path": transcript_value,
        "audio_files": [str(path) for path in audio_files],
        "video_files": [str(path) for path in video_files],
        "cleaned_video_files": [str(path) for path in cleaned_video_files],
        "cleaned_audio_files": [str(path) for path in cleaned_audio_files],
        "danmaku_files": [str(path) for path in danmaku_files],
        "danmaku_summary_path": str(item_dir / "danmaku_summary.json") if danmaku_summary else None,
        "danmaku_focus_path": danmaku_text_path,
        "frame_files": [str(frame["path"]) for frame in frame_entries],
        "frame_manifest_path": str(item_dir / "frames.json") if frame_entries else None,
        "audio_transcript_path": str(item_dir / "audio_transcript.json") if transcription_summary else None,
        "focus_transcript_path": str(item_dir / "focus_transcript.txt") if transcription_summary and (item_dir / "focus_transcript.txt").exists() else None,
        "analysis_path": str(item_dir / "analysis.md") if analysis_summary else None,
        "analysis_json_path": str(item_dir / "analysis.json") if analysis_summary else None,
    }
    write_json(item_dir / "summary.json", item_summary)
    return item_summary


def main() -> int:
    args = parse_args()
    require_yt_dlp()
    ensure_bilibili_url(args.url)
    if args.focus_seconds is None:
        args.focus_seconds = focus_seconds_from_url(args.url)
    if args.balanced_read:
        args.extract_frames = True
        args.download_video = True
        args.no_subtitles = False
        args.transcribe_audio = True
        if not args.keep_video:
            args.cleanup_video = True
    if args.extract_frames:
        args.download_video = True
    if args.transcribe_audio:
        args.download_audio = True
    if args.analyze and not args.extract_frames:
        args.extract_frames = True
        args.download_video = True
    openai_client: Any | None = None
    if args.transcription_backend == "openai" or args.analyze:
        ensure_api_key("OpenAI-powered features")
        openai_client = create_openai_client()
    elif args.transcription_backend == "auto" and os.getenv("OPENAI_API_KEY"):
        openai_client = create_openai_client()

    try:
        root_info = extract_info(args.url, download=False, options={"skip_download": True})
    except DownloadError as exc:
        print(f"Failed to inspect URL: {exc}", file=sys.stderr)
        return 1

    run_root = Path(args.out_dir).expanduser().resolve()
    run_dir = build_run_directory(run_root, root_info)
    entries = resolve_entries(root_info)

    items: list[dict[str, object]] = []
    for index, entry in enumerate(entries, start=1):
        item_url = pick_item_url(entry, args.url)
        try:
            item_summary = process_item(
                url=item_url,
                entry=entry,
                run_dir=run_dir,
                index=index,
                total=len(entries),
                args=args,
                openai_client=openai_client,
            )
        except DownloadError as exc:
            item_summary = {
                "index": index,
                "source_url": item_url,
                "item_id": entry.get("id"),
                "title": entry.get("title"),
                "error": str(exc),
            }
        items.append(item_summary)

    run_summary = {
        "requested_url": args.url,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(run_dir),
        "item_count": len(items),
        "items": items,
    }
    summary_path = run_dir / "summary.json"
    write_json(summary_path, run_summary)

    print(f"Created run folder: {run_dir}")
    print(f"Summary: {summary_path}")
    if args.print_summary_json:
        print(json.dumps(run_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
