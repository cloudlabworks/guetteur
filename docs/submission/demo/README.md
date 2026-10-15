# Submission demonstration

`storyboard.json` is the exact narration and scene order. `scripts/record_submission_demo.cjs` records actual browser interactions with the public AWS endpoint; it does not substitute canned API responses. Intro, architecture and result cards are editorial graphics. All accuracy results are explicitly synthetic development-set results.

Narration: Kokoro-82M, model revision `f3ff3571791e39611d31c381e3a41a3af07b4987`, voice `am_michael`, speed 0.95, Apache-2.0. No cloned voice or background music. Voice segments were generated in an isolated local model environment, peak-limited, and resampled to 22,050 Hz mono. AI coding assistance is disclosed.

## Re-record

Requirements: Node.js, Playwright Core, Chrome, ffmpeg, ffprobe, and WAV narration files `p00.wav` through `p07.wav` (in storyboard order). Generate them using your licensed text-to-speech setup, or record your own voice. Place them in `tmp/submission-video-20261015/`.

```sh
PLAYWRIGHT_CORE=/absolute/path/to/playwright-core \
CHROME_BIN='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
node scripts/record_submission_demo.cjs
```

The recorder writes the video, per-scene review frames, manifest and captured AWS JSON responses to the ignored output directory. It uses a fresh unauthenticated browser and records only the public synthetic demonstration. Captured frames remain ordered; each segment is conformed to its narration duration. It does not capture account consoles or private camera data.

A final submission receipt will identify the hosted video and source revision. Hosted video availability, not a local render alone, is required for submission.
