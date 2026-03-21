---
name: bilibili-video-reader
description: Extract readable text, subtitles, audio, and metadata from Bilibili video URLs. Use when Codex needs to inspect, summarize, translate, quote, review, or analyze a Bilibili video, including cases where subtitles should be downloaded first and audio should be extracted for later transcription when subtitles are missing.
---

# Bilibili Video Reader

Read a Bilibili video by turning the URL into local artifacts that another model step can inspect: metadata, subtitle files, a normalized transcript, and optional audio.

Prefer the bundled script for repeatable runs instead of re-implementing the ingest logic in each task.

## Workflow
1. Confirm the input is a public Bilibili or `b23.tv` video URL.
2. Default to subtitle-first ingestion:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/"
```

3. Read the generated `transcript.txt`, `metadata.json`, and `summary.json`.
4. If subtitles are missing or incomplete, rerun with audio extraction enabled:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --download-audio
```

5. If the task still needs speech recognition, pass the downloaded audio file into a transcription workflow.

## Decision Rules
- Prefer subtitles over audio when they exist. It is faster, cheaper, and easier to verify.
- Use `--auto-subtitles` when the user needs every possible caption track, including machine-generated tracks.
- Use `--download-audio` when the user wants a deeper summary, quote extraction, translation, or QA but no subtitle track is available.
- Keep output in a dedicated folder, defaulting to `output/bilibili/`, so repeated runs do not overwrite each other.
- Inspect `summary.json` before reading files manually; it points to the best transcript and any downloaded audio.

## Dependencies
Install `yt-dlp` if it is missing:

```bash
python -m pip install yt-dlp
```

The bundled script is intentionally conservative and does not require `ffmpeg` for the default subtitle-first workflow.

## Common Commands
Fetch metadata and subtitle text only:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>"
```

Fetch metadata, subtitles, and audio:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --download-audio
```

Request only selected language tags:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --language zh-CN \
  --language en
```

Increase subtitle coverage with automatic captions:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --auto-subtitles
```

Choose a custom output location:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --out-dir "artifacts/bilibili"
```

## Output Contract
Each run creates a timestamped folder containing one subfolder per resolved video item.

Look for these files first:
- `summary.json`: run-level index of generated files
- `metadata.json`: sanitized metadata for each item
- `transcript.txt`: normalized, plain-text transcript built from subtitle files
- `*.vtt`, `*.srt`, `*.ass`, `*.json3`: raw subtitle artifacts when available
- audio file such as `*.m4a` or `*.mp3`: only when `--download-audio` is used and the site exposes an audio stream

## Reference Map
- `references/output-map.md`: output layout, failure modes, and handoff guidance for follow-up summarization or transcription
