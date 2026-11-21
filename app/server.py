"""Local web server (stdlib only). Binds to 127.0.0.1. REST API + static UI."""
import hashlib, json, mimetypes, os, re, shutil, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import captions, core, models, plan, render, scan, worker
from .core import StudioError

STATIC = core.ROOT / "static"
MEDIA_EXT = scan.IMG_EXT | scan.VOICE_EXT | scan.MUSIC_EXT | {".mp4", ".mp3"}
ROUTES = {}


def route(method, path):
    def deco(fn):
        ROUTES[(method, path)] = fn
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def S_now():
    return core.S(core.load_settings())


def allowed_roots():
    p = core.load_settings()["paths"]
    roots = [p["parent"], p["script_dir"], p["voice_dir"], p["music_dir"], p["extra_output"], str(core.DATA / "previews"), str(core.DATA / "thumbs")]
    return [Path(r).resolve() for r in roots if r]


def safe_media(path):
    if not path:
        raise ApiError("Missing path")
    p = Path(path).resolve()
    if p.suffix.lower() not in MEDIA_EXT or not p.is_file():
        raise ApiError("Not a media file", 404)
    if not any(p.is_relative_to(r) for r in allowed_roots()):
        raise ApiError("Path is outside the configured folders", 403)
    return p


def project_path(path):
    r = worker.row_of(path)
    if not r:
        raise ApiError("Unknown project - rescan first", 404)
    return path


def counts():
    c = {k: 0 for k in ("missing", "ready", "pending", "processing", "completed", "failed", "skipped")}
    for r in core.db("SELECT status, COUNT(*) n FROM projects GROUP BY status"):
        c[r["status"]] = r["n"]
    c["total"] = sum(c.values())
    return c


def pub_project(r):
    r = dict(r)
    for k in ("started_at", "finished_at", "queued_at"):
        r.pop(k, None)
    r["info"] = json.loads(r["info"] or "{}")
    ov = json.loads(r.pop("overrides") or "{}")
    r["has_overrides"] = bool(ov.get("settings") or ov.get("voice_file") or ov.get("excluded_images") or ov.get("manual_starts") or ov.get("music_file"))
    return r


# ---------------------------------------------------------------- API
@route("GET", "/api/state")
def state(q, b):
    S = S_now()
    return dict(settings=core.load_settings(), schema=[[p, d, list(r)] for p, d, r in core.SCHEMA],
                presets_caption=core.CAPTION_PRESETS, start_default_file=S.start.file, caps=render.caps_public(), transitions=render.transition_list(),
                whisper=captions.available(), music_count=len(scan.music_files(S.paths.music_dir)),
                counts=counts(), paused=worker.manager.paused, data_dir=str(core.DATA))


@route("GET", "/api/music")
def music_list(q, b):
    return [dict(name=Path(f).name, path=f) for f in scan.music_files(S_now().paths.music_dir)]


@route("POST", "/api/settings")
def settings_save(q, b):
    s, errors = core.update_settings(b.get("settings", {}))
    if errors:
        raise ApiError("; ".join(errors))
    return dict(settings=s)


@route("POST", "/api/settings/reset")
def settings_reset(q, b):
    return dict(settings=core.reset_settings(keep_paths=True))


@route("GET", "/api/presets")
def presets_list(q, b):
    return [dict(name=r["name"], updated=r["updated"]) for r in core.db("SELECT name,updated FROM presets ORDER BY name")]


@route("POST", "/api/presets/save")
def presets_save(q, b):
    name = (b.get("name") or "").strip()
    if not name or len(name) > 60:
        raise ApiError("Preset name must be 1-60 characters")
    core.preset_save(name, core.load_settings())
    return dict(ok=True)


@route("POST", "/api/presets/load")
def presets_load(q, b):
    try:
        s, errors = core.preset_load(b.get("name", ""))
    except ValueError as e:
        raise ApiError(str(e), 404)
    if errors:
        raise ApiError("; ".join(errors))
    return dict(settings=s)


@route("POST", "/api/presets/delete")
def presets_delete(q, b):
    core.db("DELETE FROM presets WHERE name=?", (b.get("name", ""),))
    return dict(ok=True)


@route("GET", "/api/presets/export")
def presets_export(q, b):
    try:
        return core.preset_export(q.get("name") or None)
    except ValueError as e:
        raise ApiError(str(e), 404)


@route("POST", "/api/presets/import")
def presets_import(q, b):
    try:
        saved, skipped = core.preset_import(b.get("data"), bool(b.get("overwrite")))
    except ValueError as e:
        raise ApiError(str(e))
    return dict(saved=saved, skipped=skipped)


@route("GET", "/api/net")
def net(q, b):
    return dict(online=models.online())


@route("GET", "/api/models")
def models_list(q, b):
    sel = core.load_settings()["whisper"]["model"]
    return dict(models=models.status(sel), effective=models.effective(sel), installed=models.available(), online=models.online())


@route("POST", "/api/models/download")
def models_download(q, b):
    try:
        models.start_download(b.get("name", ""))
    except ValueError as e:
        raise ApiError(str(e))
    return dict(ok=True)


@route("POST", "/api/stop-all")
def stop_all(q, b):
    worker.manager.stop_all()
    core.log("warn", "", "", "STOP EVERYTHING pressed: running renders cancelled, queue emptied")
    return dict(counts=counts())


@route("POST", "/api/reset-all")
def reset_all(q, b):
    removed = worker.manager.reset_all(bool(b.get("delete_videos")))
    core.log("info", "", "", f"Reset everything done ({removed} video file(s) deleted). Settings and presets kept.")
    return dict(counts=counts(), videos_deleted=removed)


@route("POST", "/api/pick-folder")
def pick_folder(q, b):
    code = ("import tkinter as tk\nfrom tkinter import filedialog\nr=tk.Tk();r.withdraw();r.attributes('-topmost',True)\n"
            f"print(filedialog.askdirectory(title={json.dumps(b.get('title', 'Select folder'))}) or '')")
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                             creationflags=render.NOWIN).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    if not out:
        raise ApiError("No folder chosen (or the folder dialog is unavailable). You can type the path instead.")
    return dict(path=os.path.normpath(out))


@route("POST", "/api/scan")
def do_scan(q, b):
    S = S_now()
    try:
        n = scan.sync_projects(S)
    except StudioError as e:
        raise ApiError(e.message)
    core.log("info", "", "", f"Scanned {S.paths.parent}: {n} project(s) with prefix '{S.paths.prefix}'")
    return dict(found=n, counts=counts())


@route("GET", "/api/projects")
def projects(q, b):
    rows = core.db("SELECT * FROM projects")
    rows.sort(key=lambda r: scan.nat_key(r["name"]))
    return [pub_project(r) for r in rows]


@route("GET", "/api/project")
def project_detail(q, b):
    path = project_path(q.get("path", ""))
    r = worker.row_of(path)
    ov = json.loads(r["overrides"] or "{}")
    S = core.effective(core.load_settings(), ov)
    sc = scan.scan_project(path, S, ov)
    return dict(project=pub_project(r), overrides=ov, images=sc["all_images"], excluded=sc["excluded"], issues=sc["issues"],
                voice=sc["voice"], voice_candidates=sc["voice_candidates"], script=sc["script"],
                script_text=scan.read_text(sc["script"])[:20000] if sc["script"] else "",
                has_output=bool(r["output"] and os.path.exists(r["output"])))


@route("POST", "/api/project/overrides")
def project_overrides(q, b):
    path = project_path(b.get("path", ""))
    ov = b.get("overrides", {})
    clean = {}
    if ov.get("settings"):
        for p in ov["settings"]:
            if p not in core.RULES:
                raise ApiError(f"Unknown setting {p}")
        clean["settings"] = {p: core.coerce(p, v) for p, v in ov["settings"].items()}
    for k in ("voice_file", "music_file"):
        if ov.get(k):
            clean[k] = str(ov[k])
    if ov.get("excluded_images"):
        clean["excluded_images"] = [str(x) for x in ov["excluded_images"]]
    if ov.get("manual_starts"):
        clean["manual_starts"] = [float(x) for x in ov["manual_starts"]]
    core.db("UPDATE projects SET overrides=? WHERE path=?", (json.dumps(clean), path))
    scan.refresh_one(path, S_now())
    return dict(overrides=clean, counts=counts())


@route("POST", "/api/plan")
def project_plan(q, b):
    path = project_path(b.get("path", ""))
    ov = worker.overrides_of(path)
    S = core.effective(core.load_settings(), ov)
    try:
        P = plan.build_plan(path, S, ov)
    except StudioError as e:
        raise ApiError(f"[{e.category}] {e.message}")
    return dict(duration=P["duration"], mode=P["mode"], estimated=P["estimated"], score=P["score"], warnings=P["warnings"],
                transcribed=P["transcribed"], music=P["music"], sentences=P["sentences"], voice_captions=P["voice_captions"],
                sync=[dict(name=i["name"], caption=i["caption"], matched=i["matched"], confidence=i["confidence"], time=i["time"], shown=i.get("shown", True))
                      for i in (P["sync_info"] or [])],
                segments=[dict(name=Path(s["image"]).name, image=s["image"], start=s["start"], end=s["end"]) for s in P["segments"]],
                ending=S.ending.duration if S.ending.enabled else 0)


@route("POST", "/api/preview")
def preview(q, b):
    path = project_path(b.get("path", ""))
    try:
        out, warns = worker.make_preview(path, float(b.get("seconds", 8)))
    except StudioError as e:
        raise ApiError(f"[{e.category}] {e.message}")
    return dict(url=f"/api/media?path={out}&t={int(time.time())}", warnings=warns)


@route("POST", "/api/preview/audio")
def preview_audio(q, b):
    path = project_path(b.get("path", ""))
    try:
        out, music = worker.make_audio_preview(path, float(b.get("seconds", 20)))
    except StudioError as e:
        raise ApiError(f"[{e.category}] {e.message}")
    return dict(url=f"/api/media?path={out}&t={int(time.time())}", music=music)


@route("POST", "/api/batch")
def batch(q, b):
    m, act, paths = worker.manager, b.get("action"), b.get("paths")
    if act == "start_all":
        n = m.enqueue(None)
    elif act == "start_selected":
        n = m.enqueue(paths or [])
    elif act == "rerender":
        n = m.enqueue(paths or [], force=True)
    elif act == "retry_failed":
        n = m.retry_failed()
    elif act == "pause":
        m.pause(); n = 0
    elif act == "resume":
        m.resume(); n = 0
    elif act == "stop":
        m.stop(); n = 0
    elif act in ("skip", "cancel"):
        for p in paths or []:
            m.cancel(p, act)
        n = len(paths or [])
    elif act == "clear_finished":
        core.db("UPDATE projects SET status='ready', message='', progress=0 WHERE status IN ('skipped','failed')")
        n = 0
    else:
        raise ApiError("Unknown action")
    core.log("info", "", "", f"Batch action: {act} ({n} project(s))")
    return dict(queued=n, counts=counts(), paused=m.paused)


def timing(rows, now=None):
    """Per-project elapsed time + estimated time left, and a batch summary.
    Estimates: a running video is extrapolated from its own progress; queued videos use the average render time of
    the videos already finished in this batch, scheduled over the configured number of workers."""
    now = now or time.time()
    bs, be = core.kv_get("batch_start"), core.kv_get("batch_end")
    workers = core.load_settings()["batch"]["workers"]
    done = sorted((r for r in rows if r["status"] == "completed" and r.get("elapsed") and r.get("finished_at") and (bs is None or r["finished_at"] >= bs)),
                  key=lambda r: r["finished_at"])
    recent = [r["elapsed"] for r in done[-20:]]
    avg = sum(recent) / len(recent) if recent else None
    running = [r for r in rows if r["status"] == "processing" and r.get("started_at")]
    free, out = [], {}
    for r in running:
        e, p = now - r["started_at"], r["progress"] or 0
        if p > 0.05:
            left = e * (1 - p) / p
            if avg:  # blend with history while the progress figure is still noisy
                w = min(1.0, p / 0.3)
                left = w * left + (1 - w) * max(avg - e, avg * 0.1)
        else:
            left = max(avg - e, avg * 0.1) if avg else None
        out[r["path"]] = dict(elapsed=e, eta=left)
        free.append(left if left is not None else None)
    pending = sorted((r for r in rows if r["status"] == "pending"), key=lambda r: (r.get("queued_at") or 0))
    lanes = [x for x in free] + [0.0] * max(0, workers - len(free))
    known = all(x is not None for x in lanes) and avg is not None
    for r in pending:
        if known:
            i = min(range(len(lanes)), key=lambda j: lanes[j])
            lanes[i] += avg
            out[r["path"]] = dict(elapsed=None, eta=lanes[i], est=avg)
        else:
            out[r["path"]] = dict(elapsed=None, eta=None, est=avg)
    active = bool(running or pending)
    batch_eta = (max(lanes) if known and lanes else (0.0 if not active else None)) if active else 0.0
    if active and not known and not pending and running and running[0] and out[running[0]["path"]]["eta"] is not None:
        batch_eta = max(v["eta"] for v in out.values() if v["eta"] is not None)
    rendered = [r for r in rows if r["status"] in ("completed", "failed") and r.get("elapsed") is not None
                and (bs is None or (r.get("finished_at") or 0) >= bs)]
    return dict(per=out, batch=dict(started=bs, elapsed=((be if be is not None else now) - bs) if bs is not None else None, active=active,
                                    eta=batch_eta, render_sum=sum(r["elapsed"] for r in rendered), avg=avg, finished=len(done),
                                    rendered=len(rendered)))


@route("GET", "/api/batch")
def batch_status(q, b):
    rows = core.db("SELECT path,name,status,progress,message,category,output,music,started_at,finished_at,elapsed,video_seconds,queued_at FROM projects")
    rows.sort(key=lambda r: scan.nat_key(r["name"]))
    now = core.kv_get("batch_end") if worker.manager.stopping else time.time()
    now = now or time.time()
    t = timing(rows, now)
    if worker.manager.stopping:
        t["batch"]["active"] = False
        t["batch"]["eta"] = 0
    for r in rows:
        x = t["per"].get(r["path"], {})
        if x.get("elapsed") is not None:
            r["elapsed"] = x["elapsed"]
        r["eta"], r["est"] = x.get("eta"), x.get("est")
        r.pop("started_at"); r.pop("finished_at"); r.pop("queued_at")
    return dict(counts=counts(), paused=worker.manager.paused, rows=rows, timing=t["batch"])


@route("GET", "/api/logs")
def logs(q, b):
    return core.query_logs(q.get("q", ""), q.get("project", ""), q.get("level", ""), int(q.get("limit", 500)))


@route("POST", "/api/logs/clear")
def logs_clear(q, b):
    core.db("DELETE FROM logs")
    return dict(ok=True)


@route("GET", "/api/logs/ffmpeg")
def ffmpeg_log(q, b):
    f = worker.log_file(q.get("project", ""))
    return dict(text=f.read_text(encoding="utf-8", errors="replace")[-30000:] if f.exists() else "No FFmpeg log for this project yet.")


@route("POST", "/api/open-folder")
def open_folder(q, b):
    p = Path(b.get("path", "")).resolve()
    if not p.exists() or not any(p == r or p.is_relative_to(r) for r in allowed_roots()):
        raise ApiError("Folder not found or outside the configured folders", 403)
    p = p if p.is_dir() else p.parent
    if os.name == "nt":
        os.startfile(str(p))  # noqa
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return dict(ok=True)


@route("POST", "/api/clear-cache")
def clear_cache(q, b):
    n = 0
    for d in ("thumbs", "cache", "transcripts", "previews"):
        for f in (core.DATA / d).glob("*"):
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
            n += 1
    return dict(removed=n)


# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BulkStudio"

    def handle(self):
        # A browser tab can close while keep-alive is waiting for the next
        # request. On Windows that can raise WinError 10053 before _dispatch
        # runs, so contain the expected client disconnect at the handler edge.
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def log_message(self, *a):
        pass

    def _send(self, status, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Browser closed/reloaded while a long request was finishing.
            return

    def _file(self, p, download=False):
        size = p.stat().st_size
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else end
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _thumb(self, q):
        src = safe_media(q.get("path"))
        w = max(60, min(600, int(q.get("w", 240))))
        st = src.stat()
        key = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{w}".encode()).hexdigest()
        out = core.DATA / "thumbs" / f"{key}.jpg"
        if not out.exists():
            r = subprocess.run([render.tool("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vf", f"scale={w}:-2", "-frames:v", "1",
                                "-q:v", "5", str(out)], capture_output=True, creationflags=render.NOWIN)
            if r.returncode or not out.exists():
                raise ApiError("Could not create thumbnail (corrupted image?)", 422)
        self._file(out)

    def _host_ok(self):
        return self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]") in ("localhost", "127.0.0.1", "::1")

    def _dispatch(self, method):
        if not self._host_ok():
            return self._send(403, {"error": "Forbidden host"})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if method == "GET" and not u.path.startswith("/api/"):
                name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
                p = (STATIC / name).resolve()
                if not p.is_relative_to(STATIC.resolve()) or not p.is_file():
                    return self._send(404, {"error": "Not found"})
                return self._file(p)
            if method == "GET" and u.path == "/api/media":
                return self._file(safe_media(q.get("path")))
            if method == "GET" and u.path == "/api/thumb":
                return self._thumb(q)
            if method == "GET" and u.path == "/api/logs/export":
                text, ctype = core.export_logs(q.get("fmt", "txt"), project=q.get("project", ""), level=q.get("level", ""), q=q.get("q", ""))
                ext = "csv" if ctype == "text/csv" else "txt"
                return self._send(200, text, ctype + "; charset=utf-8", {"Content-Disposition": f'attachment; filename="studio-errors.{ext}"'})
            if method == "GET" and u.path == "/api/presets/export":
                data = presets_export(q, {})
                nm = re.sub(r"[^\w.-]", "_", q.get("name") or "all-presets")
                return self._send(200, json.dumps(data, indent=2), "application/json", {"Content-Disposition": f'attachment; filename="studio-preset-{nm}.json"'})
            fn = ROUTES.get((method, u.path))
            if not fn:
                return self._send(404, {"error": "Unknown endpoint"})
            body = {}
            if method == "POST":
                if self.headers.get("X-Studio") != "1":
                    return self._send(403, {"error": "Missing X-Studio header"})
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            self._send(200, fn(q, body))
        except ApiError as e:
            self._send(e.status, {"error": str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            core.log("error", "", "unexpected", f"API error on {u.path}: {e}", __import__("traceback").format_exc())
            self._send(500, {"error": f"Internal error: {e}"})

    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")


_instance_lock_handle = None


def _acquire_instance_lock():
    """Hold an OS-level lock for this data directory so another process cannot recover live jobs."""
    global _instance_lock_handle
    lock_path = core.DATA / "studio.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        _instance_lock_handle = handle
    except (OSError, ImportError):
        handle.close()
        raise SystemExit(
            "BulkVideoStudio is already running with this data folder. "
            "Close the other instance before starting another."
        )


def _release_instance_lock():
    global _instance_lock_handle
    handle, _instance_lock_handle = _instance_lock_handle, None
    if handle is None:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


def serve(port=8765, open_browser=True):
    _acquire_instance_lock()
    core.db("SELECT 1")  # initialise the database only after obtaining the instance lock
    core.recover_processing_jobs()  # only this exclusive instance may recover interrupted jobs
    worker.manager.start()
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if not srv:
        raise SystemExit("No free port found")
    srv.daemon_threads = True
    url = f"http://localhost:{srv.server_port}/"
    print(f"Local Bulk Video Production Studio running at {url}  (Ctrl+C to stop)")
    resumed = core.db("SELECT COUNT(*) n FROM projects WHERE status='pending'")[0]["n"]
    if resumed:
        print(f"Resuming {resumed} unfinished project(s) from the previous session (use Pause in Batch Processing to hold them).")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.manager.shutdown()
        # Keep the instance lock until all render workers have actually exited.
        # If a worker cannot stop, retaining the lock is safer than allowing a
        # second process to recover rows that may still belong to this process.
        workers_stopped = True
        try:
            worker.manager._wait_for_idle(timeout=180)
        except Exception as exc:
            workers_stopped = False
            try:
                core.log("error", "", "worker_failure",
                         f"Workers did not stop before server shutdown; retaining instance lock: {exc}")
            except Exception:
                pass
        srv.server_close()
        if workers_stopped:
            _release_instance_lock()
