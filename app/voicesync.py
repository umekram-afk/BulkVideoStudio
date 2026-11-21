"""Voice-based image synchronisation (deterministic text processing - no AI model, no external service).

The actual narration (Whisper words with timestamps) is the source of truth. Each image file name carries an approximate
description, e.g. ``01_Why-do-certain-people-cross-your-path.png``. For every image we look for the stretch of narration
that expresses the same idea - exact wording is NOT required - and switch to that image when the idea starts to be spoken.

How a match is scored (all plain Python):
  * normalise + tokenise, drop stop words, light stemming;
  * token similarity: same stem = 1.0, same synonym group = 0.9, prefix/typo (difflib) up to ~0.95;
  * window coverage: how much (weighted) of the caption is found inside a short stretch of narration;
  * word order inside the window, and a bonus when a caption continues right where the previous one ended;
  * a monotonic dynamic programme chooses the best set of matches in image order, so images never go backwards,
    two images never share the same moment, and weak matches (below the confidence threshold) are dropped.
"""
import difflib, re, unicodedata
from pathlib import Path

STOP = set("""a an the of to in on at is are was were be been being am and or but for with as by it its this that these those
i you he she we they me him her us them my your his our their do does did doing have has had not no so if then than too very
can could will would just about into from up out over also there here when what which who whom how all any some each more most
other such only own same s t don doesn didn isn aren wasn weren""".split())

SYNONYMS = [
    "people person persons folks humans human someone individuals individual", "cross meet encounter intersect",
    "path road way journey route track trail", "life lives living", "love affection romance", "friend friends companion buddy",
    "happy joy joyful glad cheerful happiness", "sad unhappy sorrow grief sadness", "fear afraid scared terror anxiety",
    "win victory succeed success winning", "begin start commence launch", "end finish conclude", "big large huge great giant",
    "small little tiny minor", "money wealth cash riches rich", "time moment period", "change transform shift alter",
    "learn study lesson teach teacher teaches taught", "strong powerful mighty strength", "truth fact reality real",
    "heart soul feelings emotion emotions feeling", "dream wish hope goal aim", "good excellent positive",
    "bad negative poor terrible", "fast quick rapid speed", "slow gradual", "home house family", "work job career labor",
    "mind thoughts thinking brain mental thought", "energy vibe vibration power", "leave depart go gone away",
    "come arrive approach", "help support assist aid", "trust faith believe belief confidence", "pain hurt suffering ache wound",
    "heal healing recover recovery mend", "relationship relationships bond connection connections", "reason cause purpose why",
    "destiny fate karma universe", "let allow permit", "grow growth develop development", "choose choice decide decision",
    "stay remain", "see look watch notice observe", "tell say speak talk", "think consider believe", "need require",
]


def _groups(extra_file=None):
    groups = [g.split() for g in SYNONYMS]
    if extra_file and Path(extra_file).is_file():  # optional user file: one group per line, words separated by , or spaces
        try:
            for line in Path(extra_file).read_text(encoding="utf-8").splitlines():
                ws = re.findall(r"[^\W_]+", line.lower())
                if len(ws) > 1:
                    groups.append(ws)
        except OSError:
            pass
    return groups


def stem(w):
    if len(w) > 4 and not w.endswith("ss"):
        for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("ly", ""), ("es", ""), ("s", "")):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                return w[:-len(suf)] + rep
    return w


GENERIC = set("image images img photo photos pic pics picture pictures slide slides frame scene shot screenshot untitled copy final".split())


def tokens(text, keep_stop=False):
    t = unicodedata.normalize("NFKC", text.lower()).replace("'", "").replace("’", "")
    ws = re.findall(r"[^\W_]+", t)
    out = [w for w in ws if keep_stop or w not in STOP]
    return out or ws  # a caption made only of small words still has something to match


def parse_name(path):
    """(number or None, caption) from '01_Why-do-certain-people-cross-your-path.png'."""
    stem_ = Path(path).stem
    m = re.match(r"^\s*(\d+)[\s_\-.]*(.*)$", stem_)
    num, rest = (int(m.group(1)), m.group(2)) if m else (None, stem_)
    cap = re.sub(r"[-_]+", " ", rest)
    return num, re.sub(r"\s+", " ", cap).strip()


def order_images(images):
    """Numeric order when every file starts with a number, otherwise the order given (already natural-sorted)."""
    parsed = [parse_name(p) for p in images]
    if images and all(n is not None for n, _ in parsed):
        return [p for _, _, p in sorted(((n, Path(p).name.lower(), p) for (n, _), p in zip(parsed, images)))]
    return list(images)


class Matcher:
    def __init__(self, synonym_file=None):
        self.group = {}
        for gi, g in enumerate(_groups(synonym_file)):
            for w in g:
                self.group.setdefault(stem(w), set()).add(gi)
        self._cache = {}

    def sim(self, a, b):
        if a == b:
            return 1.0
        k = (a, b) if a < b else (b, a)
        v = self._cache.get(k)
        if v is None:
            v = 0.0
            if self.group.get(a, set()) & self.group.get(b, set()):
                v = 0.9
            elif min(len(a), len(b)) >= 4:
                if a.startswith(b) or b.startswith(a):
                    v = 0.85
                else:
                    r = difflib.SequenceMatcher(None, a, b).ratio()
                    v = r * 0.95 if r >= 0.84 else 0.0
            self._cache[k] = v
        return v


def match_images(images, words, threshold=0.5, min_hold=1.0, lead=0.15, unmatched="keep", total=None, synonym_file=None):
    """Decide when each image appears.

    images: image paths (any order - sorted numerically here). words: [{'word','start','end'}] from the narration.
    Returns dict(ok, images, starts, info, warnings): ``images``/``starts`` list the images that are actually shown (the
    first one always at 0 - introductory narration is allowed); ``info`` has one entry per image with its caption,
    confidence, time and whether it was matched.
    """
    mt = Matcher(synonym_file)
    imgs = order_images(images)
    total = total if total is not None else (words[-1]["end"] if words else 0.0)
    warnings, K = [], len(imgs)
    narr = [stem(t) for w in words for t in (tokens(w["word"], keep_stop=True) or [""])[:1]]  # one stem per spoken word
    wtimes = [w["start"] for w in words]
    n = len(narr)
    info = []
    caps = []
    for p in imgs:
        num, cap = parse_name(p)
        toks = [stem(t) for t in tokens(cap) if t not in GENERIC and not (t.isdigit() and len(t) <= 2)]  # 'image_01' says nothing
        caps.append(toks)
        info.append(dict(image=p, name=Path(p).name, number=num, caption=cap, tokens=toks, matched=False, confidence=0.0, time=None, word=None))
    usable = sum(1 for t in caps if t)
    if K < 1 or n == 0:
        return dict(ok=False, images=imgs, starts=[], info=info, warnings=["No narration words available for voice-based synchronisation"])
    if usable < 2 and K > 1:
        return dict(ok=False, images=imgs, starts=[], info=info,
                    warnings=["Image file names have no descriptive text (like 01_Why-do-people-cross-your-path.png), so voice-based synchronisation cannot be used"])

    # how common a caption word is among the captions / in the narration (common words are weaker evidence)
    df = {}
    for t in caps:
        for s in set(t):
            df[s] = df.get(s, 0) + 1
    freq = {}
    for s in narr:
        freq[s] = freq.get(s, 0) + 1

    cands = []  # per image: list of (confidence, first_pos, last_pos)
    for k, toks in enumerate(caps):
        if not toks:
            cands.append([]); continue
        wts = [(len(s) ** 0.5) / (1 + 0.6 * (df[s] - 1)) / (1 + 0.15 * max(0, freq.get(s, 0) - 1)) for s in toks]
        sw = sum(wts)
        L = min(n, max(len(toks) * 2 + 2, 6))
        # per caption token: similarity with every narration position
        simrows = [[mt.sim(s, narr[p]) for p in range(n)] for s in toks]
        scored = []
        active = set()  # only windows that contain at least one real hit can match
        for row in simrows:
            for p, v in enumerate(row):
                if v >= 0.8:
                    active.update(range(max(0, p - L + 1), p + 1))
        for s0 in sorted(active):
            hi = min(n, s0 + L)
            cov, hits, last = 0.0, [], -1
            for ti, row in enumerate(simrows):
                best, bp = 0.0, -1
                for p in range(s0, hi):
                    if row[p] > best:
                        best, bp = row[p], p
                cov += wts[ti] * best
                if best >= 0.8:
                    hits.append(bp)
            if not hits:
                continue
            cov /= sw
            order = 1.0 if len(hits) < 2 else sum(1 for a, b in zip(hits, hits[1:]) if a <= b) / (len(hits) - 1)
            conf = 0.8 * cov + 0.2 * order * min(1.0, cov * 1.5)
            if len(toks) == 1:
                conf *= 0.9
            scored.append((conf, min(hits), max(hits), s0))
        # keep local maxima above (a little under) the threshold, best first, one per spoken position
        keep, seen = [], set()
        for conf, fp, lp, s0 in sorted(scored, reverse=True):
            if conf < threshold or fp in seen or any(abs(fp - q) < 3 for q in seen):
                continue
            seen.add(fp)
            keep.append((conf, fp, lp))
            if len(keep) >= 8:
                break
        cands.append(keep)

    # best monotonic assignment (dynamic programme): positions and times must increase, with a minimum hold
    best = {}  # (k, ci) -> (score, prev)
    for k in range(K):
        for ci, (conf, fp, lp) in enumerate(cands[k]):
            sc, prev = conf, None
            for k2 in range(k):
                for cj, (c2, fp2, lp2) in enumerate(cands[k2]):
                    if fp <= fp2 or wtimes[fp] - wtimes[fp2] < min_hold:
                        continue
                    cont = 0.06 if (k2 == k - 1 and -1 <= fp - lp2 <= 4) else 0.0  # caption continues where the last one ended
                    v = best[(k2, cj)][0] + conf + cont
                    if v > sc:
                        sc, prev = v, (k2, cj)
            best[(k, ci)] = (sc, prev)
    chosen = {}
    if best:
        cur = max(best, key=lambda x: best[x][0])
        while cur is not None:
            chosen[cur[0]] = cur[1]
            cur = best[cur][1]
    for k, ci in chosen.items():
        conf, fp, lp = cands[k][ci]
        info[k].update(matched=True, confidence=conf, time=wtimes[fp], word=words[fp]["word"])

    # image 0 is shown from the very beginning (intro narration is allowed before the first matching phrase)
    anchor = [(0.0, 0)]
    times = {0: 0.0}
    for k in range(1, K):
        if info[k]["matched"]:
            t = max(info[k]["time"] - lead, anchor[-1][0] + 0.3)
            times[k] = t
            anchor.append((t, k))
    skipped = [k for k in range(1, K) if not info[k]["matched"]]
    if not any(info[k]["matched"] for k in range(1, K)) and K > 1:
        return dict(ok=False, images=imgs, starts=[], info=info,
                    warnings=["None of the image captions could be found in the narration with enough confidence - lower the confidence threshold or check the file names"])
    if skipped and unmatched == "spread":
        # unmatched images share the time between their matched neighbours
        for i, (ta, ka) in enumerate(anchor):
            kb_next = anchor[i + 1][1] if i + 1 < len(anchor) else K
            tb = anchor[i + 1][0] if i + 1 < len(anchor) else total
            run = [k for k in range(ka + 1, kb_next) if k in skipped]
            for j, k in enumerate(run):
                times[k] = ta + (j + 1) * (tb - ta) / (len(run) + 1)
        skipped = []
    shown = sorted(times)
    if shown and times[shown[0]] != 0.0:
        times[shown[0]] = 0.0
    starts = [times[k] for k in shown]
    out_imgs = [imgs[k] for k in shown]
    for k in shown:
        info[k]["time"] = times[k] if k else 0.0
    if skipped:
        warnings.append(f"{len(skipped)} image(s) had no confident match in the narration and were skipped (the previous image stays on screen): "
                        + ", ".join(info[k]["name"] for k in skipped[:4]) + ("…" if len(skipped) > 4 else ""))
    for k in range(1, K):
        info[k]["shown"] = k in times
    info[0]["shown"] = True
    return dict(ok=True, images=out_imgs, starts=starts, info=info, warnings=warnings)

