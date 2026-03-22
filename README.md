# Bilibili Video Reader

Turn a public Bilibili video URL into readable local artifacts: metadata, subtitle files, a normalized transcript, optional audio, sampled frames, danmaku, and optional OpenAI-powered analysis.

This repository contains a Codex skill plus a bundled Python CLI so Bilibili videos can be inspected, summarized, translated, quoted, or reviewed with much better context than subtitles alone.

## What It Does

- Resolves public `bilibili.com` and `b23.tv` video URLs
- Downloads subtitle tracks and normalizes them into `transcript.txt`
- Prefers `.vtt` subtitle tracks when the same captions exist in multiple formats
- Extracts audio when deeper speech understanding is needed
- Samples representative video frames for slide, UI, chart, or demo-heavy videos
- Parses danmaku so audience reactions can be used as a supporting signal
- Supports timestamp-focused reading via `?t=` or `--focus-seconds`
- Optionally transcribes missing speech with `faster-whisper` or OpenAI
- Optionally generates a multimodal Markdown analysis from transcript text and frames

## Good Fit For

- Summarizing a Bilibili video into plain text
- Translating subtitle content into another language
- Reviewing code demos, UI walkthroughs, or slide decks shown on screen
- Investigating a specific moment in a video link such as `?t=284`
- Extracting audience reaction context from danmaku
- Creating follow-up analysis inputs for another LLM step

## Repository Layout

- `SKILL.md`: Codex skill instructions and workflow guidance
- `scripts/read_bilibili_video.py`: the main CLI for fetching and preparing artifacts
- `references/output-map.md`: output structure, read order, and failure modes
- `agents/openai.yaml`: agent metadata for skill packaging

## Quick Start

Install the base dependency:

```bash
python -m pip install yt-dlp
```

Optional dependencies:

```bash
python -m pip install openai
python -m pip install faster-whisper
```

If you want OpenAI-powered transcription or analysis, set `OPENAI_API_KEY` in your environment first.

## Core Commands

Read subtitles and metadata only:

```bash
python scripts/read_bilibili_video.py \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/"
```

Run the recommended balanced read mode:

```bash
python scripts/read_bilibili_video.py \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --balanced-read
```

Focus on a specific moment:

```bash
python scripts/read_bilibili_video.py \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/?t=284" \
  --balanced-read
```

Transcribe audio when captions are missing:

```bash
python scripts/read_bilibili_video.py \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --transcribe-audio
```

Generate a multimodal analysis:

```bash
python scripts/read_bilibili_video.py \
  "https://www.bilibili.com/video/BVxxxxxxxxxxx/" \
  --balanced-read \
  --analyze
```

## Recommended Mode

`--balanced-read` is the best default for "help me understand this video" tasks.

It keeps subtitles enabled, downloads video when needed, extracts representative frames, parses danmaku, tries transcription when captions are missing, and cleans up downloaded video unless `--keep-video` is used.

## Output Overview

Each run creates a timestamped folder under `output/bilibili/`, then stores one folder per resolved video item.

Look for these files first:

- `summary.json`: run-level and item-level index of generated files
- `metadata.json`: sanitized metadata for the resolved video item
- `transcript.txt`: normalized transcript built from subtitle tracks
- `focus_transcript.txt`: focused snippet around the requested timestamp
- `danmaku_summary.json`: aggregated danmaku signals
- `danmaku_focus.txt`: time-focused audience comments
- `frames.json`: manifest of extracted frames and timestamps
- `analysis.md`: Markdown analysis when `--analyze` is used

## Requirements And Notes

- Public Bilibili URLs work best
- `ffmpeg` is required for frame extraction and some audio workflows
- `OPENAI_API_KEY` is required for OpenAI transcription and multimodal analysis
- Danmaku should be treated as supplemental context, not ground truth
- Multi-part videos are split into separate item folders so downstream tools can inspect each part independently

## Example Use Cases

- "Summarize this Bilibili tutorial"
- "Translate this talk into English"
- "Tell me what happens around 4:44"
- "Inspect the slides shown on screen"
- "Use the transcript and frames to write a review"

## Why This Repo Exists

Most video tools stop at download or subtitles. This project is designed for reading and analysis workflows: it turns one Bilibili URL into structured artifacts that both humans and LLMs can inspect reliably.
