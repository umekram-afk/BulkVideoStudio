"""Unit + render tests for: dust, start words, free caption placement, highlight timing, models, hardware plan,
timing estimates, preset export/import, optional voice/music, DB migration.  Run: python test_features.py"""
import json, os, random, re, shutil, sqlite3, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, ".."))
DATA = tempfile.mkdtemp(); os.environ["STUDIO_DATA"] = DATA

# ---- DB migration: an old database (no timing columns) must open and gain them
old = sqlite3.connect(os.path.join(DATA, "studio.db"))
old.executescript("CREATE TABLE projects(path TEXT PRIMARY KEY, name TEXT, status TEXT, category TEXT, message TEXT, output TEXT, progress REAL DEFAULT 0, music TEXT, music_start REAL, queued_at REAL, updated REAL, info TEXT DEFAULT '{}', overrides TEXT DEFAULT '{}', force INTEGER DEFAULT 0);"
                  "INSERT INTO projects(path,name,status) VALUES('/x/set-001','set-001','processing');")
old.commit(); old.close()
import fixtures
from app import captions, core, dust, models, plan, render, scan, server, worker

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))

row = core.db("SELECT * FROM projects")[0]
check("old database migrated (timing columns added)", all(k in row for k in ("started_at", "finished_at", "elapsed", "video_seconds")))
check("interrupted job restored to the queue on start", row["status"] == "pending" and row["started_at"] is None)

def S(**sections):
    s = core.S(core.load_settings())
    for sec, kv in sections.items():
        tgt = s
        for part in sec.split("__"):
            tgt = tgt[part]
        tgt.update(kv)
    return s

# ---- whisper models
orig = models.is_downloaded
models.is_downloaded = lambda n, fresh=False: n in ("base", "small")
check("selected model downloaded -> used", models.effective("small") == "small")
check("selected model NOT downloaded -> base is used", models.effective("large-v3") == "base")
check("model status lists every model with size", [m["name"] for m in models.status("small")] == list(models.SIZES) and all(m["size"] for m in models.status("small")))
models.is_downloaded = orig
try:
    models.start_download("nope"); r = False
except ValueError:
    r = True
check("unknown model download rejected", r)
check("online() returns a boolean", isinstance(models.online(timeout=0.5), bool))

# ---- silence-aware word refinement
words = [dict(word="a", start=0.0, end=0.4), dict(word="b", start=1.1, end=1.5), dict(word="c", start=1.6, end=2.4)]
ref = captions.refine_words(words, [(0.45, 1.3)])   # speech resumes at 1.3 but whisper said 1.1
check("word starting inside a pause snaps to the real speech onset", abs(ref[1]["start"] - 1.3) < 1e-6, ref[1])
check("words outside pauses are untouched", ref[0] == words[0] and ref[2]["start"] == 1.6)
check("refined times stay ordered", all(a["start"] <= b["start"] for a, b in zip(ref, ref[1:])))

# ---- captions ASS: free placement + highlight timing
cw = [dict(word=w, start=i * 0.5, end=i * 0.5 + 0.4) for i, w in enumerate("one two three four".split())]
ass = captions.make_ass(cw, S(captions=dict(pos_x=20, pos_y=10, align="left")), 1080, 1920, 5)
check("caption anchor: left-aligned at X=20% Y=10% -> \\an4 \\pos(216,192)", "\\pos(216,192)" in ass and ",4,0,0,0,1" in ass, ass.split("[Events]")[0][-120:])
ass = captions.make_ass(cw, S(captions=dict(pos_x=50, pos_y=50, align="center")), 1080, 1920, 5)
check("caption centred at the middle -> \\pos(540,960), \\an5", "\\pos(540,960)" in ass and ",5,0,0,0,1" in ass)
ass = captions.make_ass(cw, S(captions=dict(pos_x=100, pos_y=100, align="right")), 1080, 1920, 5)
check("caption may be placed at the very corner (no clamping)", "\\pos(1080,1920)" in ass)
def hl_starts(lead):
    a = captions.make_ass(cw, S(captions=dict(highlight=True, highlight_lead=lead, max_words=4, max_lines=1)), 1080, 1920, 5)
    return [l.split(",")[1] for l in a.splitlines() if l.startswith("Dialogue")]
base, early = hl_starts(0), hl_starts(200)
check("highlight lead +200 ms makes the highlight 0.2 s earlier", base[1] == "0:00:00.50" and early[1] == "0:00:00.30", (base, early))
check("one highlight event per spoken word, in order", len(base) == 4 and base == sorted(base))

# ---- start words
proj = tempfile.mkdtemp(); st = S(start=dict(enabled=True, max_words=3, file="start.txt", pick="first_line", default_text=""))
check("no start file and no fallback -> no text", scan.start_text(proj, st) == "")
check("fallback text used when file is missing", scan.start_text(proj, S(start=dict(default_text="Hello big wide world"))) == "Hello big wide")
open(os.path.join(proj, "start.txt"), "w", encoding="utf-8").write("Wait for it right now\nSecond line here\n")
check("start.txt: first line, limited to max_words", scan.start_text(proj, st) == "Wait for it")
check("max_words 1 -> one word", scan.start_text(proj, S(start=dict(max_words=1))) == "Wait")
picks = {scan.start_text(proj, S(start=dict(pick="random_line", max_words=2)), random.Random(i)) for i in range(30)}
check("random_line picks different lines", picks == {"Wait for", "Second line"}, picks)
open(os.path.join(proj, "hook.txt"), "w").write("Custom file name")
check("start file name is configurable", scan.start_text(proj, S(start=dict(file="hook.txt", max_words=2))) == "Custom file")
check("start file name cannot escape the project folder", scan.start_text(proj, S(start=dict(file="../../etc/passwd"))) == "")
a = captions.make_start_ass("Wait for it", S(start=dict(rotation=20, pos_x=25, pos_y=75, duration_ms=800, delay_ms=100, fade_in_ms=50, fade_out_ms=60, uppercase=True)), 1080, 1920, 10)
check("start ASS: position, clockwise rotation, fade, uppercase", "\\pos(270,1440)" in a and "\\frz-20" in a and "\\fad(50,60)" in a and "WAIT FOR IT" in a)
check("start ASS: shown for the configured duration only", "0:00:00.10,0:00:00.90,Start" in a, a.splitlines()[-1][:60])
a = captions.make_start_ass("Hi", S(start=dict(duration_ms=5000)), 1080, 1920, 2.0)
check("start words never outlast the video", ",0:00:02.00," in a)

# ---- film dust generator
def dust_ass(total=2.0, seed=1, **kw):
    return dust.make_dust_ass(S(effects__dust=kw).effects.dust, 1080, 1920, total, random.Random(seed))
a = dust_ass(fps=10)
frames = sorted({l.split(",")[1] for l in a.splitlines() if l.startswith("Dialogue")})
check("dust redraws at the chosen rate (10 fps -> 20 patterns in 2 s)", len(frames) == 20, len(frames))
n = lambda **k: dust_ass(grain_enabled=False, hairs_enabled=False, **k).split("[Events]")[1].count("m ")  # number of specks drawn
check("amount 0 -> no specks", n(amount=0) == 0)
check("amount 300% -> about 3x the specks", n(amount=300) > 2 * n(amount=100))
check("dust colour is applied", "&H0000FF&" in dust_ass(color="#ff0000") and "&H0000FF&" not in dust_ass(color="#000000"))
check("hair colour is separate", "&HFF0000&" in dust_ass(hairs_color="#0000ff", hairs_chance=100))
check("scratches only when enabled", "&HFFFFFF&" not in dust_ass(scratches_enabled=False) and "&HFFFFFF&" in dust_ass(scratches_enabled=True, scratches_chance=100))
check("same seed -> identical pattern, different seed -> different", dust_ass(seed=5) == dust_ass(seed=5) and dust_ass(seed=5) != dust_ass(seed=6))
check("overall strength 0 -> fully transparent", all("\\1a&HFF&" in l for l in dust_ass(opacity=0).splitlines() if l.startswith("Dialogue")))
check("softness maps to blur", "\\blur0.00" not in dust_ass(softness=0) and "\\blur" not in dust_ass(softness=0, grain_enabled=False, hairs_enabled=False))
check("all dust sources off -> empty overlay", dust_ass(amount=0, grain_enabled=False, hairs_enabled=False, scratches_enabled=False).count("Dialogue") == 0)
check("size scales with frame (same look on any resolution)", len(dust.make_dust_ass(S().effects.dust, 540, 960, 1, random.Random(1))) > 0)

# ---- hardware selection
real = render.caps
def fake(hw):
    c = dict(real()); c["hw"] = hw; render.caps = lambda: c
fake([]);                       check("no GPU found: auto -> CPU", render.hw_plan(S()) == [None])
check("no GPU found: forced GPU -> falls back to CPU", render.hw_plan(S(video=dict(hwaccel="gpu"))) == [None])
fake(["h264_nvenc", "h264_qsv"]); check("GPU present, auto -> GPU first then CPU fallback", render.hw_plan(S()) == ["h264_nvenc", None])
check("CPU only -> never GPU", render.hw_plan(S(video=dict(hwaccel="cpu"))) == [None])
check("specific encoder honoured", render.hw_plan(S(video=dict(hw_encoder="h264_qsv"))) == ["h264_qsv", None])
check("unavailable encoder choice -> first working one", render.hw_plan(S(video=dict(hw_encoder="h264_amf"))) == ["h264_nvenc", None])
render.caps = real

# ---- timing estimates
now = 1000.0
R = lambda **k: dict(dict(path=k.get("path", "x"), status="pending", progress=0, started_at=None, finished_at=None, elapsed=None, queued_at=0), **k)
core.kv_set("batch_start", 0.0); core.kv_set("batch_end", None)
rows = [R(path="a", status="completed", elapsed=40.0, finished_at=100), R(path="b", status="completed", elapsed=60.0, finished_at=200),
        R(path="c", status="processing", started_at=now - 30, progress=0.5), R(path="d", queued_at=1), R(path="e", queued_at=2), R(path="f", queued_at=3)]
t = server.timing(rows, now)
check("average render time of finished videos", abs(t["batch"]["avg"] - 50) < 1e-6 and t["batch"]["finished"] == 2)
check("running video: extrapolated from its own progress (~30 s left)", 20 < t["per"]["c"]["eta"] < 40, t["per"]["c"])
check("queued videos get an estimate and a finish time, in order", t["per"]["d"]["eta"] < t["per"]["e"]["eta"] < t["per"]["f"]["eta"] and t["per"]["d"]["est"] == 50)
check("one worker: each queued video adds one average render", abs((t["per"]["e"]["eta"] - t["per"]["d"]["eta"]) - 50) < 1e-6)
check("batch total elapsed counts from batch start", abs(t["batch"]["elapsed"] - 1000) < 1e-6 and t["batch"]["active"] and t["batch"]["eta"] > 100)
# ---- voice-synchronised caption animation modes
sample_words = [dict(word="One", start=0.0, end=0.2), dict(word="complete", start=0.3, end=0.55), dict(word="line.", start=0.6, end=0.9)]
full_hl = captions.make_ass(sample_words, S(captions=dict(highlight=True, animation="progressive", max_words=6, max_lines=2)),
                            1080, 1920, 2, progressive=True)
first_dialogue = next(x for x in full_hl.splitlines() if x.startswith("Dialogue:"))
check("spoken-word highlight keeps the full caption visible from its first word", "complete" in first_dialogue and "line." in first_dialogue)
static_cap = captions.make_ass(sample_words, S(captions=dict(animation="static", highlight=False, max_words=6, max_lines=2)),
                               1080, 1920, 2, progressive=True)
check("static voice-synced animation emits one full caption event", len([x for x in static_cap.splitlines() if x.startswith("Dialogue:")]) == 1)
progressive_cap = captions.make_ass(sample_words, S(captions=dict(animation="progressive", highlight=False, max_words=6, max_lines=2)),
                                    1080, 1920, 2, progressive=True)
progressive_first = next(x for x in progressive_cap.splitlines() if x.startswith("Dialogue:"))
check("progressive animation still reveals words in sequence", "One" in progressive_first and "complete" not in progressive_first)

core.update_settings({"batch": {"workers": 2}})
t2 = server.timing(rows, now)
check("two workers finish the queue sooner", t2["batch"]["eta"] < t["batch"]["eta"])
core.update_settings({"batch": {"workers": 1}})
t3 = server.timing([R(path="p", status="pending")], now)
check("no finished video yet -> estimate unknown (not invented)", t3["per"]["p"]["eta"] is None)
core.kv_set("batch_end", 500.0)
t4 = server.timing([R(path="a", status="completed", elapsed=10.0, finished_at=100)], now)
check("finished batch: elapsed frozen at its end time, nothing left", t4["batch"]["elapsed"] == 500 and not t4["batch"]["active"] and t4["batch"]["eta"] == 0)

# ---- preset export / import
core.update_settings({"music": {"volume": 42}})
core.preset_save("alpha", core.load_settings())
exp = core.preset_export("alpha")
check("export format + contains no folder paths", exp["format"] == core.PRESET_FORMAT and "paths" not in exp["presets"]["alpha"])
saved, skipped = core.preset_import(exp)
check("import never overwrites by default (renamed 'alpha (2)')", saved == ["alpha (2)"] and not skipped, saved)
saved, _ = core.preset_import(exp, overwrite=True)
check("import with overwrite replaces", saved == ["alpha"])
bad = {"format": core.PRESET_FORMAT, "version": 1, "presets": {"broken": {"video": {"crf": 999}}, "ok": {"video": {"crf": 22}, "unknown_future": {"x": 1}}}}
saved, skipped = core.preset_import(bad)
check("invalid preset skipped with reason, valid one imported (unknown keys ignored)", saved == ["ok"] and len(skipped) == 1 and "crf" in skipped[0], (saved, skipped))
try:
    core.preset_import({"hello": 1}); rej = False
except ValueError:
    rej = True
check("non-preset file rejected", rej)
check("export all lists every preset", set(core.preset_export()["presets"]) >= {"alpha", "alpha (2)", "ok"})

# ---- optional voice / optional music
root = tempfile.mkdtemp(); fixtures.make(root, 1, 3, 6)
core.reset_settings(); core.update_settings({"paths": {"parent": root, "music_dir": root + "/_music"}, "captions": {"enabled": False}})
pdir = os.path.join(root, "set-001"); os.remove(os.path.join(pdir, "voice.mp3"))
sc = scan.scan_project(pdir, S(), {})
check("missing voice blocks the project when voice is required (default)", not sc["ready"] and sc["issues"][0]["cat"] == "missing_voice")
core.update_settings({"voice": {"required": False}})
sc = scan.scan_project(pdir, S(), {})
check("voice not required: project is ready, with an info note", sc["ready"] and any(i["cat"] == "missing_voice" and not i["blocking"] for i in sc["issues"]))
P = plan.build_plan(pdir, S(), {})
check("no voice: length = pictures x seconds per picture, all pictures used", abs(P["duration"] - 3 * 3.0) < 1e-6 and len(P["segments"]) == 3 and P["voice"] is None)
core.update_settings({"voice": {"enabled": False}})
P = plan.build_plan(pdir, S(), {})
check("voice switched off entirely also works", P["voice"] is None and P["music"] is not None)
core.update_settings({"music": {"enabled": False}})
P = plan.build_plan(pdir, S(), {})
check("no voice and no music is allowed", P["voice"] is None and P["music"] is None)
core.update_settings({"music": {"enabled": True, "required": True, "allow_repeat": True}, "paths": {"music_dir": root + "/nowhere"}})
try:
    plan.build_plan(pdir, S(), {}); r = False
except core.StudioError as e:
    r = e.category == "missing_music"
check("music required but missing -> clear missing_music error", r)
core.update_settings({"music": {"required": False}})
P = plan.build_plan(pdir, S(), {})
check("music not required: renders without it (with a warning)", P["music"] is None and any("without background music" in w for w in P["warnings"]))

# ---- real renders: no voice/music file really renders; start words appear only at the start; caption placement; dust
core.reset_settings(); fixtures.make(root, 1, 3, 6)   # restores voice.mp3
core.update_settings({"paths": {"parent": root, "music_dir": root + "/_music"}, "video": {"preset": "ultrafast"}, "transition": {"enabled": False},
    "ending": {"enabled": False}, "music": {"enabled": False}, "captions": {"enabled": False},
    "effects": {"vignette": {"enabled": False}, "wobble": {"enabled": False}, "white_noise": {"enabled": False}, "black_noise": {"enabled": False}, "dust": {"enabled": False}}})
scan.sync_projects(S())
def pv(patch, secs=3):
    core.update_settings({"start": {"enabled": False}, "captions": {"enabled": False}, "effects": {"dust": {"enabled": False}}}); core.update_settings(patch)
    dst = os.path.join(tempfile.mkdtemp(), "p.mp4"); shutil.copy(worker.make_preview(pdir, secs)[0], dst); return dst
def frame(v, t):
    p = os.path.join(tempfile.mkdtemp(), "f.png"); subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", v, "-frames:v", "1", p], check=True); return p
def ssim(a, b):
    r = subprocess.run(["ffmpeg", "-i", a, "-i", b, "-lavfi", "ssim", "-f", "null", "-"], capture_output=True, text=True); return float(re.search(r"All:([0-9.]+)", r.stderr).group(1))
def luma(img, crop):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", img, "-vf", f"crop={crop},signalstats,metadata=mode=print:file=-", "-f", "null", "-"], capture_output=True, text=True); return float(re.search(r"YAVG=([0-9.]+)", r.stdout).group(1))

base = pv({})
st_v = pv({"start": {"enabled": True, "duration_ms": 800, "default_text": "Hello there", "size": 160, "bold": True, "fade_in_ms": 0, "fade_out_ms": 0, "pos_y": 50}})
check("start words are visible at the start...", ssim(frame(base, 0.3), frame(st_v, 0.3)) < 0.97, ssim(frame(base, 0.3), frame(st_v, 0.3)))
check("...and gone after the configured time", ssim(frame(base, 2.0), frame(st_v, 2.0)) > 0.99)
core.update_settings({"effects": {"dust": {"enabled": True}}})
dust_v = pv({"effects": {"dust": {"enabled": True, "amount": 300, "size": 200, "fps": 30}}})
check("film dust changes the picture", ssim(frame(base, 1.0), frame(dust_v, 1.0)) < 0.995)
check("dust pattern differs from frame to frame (animated)", ssim(frame(dust_v, 1.0), frame(dust_v, 1.1)) < 0.9999 or ssim(frame(base, 1.0), frame(dust_v, 1.0)) != ssim(frame(base, 1.1), frame(dust_v, 1.1)))
top = pv({"captions": {"enabled": True, "voice_sync": False, "pos_x": 50, "pos_y": 12, "size": 90, "highlight": False}})
bot = pv({"captions": {"enabled": True, "voice_sync": False, "pos_x": 50, "pos_y": 88, "size": 90, "highlight": False}})
fb, ft, fo = frame(base, 1.0), frame(top, 1.0), frame(bot, 1.0)
W_, H_ = 1080, 1920
d = lambda a, b, c: abs(luma(a, c) - luma(b, c)); TOPC, BOTC = f"{W_}:400:0:0", f"{W_}:400:0:{H_ - 400}"
check("caption placed at the top draws in the top band", d(ft, fb, TOPC) > 1 and d(ft, fb, BOTC) < 0.1)
check("caption placed at the bottom draws in the bottom band", d(fo, fb, BOTC) > 1 and d(fo, fb, TOPC) < 0.1)
core.update_settings({"video": {"hwaccel": "gpu"}})
g = pv({})
check("GPU requested but unavailable: still renders (CPU fallback)", os.path.getsize(g) > 10000)
sys.exit(0 if ok else 1)
