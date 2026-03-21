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
```

The timestamp keeps runs isolated so repeated reads do not overwrite each other.

## Read Order
1. Open `summary.json` to locate the best transcript and audio path.
2. Read `transcript.txt` if it exists.
3. Fall back to raw subtitle files when transcript normalization dropped useful structure.
4. Fall back to audio only when subtitles are absent or obviously incomplete.

## Failure Modes
- Missing `yt-dlp`: install it with `python -m pip install yt-dlp`.
- Missing subtitles: rerun with `--auto-subtitles`, then with `--download-audio` if needed.
- Restricted videos: the helper is meant for public URLs. Region-locked or login-only videos may still fail.
- Multi-part videos: the script creates one folder per resolved item so downstream steps can read each part independently.

## Handoff Pattern
After ingestion, continue with a second step such as:
- summarize the transcript
- translate the subtitle text
- quote selected segments
- send the audio file to a transcription workflow if no captions were available
