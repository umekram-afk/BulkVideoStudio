"""Project detection: natural sorting, images, script and voice matching, readiness."""
import json, os, re, time
from pathlib import Path
from . import captions, core

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
OTHER_IMG = {".gif", ".bmp", ".tif", ".tiff", ".heic", ".avif"}
VOICE_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac"}
MISSING_CATS = {"missing_images", "missing_voice", "ambiguous_voice", "missing_script", "corrupted_file"}


def nat_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("utf-8", "replace")


def music_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    return sorted((str(Path(folder) / n) for n in os.listdir(folder)
                   if Path(n).suffix.lower() in MUSIC_EXT), key=nat_key)


def list_project_dirs(parent, prefix):
    if not parent or not os.path.isdir(parent):
        raise core.StudioError("invalid_settings", f"Parent folder not found: {parent}")
    pre = prefix.lower()
    with os.scandir(parent) as it:
        names = [e.name for e in it if e.is_dir() and e.name.lower().startswith(pre)]
    return [str(Path(parent) / n) for n in sorted(names, key=nat_key)]


def _find_by_stem(folder, stem, exts):
    if not folder or not os.path.isdir(folder):
        return []
    return [str(Path(folder) / n) for n in sorted(os.listdir(folder), key=nat_key)
            if Path(n).stem.lower() == stem.lower() and Path(n).suffix.lower() in exts]


def start_text(path, S, rng=None):
    """1-6 words for the start title: from <project>/<start.file> (first or random non-empty line), else the default text."""
    f = Path(path) / Path(S.start.file or "start.txt").name  # file name only - never a path
    lines = []
    if f.is_file():
        lines = [ln.strip() for ln in read_text(f).splitlines() if ln.strip()]
    if lines:
        line = (rng or __import__("random")).choice(lines) if S.start.pick == "random_line" else lines[0]
    else:
        line = S.start.default_text.strip()
    return " ".join(line.split()[:S.start.max_words])


def scan_project(path, S, ov=None):
    ov = ov or {}
    p, name, issues = Path(path), Path(path).name, []

    def issue(cat, msg, blocking=True):
        issues.append(dict(cat=cat, msg=msg, blocking=blocking))

    try:
        files = [e for e in os.scandir(p) if e.is_file()]
    except OSError as e:
        raise core.StudioError("invalid_settings", f"Cannot read project folder {path}: {e}")
    all_imgs = sorted((e.name for e in files if Path(e.name).suffix.lower() in IMG_EXT), key=nat_key)
    sizes = {e.name: e.stat().st_size for e in files}
    excluded = [n for n in ov.get("excluded_images", []) if n in all_imgs]
    images = [str(p / n) for n in all_imgs if n not in excluded]
    if not images:
        issue("missing_images", "No usable images found")
    bad = [n for n in all_imgs if sizes[n] == 0 and n not in excluded]
    if bad:
        issue("corrupted_file", f"Empty (0 byte) image file(s): {', '.join(bad[:3])}")
    other = [e.name for e in files if Path(e.name).suffix.lower() in OTHER_IMG]
    if other:
        issue("unsupported_format", f"Ignoring unsupported image format(s): {', '.join(other[:3])}", False)

    # script
    script = None
    src = S.sources.script_source
    if src == "folder":
        for cand in ("script.txt", f"{name}.txt"):
            if (p / cand).is_file():
                script = str(p / cand)
                break
    elif src == "dir":
        hits = _find_by_stem(S.paths.script_dir, name, {".txt"})
        script = hits[0] if hits else None
    if not script and S.voice.enabled:
        can = captions.available()
        issue("missing_script", "No script found - the voice will be transcribed" if can
              else "No script found and faster-whisper is not installed (cannot transcribe)", not can)

    # voice
    voice, cands = None, []
    if S.voice.enabled:
        if S.sources.voice_mode == "folder":
            cands = [str(p / e.name) for e in sorted(files, key=lambda e: nat_key(e.name))
                     if Path(e.name).suffix.lower() in VOICE_EXT]
        else:
            cands = _find_by_stem(S.paths.voice_dir, name, VOICE_EXT)
        chosen = ov.get("voice_file")
        pick = [c for c in cands if Path(c).name == chosen]
        if pick:
            voice = pick[0]
        elif len(cands) == 1:
            voice = cands[0]
        elif len(cands) > 1:
            issue("ambiguous_voice", f"{len(cands)} possible voice files - choose one")
        elif S.voice.required:
            issue("missing_voice", "No voice-over file found")
        else:
            issue("missing_voice", "No voice-over file - this video will be made without voice-over", False)
        if voice and os.path.getsize(voice) == 0:
            issue("corrupted_file", "Voice file is empty")
    if S.start.enabled and not start_text(str(p), S) and not S.start.default_text.strip():
        issue("missing_start", f"No '{Path(S.start.file).name}' with text - the start words will be skipped", False)
    return dict(name=name, path=str(p), images=images, all_images=all_imgs, excluded=excluded, script=script,
                voice=voice, voice_candidates=[Path(c).name for c in cands], issues=issues,
                ready=not any(i["blocking"] for i in issues))


def _info(sc):
    return dict(images=len(sc["images"]), total_images=len(sc["all_images"]), script=bool(sc["script"]),
                voice=Path(sc["voice"]).name if sc["voice"] else "", voice_candidates=sc["voice_candidates"],
                issues=sc["issues"])


def sync_projects(S):
    """Scan the parent folder and sync the project table. Keeps status of queued/finished projects."""
    dirs = list_project_dirs(S.paths.parent, S.paths.prefix)
    rows = {r["path"]: r for r in core.db("SELECT * FROM projects")}
    now = time.time()
    for d in dirs:
        r = rows.get(d)
        ov = json.loads(r["overrides"]) if r else {}
        sc = scan_project(d, S, ov)
        status = "ready" if sc["ready"] else "missing"
        msg = ""
        if r:
            st = r["status"]
            if st in ("pending", "processing", "skipped"):
                status, msg = st, r["message"]
            elif st == "completed" and r["output"] and os.path.exists(r["output"]):
                status, msg = st, r["message"]
            elif st == "failed" and not (sc["ready"] and r["category"] in MISSING_CATS):
                status, msg = st, r["message"]
        if not r:
            core.db("INSERT INTO projects(path,name,status,message,updated,info) VALUES(?,?,?,?,?,?)",
                    (d, sc["name"], status, msg, now, json.dumps(_info(sc))))
        else:
            core.db("UPDATE projects SET status=?, message=?, info=?, updated=? WHERE path=?",
                    (status, msg, json.dumps(_info(sc)), now, d))
    gone = [p for p in rows if p not in set(dirs)]
    for p in gone:
        core.db("DELETE FROM projects WHERE path=? AND status!='processing'", (p,))
    return len(dirs)


def refresh_one(path, S):
    """Re-evaluate one project's info/status (after overrides change)."""
    r = core.db("SELECT * FROM projects WHERE path=?", (path,))
    if not r:
        return
    r = r[0]
    sc = scan_project(path, S, json.loads(r["overrides"] or "{}"))
    status = r["status"]
    if status in ("ready", "missing"):
        status = "ready" if sc["ready"] else "missing"
    core.db("UPDATE projects SET status=?, info=?, updated=? WHERE path=?", (status, json.dumps(_info(sc)), time.time(), path))
