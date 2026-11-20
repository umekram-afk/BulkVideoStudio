"""Persistent batch worker. Jobs live in the SQLite projects table, so they survive restarts."""
import hashlib, json, os, random, re, shutil, threading, time, traceback, uuid
from pathlib import Path
from . import core, plan, render, scan
from .core import StudioError, Cancelled

MAX_THREADS = 4
_preview_lock = threading.Lock()
_preview_ctl = [None]  # control handle of the running preview (so Stop everything can cancel it)


def row_of(path):
    r = core.db("SELECT * FROM projects WHERE path=?", (path,))
    return r[0] if r else None


def overrides_of(path):
    r = row_of(path)
    return json.loads(r["overrides"]) if r else {}


def tmp_dir(path):
    return core.DATA / "tmp" / hashlib.sha1(path.encode()).hexdigest()[:12]


def log_file(name):
    return core.DATA / "logs" / (re.sub(r"[^\w.-]", "_", name) + ".ffmpeg.log")


def out_path(pr, S):
    suffix = re.sub(r"[^\w.\-]", "_", S.output.suffix)
    return Path(pr["path"]) / f"{pr['name']}{suffix}.mp4"


def _check_disk(paths, seconds):
    need = 300 * 2**20 + int(seconds * 2 * 2**20)
    for p in paths:
        try:
            free = shutil.disk_usage(p).free
        except OSError:
            continue
        if free < need:
            raise StudioError("disk_space", f"Only {free // 2**20} MB free on the drive of {p}; about {need // 2**20} MB needed")


class Ctl:
    """Per-job control: registers the running FFmpeg process so it can be cancelled."""
    def __init__(self):
        self.proc, self.flag = None, None

    def reg(self, p):
        self.proc = p
        if self.flag:
            p.terminate()

    def cancelled(self):
        return self.flag

    def cancel(self, reason):
        self.flag = reason
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class Manager:
    def __init__(self):
        self.lock = threading.Lock()
        self.ctls = {}
        self.paused = bool(core.kv_get("paused", False))
        self.stop_evt = threading.Event()
        self.threads = []

    def start(self):
        for i in range(MAX_THREADS):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)

    def shutdown(self):
        self.stop_evt.set()
        for c in list(self.ctls.values()):
            c.cancel("shutdown")

    # ---- queue operations
    def _claim(self):
        with self.lock:
            if self.paused:
                return None
            S = core.load_settings()
            running = core.db("SELECT COUNT(*) n FROM projects WHERE status='processing'")[0]["n"]
            if running >= S["batch"]["workers"]:
                return None
            r = core.db("SELECT path FROM projects WHERE status='pending' ORDER BY queued_at, name LIMIT 1")
            if not r:
                return None
            now = time.time()
            core.db("UPDATE projects SET status='processing', progress=0, message='Starting', category='', started_at=?, finished_at=NULL, elapsed=NULL, updated=? WHERE path=?",
                    (now, now, r[0]["path"]))
            return r[0]["path"]

    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                path = self._claim()
            except Exception:
                path = None
            if not path:
                self.stop_evt.wait(0.8)
                continue
            self._run(path)

    def enqueue(self, paths=None, force=False):
        """Queue projects. paths=None -> every 'ready' project; otherwise the given ones (ready/failed/skipped,
        and completed only when force=True, i.e. an explicit re-render). Returns the number queued."""
        now, n = time.time(), 0
        idle = core.db("SELECT COUNT(*) n FROM projects WHERE status IN ('pending','processing')")[0]["n"] == 0
        want = set(paths) if paths is not None else None
        for r in sorted(core.db("SELECT path,status FROM projects"), key=lambda r: scan.nat_key(Path(r["path"]).name)):
            if want is not None and r["path"] not in want:
                continue
            ok = {"ready"} if want is None else {"ready", "failed", "skipped"} | ({"completed"} if force else set())
            if r["status"] not in ok:
                continue
            n += 1
            core.db("UPDATE projects SET status='pending', queued_at=?, force=?, message='', category='', progress=0 WHERE path=?",
                    (now + n * 1e-4, int(force), r["path"]))
        if n and idle:  # a new batch starts: restart the batch clock
            core.kv_set("batch_start", now)
            core.kv_set("batch_end", None)
        return n

    def retry_failed(self):
        # retrying is an explicit request to render, so an older output of the same project is replaced
        return self.enqueue([r["path"] for r in core.db("SELECT path FROM projects WHERE status='failed'")], force=True)

    def pause(self):
        self.paused = True
        core.kv_set("paused", True)

    def resume(self):
        self.paused = False
        core.kv_set("paused", False)

    def stop(self):
        core.kv_set("batch_end", time.time())
        with self.lock:
            core.db("UPDATE projects SET status='ready', message='Queue stopped', progress=0 WHERE status='pending'")
            for c in list(self.ctls.values()):
                c.cancel("stop")

    def stop_all(self):
        """Stop everything: cancel running renders, empty the queue, cancel a running preview."""
        self.stop()
        if _preview_ctl[0]:
            _preview_ctl[0].cancel("stop")

    def reset_all(self, delete_videos=False):
        """Forget all processing data (queue, statuses, logs, caches, transcripts). Settings and presets are kept."""
        self.stop_all()
        for _ in range(60):  # wait for running jobs to wind down
            if not self.ctls:
                break
            time.sleep(0.5)
        removed = 0
        if delete_videos:
            S = core.load_settings()
            for r in core.db("SELECT path,output FROM projects WHERE output IS NOT NULL AND output!=''"):
                out = Path(r["output"])
                cands = [out]
                if S["paths"]["extra_output"]:
                    cands.append(Path(S["paths"]["extra_output"]) / out.name)
                for f in cands:
                    if f.suffix.lower() == ".mp4" and f.is_file() and (f.parent == Path(r["path"]) or f.parent == Path(S["paths"]["extra_output"] or "?")):
                        f.unlink(missing_ok=True); removed += 1
        core.db("DELETE FROM projects")
        core.db("DELETE FROM logs")
        core.db("DELETE FROM kv")
        self.paused = False
        for d in ("tmp", "thumbs", "cache", "previews", "transcripts", "logs"):
            for f in (core.DATA / d).glob("*"):
                shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
        return removed

    def cancel(self, path, reason="cancel"):
        with self.lock:
            if path in self.ctls:
                self.ctls[path].cancel(reason)
            elif reason == "skip":
                core.db("UPDATE projects SET status='skipped', message='Skipped by user' WHERE path=? AND status IN ('pending','ready','missing','failed')", (path,))
            else:
                core.db("UPDATE projects SET status='ready', message='Cancelled', progress=0 WHERE path=? AND status='pending'", (path,))

    # ---- one job
    def _run(self, path):
        ctl = Ctl()
        self.ctls[path] = ctl
        name = Path(path).name
        t0 = time.time()
        try:
            core.log("info", name, "", "Started rendering")
            out, music = process(path, ctl)
            took = time.time() - t0
            core.db("UPDATE projects SET status='completed', progress=1, output=?, music=?, message=?, category='', finished_at=?, elapsed=?, updated=? WHERE path=?",
                    (str(out), music["name"] if music else "", f"Rendered in {took:.0f}s", time.time(), took, time.time(), path))
            core.log("info", name, "", f"Completed in {time.time() - t0:.0f}s -> {out.name}")
        except Cancelled as c:
            if c.reason == "skip":
                st, msg = "skipped", "Skipped by user"
            elif c.reason == "exists":
                st, msg = "skipped", "Output already exists - skipped"
            elif c.reason == "shutdown":
                st, msg = "pending", ""
            else:
                st, msg = "ready", "Cancelled"
            core.db("UPDATE projects SET status=?, message=?, progress=0, started_at=NULL, elapsed=NULL, updated=? WHERE path=?", (st, msg, time.time(), path))
            core.log("warn", name, "", f"Job {c.reason}")
        except StudioError as e:
            self._fail(path, name, e.category, e.message, e.detail)
        except Exception as e:  # never let one project stop the batch
            self._fail(path, name, "unexpected", f"{type(e).__name__}: {e}", traceback.format_exc())
        finally:
            self.ctls.pop(path, None)
            shutil.rmtree(tmp_dir(path), ignore_errors=True)
            self._batch_end_if_idle()

    def _batch_end_if_idle(self):
        if core.db("SELECT COUNT(*) n FROM projects WHERE status IN ('pending','processing')")[0]["n"] == 0 and core.kv_get("batch_end") is None:
            core.kv_set("batch_end", time.time())

    def _fail(self, path, name, cat, msg, detail=""):
        now = time.time()
        core.db("UPDATE projects SET status='failed', category=?, message=?, progress=0, finished_at=?, elapsed=CASE WHEN started_at IS NOT NULL THEN ?-started_at END, updated=? WHERE path=?",
                (cat, msg, now, now, now, path))
        core.log("error", name, cat, msg, detail)


def process(path, ctl):
    """Render one project end-to-end. Returns (final output path, music dict)."""
    row = row_of(path)
    ov = json.loads(row["overrides"]) if row else {}
    S = core.effective(core.load_settings(), ov)
    name = Path(path).name
    P = plan.build_plan(path, S, ov, record_music=True)
    for w in P["warnings"]:
        core.log("warn", name, "", w)
    out = out_path(P["proj"], S)
    force = bool(row and row["force"])
    if out.exists() and not force:
        if S.output.skip_existing:
            raise Cancelled("exists")
        if not S.output.overwrite:
            raise StudioError("validation_failure", f"Output already exists: {out.name}",
                              "Enable 'Overwrite existing output' or 'Skip existing output' in Batch Processing.")
    total = P["duration"] + (S.ending.duration if S.ending.enabled else 0)
    core.db("UPDATE projects SET video_seconds=? WHERE path=?", (total, path))
    _check_disk([path, str(core.DATA / "tmp")], total)
    # unique paths per attempt: an orphaned FFmpeg from a crashed session can never collide with this run
    token = uuid.uuid4().hex[:8]
    shutil.rmtree(tmp_dir(path), ignore_errors=True)
    work = tmp_dir(path) / token
    for stale in out.parent.glob(out.name + ".*.part"):
        stale.unlink(missing_ok=True)
    lf = log_file(name)
    lf.write_text("", encoding="utf-8")
    core.db("UPDATE projects SET music=?, music_start=?, message='Rendering' WHERE path=?",
            (P["music"]["name"] if P["music"] else "", P["music"]["start"] if P["music"] else 0, path))
    last = [0.0]

    def prog(f):
        if time.time() - last[0] > 1:
            last[0] = time.time()
            core.db("UPDATE projects SET progress=? WHERE path=?", (f, path))
    part = out.with_name(f"{out.name}.{token}.part")
    try:
        T = render.render(P, S, work, part, ctl, lf, on_progress=prog)
        render.validate(part, T, S, want_audio=True)
        os.replace(part, out)  # only now does the final name appear
    finally:
        if part.exists():
            part.unlink(missing_ok=True)
    if S.paths.extra_output:
        try:
            Path(S.paths.extra_output).mkdir(parents=True, exist_ok=True)
            shutil.copy2(out, Path(S.paths.extra_output) / out.name)
        except OSError as e:
            core.log("warn", name, "", f"Could not copy to additional output folder: {e}")
    return out, P["music"]


# ---------------------------------------------------------------- previews (synchronous, one at a time)
def make_preview(path, seconds=8.0):
    """Render the first seconds of a project with current settings. Returns path of the preview mp4."""
    with _preview_lock:
        ov = overrides_of(path)
        S = core.effective(core.load_settings(), ov)
        P = plan.build_plan(path, S, ov)
        seconds = min(seconds, P["duration"])
        P["cut"], P["duration"] = True, seconds
        P["segments"] = [dict(s, end=min(s["end"], seconds)) for s in P["segments"] if s["start"] < seconds - 0.3]
        if not P["segments"]:
            raise StudioError("invalid_settings", "Project is too short to preview")
        work = core.DATA / "tmp" / "preview"
        shutil.rmtree(work, ignore_errors=True)
        out = core.DATA / "previews" / "preview.mp4"
        part = out.with_name("preview.part")
        lf = core.DATA / "logs" / "preview.ffmpeg.log"
        lf.write_text("", encoding="utf-8")
        ctl = _preview_ctl[0] = Ctl()
        try:
            render.render(P, S, work, part, ctl, lf)
            os.replace(part, out)
        except Cancelled:
            raise StudioError("render_failure", "Preview cancelled")
        finally:
            _preview_ctl[0] = None
            shutil.rmtree(work, ignore_errors=True)
            part.unlink(missing_ok=True)
        return out, P["warnings"]


def make_audio_preview(path, seconds=20.0):
    """Mixed voice + music preview (same audio chain as the real render)."""
    with _preview_lock:
        ov = overrides_of(path)
        S = core.effective(core.load_settings(), ov)
        P = plan.build_plan(path, S, ov, transcribe=False)
        if not P["voice"] and not P["music"]:
            raise StudioError("missing_voice", "Nothing to preview: enable voice or music")
        seconds = min(seconds, P["duration"])
        inputs, nin = [], 0
        iv = im = None
        if P["voice"]:
            inputs += ["-i", P["voice"]]; iv = nin; nin += 1
        if P["music"]:
            inputs += ["-stream_loop", "-1", "-i", P["music"]["file"]]; im = nin; nin += 1
        g, _ = render.audio_graph(P, S, iv, im, seconds, 0)
        out = core.DATA / "previews" / "mix.mp3"
        work = core.DATA / "tmp" / "audiopreview"
        work.mkdir(parents=True, exist_ok=True)
        (work / "g.txt").write_text(";\n".join(g), encoding="utf-8")
        r = render._run([render.tool("ffmpeg"), "-y", "-v", "error", *inputs, render.caps()["filter_script"], str(work / "g.txt"),
                         "-map", "[aout]", "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", "-b:a", "160k", str(out)], timeout=300)
        shutil.rmtree(work, ignore_errors=True)
        if r.returncode:
            raise StudioError("render_failure", "Audio preview failed", r.stderr[-600:])
        return out, P["music"]


manager = Manager()
