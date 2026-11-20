"""Local transcription (Faster-Whisper, cached) and ASS generation for captions and the start-words title."""
import hashlib, json, os, re, threading
from pathlib import Path
from . import core, models

_model, _mkey, _mlock = None, None, threading.Lock()
available = models.available


def _cache_file(audio, S, model):
    st = os.stat(audio)
    key = f"{audio}|{st.st_size}|{st.st_mtime_ns}|{model}|{S.whisper.language}|vad{int(S.whisper.vad)}|v3"
    return core.DATA / "transcripts" / (hashlib.sha1(key.encode()).hexdigest() + ".json")


def transcribe(audio, S):
    """Return {'words': [{'word','start','end'}], 'language', 'duration', 'model'}; cached on disk.
    Uses the chosen model only if it is downloaded; otherwise the base model (see models.effective)."""
    global _model, _mkey
    model = models.effective(S.whisper.model)
    cf = _cache_file(audio, S, model)
    if cf.exists():
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except ValueError:
            pass
    if not available():
        raise core.StudioError("caption_failure", "faster-whisper is not installed")
    if model != S.whisper.model:
        core.log("warn", "", "", f"Model '{S.whisper.model}' is not downloaded - using '{model}' instead. Download it in Video Settings.")
    from faster_whisper import WhisperModel
    lang = None if S.whisper.language in ("", "auto") else S.whisper.language

    def run(device, ctype):
        global _model, _mkey
        key = (model, device, ctype)
        if _mkey != key:
            _model, _mkey = WhisperModel(model, device=device, compute_type=ctype), key
        segs, info = _model.transcribe(audio, word_timestamps=True, language=lang, vad_filter=bool(S.whisper.vad),
                                       beam_size=3, condition_on_previous_text=False)
        words, segments = [], []
        for s in segs:  # segments are kept as a fallback when word-level timestamps are missing
            segments.append(dict(text=s.text.strip(), start=float(s.start), end=float(s.end)))
            words += [dict(word=w.word.strip(), start=float(w.start), end=float(w.end)) for w in (s.words or []) if w.word.strip()]
        return dict(words=words, segments=segments, language=info.language, duration=float(info.duration), model=model)

    # Waiting for another project's transcription must remain cancellable.
    while not _mlock.acquire(timeout=0.1):
        if ctl:
            ctl.check()
    try:
            if S.video.hwaccel == "cpu":
                # CPU mode is intentionally CPU-only for both encoding and local transcription.
                res = run("cpu", "int8")
            else:
                try:
                    res = run("auto", "auto")
                except Exception:  # e.g. missing CUDA DLLs -> safe CPU fallback
                    res = run("cpu", "int8")
        except core.Cancelled:
            raise
        except Exception as e:
            raise core.StudioError("caption_failure", f"Transcription failed: {e}",
                                   "If the model is not downloaded yet, connect to the internet once (Video Settings -> Download).")
    cf.write_text(json.dumps(res), encoding="utf-8")
    return res


def words_from_segments(segments):
    """Fallback when Whisper gives no word timestamps: spread each segment's words over the segment, by word length."""
    out = []
    for sg in segments:
        ws = sg["text"].split()
        tot = sum(len(w) + 1 for w in ws) or 1
        t = sg["start"]
        for w in ws:
            d = (len(w) + 1) / tot * (sg["end"] - sg["start"])
            out.append(dict(word=w, start=t, end=t + d)); t += d
    return out


def refine_words(words, silences, max_shift=0.6):
    """Snap word boundaries that fall inside real silence (measured from the audio) to the nearest speech edge.
    Whisper word times can drift into pauses; this keeps the highlighted word in step with what is heard."""
    out, prev = [], 0.0
    for w in words:
        s, e = w["start"], w["end"]
        for a, b in silences:
            if a < s < b and b - s <= max_shift and b < e + 0.02:
                s = b
            if a < e < b and e - a <= max_shift and a > s + 0.03:
                e = a
        s = max(s, prev)
        e = max(e, s + 0.03)
        out.append(dict(w, start=s, end=e))
        prev = s
    return out


# ---------------------------------------------------------------- ASS helpers
def caption_style(S):
    return dict(S.captions)  # presets are applied to these fields by the UI; every field stays editable


def _ass_color(hex_, opacity=100):
    h = hex_.lstrip("#")
    a = 255 - round(max(0, min(100, opacity)) / 100 * 255)
    return f"&H{a:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _esc(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _head(W, H, style_line):
    return ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
            f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
            "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
            "MarginV,Encoding\n" + style_line + "\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")


def chunk_words(words, max_words, max_lines):
    """Group words into caption blocks; break at sentence ends and long pauses."""
    per = max(1, max_words * max_lines)
    blocks, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or w["start"] - cur[-1]["end"] > 0.8):
            blocks.append(cur); cur = []
        cur.append(w)
        if re.search(r"[.!?…。！？؟]$", w["word"]):
            blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    return blocks


def chunk_natural(words, max_words, max_lines, image_starts=()):
    """Short, natural caption blocks for voice-synchronised captions: break at sentence ends, commas (once a line is
    reasonably full), pauses, when the block is full, and whenever the picture changes."""
    per = max(1, max_words * max_lines)
    changes = sorted(image_starts[1:])
    blocks, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            crossed = any(prev["start"] < t <= w["start"] for t in changes)
            soft = re.search(r"[,;:–—]$", prev["word"]) and len(cur) >= max(2, max_words // 2)
            hard = re.search(r"[.!?…。！？؟]$", prev["word"])
            if len(cur) >= per or w["start"] - prev["end"] > 0.6 or hard or soft or crossed:
                blocks.append(cur); cur = []
        cur.append(w)
    if cur:
        blocks.append(cur)
    return blocks


def _lines(texts, max_words, hl, prim, marked=None):
    out, idx = [], 0
    for i in range(0, len(texts), max_words):
        parts = []
        for t in texts[i:i + max_words]:
            parts.append(f"{{\\1c{hl}}}{t}{{\\1c{prim}}}" if marked == idx else t)
            idx += 1
        out.append(" ".join(parts))
    return "\\N".join(out)


def make_ass(words, S, W, H, total, progressive=False, image_starts=()):
    """Captions. (pos_x, pos_y) is the anchor point in % of the frame: the left edge for left-aligned text, the
    centre for centred text, the right edge for right-aligned text; vertically it is the centre of the block."""
    c = caption_style(S)
    k = min(W, H) / 1080  # font sizes are defined at 1080px short side
    an = {"left": 4, "center": 5, "right": 6}[c["align"]]
    px, py = round(W * c["pos_x"] / 100), round(H * c["pos_y"] / 100)
    box = c["bg_enabled"]
    size = int(c["size"] * k)
    outline = int((c["padding"] if box else c["outline"]) * k)
    shadow = 0 if box else int(c["shadow"] * k)
    prim = _ass_color(c["color"])
    outc = _ass_color(c["bg_color"], c["bg_opacity"]) if box else _ass_color(c["outline_color"])
    back = _ass_color(c["bg_color"], c["bg_opacity"]) if box else _ass_color("#000000", 60)
    style = (f"Style: Default,{c['font']},{size},{prim},{prim},{outc},{back},{-1 if c['bold'] else 0},0,0,0,100,100,0,0,"
             f"{3 if box else 1},{outline},{shadow},{an},0,0,0,1")
    off, lead = c["timing_offset"], c["highlight_lead"] / 1000
    pos = f"{{\\pos({px},{py})}}"
    hl = _ass_color(c["highlight_color"])
    events = []
    if progressive:  # voice-synchronised: each word appears when it is spoken, never before
        shift = max(0.0, off)
        blocks = chunk_natural(words, c["max_words"], c["max_lines"], image_starts)
        for bi, blk in enumerate(blocks):
            texts = [_esc(w["word"].upper() if c["uppercase"] else w["word"]) for w in blk]
            nxt = blocks[bi + 1][0]["start"] if bi + 1 < len(blocks) else total
            block_end = max(min(blk[-1]["end"] + 0.35, nxt), blk[-1]["start"] + 0.05)
            for wi, w in enumerate(blk):
                e = blk[wi + 1]["start"] if wi + 1 < len(blk) else block_end
                e = max(e, w["start"] + 0.04)
                animation = c.get("animation", "progressive")
                if c["highlight"] or animation == "word_highlight":
                    # Keep the complete caption visible; only the active spoken word changes colour.
                    body = _lines(texts, c["max_words"], hl, prim, wi if c["highlight"] else None)
                elif animation == "static":
                    # A single stable caption block for the duration of the phrase.
                    if wi:
                        continue
                    body = _lines(texts, c["max_words"], hl, prim)
                    e = block_end
                else:
                    body = _lines(texts[:wi + 1], c["max_words"], hl, prim)
                events.append(f"Dialogue: 0,{_ts(w['start'] + shift)},{_ts(e + shift)},Default,,0,0,0,,{pos}{body}")
        return _head(W, H, style) + "\n".join(events) + "\n"
    blocks = chunk_words(words, c["max_words"], c["max_lines"])
    for bi, blk in enumerate(blocks):
        texts = [_esc(w["word"].upper() if c["uppercase"] else w["word"]) for w in blk]
        end_blk = min(blk[-1]["end"] + 0.25, blocks[bi + 1][0]["start"] if bi + 1 < len(blocks) else total)
        end_blk = max(end_blk, blk[-1]["end"])

        def layout(marked=None):
            lines, out, idx = [texts[i:i + c["max_words"]] for i in range(0, len(texts), c["max_words"])], [], 0
            for ln in lines:
                parts = []
                for t in ln:
                    parts.append(f"{{\\1c{hl}}}{t}{{\\1c{prim}}}" if marked == idx else t)
                    idx += 1
                out.append(" ".join(parts))
            return "\\N".join(out)

        if c["highlight"]:  # one event per spoken word, with that word coloured
            for wi, w in enumerate(blk):
                e = blk[wi + 1]["start"] if wi + 1 < len(blk) else end_blk
                s0, e0 = w["start"] + off - lead, max(e, w["start"] + 0.05) + off - lead
                events.append(f"Dialogue: 0,{_ts(s0)},{_ts(e0)},Default,,0,0,0,,{pos}{layout(wi)}")
        else:
            events.append(f"Dialogue: 0,{_ts(blk[0]['start'] + off)},{_ts(end_blk + off)},Default,,0,0,0,,{pos}{layout()}")
    return _head(W, H, style) + "\n".join(events) + "\n"


def make_start_ass(text, S, W, H, total):
    """The short 'start words' title shown at the beginning of every video."""
    s = S.start
    k = min(W, H) / 1080
    box = s.bg_enabled
    prim = _ass_color(s.color)
    outc = _ass_color(s.bg_color, s.bg_opacity) if box else _ass_color(s.outline_color)
    back = _ass_color(s.bg_color, s.bg_opacity) if box else _ass_color("#000000", 60)
    style = (f"Style: Start,{s.font},{int(s.size * k)},{prim},{prim},{outc},{back},{-1 if s.bold else 0},{-1 if s.italic else 0},0,0,"
             f"100,100,{s.spacing * k:.1f},0,{3 if box else 1},{int((s.padding if box else s.outline) * k)},"
             f"{0 if box else int(s.shadow * k)},5,0,0,0,1")
    t0 = s.delay_ms / 1000
    t1 = min(t0 + s.duration_ms / 1000, total)
    dur_ms = int((t1 - t0) * 1000)
    fin, fout = min(s.fade_in_ms, dur_ms), min(s.fade_out_ms, max(0, dur_ms - s.fade_in_ms))
    txt = _esc(text.upper() if s.uppercase else text)
    tags = f"{{\\an5\\pos({round(W * s.pos_x / 100)},{round(H * s.pos_y / 100)})\\frz{-s.rotation}\\fad({fin},{fout})}}"
    return _head(W, H, style) + f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Start,,0,0,0,,{tags}{txt}\n"
