# Local Bulk Video Production Studio

A local web app (runs on your Windows PC, opens in your browser at `http://localhost:8765`) that turns folders of
**images + script + voice-over** into finished vertical videos with background music, transitions, film effects and
synchronised captions. Hundreds of projects per batch. No cloud, no paid APIs; works offline after installation.

## 1. Install (Windows, ~10 minutes, once)

1. **Python 3.10 or newer** - https://www.python.org/downloads/ . In the installer tick **"Add python.exe to PATH"**.
2. **Unzip** this folder anywhere (e.g. `C:\BulkVideoStudio`). Avoid folders that need admin rights.
3. **FFmpeg** (the video engine). Choose one:
   - Open *Command Prompt* and run `winget install Gyan.FFmpeg`, then close and reopen it; **or**
   - Download `ffmpeg-release-full` from https://www.gyan.dev/ffmpeg/builds/ , unpack it, and copy its `bin` folder to
     `ffmpeg\bin` inside this folder so that `ffmpeg\bin\ffmpeg.exe` and `ffmpeg\bin\ffprobe.exe` exist.
4. **Double-click `setup.bat`.** It checks Python and FFmpeg, creates a virtual environment, installs the one Python
   dependency (`faster-whisper`), and offers to download the transcription model (~150 MB) so it works offline.

## 2. Run

Double-click **`run.bat`**. The browser opens automatically. Keep the black window open while working; closing it stops
the app. The server listens on `127.0.0.1` only (not reachable from other computers).

If the app is closed or the PC restarts while rendering, **unfinished projects resume automatically** next time
(use *Pause* in Batch Processing if you do not want that).

## 3. Quick start

1. **Projects** -> choose the parent folder (button *Browse...* or paste a path) -> check the prefix (default `set-`) -> *Scan*.
2. **Voice & Music** -> choose the music folder; set volumes.
3. **Effects & Transitions / Captions** -> pick a look; press *Render 8-second preview* to see the real result.
4. **Batch Processing** -> *Start all*. Watch progress; the app keeps rendering if you close the tab.

Finished videos are saved inside each project folder as `<project>_final.mp4`.

### Folder layout
```
Main Folder/
  set-001/  image_01.png  image_02.png ... script.txt  voice.mp3
  set-002/  image_01.jpg  ...                          voice.wav     (no script -> voice is transcribed)
Voiceovers/ set-001.mp3  set-002.wav        (optional "separate voice directory" mode, matched by name)
Scripts/    set-001.txt                     (optional "separate script directory" mode)
```
Images are sorted naturally (`image_2` before `image_10`) and used in that order. PNG/JPG/JPEG/WEBP are supported.

### How synchronisation works
The script is split into sentences. The voice is transcribed locally (Faster-Whisper, word timestamps) and the script
words are matched to the spoken words; each image appears when its sentence begins. The *Timeline* view in a project
shows exactly when each image appears, and lets you type manual start times. If less than the configured share of the
script is found in the audio (default 60%), the project gets a **mismatch warning**, timing falls back to an estimate
(clearly labelled), and captions follow the spoken words - it never pretends the sync is accurate.

## 4. Black noise and Smooth zoom (Effects & Transitions page)

**Black noise** - dark film specks / dust over the picture. Controls: *Density*, *Darkness*, *Grain size* (1 px fine grain
to 12 px dust blobs), *Softness* (sharp pixels to blurred blobs), *Flicker* (random dips in strength, like an old
projector) and *Animation* (Slow / Normal / Fast, or **Static** = frozen dust on the lens, the cheapest to render).
Recipes: fine film grain = size 1-2, soft 0, normal; drifting dust = size 8-12, low density, soft 60, slow.

**Smooth zoom** - slow zoom on individual pictures (off by default; tick *Enable*). The first image is forced to zoom by default when this effect is enabled; disable *Always zoom the first image* to let it follow the probability. Controls:

| Control | Meaning |
|---|---|
| How often | % chance that each picture gets a zoom (100 = all, 50 = about half, chosen randomly) |
| Direction | Zoom in, zoom out, or random per picture |
| Zoom amount min / max | Extra size in % (1-100; 100 = double size). Each picture gets a random amount between min and max; set both equal for a fixed amount |
| Speed (1-100) | 1 = the zoom lasts the whole time the picture is on screen; 100 = it finishes in about 15% of that time |
| Smoothness (0-100) | 0 = constant speed; 100 = gentle ease-in and ease-out |
| Focus point | Center, 4 sides, 4 corners, **Custom X/Y** (0-100%), or **Random** per picture |
| Quality | Standard (fast) or High (smoother resampling, about 2x the render time of zoomed pictures) |

Zoom is sub-pixel smooth (no shaking). Use *Render 8-second preview* to judge it on a real project.

## 4b. More features (all on their own pages)

**Film dust (black noise, CapCut style)** - *Effects & Transitions*. Reproduces the look of the reference design: a few soft,
tilted dark specks per frame, occasional curved hairs, faint micro-grain, redrawn at a choppy rate (default 15 per second).
Options: amount, speck size, min/max opacity, overall strength, **colour** (specks, hairs and scratches each), softness/blur,
change rate, flicker, hairs (chance, count, size), micro-grain (amount, strength), **vertical film scratches** (off by default),
and "different pattern per video". The older pixel-grain effect is still there as "Black noise - fine grain".

**Start words** - *Start Words* page. 1-6 words shown for a few milliseconds at the start of every video. Put a text file
named `start.txt` (name configurable) in each project folder; the first line (or a random line) is used, cut to the word limit.
Projects without the file use an optional fallback text. Options: how long it stays (ms), delay, fade in/out, font (all fonts
installed on your computer via *Load all installed fonts* in Chrome/Edge), size, colour, bold/italic/uppercase, letter spacing,
outline, shadow, background box, **rotation**, and **X/Y position - drag the words in the preview**.

**Caption placement anywhere** - *Captions*. Drag the caption in the preview, use the X/Y sliders, or the quick buttons. Areas
covered by the Reels/Shorts interface are shaded red; you get a warning if the text would be hidden or cut off, but you are
never blocked.

**Highlight timing** - the spoken word highlight now snaps word boundaries to real pauses measured from the audio, transcription
no longer uses the (less accurate) silence filter by default, and *Highlight timing (ms)* lets you shift the highlight earlier
or later to taste.

**Whisper models** - *Video Settings*. See which models are downloaded. Choosing a model that is not downloaded keeps the **base**
model in use until the download is finished (button *Download & use*); *Back to base* returns to base at any time.

**Image synchronisation - two methods** (*Video Settings*).
*Automatic (voice)* is the default. The real narration is transcribed locally (the existing Whisper model) and each picture
appears when its idea is spoken. The picture's **file name is its description**: `01_Why-do-certain-people-cross-your-path.png`
means number 1, "Why do certain people cross your path" (hyphens/underscores become spaces; pictures are sorted numerically).
Matching is plain deterministic text processing - no AI model, no online service: word stems, a built-in synonym list
(add your own groups, one per line, to `data\synonyms.txt`), typo tolerance, word order, and the order of the pictures. Exact wording is not
needed. Introductory narration before the first matching phrase is fine (picture 1 is shown from the start); a caption split across
consecutive pictures works; each match must reach the *confidence threshold*, pictures stay on screen at least the *minimum time*, and the
switch happens slightly early (*Switch slightly early*, default 150 ms). A picture with no confident match is either skipped (the previous
picture stays) or placed between its neighbours - your choice. If the file names have no description (`image_01.png`), nothing matches, or
Whisper is not available, the app clearly says so and uses the *Manual distribution* method instead. The project drawer's **Show timeline**
lists each picture's description, confidence and switch time.
*Manual distribution* is the previous behaviour, unchanged (Distribution, Sentences per image, Mismatch threshold, On mismatch).

**Caption synchronisation** (*Captions* page) - a separate checkbox, **Synchronize Video Captions with Image and Voice**, on by default.
On: captions are generated from the actual voice (never from the script), shown word by word exactly when spoken (never early), 6 words per
line and 2 lines by default, a new caption block starts when the picture changes; if Whisper gives no word timestamps, segment timestamps are
used. Off: the previous caption behaviour, unchanged. Image and caption synchronisation are independent settings.

**GPU / CPU** - *Video Settings*: Automatic, GPU, or CPU selects the final H.264 encoder (NVENC/QSV/AMF or libx264). Image preparation, scaling, transitions, visual effects and subtitle rendering still run mainly on the CPU. CPU mode also forces local Whisper transcription to CPU; GPU/Automatic allows Whisper to use an available GPU. Hardware encoder failures fall back to CPU.

**No voice-over / no music** - *Voice & Music*. "Use voice-over" off, or "Voice-over required" off (a project without a voice
file is then made without voice; length = pictures x seconds per picture). Same for music.

**Presets** - export one preset or all of them to a `.json` file, and import them (existing names are never overwritten; imports
get "(2)").

**Time tracking** - every video shows how long it took, the batch shows total time, the sum of render times and the average; videos
that are not finished show an estimate: a running video is extrapolated from its own progress, queued videos use the average of the
finished ones (shown after the first video completes) and respect the number of workers.

**Stop everything / Reset everything** - the red *Stop everything* button (sidebar) cancels all renders and previews and empties the queue.
*Settings -> Danger zone -> Reset everything* forgets all projects, timings, logs, caches and transcripts but **keeps your settings and
presets**; it asks whether to also delete the videos this app made.

## 5. Feature notes and honest limitations

- **Zoom Out transition is unavailable.** FFmpeg's `xfade` filter has no zoom-out; it is shown disabled, never silently
  replaced. Other transitions are checked against your installed FFmpeg.
- **Caption border radius is not supported.** Captions are drawn by FFmpeg's libass renderer, which cannot make rounded
  boxes. Box colour, opacity and padding work. The browser live preview is an approximation; use *Render preview* for
  the exact result.
- Captions stay inside platform-safe margins (at least 18% from the bottom on vertical video).
- **Wobble** moves the frame in whole pixels (FFmpeg `crop`), so it is smooth at normal settings but not sub-pixel.
- Languages written without spaces (Chinese, Japanese) are transcribed, but script-to-speech word matching is
  word-based, so those projects will usually be flagged as an estimated timing.
- Hardware encoding (NVENC / Intel QSV / AMD AMF) is auto-detected by a real test encode; if it fails at render time the
  project is re-encoded on the CPU. CPU (libx264) is the default fallback and needs no GPU.
- Rendering speed depends on your CPU. Film noise and wobble add per-frame work; use the *veryfast* encoder preset
  (default) for large batches, and keep workers at 1 unless you have a powerful CPU.

## 6. Where things are stored

`data\` inside this folder: `settings.json` (your last settings), `studio.db` (projects, queue state, logs, presets),
`logs\<project>.ffmpeg.log` (technical FFmpeg logs), plus caches. Your original media is never modified; videos are
rendered to `<name>.mp4.part`, validated with ffprobe, and only then renamed.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `setup.bat`: "Python was not found" | Reinstall Python and tick *Add python.exe to PATH*. |
| "FFmpeg not found" on the Dashboard | Follow install step 3, then restart `run.bat`. |
| Captions missing / "libass" warning | Use the *full* build of FFmpeg from gyan.dev. |
| *Browse...* does nothing | Python's tkinter is missing; paste the folder path instead. |
| Project shows **missing** | Hover the Readiness text, or open the project: it lists the exact problem (no images, no voice, several voice files - choose one, no script and Faster-Whisper not installed). Fix and press *Scan* again. |
| Project **failed** | *Activity & Error Logs* shows category, detail and suggested solution; click a row for the FFmpeg log. Fix, then *Retry failed*. |
| "Transcription failed" first time | The model downloads once; connect to the internet, or run `setup.bat` and accept the model download. |
| Font not applied in captions | The font must be installed in Windows; type its exact name in *Font family*. |
| Port busy | The app automatically uses the next free port; read the address in the black window. |
| Highlight is still off | Raise or lower *Highlight timing (ms)* on the Captions page; use a bigger Whisper model (Video Settings). |
| Start words do not show | Check `start.txt` exists in the project folder (or set a fallback text) and that *Show start words* is ticked. |
| "Local - Offline" in the sidebar | That only means no internet right now; the app works fully offline. Internet is only needed to download models. |
| Slow rendering | Raise *Encoder speed preset* to `ultrafast`/`veryfast`, disable noise/wobble, or use a hardware encoder. |

## 8. Architecture (short)

```
main.py                 entry point
app/core.py             settings schema + validation, SQLite, activity log, error categories
app/scan.py             project detection, natural sort, voice/script matching
app/plan.py             script -> sentences -> word alignment -> image timeline, music selection
app/captions.py         Faster-Whisper transcription (cached), ASS caption generation
app/render.py           FFmpeg capability detection, filter graph, execution, output validation
app/worker.py           persistent job queue (SQLite), pause/resume/stop/skip/retry, previews
app/server.py           stdlib HTTP server (localhost only), REST API
static/                 vanilla JS/CSS UI
tests/                  unit, end-to-end (HTTP) and browser tests
```
The rendering worker runs inside the server process, independent of the browser. The web server uses only the Python
standard library, so the single third-party dependency is `faster-whisper`.

## 9. Running the tests (optional, needs FFmpeg)\nThe test scripts and `fixtures.py` are stored in the repository root (not in a `tests` subfolder).
```
python test_plan.py      # alignment / timeline logic
python test_effects.py   # zoom + black noise (unit + real renders)
python test_features.py  # dust, start words, placement, timing, presets, models, optional voice/music
python test_voicesync.py # voice-based image matching + voice-synchronised captions
python test_app.py       # full server + batch + restart recovery over HTTP
```
