"""Film-dust overlay ("CapCut black noise"): soft tilted specks, occasional curved hairs, micro grain.

Port of the reference HTML canvas effect. Every frame (at a choppy 15 fps by default) a fresh random set of vector
shapes is drawn. They are written as an ASS subtitle drawing and burned in with FFmpeg's libass, so there are no
extra dependencies and no per-pixel Python work. Reference geometry is a 640x360 canvas; everything is scaled so the
dust looks the same relative to the frame on any output size.
"""
import math

K = 0.5523  # bezier circle constant


def _p(x, y):  # \p2 drawing mode: integer coordinates in half-pixels
    return f"{round(x * 2)} {round(y * 2)}"


def _alpha(opacity):
    return f"&H{255 - round(max(0.0, min(1.0, opacity)) * 255):02X}&"


def _colour(hex_):
    h = hex_.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _cs(t):
    """Seconds -> ASS timestamp, rounded to whole centiseconds consistently so neighbouring events never overlap."""
    c = round(t * 100)
    return f"{c // 360000}:{c // 6000 % 60:02d}:{c // 100 % 60:02d}.{c % 100:02d}"


def ellipse(x, y, rx, ry, th):
    c, s = math.cos(th), math.sin(th)
    loc = [(1, 0), (1, K), (K, 1), (0, 1), (-K, 1), (-1, K), (-1, 0), (-1, -K), (-K, -1), (0, -1), (K, -1), (1, -K), (1, 0)]
    pts = [(x + c * px * rx - s * py * ry, y + s * px * rx + c * py * ry) for px, py in loc]
    return f"m {_p(*pts[0])} b " + " ".join(_p(*q) for q in pts[1:])


def hair(x0, y0, cx, cy, x1, y1, width):
    """Thin curved stroke (quadratic bezier) as a filled polygon."""
    n = 10
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1))
    left, right = [], []
    for i, (px, py) in enumerate(pts):
        qx, qy = pts[min(i + 1, n)], pts[max(i - 1, 0)]
        dx, dy = qx[0] - qy[0], qx[1] - qy[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * width / 2, dx / ln * width / 2
        left.append((px + nx, py + ny))
        right.append((px - nx, py - ny))
    poly = left + right[::-1]
    return f"m {_p(*poly[0])} l " + " ".join(_p(*q) for q in poly[1:])


def rect(x, y, s):
    return f"m {_p(x, y)} l {_p(x + s, y)} {_p(x + s, y + s)} {_p(x, y + s)}"


def _count(base, mult, rng):
    v = base * mult
    n = int(v)
    return n + (1 if rng.random() < v - n else 0)


def make_dust_ass(d, W, H, total, rng):
    """d = settings.effects.dust. Returns ASS text covering [0, total] seconds."""
    f = min(W, H) / 360.0                      # reference canvas is 360 px on its short side
    area = (W / f) * (H / f) / (640 * 360)     # frame area in reference units (1.0 for 9:16 and 16:9)
    op = d.opacity / 100
    col, hair_col, scr_col = _colour(d.color), _colour(d.hairs_color), _colour(d.scratches_color)
    amount, size = d.amount / 100, d.size / 100
    lo, hi = sorted((d.min_opacity / 100, d.max_opacity / 100))
    levels = 4
    blur = d.softness / 100 * f
    fps = d.fps
    head = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
            f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
            "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Dust,Arial,20,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    ev, fj = [], [1.0]  # fj: per-frame flicker factor of the whole layer

    def emit(t0, t1, opacity, path, b, colour=None):
        blur_tag = f"\\blur{b:.2f}" if b > 0 else ""
        tags = f"{{\\an7\\pos(0,0)\\p2\\bord0\\shad0{blur_tag}\\1c{colour or col}\\1a{_alpha(opacity * op * fj[0])}}}"
        ev.append(f"Dialogue: 0,{t0},{t1},Dust,,0,0,0,,{tags}{path}")

    nframes = max(1, math.ceil(total * fps))
    for i in range(nframes):
        t0, t1 = _cs(i / fps), _cs(min((i + 1) / fps, total) if i + 1 < nframes else total)
        if t0 == t1:
            continue
        fj[0] = 1 - d.jitter / 100 * rng.random()
        # 1. dust specks: asymmetric tilted ellipses, grouped into a few opacity levels (one event per level)
        buckets = {}
        for _ in range(_count(rng.randint(6, 20), area * amount, rng)):
            rx = (rng.random() * 2.2 + 0.5) * f * size
            ry = rx * (rng.random() * 0.7 + 0.4)
            o = lo + rng.random() * (hi - lo)
            lv = round((o - lo) / (hi - lo) * (levels - 1)) if hi > lo else 0
            buckets.setdefault(lv, []).append(ellipse(rng.random() * W, rng.random() * H, rx, ry, rng.random() * math.pi))
        for lv, paths in buckets.items():
            emit(t0, t1, lo + (hi - lo) * lv / max(1, levels - 1), " ".join(paths), blur)
        # 2. occasional hairs / scratches
        if d.hairs_enabled and rng.random() * 100 < d.hairs_chance:
            for _ in range(rng.randint(1, d.hairs_max)):
                sx, sy = rng.random() * W, rng.random() * H
                k = f * d.hairs_size / 100
                cx, cy = sx + (rng.random() - 0.5) * 30 * k, sy + (rng.random() - 0.5) * 30 * k
                ex, ey = cx + (rng.random() - 0.5) * 25 * k, cy + (rng.random() - 0.5) * 25 * k
                emit(t0, t1, rng.random() * 0.4 + 0.4, hair(sx, sy, cx, cy, ex, ey, (rng.random() * 1.2 + 0.4) * k), blur, hair_col)
        # 2b. long vertical film scratches (optional)
        if d.scratches_enabled and rng.random() * 100 < d.scratches_chance:
            for _ in range(rng.randint(1, 2)):
                x, y0 = rng.random() * W, rng.random() * H * 0.5
                y1 = min(H, y0 + H * (0.3 + rng.random() * 0.7))
                dx = (rng.random() - 0.5) * 6 * f
                emit(t0, t1, d.scratches_opacity / 100 * (0.6 + rng.random() * 0.4),
                     hair(x, y0, x + dx, (y0 + y1) / 2, x + dx * 0.4, y1, (rng.random() * 1.0 + 0.5) * f * d.scratches_width / 100), blur, scr_col)
        # 3. micro grain: tiny squares with random faint opacity (no blur)
        if d.grain_enabled and d.grain_opacity > 0:
            gb = {}
            for _ in range(_count(120, area * d.grain_amount / 100, rng)):
                lv = rng.randint(0, 2)
                gb.setdefault(lv, []).append(rect(rng.random() * W, rng.random() * H, max(1.0, f * 0.8)))
            for lv, paths in gb.items():
                emit(t0, t1, d.grain_opacity / 100 * (lv + 1) / 3, " ".join(paths), 0)
    return head + "\n".join(ev) + "\n"
