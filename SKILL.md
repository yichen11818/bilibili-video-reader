---
name: bilibili-video-reader
description: Extract readable text, subtitles, audio, video frames, danmaku, and metadata from Bilibili video URLs, then optionally use OpenAI to analyze the combined evidence. Use when Codex needs to inspect, summarize, translate, quote, review, or analyze a Bilibili video, including cases where subtitles should be downloaded first, audio should be transcribed when subtitles are missing, sampled frames should be used to understand on-screen visuals, or the URL includes a timestamp such as ?t=284 that should be treated as the focus point.
---

# Bilibili Video Reader

Read a Bilibili video by turning the URL into local artifacts that another model step can inspect: metadata, subtitle files, a normalized transcript, optional audio, sampled video frames, focused danmaku, and an optional OpenAI-generated analysis.

Prefer the bundled script for repeatable runs instead of re-implementing the ingest logic in each task.

## Workflow
1. Confirm the input is a public Bilibili or `b23.tv` video URL.
2. For the best understanding quality, prefer balanced reading: keep subtitles, sample frames, and when subtitles are missing automatically produce a local transcript from the audio. By default, balanced read also cleans up the downloaded video after those artifacts are created.

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --balanced-read
```

3. Use subtitle-first ingestion when you only need spoken content:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/"
```

4. Read the generated `transcript.txt`, `focus_transcript.txt`, `frames.json`, `danmaku_focus.txt`, `metadata.json`, and `summary.json`.
5. If subtitles are missing or incomplete, rerun with audio extraction enabled:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --download-audio
```

6. If the task still needs speech recognition, pass the downloaded audio file into a transcription workflow.
7. If the task needs a written combined interpretation, rerun with analysis:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --balanced-read \
  --analyze
```

## Decision Rules
- Prefer balanced reading for comprehension tasks: subtitles explain the spoken content, frames preserve visual context, and local transcription fills gaps when captions are missing.
- Prefer subtitles over audio when they exist. It is faster, cheaper, and easier to verify.
- Prefer `.vtt` subtitle tracks when multiple subtitle formats exist for the same track. The script now treats VTT as the primary subtitle source for transcript building.
- Prefer timestamp-aware reading when the URL includes `?t=` or `--focus-seconds` is provided. Frames, danmaku, and focused transcript snippets should center on that moment.
- Treat danmaku as a supplemental signal, not as ground truth. It is useful for reactions, hints about visual events, and audience interpretation.
- Use `--auto-subtitles` when the user needs every possible caption track, including machine-generated tracks.
- Use `--download-audio` when the user wants a deeper summary, quote extraction, translation, or QA but no subtitle track is available.
- Use `--transcribe-audio` when subtitles are missing but spoken content still matters. In `auto` backend mode the script prefers local `faster-whisper` and falls back to OpenAI only when needed and configured.
- Use `--extract-frames` when the user cares about charts, slides, demonstrations, UI flows, or visual changes in the video.
- Use `--balanced-read` as the default "understand this video" command. It keeps subtitle download on, extracts representative frames, parses danmaku, attempts local transcription when subtitles are absent, and cleans the downloaded video by default.
- Use `--keep-video` when the user explicitly wants to retain the downloaded video file on disk after a balanced read.
- Use `--cleanup-video` when the user wants to remove downloaded video files after processing in non-balanced workflows too.
- Use `--analyze` only when `OPENAI_API_KEY` is configured. It combines transcript text and sampled frames into a markdown report.
- Keep output in a dedicated folder, defaulting to `output/bilibili/`, so repeated runs do not overwrite each other.
- Inspect `summary.json` before reading files manually; it points to the best transcript and any downloaded audio.

## Dependencies
Install `yt-dlp` if it is missing:

```bash
python -m pip install yt-dlp
```

The bundled script is intentionally conservative and does not require `ffmpeg` for the default subtitle-first workflow.

Install `openai` if you want audio transcription or multimodal analysis:

```bash
python -m pip install openai
```

Set `OPENAI_API_KEY` locally before using `--transcribe-audio` or `--analyze`.

Install `faster-whisper` if you want offline local transcription:

```bash
python -m pip install faster-whisper
```

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

Run the recommended balanced read mode:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --balanced-read
```

Keep the downloaded video even in balanced mode:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --balanced-read \
  --keep-video
```

Focus on the timestamp embedded in the URL or pass one explicitly:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>?t=284" \
  --balanced-read
```

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --balanced-read \
  --focus-seconds 284
```

Fetch metadata, subtitles, downloaded video, and sampled frames:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --extract-frames
```

Transcribe spoken audio when captions are missing:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --transcribe-audio
```

Generate a multimodal markdown analysis:

```bash
python "$CODEX_HOME/skills/bilibili-video-reader/scripts/read_bilibili_video.py" \
  "<bilibili-url>" \
  --balanced-read \
  --analyze
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
- `focus_transcript.txt`: timestamp-aligned snippet around the requested focus point when local transcription ran
- `*.vtt`, `*.srt`, `*.ass`, `*.json3`: raw subtitle artifacts when available
- `preferred_subtitle_path` in `summary.json`: the subtitle file actually chosen for transcript generation, with `.vtt` preferred when duplicate tracks exist in multiple formats
- `danmaku_summary.json` and `danmaku_focus.txt`: audience-reaction context, especially useful when subtitles are missing
- audio file such as `*.m4a` or `*.mp3`: only when `--download-audio` is used and the site exposes an audio stream
- video file such as `*.mp4`: when `--download-video` or `--extract-frames` is used
- `cleaned_video_files` in `summary.json`: video files that were intentionally removed after processing
- `cleaned_audio_files` in `summary.json`: temporary extracted audio files that were removed after transcription
- `frames.json` plus `frames/*.jpg`: sampled frames and their timestamps
- `audio_transcript.json`: OpenAI transcription output when `--transcribe-audio` is used
- `analysis.md` and `analysis.json`: multimodal model output when `--analyze` is used

## Reference Map
- `references/output-map.md`: output layout, failure modes, and handoff guidance for follow-up summarization or transcription
