# Test results (Linux sandbox, FFmpeg 6.1; Windows scripts, real Faster-Whisper and GPU encoders could NOT be run there)

| Suite | Result |
|---|---|
| tests/test_plan.py - sentence splitting, word alignment, image distribution, ASS captions | 13/13 |
| tests/test_effects.py - zoom + black grain (unit + real renders) | 27/27 |
| tests/test_features.py - dust, start words, free caption placement, highlight timing, models, GPU/CPU plan, timing estimates, preset import/export, optional voice/music, DB migration | 70/70 |
| tests/test_voicesync.py - voice-based image matching (synonyms, stems, typos, split captions, duplicates, thresholds, fallbacks), voice-synchronised captions, plan integration, rendered frame-change times | 46/46 |
| tests/test_app.py - real server over HTTP: security, scan, batch, failures, retry, restart recovery, timing, presets, Stop/Reset everything, optional voice | 71/71 |
| tests/ui_smoke.js - real Chromium: all pages, tooltips on every control, no control covered by another element, drag placement, sync/caption settings, presets export/import, Stop/Reset | 81/81 |

Whisper is simulated in the voice-sync tests (a deterministic transcript is injected); real transcription accuracy is not measured here.
