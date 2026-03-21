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
      *.vtt / *.srt / *.ass / *.json3
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
2. Check `preferred_subtitle_path` in `summary.json` to see which subtitle track was chosen for transcript generation. VTT is preferred when the same track exists in multiple formats.
3. Read `transcript.txt` if it exists.
4. If the run used `--balanced-read` or `--extract-frames`, inspect `frames.json` and the sampled images next.
5. Fall back to raw subtitle files when transcript normalization dropped useful structure.
6. Fall back to audio only when subtitles are absent or obviously incomplete.
7. Read `analysis.md` when the run used `--analyze`.

## Failure Modes
- Missing `yt-dlp`: install it with `python -m pip install yt-dlp`.
- Missing subtitles: rerun with `--auto-subtitles`, then with `--download-audio` if needed.
- Missing spoken transcript: rerun with `--transcribe-audio` after setting `OPENAI_API_KEY`.
- Missing visual understanding: rerun with `--extract-frames`.
- Best overall understanding: rerun with `--balanced-read`.
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
- read `analysis.md` as the fastest combined visual + spoken summary
