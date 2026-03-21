#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover - exercised through CLI runtime
    YoutubeDL = None
    DownloadError = Exception


TEXT_EXTENSIONS = {".vtt", ".srt", ".ass", ".ssa", ".json", ".json3"}
HTML_TAG_RE = re.compile(r"<[^>]+>")
TIMECODE_RE = re.compile(
    r"^\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s+-->\s+\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a Bilibili video URL into metadata, subtitles, transcript text, and optional audio."
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


def find_generated_files(folder: Path) -> tuple[list[Path], list[Path]]:
    subtitle_files: list[Path] = []
    audio_files: list[Path] = []
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
    subtitle_files.sort()
    audio_files.sort()
    return subtitle_files, audio_files


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


def build_transcript(subtitle_files: list[Path]) -> str:
    sections: list[str] = []
    for subtitle_file in subtitle_files:
        transcript = read_subtitle_text(subtitle_file)
        if not transcript:
            continue
        heading = subtitle_file.name
        sections.append(f"[{heading}]\n{transcript}")
    return "\n\n".join(sections).strip()


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


def process_item(
    *,
    url: str,
    entry: dict,
    run_dir: Path,
    index: int,
    total: int,
    args: argparse.Namespace,
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
    if args.download_audio:
        fetch_options["format"] = "bestaudio/best"
    else:
        fetch_options["skip_download"] = True

    info = extract_info(url, download=True, options=fetch_options)
    metadata_path = item_dir / "metadata.json"
    write_json(metadata_path, info)

    subtitle_files, audio_files = find_generated_files(item_dir)
    transcript_text = build_transcript(subtitle_files)
    transcript_path = item_dir / "transcript.txt"
    if transcript_text:
        transcript_path.write_text(transcript_text + "\n", encoding="utf-8")
        transcript_value: str | None = str(transcript_path)
    else:
        transcript_value = None

    item_summary = {
        "index": index,
        "source_url": url,
        "item_id": info.get("id") or entry.get("id"),
        "title": info.get("title") or entry.get("title"),
        "output_dir": str(item_dir),
        "metadata_path": str(metadata_path),
        "subtitle_files": [str(path) for path in subtitle_files],
        "transcript_path": transcript_value,
        "audio_files": [str(path) for path in audio_files],
    }
    write_json(item_dir / "summary.json", item_summary)
    return item_summary


def main() -> int:
    args = parse_args()
    require_yt_dlp()
    ensure_bilibili_url(args.url)

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
