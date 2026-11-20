"""Shared core: settings schema + persistence, SQLite access, activity log, errors."""
import copy, csv, io, json, os, re, sqlite3, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("STUDIO_DATA", ROOT / "data"))
for _d in ("logs", "tmp", "thumbs", "cache", "previews", "transcripts"):
    (DATA / _d).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- errors
SUGGEST = {
    "missing_images": "Add PNG/JPG/WEBP images to the project folder (or un-exclude images), then rescan.",
    "missing_voice": "Put an MP3/WAV voice-over in the project folder or the voice directory (named after the project), then rescan.",
    "ambiguous_voice": "Several voice files match. Open the project and choose the correct one.",
    "missing_script": "Add script.txt, or enable transcription (install faster-whisper) so the script can be generated from the voice.",
    "unsupported_format": "Convert the file to a supported format (images: PNG/JPG/WEBP, audio: MP3/WAV/M4A/AAC).",
    "corrupted_file": "Open the file in another program to check it. Replace or exclude it, then retry.",
    "script_mismatch": "The script does not match the voice-over. Fix the script or re-record, or set the mismatch action to 'estimate'.",
    "missing_music": "Choose a music folder that contains MP3/WAV/M4A/AAC files, or disable background music.",
    "invalid_settings": "Correct the highlighted setting and save again.",
    "unsupported_filter": "Choose another option, or install a newer FFmpeg build (gyan.dev 'full' build is recommended).",
    "render_failure": "Open the FFmpeg log for this project. Retry; if it fails again, try CPU encoding or lower the quality settings.",
    "disk_space": "Free up disk space on the output/temp drive and retry.",
    "caption_failure": "Check that faster-whisper is installed and the model is downloaded (needs internet once), or disable captions.",
    "validation_failure": "The rendered file failed validation and was discarded. Retry; check the FFmpeg log.",
    "unexpected": "Unexpected error. See the technical detail and the FFmpeg log; retry the project.",
    "ffmpeg_missing": "Install FFmpeg and make sure ffmpeg and ffprobe are on PATH (see README).",
}


class StudioError(Exception):
    def __init__(self, category, message, detail=""):
        super().__init__(message)
        self.category, self.message, self.detail = category, message, detail


class Cancelled(Exception):
    def __init__(self, reason="cancel"):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- settings schema
_FIT = ["crop", "blur", "solid"]
_SPD = ["slow", "normal", "fast"]
TRANSITION_IDS = ["fade", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright",
                  "slideup", "slidedown", "smoothleft", "smoothright", "circleopen", "circleclose", "radial",
                  "pixelize", "diagonal", "distance", "zoomin", "zoomout"]
FOCUS_POINTS = {"center": (.5, .5), "top": (.5, 0), "bottom": (.5, 1), "left": (0, .5), "right": (1, .5),
                "top_left": (0, 0), "top_right": (1, 0), "bottom_left": (0, 1), "bottom_right": (1, 1)}
SCHEMA = [  # (path, default, rule)
    ("paths.parent", "", ("str",)), ("paths.prefix", "set-", ("str",)),
    ("paths.script_dir", "", ("str",)), ("paths.voice_dir", "", ("str",)),
    ("paths.music_dir", "", ("str",)), ("paths.extra_output", "", ("str",)),
    ("sources.script_source", "folder", ("enum", ["folder", "dir", "transcribe"])),
    ("sources.voice_mode", "folder", ("enum", ["folder", "dir"])),
    ("voice.enabled", True, ("bool",)), ("voice.required", True, ("bool",)), ("voice.volume", 100, ("int", 0, 200)),
    ("voice.fade_in", 0.0, ("float", 0, 10)), ("voice.fade_out", 0.3, ("float", 0, 10)),
    ("voice.normalize", True, ("bool",)),
    ("music.enabled", True, ("bool",)), ("music.required", True, ("bool",)), ("music.volume", 15, ("int", 0, 100)),
    ("music.random_start", True, ("bool",)), ("music.min_start", 0, ("float", 0, 3600)),
    ("music.max_start", 120, ("float", 0, 3600)), ("music.manual_start", 0, ("float", 0, 3600)),
    ("music.fade_in", 1.0, ("float", 0, 30)), ("music.fade_out", 2.0, ("float", 0, 30)),
    ("music.ducking", False, ("bool",)), ("music.duck_amount", 50, ("int", 0, 100)),
    ("music.allow_repeat", False, ("bool",)),
    ("video.aspect", "9:16", ("enum", ["9:16", "16:9", "1:1"])), ("video.fps", 30, ("enum", [24, 25, 30, 60])),
    ("video.crf", 20, ("int", 14, 35)),
    ("video.preset", "veryfast", ("enum", ["ultrafast", "veryfast", "faster", "fast", "medium", "slow"])),
    ("video.fit", "blur", ("enum", _FIT)), ("video.bg_color", "#000000", ("color",)),
    ("video.hwaccel", "auto", ("enum", ["auto", "gpu", "cpu"])),
    ("video.hw_encoder", "auto", ("enum", ["auto", "h264_nvenc", "h264_qsv", "h264_amf"])), ("video.image_seconds", 3.0, ("float", 0.5, 30)),
    ("sync.method", "voice", ("enum", ["voice", "manual"])), ("sync.match_threshold", 50, ("int", 10, 95)),
    ("sync.min_hold", 1.0, ("float", 0.2, 10)), ("sync.lead_ms", 150, ("int", 0, 1500)),
    ("sync.unmatched", "keep", ("enum", ["keep", "spread"])),
    ("sync.mode", "sentence", ("enum", ["sentence", "group", "even"])), ("sync.group_size", 2, ("int", 1, 20)),
    ("sync.mismatch_threshold", 60, ("int", 0, 100)),
    ("sync.mismatch_action", "estimate", ("enum", ["estimate", "fail"])),
    ("whisper.model", "base", ("enum", ["tiny", "base", "small", "medium", "large-v3"])),
    ("whisper.language", "auto", ("str",)), ("whisper.vad", False, ("bool",)),
    ("effects.vignette.enabled", False, ("bool",)), ("effects.vignette.intensity", 35, ("int", 0, 100)),
    ("effects.vignette.range", 50, ("int", 0, 100)), ("effects.vignette.feather", 60, ("int", 0, 100)),
    ("effects.black_noise.enabled", False, ("bool",)), ("effects.black_noise.intensity", 25, ("int", 0, 100)),
    ("effects.black_noise.opacity", 25, ("int", 0, 100)), ("effects.black_noise.speed", "normal", ("enum", _SPD + ["static"])),
    ("effects.black_noise.size", 2, ("int", 1, 12)), ("effects.black_noise.softness", 0, ("int", 0, 100)),
    ("effects.black_noise.flicker", 0, ("int", 0, 100)),
    ("effects.dust.enabled", False, ("bool",)), ("effects.dust.amount", 100, ("int", 0, 500)),
    ("effects.dust.size", 100, ("int", 20, 500)), ("effects.dust.min_opacity", 40, ("int", 0, 100)),
    ("effects.dust.max_opacity", 90, ("int", 0, 100)), ("effects.dust.opacity", 100, ("int", 0, 100)),
    ("effects.dust.color", "#000000", ("color",)), ("effects.dust.softness", 40, ("int", 0, 300)),
    ("effects.dust.fps", 15, ("int", 2, 30)), ("effects.dust.jitter", 0, ("int", 0, 100)),
    ("effects.dust.hairs_enabled", True, ("bool",)), ("effects.dust.hairs_chance", 65, ("int", 0, 100)),
    ("effects.dust.hairs_max", 2, ("int", 1, 8)), ("effects.dust.hairs_size", 100, ("int", 20, 500)),
    ("effects.dust.hairs_color", "#000000", ("color",)),
    ("effects.dust.grain_enabled", True, ("bool",)), ("effects.dust.grain_amount", 100, ("int", 0, 500)),
    ("effects.dust.grain_opacity", 25, ("int", 0, 100)),
    ("effects.dust.scratches_enabled", False, ("bool",)), ("effects.dust.scratches_chance", 25, ("int", 0, 100)),
    ("effects.dust.scratches_opacity", 35, ("int", 0, 100)), ("effects.dust.scratches_width", 100, ("int", 20, 500)),
    ("effects.dust.scratches_color", "#ffffff", ("color",)), ("effects.dust.random_per_video", True, ("bool",)),
    ("effects.white_noise.enabled", False, ("bool",)), ("effects.white_noise.intensity", 25, ("int", 0, 100)),
    ("effects.white_noise.opacity", 20, ("int", 0, 100)), ("effects.white_noise.speed", "normal", ("enum", _SPD)),
    ("effects.wobble.enabled", False, ("bool",)), ("effects.wobble.intensity", 40, ("int", 0, 100)),
    ("effects.wobble.range", 30, ("int", 0, 100)), ("effects.wobble.speed", "slow", ("enum", _SPD)),
    ("effects.zoom.enabled", False, ("bool",)), ("effects.zoom.first_image", True, ("bool",)), ("effects.zoom.probability", 70, ("int", 0, 100)),
    ("effects.zoom.direction", "in", ("enum", ["in", "out", "random"])),
    ("effects.zoom.amount_min", 8, ("int", 1, 100)), ("effects.zoom.amount_max", 25, ("int", 1, 100)),
    ("effects.zoom.speed", 20, ("int", 1, 100)), ("effects.zoom.smoothness", 80, ("int", 0, 100)),
    ("effects.zoom.focus", "center", ("enum", list(FOCUS_POINTS) + ["custom", "random"])),
    ("effects.zoom.quality", "standard", ("enum", ["standard", "high"])), ("effects.zoom.focus_x", 50, ("int", 0, 100)), ("effects.zoom.focus_y", 50, ("int", 0, 100)),
    ("start.enabled", False, ("bool",)), ("start.file", "start.txt", ("str",)),
    ("start.pick", "first_line", ("enum", ["first_line", "random_line"])), ("start.max_words", 3, ("int", 1, 6)),
    ("start.default_text", "", ("str",)), ("start.duration_ms", 1200, ("int", 50, 15000)),
    ("start.delay_ms", 0, ("int", 0, 10000)), ("start.fade_in_ms", 120, ("int", 0, 2000)),
    ("start.fade_out_ms", 150, ("int", 0, 2000)), ("start.font", "Impact", ("str",)),
    ("start.size", 130, ("int", 20, 500)), ("start.color", "#ffffff", ("color",)), ("start.bold", False, ("bool",)),
    ("start.italic", False, ("bool",)), ("start.uppercase", True, ("bool",)), ("start.spacing", 0, ("int", -10, 60)),
    ("start.outline", 6, ("int", 0, 40)), ("start.outline_color", "#000000", ("color",)), ("start.shadow", 3, ("int", 0, 30)),
    ("start.bg_enabled", False, ("bool",)), ("start.bg_color", "#000000", ("color",)), ("start.bg_opacity", 60, ("int", 0, 100)),
    ("start.padding", 16, ("int", 0, 80)), ("start.rotation", 0, ("int", -180, 180)),
    ("start.pos_x", 50, ("int", 0, 100)), ("start.pos_y", 40, ("int", 0, 100)),
    ("transition.enabled", True, ("bool",)), ("transition.name", "fade", ("enum", TRANSITION_IDS)),
    ("transition.duration", 0.5, ("float", 0.1, 3)),
    ("transition.timing", "center", ("enum", ["before", "center", "after"])),
    ("transition.random", False, ("bool",)),
    ("transition.random_scope", "per_image", ("enum", ["per_image", "per_project"])),
    ("ending.enabled", True, ("bool",)), ("ending.duration", 2.0, ("float", 0.5, 15)),
    ("ending.fade_video", True, ("bool",)), ("ending.fade_audio", True, ("bool",)),
    ("captions.enabled", True, ("bool",)),
    ("captions.preset", "classic", ("enum", ["classic", "black_box", "yellow", "cinematic", "social", "custom"])),
    ("captions.animation", "progressive", ("enum", ["progressive", "word_highlight", "static"])),
    ("captions.font", "Arial", ("str",)), ("captions.size", 64, ("int", 20, 160)),
    ("captions.color", "#ffffff", ("color",)), ("captions.bold", True, ("bool",)),
    ("captions.bg_enabled", False, ("bool",)), ("captions.bg_color", "#000000", ("color",)),
    ("captions.bg_opacity", 70, ("int", 0, 100)), ("captions.padding", 12, ("int", 0, 60)),
    ("captions.pos_x", 50, ("int", 0, 100)), ("captions.pos_y", 78, ("int", 0, 100)),
    ("captions.voice_sync", True, ("bool",)), ("captions.max_words", 6, ("int", 1, 12)), ("captions.max_lines", 2, ("int", 1, 4)),
    ("captions.align", "center", ("enum", ["left", "center", "right"])),
    ("captions.outline", 4, ("int", 0, 20)), ("captions.outline_color", "#000000", ("color",)),
    ("captions.shadow", 2, ("int", 0, 20)), ("captions.uppercase", False, ("bool",)),
    ("captions.highlight", False, ("bool",)), ("captions.highlight_color", "#ffe600", ("color",)),
    ("captions.timing_offset", 0.0, ("float", -2, 2)), ("captions.highlight_lead", 0, ("int", -500, 500)),
    ("output.suffix", "_final", ("str",)), ("output.overwrite", False, ("bool",)),
    ("output.skip_existing", True, ("bool",)),
    ("batch.workers", 1, ("int", 1, 4)),
]
RULES = {p: r for p, _, r in SCHEMA}

_CAP_BASE = dict(font="Arial", size=64, color="#ffffff", bold=True, bg_enabled=False, bg_color="#000000", bg_opacity=70,
                 padding=12, pos_x=50, pos_y=78, max_words=6, max_lines=2, align="center", outline=4,
                 outline_color="#000000", shadow=2, uppercase=False, highlight=False, highlight_color="#ffe600")
_CAP_RAW = {
    "classic": dict(font="Arial", size=64, color="#ffffff", bold=True, bg_enabled=False, outline=4,
                    outline_color="#000000", shadow=2, uppercase=False, highlight=False),
    "black_box": dict(font="Arial", size=58, color="#ffffff", bold=True, bg_enabled=True, bg_color="#000000",
                      bg_opacity=70, padding=14, outline=0, shadow=0, uppercase=False, highlight=False),
    "yellow": dict(font="Arial", size=66, color="#ffffff", bold=True, bg_enabled=False, outline=5,
                   outline_color="#000000", shadow=2, uppercase=False, highlight=True, highlight_color="#ffe600"),
    "cinematic": dict(font="Georgia", size=52, color="#f2f2f2", bold=False, bg_enabled=False, outline=2,
                      outline_color="#000000", shadow=3, uppercase=False, highlight=False, pos_y=80),
    "social": dict(font="Impact", size=84, color="#ffffff", bold=False, bg_enabled=False, outline=7,
                   outline_color="#000000", shadow=3, uppercase=True, highlight=True, highlight_color="#ffe600",
                   max_words=3, pos_y=50),
}


CAPTION_PRESETS = {k: {**_CAP_BASE, **v} for k, v in _CAP_RAW.items()}


def _nest(flat):
    out = {}
    for path, v in flat.items():
        d = out
        *head, last = path.split(".")
        for k in head:
            d = d.setdefault(k, {})
        d[last] = copy.deepcopy(v)
    return out


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        p = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, p + "."))
        else:
            out[p] = v
    return out


def defaults():
    return _nest({p: v for p, v, _ in SCHEMA})


def coerce(path, v):
    rule = RULES.get(path)
    if rule is None:
        raise ValueError(f"Unknown setting '{path}'")
    k = rule[0]
    if k == "bool":
        if isinstance(v, bool):
            return v
        raise ValueError(f"{path}: must be true or false")
    if k in ("int", "float"):
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{path}: must be a number")
        if not rule[1] <= n <= rule[2]:
            raise ValueError(f"{path}: must be between {rule[1]} and {rule[2]}")
        return int(round(n)) if k == "int" else n
    if k == "enum":
        for o in rule[1]:
            if str(o) == str(v):
                return o
        raise ValueError(f"{path}: must be one of {rule[1]}")
    if k == "color":
        if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            return v.lower()
        raise ValueError(f"{path}: must be a #RRGGBB colour")
    if isinstance(v, str):
        return v.strip().strip('"')
    raise ValueError(f"{path}: must be text")


class S(dict):
    """Dict with attribute access so code reads S.video.fps."""
    def __getattr__(self, k):
        try:
            v = self[k]
        except KeyError:
            raise AttributeError(k)
        return S(v) if isinstance(v, dict) else v


_slock = threading.RLock()
SETTINGS_FILE = DATA / "settings.json"


def _write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_settings():
    cur = flatten(defaults())
    with _slock:
        if SETTINGS_FILE.exists():
            try:
                saved = flatten(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                saved = {}
            for p, v in saved.items():
                if p in RULES:
                    try:
                        cur[p] = coerce(p, v)
                    except ValueError:
                        pass
    return _nest(cur)


def update_settings(patch):
    """Merge a (nested or flat) patch. Returns (settings, errors). Nothing is saved if any error."""
    with _slock:
        flat, errors = flatten(load_settings()), []
        for p, v in flatten(patch).items():
            try:
                flat[p] = coerce(p, v)
            except ValueError as e:
                errors.append(str(e))
        if errors:
            return load_settings(), errors
        s = _nest(flat)
        _write_json(SETTINGS_FILE, s)
        return s, []


def reset_settings(keep_paths=True):
    with _slock:
        d = defaults()
        if keep_paths:
            d["paths"] = load_settings()["paths"]
        _write_json(SETTINGS_FILE, d)
        return d


def effective(settings, ov):
    """Global settings with project overrides (ov['settings'], flat paths) applied."""
    flat = flatten(settings)
    for p, v in (ov or {}).get("settings", {}).items():
        if p in RULES:
            try:
                flat[p] = coerce(p, v)
            except ValueError:
                pass
    return S(_nest(flat))


# ---------------------------------------------------------------- SQLite
_dblock = threading.RLock()
_conn = None
DB_FILE = DATA / "studio.db"

DDL = """
CREATE TABLE IF NOT EXISTS projects(path TEXT PRIMARY KEY, name TEXT, status TEXT, category TEXT, message TEXT,
  output TEXT, progress REAL DEFAULT 0, music TEXT, music_start REAL, queued_at REAL, updated REAL,
  info TEXT DEFAULT '{}', overrides TEXT DEFAULT '{}', force INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, project TEXT,
  category TEXT, message TEXT, detail TEXT, suggestion TEXT);
CREATE TABLE IF NOT EXISTS presets(name TEXT PRIMARY KEY, data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def db(sql, args=()):
    global _conn
    with _dblock:
        if _conn is None:
            _conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(DDL)
            have = {r[1] for r in _conn.execute("PRAGMA table_info(projects)")}  # migrate databases from older versions
            for col in ("started_at", "finished_at", "elapsed", "video_seconds"):
                if col not in have:
                    _conn.execute(f"ALTER TABLE projects ADD COLUMN {col} REAL")
            # crash/restart recovery: unfinished jobs go back to the queue
            _conn.execute("UPDATE projects SET status='pending', progress=0, started_at=NULL WHERE status='processing'")
            _conn.commit()
        cur = _conn.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        _conn.commit()
        return rows


def kv_get(k, default=None):
    r = db("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(r[0]["v"]) if r else default


def kv_set(k, v):
    db("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def log(level, project, category, message, detail="", suggestion=None):
    if suggestion is None:
        suggestion = SUGGEST.get(category, "") if level == "error" else ""
    db("INSERT INTO logs(ts,level,project,category,message,detail,suggestion) VALUES(?,?,?,?,?,?,?)",
       (time.time(), level, project or "", category or "", message, detail or "", suggestion))
    db("DELETE FROM logs WHERE id < (SELECT MAX(id) FROM logs) - 20000")


def query_logs(q="", project="", level="", limit=500):
    sql, args = "SELECT * FROM logs WHERE 1=1", []
    if project:
        sql += " AND project=?"; args.append(project)
    if level:
        sql += " AND level=?"; args.append(level)
    if q:
        sql += " AND (message LIKE ? OR detail LIKE ? OR category LIKE ? OR project LIKE ?)"
        args += [f"%{q}%"] * 4
    return db(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))


def export_logs(fmt, **filters):
    rows = query_logs(limit=20000, **filters)
    fields = ["time", "project", "level", "category", "message", "detail", "suggestion"]
    fmt_ts = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(fields)
        for r in rows:
            w.writerow([fmt_ts(r["ts"]), r["project"], r["level"], r["category"], r["message"], r["detail"], r["suggestion"]])
        return buf.getvalue(), "text/csv"
    out = []
    for r in rows:
        out.append(f"[{fmt_ts(r['ts'])}] {r['level'].upper()} {r['project']} {r['category']}\n  {r['message']}")
        if r["detail"]:
            out.append("  Detail: " + r["detail"].replace("\n", "\n    "))
        if r["suggestion"]:
            out.append("  Suggested solution: " + r["suggestion"])
        out.append("")
    return "\n".join(out), "text/plain"


# presets (everything except machine-specific paths)
def preset_save(name, settings):
    data = {k: v for k, v in settings.items() if k != "paths"}
    db("INSERT INTO presets(name,data,updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated=excluded.updated",
       (name, json.dumps(data), time.time()))


def preset_load(name):
    r = db("SELECT data FROM presets WHERE name=?", (name,))
    if not r:
        raise ValueError("Preset not found")
    return update_settings(json.loads(r[0]["data"]))


PRESET_FORMAT = "bulk-video-studio-presets"


def preset_export(name=None):
    """Dict ready to be saved as a .json file. name=None exports every preset."""
    rows = db("SELECT name,data FROM presets" + (" WHERE name=?" if name else "") + " ORDER BY name", (name,) if name else ())
    if name and not rows:
        raise ValueError("Preset not found")
    return {"format": PRESET_FORMAT, "version": 1, "presets": {r["name"]: json.loads(r["data"]) for r in rows}}


def preset_import(data, overwrite=False):
    """Validate and store presets from an exported file. Returns (saved names, skipped messages)."""
    if not isinstance(data, dict) or data.get("format") != PRESET_FORMAT or not isinstance(data.get("presets"), dict):
        raise ValueError("This is not a Bulk Video Studio preset file")
    saved, skipped = [], []
    for name, settings in data["presets"].items():
        name = str(name).strip()[:60]
        if not name or not isinstance(settings, dict):
            skipped.append(f"'{name}': invalid entry"); continue
        flat, errors = {}, []
        for p, v in flatten({k: v for k, v in settings.items() if k != "paths"}).items():
            if p not in RULES:
                continue  # setting from another version: ignored
            try:
                flat[p] = coerce(p, v)
            except ValueError as e:
                errors.append(str(e))
        if errors:
            skipped.append(f"'{name}': {errors[0]}"); continue
        final, n = name, 2
        exists = lambda x: bool(db("SELECT 1 FROM presets WHERE name=?", (x,)))
        if exists(final) and not overwrite:
            while exists(f"{name} ({n})"):
                n += 1
            final = f"{name} ({n})"
        db("INSERT INTO presets(name,data,updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated=excluded.updated",
           (final, json.dumps(_nest(flat)), time.time()))
        saved.append(final)
    return saved, skipped
