# Output Map

## Layout
The bundled CLI writes into `output/bilibili/` by default, then creates a per-run folder:

```text
output/bilibili/
  20260322T010203Z-BVxxxxxxxxxxx-sample-title/
    summary.json
    01-BVxxxxxxxxxxx-sample-title/
      metadata.json
      transcript.txt
      focus_transcript.txt
      *.vtt / *.srt / *.ass / *.json3
      *.danmaku.xml
      danmaku_summary.json
      danmaku_focus.txt
      *.m4a / *.mp3
      *.mp4
      frames.json
      frames/
        frame-01-000123000ms.jpg
      audio_transcript.json
      analysis.md
      analysis.json
```

The timestamp keeps runs isolated so repeated reads do not overwrite each other.

## Read Order
1. Open `summary.json` to locate the best transcript and audio path.
2. Check `focus_timestamp` in `summary.json`. If it is present, the run was centered around that playback moment.
3. Check `preferred_subtitle_path` in `summary.json` to see which subtitle track was chosen for transcript generation. VTT is preferred when the same track exists in multiple formats.
4. Read `focus_transcript.txt` first when you care about a specific `?t=` timestamp.
5. Read `transcript.txt` if it exists.
6. Read `danmaku_focus.txt` and `danmaku_summary.json` for audience reactions and hints about what is happening on screen.
7. If the run used `--balanced-read` or `--extract-frames`, inspect `frames.json` and the sampled images next.
   If `video_files` is empty but `cleaned_video_files` is present, the downloaded video was intentionally removed after frame extraction.
8. Fall back to raw subtitle files when transcript normalization dropped useful structure.
9. Fall back to audio only when subtitles are absent or obviously incomplete.
10. Read `analysis.md` when the run used `--analyze`.

## Failure Modes
- Missing `yt-dlp`: install it with `python -m pip install yt-dlp`.
- Missing subtitles: rerun with `--auto-subtitles`, then with `--download-audio` if needed.
- Missing local transcript: install `faster-whisper`, then rerun with `--balanced-read` or `--transcribe-audio`.
- Missing spoken transcript: rerun with `--transcribe-audio` after setting `OPENAI_API_KEY`.
- Missing visual understanding: rerun with `--extract-frames`.
- Best overall understanding: rerun with `--balanced-read`.
- Timestamp-specific questions: pass a `?t=` URL or `--focus-seconds` so frames, danmaku, and local transcript snippets target the right moment.
- Need to retain the raw downloaded video: add `--keep-video` or avoid `--cleanup-video`.
- Missing multimodal analysis: set `OPENAI_API_KEY`, then rerun with `--analyze`.
- Restricted videos: the helper is meant for public URLs. Region-locked or login-only videos may still fail.
- Multi-part videos: the script creates one folder per resolved item so downstream steps can read each part independently.

## Handoff Pattern
After ingestion, continue with a second step such as:
- summarize the transcript
- translate the subtitle text
- quote selected segments
- send the audio file to a transcription workflow if no captions were available
- inspect sampled frames for slides, charts, code, or UI changes
- inspect danmaku when the audience reaction matters or the spoken audio is hard to decode
- start with `focus_transcript.txt` when the user linked to a specific timestamp
- read `analysis.md` as the fastest combined visual + spoken summary
