# Bulk Video Studio — Clean Rebuild Specification

Status: implementation specification for a new application. This is not a patch plan for the legacy app.

## Product boundary

Build a Windows-first, local-only bulk video production application. It converts folders of still images, scripts and voice recordings into finished videos. It must work offline after installation, use no paid/cloud APIs, preserve source media, and open a browser UI on localhost. This project is Bulk Video Studio only; it is not the separate AI Video Factory / Reel Factory.

The legacy application remains untouched. The replacement is implemented as an independent application under `new_studio/` on the `rewrite/clean-bulk-video-studio` branch.

## Inputs and folder conventions

- Parent directory contains project folders selected by a configurable prefix (default `set-`).
- Each project may contain PNG/JPG/JPEG/WEBP still images, naturally sorted by filename; optional `script.txt` or `<project>.txt`; and one voice recording (MP3/WAV/M4A/AAC/FLAC/OGG).
- Optional separate script and voice directories match files by project stem, never by directory position.
- Optional music directory contains MP3/WAV/M4A/AAC tracks.
- Optional `start.txt` (configurable filename) supplies opening words; first or random non-empty line can be selected.
- Support excluded images, project-level overrides, ambiguous-voice selection, missing-file/readiness diagnostics, and safe rescan without losing finished/queued state.
- Outputs are MP4 files named `<project><suffix>.mp4` in the project folder, with optional extra output copy. Render to a temporary .part file, validate with ffprobe, then atomically publish the final file.

## User interface pages

1. Dashboard: environment/FFmpeg/Whisper capability, project counts, batch timing and quick actions.
2. Projects: parent path and prefix, scan, search/filter, select all/none, project readiness/status, project detail drawer, image exclusions/order, voice selection, script preview/edit source, timeline, manual image start times, per-project overrides, preview and retry.
3. Voice & Music: enable/require voice, folder/separate-directory source, optional voice, volume, fades; music enable/require, folder, random/manual selection, avoid repeats, random/manual start, volume, fades, ducking and audio preview.
4. Video Settings: aspect ratio (9:16, 16:9, 1:1), resolution/FPS/fit mode, encoder policy (auto/GPU/CPU), hardware encoder choice, quality/speed preset, Whisper model/language/VAD and model download/status.
5. Effects & Transitions: vignette; smooth zoom with probability, direction, min/max amount, speed, smoothness, focus/custom X/Y and quality; wobble; black/white noise; film dust with specks, opacity, colour, softness, change rate, flicker/jitter, hairs, grain, scratches and per-video random patterns; transition type/duration/timing/random scope; preview.
6. Captions: enable/disable, presets/custom styling, installed font discovery and reliable font loading, font size/colour/bold/uppercase, outline/shadow/background/padding, alignment and draggable X/Y placement, safe-area overlay, max words/lines, voice-sync, highlight and highlight timing offset. Spoken-word highlight mode must keep the caption text stationary while only the active-word highlight advances.
7. Start Words: enable, per-project file or fallback text, first/random line, 1–6 word limit, duration/delay/fades, installed fonts, size/colour/weight/italic/uppercase/spacing/outline/shadow/background, rotation and draggable X/Y.
8. Batch Processing: start all or selected, explicit re-render/overwrite/skip-existing policy, parallel workers 1–4, queue, per-job progress/status/time/ETA, total elapsed time, sum and average render time, pause/resume, stop everything, retry failed, skip/cancel individual jobs.
9. Settings: validated settings, reset defaults, presets save/load/delete/export/import without silently overwriting existing names, data/cache management, danger zone reset.
10. Activity & Error Logs: filter/search by project and level, export logs, categorized errors with actionable guidance, open technical FFmpeg log.

## Synchronization requirements

### Voice-driven image synchronization (default)
- Transcribe narration locally with Faster-Whisper word timestamps and cache transcripts by source file fingerprint + model/options.
- Use each image filename as its semantic description. Normalize hyphens/underscores, numeric prefixes, punctuation and case.
- Match image descriptions to spoken transcript using deterministic local matching: token/stem normalization, built-in and user-editable synonym groups, typo tolerance, word order/context and sequence order. No cloud or paid AI.
- Configurable confidence threshold, minimum hold time, early switch offset and unmatched-image policy (skip or place between neighbours).
- Introductory narration before the first match is allowed; first image is visible from the beginning.
- If descriptions are absent, transcription unavailable, or matching confidence is inadequate, clearly explain and fall back to manual distribution. Never label estimated timing as accurate.
- Timeline exposes image description, confidence and switch time.
- Support script/audio alignment and mismatch threshold/action (estimate with warning or fail).

### Manual distribution
- Preserve sentence, grouped-sentence and even-distribution modes, configurable group size, mismatch threshold and mismatch action.
- Permit manual start-time editing.

### Captions
- When voice synchronization is enabled, captions come from the actual narration, not the script. Show words only as spoken; default six words per line and two lines; begin a new block at image changes. Use segment timestamps if word timestamps are unavailable.
- Caption synchronization is independent from image synchronization.
- When spoken-word highlighting is active, the caption block remains fixed in position and content while the highlight advances word-by-word.

## Rendering and media

- Use FFmpeg/ffprobe with capability probing for filters, transitions and hardware encoders.
- CPU-only must never select a GPU encoder. GPU mode tries the selected/available hardware encoder; failures fall back to CPU for that video and are logged. Auto chooses a verified available encoder.
- First image must always receive the required opening zoom treatment; per-image smooth zoom remains separately configurable.
- Support transition families only when the installed FFmpeg build proves they work; unavailable choices are visibly disabled, never silently substituted.
- Mix voice and optional music with volume, fades, random/manual start, looping, ducking and limiting.
- Support optional no-voice and no-music output; duration derives from voice when present or image count × configured seconds-per-image otherwise.
- Include configurable ending duration, video/audio fade.
- Preserve output aspect ratio without stretching; support crop, blurred background or solid background.
- Preview uses the same render graph and settings as final output.
- Use render progress from FFmpeg, output validation and actionable logs.

## Queue, lifecycle and timing — critical acceptance requirements

Use a durable SQLite-backed queue with explicit job records and a scheduler independent of HTTP requests/browser tabs. Do not implement scheduling as untracked daemon threads that mutate project state in multiple places.

- Parallelism 1–4 must process every eligible selected project exactly once, in deterministic queue order, never leaving later jobs pending after an earlier job completes.
- Queue claims must be atomic and transactional. A worker crash/start failure must release or recover its lease and must never leave a permanent phantom active slot.
- Each render runs in an isolated worker process with a recorded process ID and process group/job object strategy appropriate for Windows. The supervisor owns lifecycle and process cleanup.
- Pause prevents new claims while allowing active renders to finish; resume continues queued work.
- Stop cancels previews and all active renders, empties the pending queue, freezes timers, and returns the app to an idle state that can immediately start a new batch.
- Reset waits for confirmed worker/process termination before deleting project state, logs and caches; preserve settings and presets; optionally delete only videos created by the app. Reset must not return success while background work is still active.
- Browser disconnects must not cancel jobs or break the HTTP response handler.
- Startup recovery reconciles orphaned running jobs and stale process leases safely. Explicitly distinguish resume-on-restart policy from a user-requested stop.
- Per-project elapsed time uses monotonic elapsed time persisted on terminal state; completed/failed/cancelled times freeze. Never calculate from a stale start timestamp after stop/reset.
- Batch elapsed time has a single well-defined start/end and freezes on completion or stop. Report finished count, render-time sum, average, and estimates for running/queued jobs after enough samples exist.
- Every state transition is audited with timestamp, project, job ID and reason. No silent exception swallowing in scheduler or workers.

## Performance and reliability

- Bound CPU thread usage per FFmpeg process and coordinate it with parallel worker count to avoid oversubscription.
- Cache expensive probes/transcripts/thumbnails with invalidation based on source fingerprints.
- Avoid regenerating timeline/transcripts when inputs and settings have not changed.
- Stream progress and logs without blocking rendering.
- Validate every setting at the API boundary and persist settings atomically.
- Use SQLite WAL, foreign keys, migrations, busy timeout and short transactions.
- Never delete or overwrite user source media. Clean temporary artifacts only after worker termination.
- Provide diagnostics for FFmpeg, ffprobe, Whisper model/device, available encoders and fonts.

## Technical direction for the replacement

- Windows-first Python application with a small local HTTP server and vanilla HTML/CSS/JavaScript UI.
- Python standard library for server, persistence and scheduling; Faster-Whisper is optional/local for transcription; FFmpeg/ffprobe are external executables.
- Separate modules for configuration/schema, database/migrations, scanning, transcription, voice/image matching, timeline planning, caption generation, FFmpeg graph/rendering, worker supervisor, REST API and static UI.
- Use a process supervisor + SQLite leases rather than relying on in-process render threads.
- Provide setup/run scripts, clear diagnostics, structured logs, exportable presets and automated tests.
- Tests must cover queue ordering and parallelism 1–4, failure recovery, stop/restart, reset, timer freezing, browser disconnect, CPU/GPU selection logic, timeline/sync/caption timing, FFmpeg integration and Windows process cleanup.

## Definition of done

The replacement is not complete merely because unit tests pass. It must include:
1. All pages and settings above wired to working backend behavior.
2. A real Windows installation/run path.
3. End-to-end renders from representative project folders.
4. Actual FFmpeg/Whisper and CPU/GPU hardware tests on the target PC, with honest reporting of unsupported hardware.
5. Batch acceptance runs with at least five projects at worker counts 1, 2, 3 and 4, including injected worker failure, stop during render, restart after stop, and reset followed by a fresh batch.
6. Verified elapsed-time records and exported logs for each acceptance run.
