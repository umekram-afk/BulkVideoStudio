"""FFmpeg rendering engine: capability detection, filter-graph building, execution, validation."""
import functools, hashlib, json, os, random, re, shutil, subprocess, tempfile, threading, time
from pathlib import Path
from . import core, captions, dust as dustgen
from .core import StudioError, Cancelled

NOWIN = 0x08000000 if os.name == "nt" else 0
_still_cache_lock = threading.RLock()
RES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
XFADE = {"fade": "fade", "dissolve": "dissolve", "wipeleft": "wipeleft", "wiperight": "wiperight",
         "wipeup": "wipeup", "wipedown": "wipedown", "slideleft": "slideleft", "slideright": "slideright",
         "slideup": "slideup", "slidedown": "slidedown", "smoothleft": "smoothleft", "smoothright": "smoothright",
         "circleopen": "circleopen", "circleclose": "circleclose", "radial": "radial", "pixelize": "pixelize",
         "diagonal": "diagtl", "distance": "distance", "zoomin": "zoomin", "zoomout": None}
LABELS = {"fade": "Fade", "dissolve": "Dissolve", "wipeleft": "Wipe Left", "wiperight": "Wipe Right",
          "wipeup": "Wipe Up", "wipedown": "Wipe Down", "slideleft": "Slide Left", "slideright": "Slide Right",
          "slideup": "Slide Up", "slidedown": "Slide Down", "smoothleft": "Smooth Left",
          "smoothright": "Smooth Right", "circleopen": "Circle Open", "circleclose": "Circle Close",
          "radial": "Radial", "pixelize": "Pixelize", "diagonal": "Diagonal Wipe", "distance": "Distance",
          "zoomin": "Zoom In", "zoomout": "Zoom Out"}


def tool(name):
    local = core.ROOT / "ffmpeg" / "bin" / (name + (".exe" if os.name == "nt" else ""))
    return str(local) if local.exists() else (shutil.which(name) or name)


def _run(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NOWIN,
                          encoding="utf-8", errors="replace")


def parse_filter_list(text):
    """Names from `ffmpeg -filters`. Format-agnostic: a line is `<3 flag chars> <name> <io> <description>`; flag
    characters differ between builds (e.g. 'T.C', '.S.', '...'), so split on whitespace instead of matching them."""
    names = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 3 and re.fullmatch(r"\w+", parts[1]) and "->" in parts[2]:
            names.add(parts[1])
        elif len(parts) >= 4 and re.fullmatch(r"[A-Za-z.|]", parts[0]) and re.fullmatch(r"[A-Za-z.|]{1,2}", parts[1]) \
                and re.fullmatch(r"\w+", parts[2]) and "->" in parts[3]:
            names.add(parts[2])  # flags rendered with a space inside, e.g. '. S'
    return names


def probe_filter(name):
    """Ask FFmpeg directly whether a filter exists (works regardless of the -filters layout)."""
    r = _run([tool("ffmpeg"), "-hide_banner", "-h", f"filter={name}"])
    out = r.stdout + r.stderr
    return "Unknown filter" not in out and f"Filter {name}" in out


def parse_xfade(text):
    return set(re.findall(r"^\s+(\w+)\s+-?\d+\s+[.\w]*FV", text, re.M))


def probe_xfade(name):
    r = _run([tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=d=1:s=64x64", "-f", "lavfi", "-i", "color=d=1:s=64x64",
              "-filter_complex", f"[0][1]xfade=transition={name}:duration=0.4:offset=0.5", "-t", "0.6", "-f", "null", "-"])
    return r.returncode == 0


NEEDED_FILTERS = ("xfade", "format", "fps", "ass", "blend", "lut", "noise", "geq", "scale", "crop", "amix", "loudnorm",
                  "sidechaincompress", "alimiter", "afade", "apad", "atrim", "aresample", "aformat", "asplit")


@functools.lru_cache(maxsize=1)
def caps():
    c = dict(ok=False, version="", filters=set(), xfade=set(), hw=[], amix_normalize=False, filter_script="-filter_complex_script")
    try:
        c["version"] = _run([tool("ffmpeg"), "-hide_banner", "-version"]).stdout.splitlines()[0]
        _run([tool("ffprobe"), "-version"])
    except (OSError, IndexError, subprocess.SubprocessError):
        return c
    c["ok"] = True
    c["filters"] = parse_filter_list(_run([tool("ffmpeg"), "-hide_banner", "-filters"]).stdout)
    for n in NEEDED_FILTERS:  # parsing is a shortcut; the probe is the source of truth
        if n not in c["filters"] and probe_filter(n):
            c["filters"].add(n)
    c["xfade"] = parse_xfade(_run([tool("ffmpeg"), "-hide_banner", "-h", "filter=xfade"]).stdout)
    if not c["xfade"] and "xfade" in c["filters"]:
        c["xfade"] = {XFADE[t] for t in core.TRANSITION_IDS if XFADE[t] and probe_xfade(XFADE[t])}
    c["amix_normalize"] = "normalize" in _run([tool("ffmpeg"), "-hide_banner", "-h", "filter=amix"]).stdout
    # which option name loads a filter graph from a file (changed in FFmpeg 7)
    with tempfile.TemporaryDirectory() as d:
        g = Path(d) / "g.txt"
        g.write_text("color=d=0.1:s=64x64[o]")
        for opt in ("-filter_complex_script", "-/filter_complex"):
            r = _run([tool("ffmpeg"), "-v", "error", opt, str(g), "-map", "[o]", "-f", "null", "-"])
            if r.returncode == 0:
                c["filter_script"] = opt
                break
    enc = _run([tool("ffmpeg"), "-hide_banner", "-encoders"]).stdout
    for e in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if e in enc:
            r = _run([tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.2", "-c:v", e, "-f", "null", "-"])
            if r.returncode == 0:
                c["hw"].append(e)
    return c


def caps_public():
    c = caps()
    return dict(ok=c["ok"], version=c["version"], hw=c["hw"], xfade=sorted(c["xfade"]), amix_normalize=c["amix_normalize"],
                libass="ass" in c["filters"])


def transition_list():
    c = caps()
    out = []
    for tid in core.TRANSITION_IDS:
        ff = XFADE[tid]
        ok = bool(ff and ff in c["xfade"])
        out.append(dict(id=tid, label=LABELS[tid], available=ok,
                        reason="" if ok else ("FFmpeg's xfade filter has no zoom-out transition" if ff is None
                                              else "Not supported by the installed FFmpeg build")))
    return out


def need_filters(*names):
    missing = [n for n in names if n not in caps()["filters"]]
    if missing:
        raise StudioError("unsupported_filter", f"The installed FFmpeg lacks required filter(s): {', '.join(missing)}")


# ---------------------------------------------------------------- probing
_dur = {}


def duration(path):
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _dur:
        r = _run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", path])
        try:
            d = float(json.loads(r.stdout)["format"]["duration"])
        except (ValueError, KeyError, TypeError):
            raise StudioError("corrupted_file", f"Cannot read media duration: {Path(path).name}", r.stderr[-400:])
        if d <= 0:
            raise StudioError("corrupted_file", f"Media has no duration: {Path(path).name}")
        _dur[key] = d
    return _dur[key]


_sil = {}


def silences(path, noise_db=-35, min_dur=0.08):
    """[(start, end)] of silent stretches in an audio file (FFmpeg silencedetect), cached."""
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _sil:
        r = _run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", path, "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"], timeout=300)
        starts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", r.stderr)]
        ends = [float(x) for x in re.findall(r"silence_end: (-?[0-9.]+)", r.stderr)]
        _sil[key] = list(zip(starts, ends))  # a trailing unmatched start (silence until EOF) is ignored
    return _sil[key]


# ---------------------------------------------------------------- process runner
def run_ffmpeg(args, cwd, logfile, total, on_progress=None, reg=None, stall=600):
    """Run FFmpeg, stream -progress, write stderr to logfile. Returns return code."""
    with open(logfile, "ab") as lf:
        lf.write(("\n$ " + subprocess.list2cmdline(args) + "\n").encode("utf-8", "replace"))
        lf.flush()
        p = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=lf, text=True, creationflags=NOWIN,
                             stdin=subprocess.DEVNULL)
        if reg:
            reg(p)
        last = [time.time()]
        done = threading.Event()

        def watchdog():
            while not done.wait(5):
                if time.time() - last[0] > stall:
                    lf.write(b"\n[studio] FFmpeg stalled - killed by watchdog\n")
                    p.kill()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            for line in p.stdout:
                last[0] = time.time()
                m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
                if m and on_progress and total:
                    on_progress(min(0.99, int(m.group(1)) / 1e6 / total))
        finally:
            p.wait()
            done.set()
    return p.returncode


def _tail(logfile, n=1500):
    try:
        return Path(logfile).read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


# ---------------------------------------------------------------- building blocks
def _ass_filter(filename):
    """Expose Windows system fonts to libass so selected font families resolve consistently."""
    if os.name == "nt":
        fonts = (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts").as_posix().replace(":", r"\:")
        return f"ass={filename}:fontsdir='{fonts}'"
    return f"ass={filename}"


def _fit_filter(S, W, H):
    fit = S.video.fit
    if fit == "crop":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=rgb24"
    if fit == "solid":
        bg = "0x" + S.video.bg_color.lstrip("#")
        return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={bg},"
                f"setsar=1,format=rgb24")
    return (f"format=rgba,split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"scale={W // 8}:{H // 8},gblur=sigma=6,scale={W}:{H},eq=brightness=-0.06[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=rgb24")


def prepare_still(img, out, S, W, H, logfile):
    """Reuse fitted stills across renders; source mtime/size and fit settings invalidate the cache."""
    source = Path(img)
    try:
        st = source.stat()
        key_data = (str(source.resolve()), st.st_size, st.st_mtime_ns, W, H, S.video.fit, S.video.bg_color)
        key = hashlib.sha1(repr(key_data).encode("utf-8", "replace")).hexdigest()
        cache_dir = core.DATA / "cache" / "stills"
        cached = cache_dir / f"{key}.png"
        with _still_cache_lock:
            if cached.is_file() and cached.stat().st_size > 1000:
                shutil.copy2(cached, out)
                return
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Do not hold the cache lock while FFmpeg runs: different projects must
        # prepare images concurrently when batch parallelism is enabled.
        temp = cache_dir / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp.png"
        r = subprocess.run([tool("ffmpeg"), "-y", "-v", "error", "-threads", "1", "-i", str(source), "-vf", _fit_filter(S, W, H), "-frames:v", "1",
                            "-c:v", "png", "-compression_level", "1", str(temp)], capture_output=True, text=True,
                           timeout=90, creationflags=NOWIN, encoding="utf-8", errors="replace")
        if r.returncode or not temp.exists() or temp.stat().st_size < 1000:
            temp.unlink(missing_ok=True)
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(f"\n[prepare {img}]\n{r.stderr}")
            raise StudioError("corrupted_file", f"Cannot decode image: {source.name}", r.stderr[-500:])
        with _still_cache_lock:
            if cached.is_file() and cached.stat().st_size > 1000:
                temp.unlink(missing_ok=True)
            else:
                os.replace(temp, cached)
            shutil.copy2(cached, out)
    except subprocess.TimeoutExpired:
        raise StudioError("render_failure", f"Image preparation timed out: {source.name}")
    except OSError as e:
        raise StudioError("render_failure", f"Could not prepare image {source.name}: {e}")


def vignette_mask(S, W, H):
    """Gray multiply-mask (white centre, darker edges), cached on disk per parameter set."""
    v = S.effects.vignette
    key = hashlib.sha1(f"m{W}x{H}|{v.intensity}|{v.range}|{v.feather}".encode()).hexdigest()[:16]
    out = core.DATA / "cache" / f"vig_{key}.png"
    if out.exists():
        return str(out)
    r_in = 0.75 - 0.6 * v.range / 100
    r_out = min(1.25, r_in + 0.25 + 0.9 * v.feather / 100)
    dark = 255 * 0.85 * v.intensity / 100
    expr = f"255-{dark:.2f}*pow(clip((hypot((X-W/2)/(W/2),(Y-H/2)/(H/2))/1.4142-{r_in:.3f})/{r_out - r_in:.3f},0,1),2)"
    r = _run([tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}",
              "-vf", f"format=gray,geq=lum='{expr}'", "-frames:v", "1", str(out)], timeout=120)
    if r.returncode:
        raise StudioError("render_failure", "Could not generate vignette", r.stderr[-500:])
    return str(out)


def _noise_chain(kind, intensity, opacity, W, H):
    """Sparse grain layer from a half-size noise source (coarser, film-like grain). Works on raw YUV luma so a
    neutral pixel is exactly 0 (screen layer) or 255 (multiply layer) and leaves the picture untouched.
    FFmpeg's noise filter output spans ~76..175 at alls=100; opacity is folded into the layer values."""
    f, op = intensity / 100 * 0.12, opacity / 100  # f = fraction of pixels carrying a speck at full intensity
    if kind == "white":
        thr = 175 - 99 * f
        lut = f"if(gt(val,{thr:.1f}),(val-{thr:.1f})*{255 * op / (175 - thr):.3f},0)"
    else:
        thr = 76 + 99 * f
        lut = f"if(lt(val,{thr:.1f}),255-({thr:.1f}-val)*{255 * op / (thr - 76):.3f},255)"
    return f"format=yuv420p,noise=alls=100:allf=t+u,lut=y='{lut}',scale={W}:{H}:flags=neighbor"


def black_noise_chain(cfg, W, H, fps):
    """Black speckle layer (multiply). Built in the 'darkness' domain (neutral = 0) so softness, flicker and
    inversion are exact; runs at reduced resolution (grain size) and is scaled up, so it is cheap.
    Returns (filter chain, source frame rate)."""
    f, op = cfg.intensity / 100 * 0.12, cfg.opacity / 100
    thr = 76 + 99 * f
    ch = ["format=yuv420p"]
    static = cfg.speed == "static"
    ch.append("noise=alls=100:allf=u:all_seed=7" if static else "noise=alls=100:allf=t+u")
    ch.append(f"lut=y='if(lt(val,{thr:.1f}),({thr:.1f}-val)*{255 * op / (thr - 76):.3f},0)'")
    if cfg.softness:
        ch.append(f"gblur=sigma={0.4 + cfg.softness / 100 * 2.6:.2f}:steps=1")
    if cfg.flicker:  # randomly dips the layer's strength over time (film flicker)
        k = f"(1-{cfg.flicker / 100:.2f}*abs(sin(t*37.1)*sin(t*11.3+1.7)))"
        ch.append(f"eq=contrast='{k}':brightness='0.5*{k}-0.5':eval=frame")
    ch.append("lut=y='negval'")
    ch.append(f"scale={W}:{H}:flags={'bicubic' if cfg.softness else 'neighbor'}")
    rate = {"slow": "4", "normal": "12", "fast": str(fps), "static": "12" if cfg.flicker else "1"}[cfg.speed]
    return ",".join(ch), rate


def zoom_filter(S, rng, clip_len, fps, force=False):
    """Per-image smooth zoom (sub-pixel, via the perspective filter). Returns '' or a filter string ending in ','."""
    z = S.effects.zoom
    if not z.enabled or (not force and rng.random() * 100 >= z.probability):
        return ""
    lo, hi = sorted((z.amount_min, z.amount_max))
    amt = rng.uniform(lo, hi) / 100
    d = rng.choice(["in", "out"]) if z.direction == "random" else z.direction
    if z.focus == "random":
        fx, fy = rng.uniform(.2, .8), rng.uniform(.2, .8)
    elif z.focus == "custom":
        fx, fy = z.focus_x / 100, z.focus_y / 100
    else:
        fx, fy = core.FOCUS_POINTS[z.focus]
    frac = 1 - 0.85 * (z.speed - 1) / 99  # speed 1 = zoom lasts the whole image time, 100 = about 15% of it
    frames = max(3.0, fps * clip_len * frac)
    sm = z.smoothness / 100
    p = f"min(1,in/{frames:.2f})"
    e = f"((1-{sm:.3f})*{p}+{sm:.3f}*pow({p},3)*({p}*(6*{p}-15)+10))"  # linear -> smootherstep
    zz = f"(1+{amt:.4f}*{e})" if d == "in" else f"(1+{amt:.4f}*(1-{e}))"
    w, h = f"(W/{zz})", f"(H/{zz})"
    x0, y0 = f"{fx:.3f}*(W-{w})", f"{fy:.3f}*(H-{h})"
    return (f"perspective=x0='{x0}':y0='{y0}':x1='{x0}+{w}':y1='{y0}':x2='{x0}':y2='{y0}+{h}':x3='{x0}+{w}':y3='{y0}+{h}'"
            f":sense=source:eval=frame:interpolation={'cubic' if z.quality == 'high' else 'linear'},")


def pick_transitions(S, count, rng):
    c = caps()
    if not S.transition.enabled:
        return ["fade"] * count, True
    if S.transition.random:
        pool = [t["id"] for t in transition_list() if t["available"]]
        if S.transition.random_scope == "per_project":
            return [rng.choice(pool)] * count, False
        return [rng.choice(pool) for _ in range(count)], False
    name = S.transition.name
    if not XFADE[name] or XFADE[name] not in c["xfade"]:
        raise StudioError("unsupported_filter", f"Transition '{LABELS[name]}' is not supported by the installed FFmpeg",
                          "Choose another transition in Effects & Transitions.")
    return [name] * count, False


def hw_plan(S):
    """Ordered list of encoders to try; None = CPU. Auto: GPU if one works, else CPU. GPU: forced GPU (CPU only if it
    fails). CPU: never use the GPU."""
    c = caps()
    if S.video.hwaccel == "cpu" or not c["hw"]:
        return [None]
    pick = S.video.hw_encoder if S.video.hw_encoder in c["hw"] else c["hw"][0]
    return [pick, None]


def encoder_args(S, hw):
    crf = S.video.crf
    if hw == "h264_nvenc":
        return ["-c:v", hw, "-preset", "p3", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if hw == "h264_qsv":
        return ["-c:v", hw, "-global_quality", str(crf), "-preset", "medium"]
    if hw == "h264_amf":
        return ["-c:v", hw, "-quality", "quality", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf), "-qp_b", str(crf)]
    return ["-c:v", "libx264", "-preset", S.video.preset, "-crf", str(crf)]


def audio_graph(P, S, idx_voice, idx_music, T, E, preview=False):
    """Returns (filter lines, output label) for the mixed audio, or (None, None) if there is no audio source."""
    f, Tv = [], P["duration"]
    has_v, m = P.get("voice") and S.voice.enabled, P.get("music")
    if has_v:
        v = S.voice
        ch = ["aresample=44100,aformat=channel_layouts=stereo"]
        if v.normalize:
            ch.append("loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100")
        ch.append(f"volume={v.volume / 100:.3f}")
        if v.fade_in:
            ch.append(f"afade=t=in:d={v.fade_in}")
        if v.fade_out:
            ch.append(f"afade=t=out:st={max(0, Tv - v.fade_out):.3f}:d={min(v.fade_out, Tv)}")
        ch.append(f"apad=whole_dur={T:.3f}")
        f.append(f"[{idx_voice}:a]" + ",".join(ch) + "[voice]")
    if m:
        mu = S.music
        ch = ["aresample=44100,aformat=channel_layouts=stereo", f"atrim=start={m['start']:.3f},asetpts=PTS-STARTPTS",
              f"atrim=duration={T:.3f}", f"volume={mu.volume / 100:.3f}"]
        if mu.fade_in:
            ch.append(f"afade=t=in:d={mu.fade_in}")
        if mu.fade_out:
            ch.append(f"afade=t=out:st={max(0, T - mu.fade_out):.3f}:d={min(mu.fade_out, T)}")
        f.append(f"[{idx_music}:a]" + ",".join(ch) + "[music]")
    if has_v and m:
        mixname = "amix=inputs=2:duration=longest:dropout_transition=0" + (":normalize=0" if caps()["amix_normalize"] else ",volume=2")
        if S.music.ducking:
            ratio = 2 + S.music.duck_amount / 100 * 10
            f.append("[voice]asplit=2[vm][vs]")
            f.append(f"[music][vs]sidechaincompress=threshold=0.03:ratio={ratio:.1f}:attack=20:release=400[md]")
            f.append(f"[vm][md]{mixname}[mix]")
        else:
            f.append(f"[voice][music]{mixname}[mix]")
        last = "mix"
    elif has_v:
        last = "voice"
    elif m:
        last = "music"
    else:
        return None, None
    tail = ["alimiter=limit=0.97"]
    if E and S.ending.fade_audio:
        tail.append(f"afade=t=out:st={Tv:.3f}:d={E:.3f}")
    f.append(f"[{last}]" + ",".join(tail) + "[aout]")
    return f, "aout"


def build(P, S, work, rng, logfile):
    """Prepare stills and build the FFmpeg input list + filter graph. Returns (inputs, graph_lines, T, has_audio)."""
    W, H = RES[S.video.aspect]
    fps = S.video.fps
    segs, Tv = P["segments"], P["duration"]
    E = S.ending.duration if (S.ending.enabled and not P.get("cut")) else 0.0
    T = Tv + E
    n = len(segs)
    need_filters("xfade", "format", "fps")
    inputs, g = [], []

    # video clips: stills, then optional black end clip
    for i, s in enumerate(segs):
        still = work / f"img_{i}.png"
        prepare_still(s["image"], still, S, W, H, logfile)
    clips = n + (1 if E else 0)
    L = [segs[i + 1]["start"] - segs[i]["start"] if i + 1 < n else Tv - segs[i]["start"] for i in range(n)]
    if E:
        L.append(E)
    names, no_trans = pick_transitions(S, clips - 1, rng)
    req = S.transition.duration
    bounds = [segs[i + 1]["start"] for i in range(n - 1)] + ([Tv] if E else [])
    fac = {"before": 1.0, "center": 0.5, "after": 0.0}[S.transition.timing]
    d, o = [], []
    for j, b in enumerate(bounds):
        end_fade = bool(E) and j == len(bounds) - 1
        if end_fade:
            dj, oj = (min(1.0, E / 2) if S.ending.fade_video else 0.04), b
        else:
            dj = 0.04 if no_trans else min(req, 0.45 * min(L[j], L[j + 1]))
            oj = b - fac * dj
        d.append(max(0.04, dj)); o.append(oj)
    for k in range(clips):
        start = o[k - 1] if k else 0.0
        end = (o[k] + d[k]) if k < clips - 1 else T
        ln = end - start + 0.3
        if k < n:
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{ln:.3f}", "-i", str(work / f"img_{k}.png")]
        else:
            inputs += ["-f", "lavfi", "-t", f"{ln:.3f}", "-i", f"color=c=black:s={W}x{H}:r={fps}"]
        zf = zoom_filter(S, rng, ln, fps, force=(k == 0 and S.effects.zoom.first_image)) if k < n else ""
        g.append(f"[{k}:v]format=yuv420p,{zf}fps={fps},setsar=1,settb=AVTB[c{k}]")
    cur = "c0"
    for j in range(clips - 1):
        tr = "fade" if (E and j == clips - 2) else names[j]
        g.append(f"[{cur}][c{j + 1}]xfade=transition={XFADE[tr]}:duration={d[j]:.3f}:offset={o[j]:.3f}[x{j}]")
        cur = f"x{j}"
    nin = clips
    ef = S.effects

    # wobble (gentle sway of an enlarged frame)
    if ef.wobble.enabled and ef.wobble.intensity > 0:
        margin = (0.01 + 0.05 * ef.wobble.range / 100) * W
        W2 = int(round((W + 2 * margin) / 2) * 2)
        H2 = int(round(H * W2 / W / 2) * 2)
        A = ((W2 - W) / 2) * ef.wobble.intensity / 100
        fr = {"slow": 0.12, "normal": 0.25, "fast": 0.5}[ef.wobble.speed]
        tw = 6.2832 * fr
        x = f"(iw-ow)/2+{A:.2f}*(0.7*sin({tw:.4f}*t)+0.3*sin({tw * 1.9:.4f}*t+1.3))"
        y = f"(ih-oh)/2+{A * H / W:.2f}*(0.7*cos({tw * 0.8:.4f}*t+0.5)+0.3*sin({tw * 2.3:.4f}*t))"
        g.append(f"[{cur}]scale={W2}:{H2}:flags=bicubic,crop={W}:{H}:x='{x}':y='{y}'[wob]")
        cur = "wob"
    if ef.vignette.enabled and ef.vignette.intensity > 0:
        need_filters("blend", "geq")
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{T + 1:.3f}", "-i", vignette_mask(S, W, H)]
        g.append(f"[{nin}:v]format=yuv420p,lut=y='clip((val-16)*255/219,0,255)'[mk{nin}]")  # undo limited-range squeeze
        g.append(f"[mk{nin}][{cur}]blend=c0_mode=multiply:c1_expr='B':c2_expr='B':shortest=1[vig]")  # luma only; video is 2nd input
        cur, nin = "vig", nin + 1
    for kind, cfg, mode in (("white", ef.white_noise, "screen"), ("black", ef.black_noise, "multiply")):
        if cfg.enabled and cfg.intensity > 0 and cfg.opacity > 0:
            need_filters("noise", "blend", "lut", "eq", "gblur")
            size = cfg.size if kind == "black" else 2
            sw, sh = 2 * -(-W // (2 * size)), 2 * -(-H // (2 * size))  # even source size (grain size in output pixels)
            if kind == "black":
                chain, rate = black_noise_chain(cfg, W, H, fps)
            else:
                chain, rate = _noise_chain(kind, cfg.intensity, cfg.opacity, W, H), {"slow": 4, "normal": 12, "fast": fps}[cfg.speed]
            inputs += ["-f", "lavfi", "-t", f"{T + 1:.3f}", "-i", f"color=c=0x808080:s={sw}x{sh}:r={rate}"]
            g.append(f"[{nin}:v]{chain}[n{nin}]")
            g.append(f"[n{nin}][{cur}]blend=c0_mode={mode}:c1_expr='B':c2_expr='B':shortest=1[b{nin}]")
            cur, nin = f"b{nin}", nin + 1
    dust = ef.dust
    if dust.enabled and dust.opacity > 0:
        need_filters("ass")
        drng = random.Random(rng.random() if dust.random_per_video else 1234)
        (work / "dust.ass").write_text(dustgen.make_dust_ass(dust, W, H, T, drng), encoding="utf-8")
        g.append(f"[{cur}]{_ass_filter('dust.ass')}[dust]")
        cur = "dust"
    if S.captions.enabled and P.get("words"):
        need_filters("ass")
        (work / "captions.ass").write_text(captions.make_ass(P["words"], S, W, H, Tv, progressive=P.get("voice_captions", False),
                                                              image_starts=[s["start"] for s in P["segments"]]), encoding="utf-8")
        g.append(f"[{cur}]{_ass_filter('captions.ass')}[cap]")
        cur = "cap"
    if S.start.enabled and P.get("start_text"):
        need_filters("ass")
        (work / "start.ass").write_text(captions.make_start_ass(P["start_text"], S, W, H, Tv), encoding="utf-8")
        g.append(f"[{cur}]{_ass_filter('start.ass')}[start]")
        cur = "start"
    g.append(f"[{cur}]format=yuv420p[vout]")

    # audio inputs
    iv = im = None
    if P.get("voice") and S.voice.enabled:
        inputs += ["-i", P["voice"]]; iv = nin; nin += 1
    if P.get("music"):
        inputs += ["-stream_loop", "-1", "-i", P["music"]["file"]]; im = nin; nin += 1
    ag, _ = audio_graph(P, S, iv, im, T, E)
    if ag is None:
        inputs += ["-f", "lavfi", "-t", f"{T:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        ag = [f"[{nin}:a]anull[aout]"]
    return inputs, g + ag, T


def render(P, S, work, out_part, ctl, logfile, rng=None, on_progress=None):
    """Render plan P to out_part. Returns expected duration. ctl: object with reg(proc) and cancelled()."""
    c = caps()
    if not c["ok"]:
        raise StudioError("ffmpeg_missing", "FFmpeg/ffprobe not found")
    rng = rng or random.Random()
    work.mkdir(parents=True, exist_ok=True)
    inputs, graph, T = build(P, S, work, rng, logfile)
    (work / "graph.txt").write_text(";\n".join(graph), encoding="utf-8")
    hw = hw_plan(S)
    if hw and hw[0]:
        core.log("info", P["proj"]["name"], "", f"Video encoding: {hw[0]} (GPU); image scaling, transitions and filters remain CPU-based")
    else:
        core.log("info", P["proj"]["name"], "", "Video encoding: libx264 (CPU)")
    if S.video.hwaccel == "gpu" and hw == [None]:
        core.log("warn", P["proj"]["name"], "", "GPU encoding was requested but no working GPU encoder was found - using CPU")
    # FFmpeg otherwise starts a large filter/encoder thread pool for every
    # simultaneous project. Divide available CPU threads across active workers.
    workers = max(1, int(S.batch.workers))
    cpu_threads = max(1, (os.cpu_count() or 2) // workers)
    for attempt, enc in enumerate(hw):
        args = [tool("ffmpeg"), "-y", "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-filter_complex_threads", str(cpu_threads), "-progress", "pipe:1", "-nostats",
                *inputs, c["filter_script"], "graph.txt", "-map", "[vout]", "-map", "[aout]",
                "-threads", str(cpu_threads), *encoder_args(S, enc),
                "-pix_fmt", "yuv420p", "-r", str(S.video.fps), "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-movflags", "+faststart", "-t", f"{T:.3f}", "-f", "mp4", str(out_part)]
        rc = run_ffmpeg(args, str(work), logfile, T, on_progress, ctl.reg)
        if ctl.cancelled():
            raise Cancelled(ctl.cancelled())
        if rc == 0:
            return T
        if enc:
            core.log("warn", P["proj"]["name"], "render_failure", f"Hardware encoder {enc} failed - retrying on CPU")
    raise StudioError("render_failure", f"FFmpeg exited with code {rc}", _tail(logfile))


def validate(path, expected, S, want_audio=True):
    p = Path(path)
    if not p.exists() or p.stat().st_size < 10_000:
        raise StudioError("validation_failure", "Output file missing or too small")
    r = _run([tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
              "-of", "json", str(p)])
    try:
        info = json.loads(r.stdout)
        streams = {s["codec_type"]: s for s in info["streams"]}
        dur = float(info["format"]["duration"])
    except (ValueError, KeyError):
        raise StudioError("validation_failure", "Output could not be read by ffprobe", r.stderr[-400:])
    W, H = RES[S.video.aspect]
    v = streams.get("video")
    if not v or v.get("codec_name") != "h264" or (v["width"], v["height"]) != (W, H):
        raise StudioError("validation_failure", f"Unexpected video stream: {v}")
    if want_audio and "audio" not in streams:
        raise StudioError("validation_failure", "Output has no audio stream")
    if abs(dur - expected) > max(0.6, expected * 0.02):
        raise StudioError("validation_failure", f"Duration {dur:.2f}s differs from expected {expected:.2f}s")
    t = _run([tool("ffmpeg"), "-v", "error", "-sseof", "-1.5", "-i", str(p), "-f", "null", "-"], timeout=120)
    if t.returncode or t.stderr.strip():
        raise StudioError("validation_failure", "Decode check of the output tail failed", t.stderr[-400:])
    return dur


class NoCtl:
    def reg(self, p): pass
    def cancelled(self): return None
