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
from urllib.parse import urlparse

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
        help="Send downloaded audio to OpenAI transcription when subtitles are missing or insufficient",
    )
    parser.add_argument(
        "--transcribe-model",
        default=DEFAULT_TRANSCRIBE_MODEL,
        help=f"OpenAI transcription model (default: {DEFAULT_TRANSCRIBE_MODEL})",
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


def find_generated_files(folder: Path) -> tuple[list[Path], list[Path], list[Path]]:
    subtitle_files: list[Path] = []
    audio_files: list[Path] = []
    video_files: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        lower_suffix = path.suffix.lower()
        lower_name = path.name.lower()
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
    return subtitle_files, audio_files, video_files


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


def compute_frame_timestamps(duration: float | None, frame_count: int) -> list[float]:
    if frame_count < 1:
        return []
    if duration is None or duration <= 0:
        return [float(index * 30) for index in range(frame_count)]
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
) -> list[dict[str, object]]:
    ffmpeg = get_ffmpeg_binary("ffmpeg")
    duration = read_media_duration(video_path)
    timestamps = compute_frame_timestamps(duration, frame_count)
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


def transcribe_audio_file(
    client: Any,
    audio_path: Path,
    *,
    model: str,
) -> dict[str, object]:
    with audio_path.open("rb") as audio_file:
        result = client.audio.transcriptions.create(
            file=audio_file,
            model=model,
            response_format="text",
        )
    text = result if isinstance(result, str) else getattr(result, "text", str(result))
    return {
        "model": model,
        "text": text.strip(),
    }


def analyze_video_item(
    client: Any,
    *,
    item_title: str,
    source_url: str,
    transcript_text: str,
    frame_entries: list[dict[str, object]],
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
                "Instructions:\n"
                f"{focus}\n\n"
                "Return markdown with these sections:\n"
                "1. Summary\n"
                "2. Key Points\n"
                "3. Visual Observations\n"
                "4. Uncertainty / Missing Context\n\n"
                "Transcript:\n"
                f"{transcript_snippet(transcript_text)}"
            ),
        }
    ]
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

    subtitle_files, audio_files, video_files = find_generated_files(item_dir)
    transcript_text, transcript_source_files = build_transcript(subtitle_files)
    transcript_path = item_dir / "transcript.txt"
    if transcript_text:
        transcript_path.write_text(transcript_text + "\n", encoding="utf-8")
        transcript_value: str | None = str(transcript_path)
    else:
        transcript_value = None

    frame_entries: list[dict[str, object]] = []
    if args.extract_frames and video_files:
        frames_dir = item_dir / "frames"
        frame_entries = extract_frames_from_video(
            video_files[0],
            frames_dir,
            max(1, args.frame_count),
            max(160, args.frame_width),
        )
        write_json(item_dir / "frames.json", frame_entries)

    transcription_summary: dict[str, object] | None = None
    if args.transcribe_audio and audio_files and not transcript_text:
        transcription_summary = transcribe_audio_file(
            openai_client,
            audio_files[0],
            model=args.transcribe_model,
        )
        transcript_text = str(transcription_summary["text"])
        transcript_path.write_text(transcript_text + "\n", encoding="utf-8")
        transcript_value = str(transcript_path)
        write_json(item_dir / "audio_transcript.json", transcription_summary)

    analysis_summary: dict[str, object] | None = None
    if args.analyze:
        analysis_summary = analyze_video_item(
            openai_client,
            item_title=str(info.get("title") or entry.get("title") or "Bilibili video"),
            source_url=url,
            transcript_text=transcript_text or "No transcript was available. Focus on the visual evidence in the sampled frames.",
            frame_entries=frame_entries,
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
        "frame_files": [str(frame["path"]) for frame in frame_entries],
        "frame_manifest_path": str(item_dir / "frames.json") if frame_entries else None,
        "audio_transcript_path": str(item_dir / "audio_transcript.json") if transcription_summary else None,
        "analysis_path": str(item_dir / "analysis.md") if analysis_summary else None,
        "analysis_json_path": str(item_dir / "analysis.json") if analysis_summary else None,
    }
    write_json(item_dir / "summary.json", item_summary)
    return item_summary


def main() -> int:
    args = parse_args()
    require_yt_dlp()
    ensure_bilibili_url(args.url)
    if args.balanced_read:
        args.extract_frames = True
        args.download_video = True
        args.no_subtitles = False
    if args.extract_frames:
        args.download_video = True
    if args.transcribe_audio:
        args.download_audio = True
    if args.analyze and not args.extract_frames:
        args.extract_frames = True
        args.download_video = True
    openai_client: Any | None = None
    if args.transcribe_audio or args.analyze:
        ensure_api_key("OpenAI-powered features")
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
