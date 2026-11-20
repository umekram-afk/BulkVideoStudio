"""Voice-based image sync + voice-synchronised captions.  Whisper is replaced by a simulated transcript (deterministic)
so the matching, planning, captions and the real rendered frame-change times are all verified.  Run: python tests/test_voicesync.py"""
import os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, ".."))
os.environ["STUDIO_DATA"] = tempfile.mkdtemp()
import fixtures
from app import captions, core, plan, render, scan, voicesync as vs, worker

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))

TEXT = ("Hey everyone welcome back. Today we talk about why do certain people cross your path in life. Some folks come to teach us lessons. "
        "Others bring pain but also growth. And finally you learn how to let go and heal.")
def narration(text=TEXT, t0=1.0, step=0.3):
    out, t = [], t0
    for w in text.split():
        out.append(dict(word=w, start=t, end=t + step * 0.8)); t += step
    return out
W = narration()
IMGS = ["/p/02_Some-people-teach-lessons.png", "/p/01_Why-do-certain-people-cross-your-path.png", "/p/03_Pain-and-growth.png",
        "/p/04_Learning-to-let-go-and-healing.png", "/p/05_Spaceships-and-aliens.png"]
def run(imgs=IMGS, words=W, thr=0.5, hold=1.0, lead=0.15, un="keep"):
    return vs.match_images(imgs, words, thr, hold, lead, un, words[-1]["end"] + 1)
def when(r, name):
    return next((s for i, s in zip(r["images"], r["starts"]) if os.path.basename(i).startswith(name)), None)
def spoken(word_text, nth=0):
    return [w["start"] for w in W if w["word"].lower().strip(".,") == word_text][nth]

# ---- file names
check("filename -> number + caption (hyphens/underscores become spaces)", vs.parse_name("/x/01_Why-do-certain-people-cross-your-path.png") == (1, "Why do certain people cross your path"))
check("underscore/space separators and no number", vs.parse_name("my_great_photo.jpg") == (None, "my great photo") and vs.parse_name("7 - Hello_World.png") == (7, "Hello World"))
check("images sorted numerically (2 before 10)", [os.path.basename(p)[:2] for p in vs.order_images(["/p/10_a.png", "/p/2_b.png", "/p/01_c.png"])] == ["01", "2_", "10"])
check("mixed numbered/unnumbered keeps the given order", vs.order_images(["/p/b.png", "/p/01_a.png"]) == ["/p/b.png", "/p/01_a.png"])

# ---- matching
r = run()
check("voice sync succeeds", r["ok"])
check("image 1 is shown from the very beginning although the intro narration comes first", r["starts"][0] == 0.0 and os.path.basename(r["images"][0]).startswith("01"))
check("images are used in numeric order, times strictly increase", [os.path.basename(p)[:2] for p in r["images"]][:4] == ["01", "02", "03", "04"] and all(a < b for a, b in zip(r["starts"], r["starts"][1:])))
check("switch happens when the idea is spoken (synonym 'folks' ~ 'people')", abs(when(r, "02") - (spoken("folks") - 0.15)) < 0.45, when(r, "02"))
check("image 3 switches at 'pain'", abs(when(r, "03") - (spoken("pain") - 0.15)) < 0.45)
check("stemmed/different wording: 'Learning...healing' ~ 'you learn ... heal'", when(r, "04") is not None and abs(when(r, "04") - (spoken("learn") - 0.15)) < 0.8, when(r, "04"))
check("unrelated caption is NOT forced in: previous image stays (skipped)", when(r, "05") is None and any("no confident match" in w for w in r["warnings"]))
check("'spread' mode places the unmatched image between neighbours instead", (lambda rr: when(rr, "05") is not None and when(rr, "05") > when(rr, "04"))(run(un="spread")))
check("a very high threshold drops weak matches", len(run(thr=0.95)["images"]) <= len(r["images"]))
check("exact wording is not required: shorter caption '01_Certain-people' still matches", (lambda rr: when(rr, "01") == 0.0 and when(rr, "02") is not None)(run(["/p/01_Certain-people.png", "/p/02_Pain-growth.png"])))
check("typo tolerance (fuzzy): 'Pian-and-growth'", run(["/p/01_Why-certain-people.png", "/p/02_Pian-and-growth.png"])["info"][1]["matched"])
# split caption across consecutive images
split = run(["/p/01_Why-do-certain-people.png", "/p/02_cross-your-path.png", "/p/03_in-life-some-folks.png", "/p/04_Pain-and-growth.png"], hold=0.5)
check("one sentence split over consecutive images keeps numeric order", split["ok"] and [os.path.basename(p)[:2] for p in split["images"]] == sorted(os.path.basename(p)[:2] for p in split["images"]))
# duplicates
dup = run(["/p/01_Intro.png", "/p/02_Pain-and-growth.png", "/p/03_Pain-and-growth.png"], hold=1.0)
t = [i["time"] for i in dup["info"] if i["matched"] and i["name"][:2] in ("02", "03")]
check("two images with the same caption never land on the same moment", len(t) < 2 or abs(t[0] - t[1]) >= 1.0, t)
# repeated phrase: later image must use the later occurrence (sequential context)
rep_words = narration("money matters. then we discuss love and friends. money matters again at the end of the story.")
rr = vs.match_images(["/p/01_Love-and-friends.png", "/p/02_Money-matters.png"], rep_words, 0.5, 0.5, 0.1, "keep", 20)
check("sequence context: image 2 matches the money mention AFTER image 1's topic", rr["ok"] and rr["info"][1]["time"] > rep_words[7]["start"], rr["info"])
# not enough info
check("file names without descriptions -> voice sync declines (clear reason)", (lambda x: not x["ok"] and "no descriptive text" in x["warnings"][0])(run(["/p/image_01.png", "/p/image_02.png"])))
check("nothing matches -> declines instead of showing one image forever", not run(["/p/01_Alpha-beta.png", "/p/02_Gamma-delta.png"])["ok"])
check("no narration -> declines", not vs.match_images(IMGS, [], 0.5)["ok"])
check("weak single-word guess below threshold is rejected", not run(["/p/01_Intro.png", "/p/02_the.png"], thr=0.6)["info"][1]["matched"])
check("minimum hold respected between switches", all(b - a >= 0.3 for a, b in zip(r["starts"], r["starts"][1:])))

# ---- captions from the voice
cw = [dict(word=w, start=1 + i * 0.4, end=1.3 + i * 0.4) for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve thirteen".split())]
S = core.S(core.load_settings())
S["captions"].update(max_words=6, max_lines=2)
a = captions.make_ass(cw, S, 1080, 1920, 9, progressive=True)
ev = [l for l in a.splitlines() if l.startswith("Dialogue")]
t2s = lambda s: sum(float(x) * m for x, m in zip(s.split(":"), (3600, 60, 1)))
start_of = {w["word"]: w["start"] for w in cw}
body = lambda l: l.split("}", 1)[1]  # the visible caption text of a Dialogue line
bad = [l for l in ev for w in re.findall(r"[a-z]+", body(l).replace("\\N", " ")) if w in start_of and t2s(l.split(",")[1]) < start_of[w] - 0.011]
check("never shows a word before it is spoken", not bad and len(re.findall(r"[a-z]+", body(ev[5]))) >= 6, bad[:1])
check("words appear progressively (cumulative text, one event per word)", len(ev) == 13 and ev[0].endswith("one") and ev[2].endswith("one two three"))
check("max 6 words per line, 2 lines (default)", S.captions.max_words == 6 and S.captions.max_lines == 2 and any("\\N" in l for l in ev) and max(len(body(l).split("\\N")[0].split()) for l in ev) <= 6)
check("a 13-word stretch becomes short natural blocks (<= 12 words)", max(len(body(l).replace("\\N", " ").split()) for l in ev) <= 12)
S2 = core.S(core.load_settings()); S2["captions"].update(timing_offset=-1.0)
a2 = captions.make_ass(cw, S2, 1080, 1920, 9, progressive=True)
check("a negative timing offset cannot make captions appear early", min(t2s(l.split(",")[1]) for l in a2.splitlines() if l.startswith("Dialogue")) >= 1.0 - 1e-6)
blocks = captions.chunk_natural(cw, 6, 2, image_starts=[0, 2.6])
check("a picture change starts a new caption block", any(b[0]["start"] >= 2.6 for b in blocks) and not any(b[0]["start"] < 2.6 <= b[-1]["start"] for b in blocks))
seg = captions.words_from_segments([dict(text="hello there my friend", start=2.0, end=4.0)])
check("segment fallback: words spread over the segment, monotonic", len(seg) == 4 and seg[0]["start"] == 2.0 and abs(seg[-1]["end"] - 4.0) < 1e-6 and all(a["end"] <= b["start"] + 1e-9 for a, b in zip(seg, seg[1:])))
check("old behaviour unchanged when not progressive (whole block at once)", len([l for l in captions.make_ass(cw, S, 1080, 1920, 9).splitlines() if l.startswith("Dialogue")]) < 13)

# ---- planning with a simulated Whisper transcript
root = tempfile.mkdtemp(); fixtures.make(root, 1, 1, 14)
pdir = os.path.join(root, "set-001")
for f in os.listdir(pdir):
    if f.endswith(".png"): os.remove(os.path.join(pdir, f))
names = ["01_Why-do-certain-people-cross-your-path.png", "02_Some-people-teach-lessons.png", "03_Pain-and-growth.png", "04_Learning-to-let-go-and-healing.png"]
for i, n in enumerate(names): fixtures.image(os.path.join(pdir, n), i * 2, 720, 1280)
open(os.path.join(pdir, "script.txt"), "w").write("This script says something completely different from the audio so it must never be used for captions.")
core.update_settings({"paths": {"parent": root, "music_dir": root + "/_music"}, "video": {"preset": "ultrafast"}, "music": {"enabled": False}, "ending": {"enabled": False},
                      "transition": {"enabled": False}, "captions": {"enabled": True},
                      "effects": {"vignette": {"enabled": False}, "wobble": {"enabled": False}, "white_noise": {"enabled": False}, "black_noise": {"enabled": False}, "dust": {"enabled": False}}})
TR = dict(words=W, segments=[dict(text=TEXT, start=1.0, end=13.0)], language="en", duration=14.0, model="base")
captions.available = lambda: True
state = {"tr": TR}
captions.transcribe = lambda audio, S: state["tr"]
render.silences = lambda p: []
def P(**kw):
    core.update_settings(kw); return plan.build_plan(pdir, core.S(core.load_settings()), {})
p = P(sync={"method": "voice"}, captions={"voice_sync": True})
check("plan: voice method -> mode 'voice', 4 images, first at 0", p["mode"] == "voice" and len(p["segments"]) == 4 and p["segments"][0]["start"] == 0)
check("plan: image times follow the spoken ideas (not the script, not even spacing)", abs(p["segments"][2]["start"] - (spoken("pain") - 0.15)) < 0.45 and p["segments"][1]["start"] > 5, [round(s["start"], 1) for s in p["segments"]])
check("plan: confidence info per image is available", len(p["sync_info"]) == 4 and all(i["matched"] for i in p["sync_info"]))
check("plan: captions come from the transcript, never from the script", p["voice_captions"] and [w["word"] for w in p["words"]] == [w["word"] for w in W] and not any("script" in w["word"].lower() for w in p["words"]))
open(os.path.join(pdir, "script.txt"), "w").write(TEXT.upper())   # a script that matches the audio, in capitals
p = P(captions={"voice_sync": False})
check("plan: with caption sync OFF the old script-based captions are kept (script wording)", not p["voice_captions"] and p["words"][0]["word"] == "HEY")
p = P(captions={"voice_sync": True})
check("plan: with caption sync ON the same project uses the spoken wording instead", p["voice_captions"] and p["words"][0]["word"] == "Hey")
p = P(captions={"voice_sync": True}, sync={"method": "manual"})
check("plan: manual method = old behaviour (script-based distribution)", p["mode"] in ("sentence", "even") and p["sync_info"] is None)
check("image sync and caption sync are independent (manual images + voice captions)", p["voice_captions"] and [w["word"] for w in p["words"]] == [w["word"] for w in W])
p = P(sync={"method": "voice", "match_threshold": 95})
check("plan: threshold is configurable (95% keeps fewer images)", len(p["segments"]) <= 4)
core.update_settings({"sync": {"method": "voice", "match_threshold": 50}})
state["tr"] = dict(TR, words=[])
p = plan.build_plan(pdir, core.S(core.load_settings()), {})
check("no word timestamps: captions fall back to segment timing", p["voice_captions"] and len(p["words"]) == len(TEXT.split()) and any("segment timing" in w for w in p["warnings"]))
check("no word timestamps: images fall back to manual distribution with a reason", p["mode"] != "voice" and any("transcribed" in w or "not possible" in w for w in p["warnings"]), p["warnings"])
state["tr"] = TR
captions.available = lambda: False
p = plan.build_plan(pdir, core.S(core.load_settings()), {})
check("no Whisper: voice-synced captions are skipped with an install hint (never from the script)", p["words"] == [] and any("Faster-Whisper" in w for w in p["warnings"]))
check("no Whisper: image sync falls back to manual distribution", p["mode"] in ("sentence", "even"))
captions.available = lambda: True
core.update_settings({"sync": {"method": "voice"}})
os.rename(os.path.join(pdir, names[0]), os.path.join(pdir, "image_01.png")); os.rename(os.path.join(pdir, names[1]), os.path.join(pdir, "image_02.png"))
os.rename(os.path.join(pdir, names[2]), os.path.join(pdir, "image_03.png")); os.rename(os.path.join(pdir, names[3]), os.path.join(pdir, "image_04.png"))
p = plan.build_plan(pdir, core.S(core.load_settings()), {})
check("plain file names (image_01.png): voice sync declines, images still evenly spread", p["mode"] != "voice" and len(p["segments"]) == 4 and any("descriptive" in w for w in p["warnings"]))
for n, o in zip(names, ("image_01.png", "image_02.png", "image_03.png", "image_04.png")): os.rename(os.path.join(pdir, o), os.path.join(pdir, n))

# ---- real render: the picture really changes when the idea is spoken
scan.sync_projects(core.S(core.load_settings()))
core.update_settings({"sync": {"method": "voice", "lead_ms": 0}, "captions": {"enabled": False}})
exp = plan.build_plan(pdir, core.S(core.load_settings()), {})
import shutil
out = os.path.join(tempfile.mkdtemp(), "v.mp4"); shutil.copy(worker.make_preview(pdir, 14)[0], out)
r = subprocess.run(["ffmpeg", "-i", out, "-vf", "select='gt(scene,0.02)',showinfo", "-f", "null", "-"], capture_output=True, text=True)
cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
want = [s["start"] for s in exp["segments"][1:]]
check("rendered video switches pictures at the matched moments (+-0.25 s)", len(want) == 3 and all(any(abs(c - w) < 0.25 for c in cuts) for w in want), (want, cuts))
sys.exit(0 if ok else 1)
