"""Unit tests for script/voice alignment and image timeline logic (no FFmpeg needed).  Run: python tests/test_plan.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("STUDIO_DATA", tempfile.mkdtemp())
from app import plan, scan, captions, core

ok = True
def check(n, c):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n)

check("natural sort (image_10 after image_9)", sorted(["image_10.png", "image_2.png", "image_01.png", "image_9.png"], key=scan.nat_key) == ["image_01.png", "image_2.png", "image_9.png", "image_10.png"])
sents = plan.split_sentences("Sometimes life doesn't go as planned. We make wishes! But life has its own direction?\nDr. Smith agrees.")
check("sentence splitting (abbreviations kept)", sents == ["Sometimes life doesn't go as planned.", "We make wishes!", "But life has its own direction?", "Dr. Smith agrees."])

script = "Sometimes life doesn't go as planned. We make wishes. But life has its own direction."
words = script.split()
sents = plan.split_sentences(script)
sw = [(w, i) for i, s in enumerate(sents) for w in s.split()]
spoken = [dict(word=w, start=2 + k * 0.5, end=2.4 + k * 0.5) for k, w in enumerate(words)]   # speech starts at 2s
t, score = plan.align(sw, spoken, 20)
check("perfect match -> score 1.0 and exact times", score == 1.0 and t[0] == (2.0, 2.4))
spoken2 = [w for w in spoken if w["word"] not in ("make", "wishes.")]                              # speaker skipped words
t, score = plan.align(sw, spoken2, 20)
check("skipped words interpolated, score reflects mismatch", 0.7 < score < 1.0 and all(a[0] <= b[0] for a, b in zip(t, t[1:])))
t, score = plan.align(sw, [dict(word="completely", start=0, end=1), dict(word="different", start=1, end=2)], 5)
check("unrelated audio -> low score", score < 0.2)

starts = [0, 3, 6]  # 3 sentences
check("1 image per sentence", plan.image_starts(starts, 9, 3, "sentence", 1) == [0, 3, 6])
check("fewer images than sentences keeps order", plan.image_starts([0, 2, 4, 6], 8, 2, "sentence", 1) == [0, 4])
extra = plan.image_starts(starts, 9, 5, "sentence", 1)
check("more images than sentences: 5 images, increasing, first at 0", len(extra) == 5 and extra == sorted(extra) and extra[0] == 0)
check("even distribution", plan.image_starts(starts, 8, 4, "even", 1) == [0, 2, 4, 6])
check("group mode (2 sentences per image)", plan.image_starts([0, 1, 2, 3, 4], 10, 5, "group", 2) == [0, 2, 4])
w = []
check("enforce_gaps drops images that cannot fit", len(plan.enforce_gaps([0, 0.1, 0.2, 0.3, 1], 1.2, w)) < 5 and w)

ass = captions.make_ass([dict(word=x, start=i * 0.4, end=i * 0.4 + 0.35) for i, x in enumerate("Hello there my friend how are you today now".split())], core.S(core.load_settings()), 1080, 1920, 5)
check("ASS: header, play resolution and dialogue lines", "PlayResY: 1920" in ass and ass.count("Dialogue:") == 2)
S = core.S(core.load_settings()); S["captions"]["highlight"] = True
ass = captions.make_ass([dict(word=x, start=i * 0.4, end=i * 0.4 + 0.35) for i, x in enumerate("Hello there my friend".split())], S, 1080, 1920, 5)
check("ASS: per-word highlight events", ass.count("Dialogue:") == 4 and "\\1c" in ass)
sys.exit(0 if ok else 1)
