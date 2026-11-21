"""Planning: script -> sentences -> word timings -> image timeline; music selection."""
import difflib, os, random, re
from pathlib import Path
from . import core, scan, captions, render, voicesync
from .core import StudioError

ABBREV = ("mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "prof.", "etc.", "e.g.", "i.e.", "no.")
MIN_GAP = 0.3  # minimum seconds an image stays on screen


def split_sentences(text):
    out = []
    for line in re.split(r"[\r\n]+", text):
        acc = []
        parts = re.split(r'(?<=[.!?…؟。！？]["”’)\]])\s+|(?<=[.!?…؟。！？])\s+|(?<=[。！？])', line.strip())
        for p in (x.strip() for x in parts if x and x.strip()):
            if acc and acc[-1].lower().endswith(ABBREV):
                acc[-1] += " " + p  # don't split after abbreviations like "Dr."
            else:
                acc.append(p)
        out += acc
    return out


def norm(w):
    return re.sub(r"[\W_]+", "", w.lower())


def estimate(sw, lo, hi):
    """Spread words over [lo, hi] proportionally to their length."""
    tot = sum(len(w) + 1 for w, _ in sw) or 1
    t, out = lo, []
    for w, _ in sw:
        d = (len(w) + 1) / tot * (hi - lo)
        out.append((t, t + d))
        t += d
    return out


def align(sw, tw, total):
    """Match script words to transcript words (difflib). Returns (times per script word, match ratio)."""
    a = [norm(w) or f"\0{i}" for i, (w, _) in enumerate(sw)]
    b = [norm(w["word"]) or f"\1{i}" for i, w in enumerate(tw)]
    t, hit = [None] * len(a), 0
    for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(m.size):
            w = tw[m.b + k]
            t[m.a + k] = (w["start"], w["end"])
            hit += 1
    i = 0
    while i < len(t):
        if t[i] is None:
            j = i
            while j < len(t) and t[j] is None:
                j += 1
            lo = t[i - 1][1] if i else 0.0
            hi = max(t[j][0] if j < len(t) else total, lo)
            step = (hi - lo) / (j - i)
            for k in range(i, j):
                t[k] = (lo + (k - i) * step, lo + (k - i + 1) * step)
            i = j
        else:
            i += 1
    return t, hit / max(1, len(a))


def transcript_sentences(words):
    """Sentences (text, start, end) built from transcript words (used when there is no script)."""
    out, cur = [], []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] > 1.2:
            out.append(cur); cur = []
        cur.append(w)
        if re.search(r"[.!?…。！？؟]$", w["word"]):
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return [(" ".join(x["word"] for x in s), s[0]["start"], s[-1]["end"]) for s in out]


def image_starts(sent_starts, total, m, mode, group):
    """Start time for each image (len <= m). Images keep their order."""
    n = len(sent_starts)
    if m == 0:
        return []
    if mode == "even" or n == 0:
        return [i * total / m for i in range(m)]
    if mode == "group":
        return [sent_starts[j * group] for j in range(m) if j * group < n]
    if m <= n:
        return [sent_starts[(j * n) // m] for j in range(m)]
    ends = sent_starts[1:] + [total]
    dur = [e - s for s, e in zip(sent_starts, ends)]
    k = [1] * n
    order = sorted(range(n), key=lambda i: (-dur[i], i))
    for x in range(m - n):  # give extra images to the longest sentences
        k[order[x % n]] += 1
    starts = []
    for i in range(n):
        starts += [sent_starts[i] + t * dur[i] / k[i] for t in range(k[i])]
    return starts


def enforce_gaps(starts, total, warnings):
    out = []
    for s in starts:
        s = max(s, out[-1] + MIN_GAP) if out else 0.0
        if s > total - MIN_GAP:
            break
        out.append(s)
    if len(out) < len(starts):
        warnings.append(f"{len(starts) - len(out)} image(s) dropped: not enough time for them at this voice length")
    return out


def pick_music(S, ov, record=False, rng=None):
    if not S.music.enabled:
        return None
    rng = rng or random.Random()
    lib = scan.music_files(S.paths.music_dir)
    wanted = (ov or {}).get("music_file")
    track = None
    if not lib and not wanted and not S.music.required:
        return None  # music is optional: render without it
    if wanted:
        track = next((f for f in lib if Path(f).name == wanted), None)
        if not track:
            raise StudioError("missing_music", f"Selected music file not found: {wanted}")
    else:
        if not lib:
            raise StudioError("missing_music", "No music files found in the music folder" if S.paths.music_dir
                              else "No music folder selected")
        if S.music.allow_repeat:
            pool = lib
        else:
            used = set(core.kv_get("music_used", []))
            pool = [f for f in lib if f not in used]
            if not pool:
                core.kv_set("music_used", [])
                pool = lib
        track = rng.choice(pool)
    if record and not S.music.allow_repeat:
        core.kv_set("music_used", list(set(core.kv_get("music_used", [])) | {track}))
    try:
        dur = render.duration(track)
    except StudioError as e:
        raise StudioError("corrupted_file", f"Music file unreadable: {Path(track).name}", e.detail)
    warn = None
    if S.music.random_start:
        lo = min(S.music.min_start, dur - 1)
        hi = min(S.music.max_start, dur - 1)
        lo, hi = max(0.0, lo), max(0.0, hi)
        start = rng.uniform(min(lo, hi), max(lo, hi))
    else:
        start = S.music.manual_start
        if start > dur - 1:
            warn = f"Music start {start:.0f}s is beyond the track length ({dur:.0f}s) - using 0"
            start = 0.0
    return dict(file=track, name=Path(track).name, start=round(start, 2), duration=dur, warning=warn)


def build_plan(path, S, ov=None, transcribe=True, music=True, record_music=False, rng=None, ctl=None):
    if ctl:
        ctl.check()
    ov = ov or {}
    pr = scan.scan_project(path, S, ov)
    block = [i for i in pr["issues"] if i["blocking"]]
    if block:
        raise StudioError(block[0]["cat"], block[0]["msg"])
    images, warnings = pr["images"], []
    for i in pr["issues"]:
        if not i["blocking"] and i["cat"] != "missing_script":
            warnings.append(i["msg"])
    voice = pr["voice"] if S.voice.enabled else None
    vdur = render.duration(voice) if voice else None
    script = scan.read_text(pr["script"]) if pr["script"] else ""
    sentences = split_sentences(script)
    if script.strip() and not sentences:
        sentences = [script.strip()]

    tr = None
    need = voice and transcribe and (not sentences or S.captions.enabled or S.sync.mode != "even" or S.sync.method == "voice")
    if need:
        if ctl:
            ctl.check()
        if captions.available():
            try:
                tr = captions.transcribe(voice, S, ctl=ctl)
                if ctl:
                    ctl.check()
            except StudioError as e:
                if not sentences:
                    raise
                warnings.append(f"Transcription failed ({e.message}) - timing is estimated from the script")
        elif not sentences:
            raise StudioError("missing_script", "No script and faster-whisper is not installed, so the voice cannot be transcribed")
        else:
            warnings.append("faster-whisper is not installed - timing is estimated from the script")
    twords = tr["words"] if tr else []
    if twords and voice:  # keep word times in step with the real audio (pauses measured with silencedetect)
        try:
            twords = captions.refine_words(twords, render.silences(voice))
        except Exception:
            pass
    Tv = vdur if voice else len(images) * S.video.image_seconds
    sent_list, cap_words, score, estimated = [], [], None, False

    if voice and sentences:
        sw = [(w, si) for si, s in enumerate(sentences) for w in s.split()]
        if twords:
            times, score = align(sw, twords, Tv)
            if score * 100 < S.sync.mismatch_threshold:
                msg = f"Script/audio mismatch: only {score * 100:.0f}% of script words were found in the speech"
                if S.sync.mismatch_action == "fail" and S.sync.method == "manual":
                    raise StudioError("script_mismatch", msg)
                if not (S.sync.method == "voice" and (not S.captions.enabled or S.captions.voice_sync)):  # script is not used then
                    warnings.append(msg + " - timing estimated; captions follow the spoken words")
                times, estimated = estimate(sw, twords[0]["start"], twords[-1]["end"]), True
                cap_words = twords
            else:
                cap_words = [dict(word=w, start=a, end=b) for (w, _), (a, b) in zip(sw, times)]
        else:
            times, estimated = estimate(sw, 0.0, Tv), True
            cap_words = [dict(word=w, start=a, end=b) for (w, _), (a, b) in zip(sw, times)]
        for si, s in enumerate(sentences):
            idx = [k for k, (_, x) in enumerate(sw) if x == si]
            if idx:
                sent_list.append(dict(text=s, start=times[idx[0]][0], end=times[idx[-1]][1]))
    elif voice and twords:
        sent_list = [dict(text=t, start=a, end=b) for t, a, b in transcript_sentences(twords)]
        cap_words = twords
    elif not voice and sentences and S.captions.enabled:  # no voice-over: spread the script evenly over the video
        sw = [(w, si) for si, s in enumerate(sentences) for w in s.split()]
        cap_words = [dict(word=w, start=a, end=b) for (w, _), (a, b) in zip(sw, estimate(sw, 0.0, Tv))]
        warnings.append("No voice-over: caption timing is spread evenly over the video")
    if ctl:
        ctl.check()
    for i in range(len(sent_list)):  # make sentence starts monotonic
        if i and sent_list[i]["start"] < sent_list[i - 1]["start"]:
            sent_list[i]["start"] = sent_list[i - 1]["start"]

    w_manual = len(warnings)  # warnings produced by the manual (script-based) distribution start here
    mode = S.sync.mode if sent_list else "even"
    if voice and not sent_list and S.sync.mode != "even":
        warnings.append("No sentence timing available - images are distributed evenly")
    starts = image_starts([s["start"] for s in sent_list], Tv, len(images), mode, S.sync.group_size)
    if len(starts) < len(images):
        warnings.append(f"{len(images) - len(starts)} image(s) unused in this sync mode")
    starts = enforce_gaps(starts, Tv, warnings)
    sync_info = None
    if S.sync.method == "voice" and voice:  # the real narration decides when each image appears
        if twords:
            vs = voicesync.match_images(images, twords, S.sync.match_threshold / 100, S.sync.min_hold, S.sync.lead_ms / 1000,
                                        S.sync.unmatched, Tv, synonym_file=str(core.DATA / "synonyms.txt"))
            sync_info = vs["info"]
            if vs["ok"]:
                del warnings[w_manual:]  # script-based distribution is not used, so its notes do not apply
                images, starts, mode = vs["images"], vs["starts"], "voice"
                warnings += vs["warnings"]
                starts = enforce_gaps(starts, Tv, warnings)
            else:
                warnings += vs["warnings"] + ["Voice-based image synchronisation was not possible - using manual distribution instead"]
        else:
            warnings.append("Voice-based image synchronisation needs the narration transcribed (Faster-Whisper) - using manual distribution instead")
    manual = ov.get("manual_starts")
    if manual:
        if len(manual) == len(starts) and all(a < b for a, b in zip(manual, manual[1:])) and manual[0] == 0 and manual[-1] < Tv:
            starts, mode = [float(x) for x in manual], "manual"
        else:
            warnings.append("Manual image timing ignored: it no longer matches the number of images")
    segs = [dict(image=images[i], start=s, end=(starts[i + 1] if i + 1 < len(starts) else Tv)) for i, s in enumerate(starts)]

    if ctl:
        ctl.check()
    pick = None
    if music:
        pick = pick_music(S, ov, record=record_music, rng=rng)
        if pick and pick.get("warning"):
            warnings.append(pick["warning"])
        if not pick and S.music.enabled:
            warnings.append("No music available - rendering without background music")
    start = scan.start_text(path, S, rng) if S.start.enabled else ""
    vcaps = bool(S.captions.enabled and S.captions.voice_sync)
    if vcaps:  # captions come from what is actually spoken - never from the script document
        if voice and twords:
            cap_words = twords
        elif voice and tr and tr.get("segments"):
            cap_words = captions.words_from_segments(tr["segments"])
            warnings.append("No word-level timestamps - captions use Whisper segment timing")
        else:
            cap_words = []
            warnings.append("Voice-synchronised captions need the narration transcribed (Faster-Whisper) - captions were skipped. "
                            "Install it with setup.bat, or untick 'Synchronize Video Captions with Image and Voice'.")
    return dict(proj=pr, images=images, voice=voice, voice_dur=vdur, duration=Tv, segments=segs, sentences=sent_list,
                words=cap_words if S.captions.enabled else [], score=score, estimated=estimated, mode=mode,
                warnings=warnings, music=pick, transcribed=bool(tr), start_text=start, sync_info=sync_info, voice_captions=vcaps)
