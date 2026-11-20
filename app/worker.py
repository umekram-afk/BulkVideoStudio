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
    """Thread-safe per-job control; cancellation is remembered before FFmpeg starts."""
    def __init__(self):
        self.proc, self.flag = None, None
        self.lock = threading.Lock()

    def reg(self, p):
        with self.lock:
            self.proc = p
            cancelled = self.flag is not None
        if cancelled and p.poll() is None:
            p.terminate()

    def cancelled(self):
        with self.lock:
            return self.flag

    def check(self):
        reason = self.cancelled()
        if reason:
            raise Cancelled(reason)

    def cancel(self, reason):
        with self.lock:
            # Preserve an explicit user stop/skip if application shutdown follows.
            if self.flag is None:
                self.flag = reason
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


class Manager:
    """Single-dispatcher job queue.

    One scheduler owns all queue claims. Rendering happens in at most four
    independent job threads, limited by the current Batch Processing setting.
    No render thread is responsible for starting the next job.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.ctls = {}
        self.deferred = {}
        self.paused = bool(core.kv_get("paused", False))
        self.resetting = False
        self.stopping = False
        self.stop_evt = threading.Event()
        self.dispatcher = None
        self.job_threads = set()

    def start(self):
        with self.changed:
            if self.dispatcher and self.dispatcher.is_alive():
                return
            self.stop_evt.clear()
            self.dispatcher = threading.Thread(
                target=self._dispatch_loop, name="studio-dispatcher", daemon=True
            )
            self.dispatcher.start()
            self.changed.notify_all()

    def shutdown(self):
        with self.changed:
            self.stop_evt.set()
            for ctl in list(self.ctls.values()):
                ctl.cancel("shutdown")
            self.changed.notify_all()

    def _dispatch_loop(self):
        """The only code allowed to claim pending projects."""
        while not self.stop_evt.is_set():
            launched = 0
            with self.changed:
                if self.paused or self.resetting or self.stopping:
                    self.changed.wait(timeout=0.25)
                    continue

                try:
                    limit = max(1, min(MAX_THREADS, int(core.load_settings()["batch"]["workers"])))
                    slots = max(0, limit - len(self.ctls))
                    if slots:
                        pending = core.db(
                            "SELECT path FROM projects WHERE status='pending' "
                            "ORDER BY queued_at, name LIMIT ?", (slots,)
                        )
                        for row in pending:
                            path = row["path"]
                            # Recheck the state in the UPDATE. The manager lock
                            # serializes queue operations; the predicate protects
                            # against external database changes as well.
                            now = time.time()
                            core.db(
                                "UPDATE projects SET status='processing', progress=0, "
                                "message='Starting', category='', started_at=?, "
                                "finished_at=NULL, elapsed=NULL, updated=? "
                                "WHERE path=? AND status='pending'",
                                (now, now, path),
                            )
                            claimed = core.db(
                                "SELECT status FROM projects WHERE path=?", (path,)
                            )
                            if not claimed or claimed[0]["status"] != "processing":
                                continue
                            ctl = Ctl()
                            self.ctls[path] = ctl
                            thread = threading.Thread(
                                target=self._execute, args=(path, ctl),
                                name=f"render-{Path(path).name[:40]}", daemon=True
                            )
                            self.job_threads.add(thread)
                            thread.start()
                            launched += 1
                except Exception as exc:
                    try:
                        core.log("error", "", "worker_failure",
                                 f"Queue dispatcher error: {type(exc).__name__}: {exc}",
                                 traceback.format_exc())
                    except Exception:
                        pass

                if not launched:
                    self.changed.wait(timeout=0.25)

    def _execute(self, path, ctl):
        try:
            self._run_job(path, ctl)
        except BaseException as exc:
            # A failure in bookkeeping must never strand the queue slot.
            try:
                now = time.time()
                reason = ctl.cancelled()
                if reason in ("stop", "shutdown", "skip"):
                    status = "skipped" if reason == "skip" else ("pending" if reason == "shutdown" else "ready")
                    message = "Skipped by user" if reason == "skip" else ("Cancelled" if reason == "stop" else "")
                    core.db(
                        "UPDATE projects SET status=?, message=?, progress=0, queued_at=NULL, "
                        "started_at=NULL, finished_at=NULL, elapsed=NULL, updated=? "
                        "WHERE path=? AND status='processing'",
                        (status, message, now, path),
                    )
                else:
                    core.db(
                        "UPDATE projects SET status='failed', category='worker_failure', "
                        "message=?, progress=0, finished_at=?, elapsed=CASE WHEN started_at "
                        "IS NOT NULL THEN ?-started_at END, updated=? "
                        "WHERE path=? AND status='processing'",
                        (f"Worker stopped unexpectedly: {type(exc).__name__}: {exc}", now, now, now, path),
                    )
                core.log("error", Path(path).name, "worker_failure",
                         f"Job wrapper recovered from {type(exc).__name__}: {exc}",
                         traceback.format_exc())
            except Exception:
                pass
        finally:
            try:
                shutil.rmtree(tmp_dir(path), ignore_errors=True)
            except Exception:
                pass

            with self.changed:
                self.ctls.pop(path, None)
                self.job_threads.discard(threading.current_thread())

                # A user may queue the same project while its previous attempt
                # is still unwinding. Start that new attempt only after cleanup.
                deferred_force = self.deferred.pop(path, None)
                if deferred_force is not None and not self.resetting:
                    now = time.time()
                    core.db(
                        "UPDATE projects SET status='pending', queued_at=?, started_at=NULL, "
                        "finished_at=NULL, elapsed=NULL, force=?, message='', category='', "
                        "progress=0, updated=? WHERE path=? AND status IN "
                        "('ready','completed','failed','skipped')",
                        (now, int(deferred_force), now, path),
                    )

                if self.stopping and not self.ctls:
                    self.stopping = False

                try:
                    self._batch_end_if_idle()
                except Exception as exc:
                    try:
                        core.log("error", Path(path).name, "worker_failure",
                                 f"Could not finalize batch timing: {exc}")
                    except Exception:
                        pass
                self.changed.notify_all()

    def enqueue(self, paths=None, force=False):
        with self.changed:
            if self.resetting:
                return 0
            now = time.time()
            active_before = core.db(
                "SELECT COUNT(*) n FROM projects WHERE status IN ('pending','processing')"
            )[0]["n"]
            idle = active_before == 0 and not self.ctls
            wanted = set(paths) if paths is not None else None
            rows = sorted(
                core.db("SELECT path,status FROM projects"),
                key=lambda row: scan.nat_key(Path(row["path"]).name),
            )
            queued = 0
            for row in rows:
                path, status = row["path"], row["status"]
                if wanted is not None and path not in wanted:
                    continue
                allowed = {"ready"} if wanted is None else {"ready", "failed", "skipped"}
                if force:
                    allowed.add("completed")

                if path in self.ctls:
                    previous = self.deferred.get(path)
                    self.deferred[path] = bool(force or previous)
                    if previous is None:
                        queued += 1
                    continue
                if status not in allowed:
                    continue

                queued += 1
                core.db(
                    "UPDATE projects SET status='pending', queued_at=?, started_at=NULL, "
                    "finished_at=NULL, elapsed=NULL, force=?, message='', category='', "
                    "progress=0, updated=? WHERE path=? AND status=?",
                    (now + queued * 0.0001, int(force), now, path, status),
                )

            if queued:
                if idle or self.stopping:
                    core.kv_set("batch_start", now)
                core.kv_set("batch_end", None)
                self.changed.notify_all()
            return queued

    def retry_failed(self):
        paths = [row["path"] for row in core.db(
            "SELECT path FROM projects WHERE status='failed'"
        )]
        return self.enqueue(paths, force=True)

    def pause(self):
        with self.changed:
            self.paused = True
            core.kv_set("paused", True)
            self.changed.notify_all()

    def resume(self):
        with self.changed:
            if not self.resetting:
                self.paused = False
                core.kv_set("paused", False)
                self.changed.notify_all()

    def stop(self):
        """Empty the waiting queue and request cancellation of every active job."""
        with self.changed:
            now = time.time()
            self.paused = False
            core.kv_set("paused", False)
            core.kv_set("batch_end", now)
            core.db(
                "UPDATE projects SET status='ready', message='Queue stopped', progress=0, "
                "queued_at=NULL, started_at=NULL, finished_at=NULL, elapsed=NULL, updated=? "
                "WHERE status='pending'", (now,)
            )
            self.stopping = bool(self.ctls)
            self.deferred.clear()
            for ctl in list(self.ctls.values()):
                ctl.cancel("stop")
            if not self.ctls:
                self.stopping = False
            self.changed.notify_all()
        return True

    def stop_all(self):
        self.stop()
        with self.changed:
            ctl = _preview_ctl[0]
            if ctl:
                ctl.cancel("stop")
            self.changed.notify_all()
        return True

    def _wait_for_idle(self, include_preview=False, timeout=180):
        deadline = time.monotonic() + timeout
        with self.changed:
            while self.ctls or (include_preview and _preview_ctl[0] is not None):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StudioError(
                        "render_failure",
                        "Background work did not stop in time. No project data was deleted; wait and try again."
                    )
                self.changed.wait(timeout=min(0.25, remaining))

    def reset_all(self, delete_videos=False):
        with self.changed:
            self.resetting = True
            self.deferred.clear()
            now = time.time()
            core.kv_set("batch_end", now)
            core.db(
                "UPDATE projects SET status='ready', message='Resetting', progress=0, "
                "queued_at=NULL, started_at=NULL, finished_at=NULL, elapsed=NULL, updated=? "
                "WHERE status='pending'", (now,)
            )
            for ctl in list(self.ctls.values()):
                ctl.cancel("stop")
            preview = _preview_ctl[0]
            if preview:
                preview.cancel("stop")
            self.changed.notify_all()

        reset_complete = False
        try:
            self._wait_for_idle(include_preview=True, timeout=120)
            with self.changed:
                removed = 0
                if delete_videos:
                    settings = core.load_settings()
                    extra = Path(settings["paths"]["extra_output"]).expanduser() if settings["paths"]["extra_output"] else None
                    suffix = re.sub(r"[^\\w.\\-]", "_", settings["output"]["suffix"])
                    rows = core.db("SELECT path,name,output FROM projects")
                    candidates = set()

                    # Use the output naming convention as well as the database
                    # pointer. Older completed records often have no output
                    # field, so relying on that field silently leaves videos.
                    project_dirs = {}
                    for row in rows:
                        project_dir = Path(row["path"]).expanduser()
                        project_name = row.get("name") or project_dir.name
                        project_dirs[project_dir] = project_name
                        for ending in {suffix, "_final"}:
                            candidates.add(project_dir / f"{project_name}{ending}.mp4")
                            if extra:
                                candidates.add(extra / f"{project_name}{ending}.mp4")
                        if row.get("output"):
                            recorded = Path(row["output"]).expanduser()
                            # Only delete a recorded output when it is in the
                            # project folder or the configured extra-output folder.
                            if recorded.parent == project_dir or (extra and recorded.parent == extra):
                                candidates.add(recorded)
                            if extra:
                                candidates.add(extra / recorded.name)

                    # Include project folders on disk even if a prior reset,
                    # database migration, or old scan left no project row.
                    parent = Path(settings["paths"]["parent"]).expanduser() if settings["paths"]["parent"] else None
                    prefix = settings["paths"]["prefix"]
                    if parent and parent.is_dir():
                        for project_dir in parent.iterdir():
                            if project_dir.is_dir() and project_dir.name.startswith(prefix):
                                for ending in {suffix, "_final"}:
                                    candidates.add(project_dir / f"{project_dir.name}{ending}.mp4")
                                    if extra:
                                        candidates.add(extra / f"{project_dir.name}{ending}.mp4")

                    failures = []
                    for candidate in sorted(candidates, key=lambda p: str(p).casefold()):
                        try:
                            if candidate.suffix.lower() == ".mp4" and candidate.is_file():
                                candidate.unlink()
                                removed += 1
                        except OSError as exc:
                            failures.append(f"{candidate}: {exc}")
                    if failures:
                        raise StudioError(
                            "render_failure",
                            f"Could not delete {len(failures)} generated video file(s). Close any player or editor using them and try Reset again.",
                            "\\n".join(failures[:20])
                        )

                core.db("DELETE FROM projects")
                core.db("DELETE FROM logs")
                core.db("DELETE FROM kv")
                self.paused = False
                for folder in ("tmp", "thumbs", "cache", "previews", "transcripts", "logs"):
                    target = core.DATA / folder
                    for item in target.glob("*"):
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
                reset_complete = True
                return removed
        finally:
            with self.changed:
                self.resetting = False
                self.paused = False
                self.stopping = False
                self.deferred.clear()
                if not reset_complete:
                    core.kv_set("paused", False)
                self.changed.notify_all()

    def cancel(self, path, reason="cancel"):
        with self.changed:
            if path in self.ctls:
                self.ctls[path].cancel(reason)
            elif reason == "skip":
                core.db(
                    "UPDATE projects SET status='skipped', message='Skipped by user', queued_at=NULL, "
                    "started_at=NULL, elapsed=NULL WHERE path=? AND status IN "
                    "('pending','ready','missing','failed')", (path,)
                )
            else:
                core.db(
                    "UPDATE projects SET status='ready', message='Cancelled', progress=0, "
                    "queued_at=NULL, started_at=NULL, elapsed=NULL WHERE path=? AND status='pending'",
                    (path,)
                )
            self.changed.notify_all()

    def _run_job(self, path, ctl):
        name = Path(path).name
        started = time.time()
        try:
            ctl.check()
            core.db(
                "UPDATE projects SET message='Analyzing audio and preparing timeline', progress=0.01 "
                "WHERE path=? AND status='processing'", (path,)
            )
            core.log("info", name, "", "Started rendering")
            output, music = process(path, ctl)
            ctl.check()
            finished = time.time()
            elapsed = finished - started
            core.db(
                "UPDATE projects SET status='completed', progress=1, output=?, music=?, message=?, "
                "category='', finished_at=?, elapsed=?, updated=? "
                "WHERE path=? AND status='processing'",
                (str(output), music["name"] if music else "", f"Rendered in {elapsed:.1f}s",
                 finished, elapsed, finished, path),
            )
            core.log("info", name, "", f"Completed in {elapsed:.1f}s -> {output.name}")
        except Cancelled as exc:
            if exc.reason == "skip":
                status, message = "skipped", "Skipped by user"
            elif exc.reason == "exists":
                status, message = "skipped", "Output already exists - skipped"
            elif exc.reason == "shutdown":
                status, message = "pending", ""
            else:
                status, message = "ready", "Cancelled"
            now = time.time()
            core.db(
                "UPDATE projects SET status=?, message=?, progress=0, started_at=NULL, "
                "finished_at=NULL, elapsed=NULL, queued_at=NULL, updated=? WHERE path=? "
                "AND status='processing'",
                (status, message, now, path),
            )
            core.log("warn", name, "", f"Job {exc.reason}")
        except StudioError as exc:
            self._fail(path, name, exc.category, exc.message, exc.detail)
        except Exception as exc:
            self._fail(path, name, "unexpected",
                       f"{type(exc).__name__}: {exc}", traceback.format_exc())

    def _batch_end_if_idle(self):
        active = core.db(
            "SELECT COUNT(*) n FROM projects WHERE status IN ('pending','processing')"
        )[0]["n"]
        if active == 0 and core.kv_get("batch_end") is None:
            core.kv_set("batch_end", time.time())

    def _fail(self, path, name, category, message, detail=""):
        now = time.time()
        core.db(
            "UPDATE projects SET status='failed', category=?, message=?, progress=0, "
            "finished_at=?, elapsed=CASE WHEN started_at IS NOT NULL THEN ?-started_at END, "
            "updated=? WHERE path=? AND status='processing'",
            (category, message, now, now, now, path),
        )
        core.log("error", name, category, message, detail)

def process(path, ctl):
    """Render one project end-to-end. Returns (final output path, music dict)."""
    ctl.check()
    row = row_of(path)
    ov = json.loads(row["overrides"]) if row else {}
    S = core.effective(core.load_settings(), ov)
    name = Path(path).name
    P = plan.build_plan(path, S, ov, record_music=True, ctl=ctl)
    ctl.check()
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
        ctl.check()
        render.validate(part, T, S, want_audio=True)
        ctl.check()
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
def _begin_preview():
    with manager.lock:
        if manager.resetting:
            raise StudioError("render_failure", "Reset is in progress; wait before starting a preview.")
        ctl = Ctl()
        _preview_ctl[0] = ctl
        return ctl


def _end_preview():
    with manager.lock:
        _preview_ctl[0] = None


def make_preview(path, seconds=8.0):
    """Render the first seconds of a project with current settings. Returns path of the preview mp4."""
    with _preview_lock:
        ctl = _begin_preview()
        work = core.DATA / "tmp" / "preview"
        out = core.DATA / "previews" / "preview.mp4"
        part = out.with_name("preview.part")
        lf = core.DATA / "logs" / "preview.ffmpeg.log"
        try:
            ctl.check()
            ov = overrides_of(path)
            S = core.effective(core.load_settings(), ov)
            P = plan.build_plan(path, S, ov, ctl=ctl)
            ctl.check()
            seconds = min(seconds, P["duration"])
            P["cut"], P["duration"] = True, seconds
            P["segments"] = [dict(s, end=min(s["end"], seconds)) for s in P["segments"] if s["start"] < seconds - 0.3]
            if not P["segments"]:
                raise StudioError("invalid_settings", "Project is too short to preview")
            shutil.rmtree(work, ignore_errors=True)
            lf.write_text("", encoding="utf-8")
            render.render(P, S, work, part, ctl, lf)
            ctl.check()
            os.replace(part, out)
            return out, P["warnings"]
        except Cancelled:
            raise StudioError("render_failure", "Preview cancelled")
        finally:
            shutil.rmtree(work, ignore_errors=True)
            part.unlink(missing_ok=True)
            _end_preview()


def make_audio_preview(path, seconds=20.0):
    """Mixed voice + music preview (same audio chain as the real audio render)."""
    with _preview_lock:
        ctl = _begin_preview()
        work = core.DATA / "tmp" / "audiopreview"
        out = core.DATA / "previews" / "mix.mp3"
        part = core.DATA / "previews" / "mix.part.mp3"
        lf = core.DATA / "logs" / "audio-preview.ffmpeg.log"
        try:
            ctl.check()
            ov = overrides_of(path)
            S = core.effective(core.load_settings(), ov)
            P = plan.build_plan(path, S, ov, transcribe=False, ctl=ctl)
            ctl.check()
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
            work.mkdir(parents=True, exist_ok=True)
            (work / "g.txt").write_text(";\n".join(g), encoding="utf-8")
            lf.write_text("", encoding="utf-8")
            args = [render.tool("ffmpeg"), "-y", "-nostdin", "-hide_banner", "-loglevel", "warning",
                    "-progress", "pipe:1", "-nostats", *inputs, render.caps()["filter_script"], "g.txt",
                    "-map", "[aout]", "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", "-b:a", "160k", str(part)]
            rc = render.run_ffmpeg(args, str(work), lf, seconds, reg=ctl.reg)
            ctl.check()
            if rc:
                raise StudioError("render_failure", "Audio preview failed", render._tail(lf, 600))
            os.replace(part, out)
            return out, P["music"]
        except Cancelled:
            raise StudioError("render_failure", "Audio preview cancelled")
        finally:
            shutil.rmtree(work, ignore_errors=True)
            part.unlink(missing_ok=True)
            _end_preview()


manager = Manager()
