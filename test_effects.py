"""Tests for the zoom and black-noise effects (needs FFmpeg).  Run: python test_effects.py"""
import os, random, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, ".."))
os.environ["STUDIO_DATA"] = tempfile.mkdtemp()
import fixtures
from app import core, render, scan, worker

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))

def S(**zoom):
    s = core.S(core.load_settings()); s["effects"]["zoom"].update(enabled=True, **zoom); return s

# ---- unit: zoom filter generation
rng = random.Random(1)
check("zoom disabled -> no filter", render.zoom_filter(core.S(core.load_settings()), rng, 4, 30) == "")
check("probability 0 -> never zooms", all(render.zoom_filter(S(probability=0), rng, 4, 30) == "" for _ in range(50)))
check("mandatory first image zoom overrides probability 0", bool(render.zoom_filter(S(probability=0), rng, 4, 30, force=True)))
check("probability 100 -> always zooms", all(render.zoom_filter(S(probability=100), rng, 4, 30) for _ in range(50)))
hits = sum(bool(render.zoom_filter(S(probability=50), rng, 4, 30)) for _ in range(1000))
check("probability 50 -> about half of pictures", 430 < hits < 570, hits)
f_in = render.zoom_filter(S(probability=100, direction="in"), rng, 4, 30)
f_out = render.zoom_filter(S(probability=100, direction="out"), rng, 4, 30)
check("zoom in/out use different expressions", f_in != f_out and "(1-" in f_out)
dirs = {("*(1-((" in render.zoom_filter(S(probability=100, direction="random"), rng, 4, 30)) for _ in range(60)}
check("random direction yields both in and out", dirs == {True, False})
amts = [float(re.search(r"\(1\+([0-9.]+)\*", render.zoom_filter(S(probability=100, amount_min=10, amount_max=40), rng, 4, 30)).group(1)) for _ in range(200)]
check("random amount stays within 10-40% and varies", 0.10 <= min(amts) and max(amts) <= 0.40 and max(amts) - min(amts) > 0.15, (min(amts), max(amts)))
amts = [float(re.search(r"\(1\+([0-9.]+)\*", render.zoom_filter(S(probability=100, amount_min=60, amount_max=20), rng, 4, 30)).group(1)) for _ in range(50)]
check("min > max is tolerated (sorted)", all(0.2 <= a <= 0.6 for a in amts))
check("focus top_left anchors at 0,0", "x0='0.000*" in render.zoom_filter(S(probability=100, focus="top_left"), rng, 4, 30))
check("custom focus 80/30", "x0='0.800*" in (c := render.zoom_filter(S(probability=100, focus="custom", focus_x=80, focus_y=30), rng, 4, 30)) and "y0='0.300*" in c)
rf = {re.search(r"x0='([0-9.]+)\*", render.zoom_filter(S(probability=100, focus="random"), rng, 4, 30)).group(1) for _ in range(30)}
check("random focus varies per picture", len(rf) > 10)
slow = float(re.search(r"in/([0-9.]+)\)", render.zoom_filter(S(probability=100, speed=1), rng, 4, 30)).group(1))
fast = float(re.search(r"in/([0-9.]+)\)", render.zoom_filter(S(probability=100, speed=100), rng, 4, 30)).group(1))
check("speed 1 spans the whole image time, speed 100 ~15% of it", abs(slow - 120) < 1 and abs(fast - 18) < 1, (slow, fast))
check("smoothness 0 is linear (no cubic term weight)", "(1-0.000)" in render.zoom_filter(S(probability=100, smoothness=0), rng, 4, 30))
check("quality high -> cubic, standard -> linear", "cubic" in render.zoom_filter(S(probability=100, quality="high"), rng, 4, 30) and "linear" in render.zoom_filter(S(probability=100, quality="standard"), rng, 4, 30))
for bad in ({"amount_min": 0}, {"amount_max": 101}, {"speed": 0}, {"focus": "nowhere"}):
    _, errs = core.update_settings({"effects": {"zoom": bad}})
    check(f"invalid zoom setting rejected: {bad}", errs)

# ---- render: zoom really zooms, black noise really darkens
root = tempfile.mkdtemp()
fixtures.make(root, 1, 3, 6)
core.update_settings({"paths": {"parent": root, "music_dir": root + "/_music"}, "video": {"preset": "ultrafast"}, "captions": {"enabled": False},
                      "transition": {"enabled": False}, "ending": {"enabled": False}, "music": {"enabled": False},
                      "effects": {"vignette": {"enabled": False}, "wobble": {"enabled": False}, "white_noise": {"enabled": False}, "black_noise": {"enabled": False}}})
scan.sync_projects(core.S(core.load_settings()))
proj = os.path.join(root, "set-001")

def render_preview(patch):
    core.update_settings({"effects": {"zoom": {"enabled": False}, "black_noise": {"enabled": False}}}); core.update_settings(patch)
    import shutil
    dst = os.path.join(tempfile.mkdtemp(), "p.mp4")  # previews overwrite one file, so keep a private copy
    shutil.copy(worker.make_preview(proj, 3)[0], dst)
    return dst

def frame(video, t):
    p = f"{tempfile.mkdtemp()}/f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", video, "-frames:v", "1", p], check=True)
    return p

def ssim_files(a, b):
    r = subprocess.run(["ffmpeg", "-i", a, "-i", b, "-lavfi", "ssim", "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"All:([0-9.]+)", r.stderr).group(1))

Z = lambda **k: {"effects": {"zoom": {"enabled": True, "probability": 100, "amount_min": 40, "amount_max": 40, **k}}}
flat_v = render_preview({})
slow_v = render_preview(Z(speed=1, smoothness=50))
fast_v = render_preview(Z(speed=100, smoothness=0))
zero_v = render_preview({"effects": {"zoom": {"enabled": True, "probability": 0}}})
f0 = frame(flat_v, 0)
check("first frame of a zoom starts unzoomed (same as no zoom)", ssim_files(f0, frame(slow_v, 0)) > 0.97, ssim_files(f0, frame(slow_v, 0)))
mid = frame(flat_v, 1.3)
s_slow, s_fast = ssim_files(mid, frame(slow_v, 1.3)), ssim_files(mid, frame(fast_v, 1.3))
check("zoom changes the picture over time", s_slow < 0.95, s_slow)
check("fast zoom has zoomed further than slow zoom at the same moment", s_fast < s_slow, (s_slow, s_fast))
check("probability 0 renders an unzoomed picture", ssim_files(mid, frame(zero_v, 1.3)) > 0.99)

def mean_luma(video):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", video, "-frames:v", "1", "-vf", "signalstats,metadata=mode=print:file=-", "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"YAVG=([0-9.]+)", r.stdout).group(1))
base = mean_luma(render_preview({}))
for name, kw in (("sharp fine grain", dict(size=1, softness=0)), ("big soft dust", dict(size=10, softness=100)), ("static", dict(speed="static")), ("static + flicker", dict(speed="static", flicker=70))):
    v = render_preview({"effects": {"black_noise": {"enabled": True, "intensity": 100, "opacity": 100, **kw}}})
    check(f"black noise '{name}' renders and darkens the picture", mean_luma(v) < base - 0.3, (base, mean_luma(v)))
off = render_preview({"effects": {"black_noise": {"enabled": True, "intensity": 100, "opacity": 0}}})
check("black noise at 0 opacity leaves picture unchanged", abs(mean_luma(off) - base) < 0.3)
sys.exit(0 if ok else 1)
