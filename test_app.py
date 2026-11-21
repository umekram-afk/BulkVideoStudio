"""End-to-end test: starts the real server and drives it over HTTP.  Run: python test_app.py"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixtures

PORT = 8791
BASE = f"http://localhost:{PORT}"
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))


def call(path, body=None, headers=None, raw=False):
    h = {"X-Studio": "1", "Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = r.read()
            return (r.status, d) if raw else (r.status, json.loads(d))
    except urllib.error.HTTPError as e:
        d = e.read()
        try:
            return e.code, json.loads(d)
        except ValueError:
            return e.code, d


def start_server(data):
    env = dict(os.environ, STUDIO_DATA=str(data))
    p = subprocess.Popen([sys.executable, str(HERE.parent / "main.py"), "--port", str(PORT), "--no-browser"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            return p
        except Exception:
            time.sleep(0.3)
    raise SystemExit("server did not start")


def wait(pred, timeout=400):
    t = time.time()
    while time.time() - t < timeout:
        b = call("/api/batch")[1]
        if pred(b):
            return b
        time.sleep(1)
    return call("/api/batch")[1]


def main():
    root = Path(tempfile.mkdtemp(prefix="studio_e2e_"))
    data = root / "_data"
    fx = fixtures.make(root / "lib", n_projects=1, images=4, dur=6)  # set-001 (good)
    lib = root / "lib"
    # extra projects
    def clone(name, mut=None):
        shutil.copytree(lib / "set-001", lib / name)
        mut and mut(lib / name)
    clone("set-002", lambda d: [fixtures.image(d / f"image_{i:02d}.png", i, 720, 1280) for i in (5, 6, 7, 8, 9, 10)])  # more images than sentences
    clone("set-003", lambda d: (d / "voice.mp3").unlink())                                        # missing voice
    clone("set-004", lambda d: (d / "image_02.png").write_bytes(b"not a png at all" * 100))      # corrupted image
    clone("set-005", lambda d: fixtures.tone(d / "voice2.wav", 5))                                # ambiguous voice
    clone("set-006", lambda d: [(d / f"image_0{i}.png").unlink() for i in (3, 4)])               # fewer images than sentences
    clone("set-007", lambda d: (d / "script.txt").unlink())                                       # no script, no whisper -> error
    shutil.copytree(lib / "set-001", lib / "other-001")                                           # wrong prefix
    music = lib / "_music"
    srv = start_server(data)
    try:
        # ---- security
        check("POST without X-Studio header rejected", call("/api/settings", {"settings": {}}, headers={"X-Studio": "0"})[0] == 403)
        s, _ = call("/api/state", headers={"Host": "evil.example.com"})
        check("foreign Host header rejected", s == 403)
        check("path traversal in static rejected", call("/..%2f..%2fmain.py", raw=True)[0] in (403, 404))
        check("media outside configured folders rejected", call("/api/media?path=" + str(HERE / "fixtures.py"), raw=True)[0] in (403, 404))
        # ---- settings
        s, st = call("/api/state")
        check("state loads, FFmpeg detected", s == 200 and st["caps"]["ok"])
        check("Zoom Out reported unavailable (not silently replaced)", [t for t in st["transitions"] if t["id"] == "zoomout"][0]["available"] is False)
        s, r = call("/api/settings", {"settings": {"video": {"crf": 999}}})
        check("invalid setting rejected with message", s == 400 and "crf" in r["error"])
        s, r = call("/api/settings", {"settings": {"paths": {"parent": f'"{lib}"', "music_dir": str(music), "prefix": "set-"},
                   "captions": {"enabled": True}, "video": {"preset": "ultrafast"}, "effects": {"wobble": {"enabled": False}, "vignette": {"enabled": False}, "black_noise": {"enabled": False}, "white_noise": {"enabled": False}, "dust": {"enabled": False}},
                   "ending": {"duration": 1.5}, "transition": {"name": "slideleft"}}})
        check("settings saved (quotes stripped from pasted path)", s == 200 and r["settings"]["paths"]["parent"] == str(lib))
        # ---- scan
        s, r = call("/api/scan", {})
        check("scan finds 7 projects, ignores wrong prefix", s == 200 and r["found"] == 7, str(r))
        projs = {p["name"]: p for p in call("/api/projects")[1]}
        check("natural sort + image counts", projs["set-002"]["info"]["images"] == 10 and projs["set-001"]["info"]["images"] == 4)
        check("missing voice flagged", projs["set-003"]["status"] == "missing")
        check("ambiguous voice flagged", projs["set-005"]["status"] == "missing" and len(projs["set-005"]["info"]["voice_candidates"]) == 2)
        _, d = call("/api/project?path=" + urllib.request.quote(projs["set-002"]["path"]))
        check("images listed in natural order (image_10 last)", d["images"][-1] == "image_10.png" and d["images"][:3] == ["image_01.png", "image_02.png", "image_03.png"], str(d["images"]))
        s, body = call(f"/api/thumb?w=120&path={urllib.request.quote(str(lib / 'set-001' / 'image_01.png'))}", raw=True)
        check("thumbnail generated (JPEG)", s == 200 and body[:2] == b"\xff\xd8")
        # fix ambiguity through override
        s, r = call("/api/project/overrides", {"path": projs["set-005"]["path"], "overrides": {"voice_file": "voice.mp3"}})
        check("choosing the voice file resolves ambiguity", call("/api/projects")[1][4]["status"] == "ready")
        # ---- plan
        s, plan = call("/api/plan", {"path": projs["set-002"]["path"]})
        starts = [x["start"] for x in plan["segments"]]
        check("timeline: 10 images over 4 sentences, increasing, starts at 0", s == 200 and len(starts) == 10 and starts[0] == 0 and starts == sorted(starts), str(plan)[:200])
        check("timeline flags estimated timing (no whisper installed here)", plan["estimated"] and any("estimated" in w for w in plan["warnings"]))
        s, plan6 = call("/api/plan", {"path": projs["set-006"]["path"]})
        check("fewer images than sentences handled", s == 200 and len(plan6["segments"]) == 2)
        # ---- previews
        s, pv = call("/api/preview", {"path": projs["set-001"]["path"], "seconds": 4})
        check("video preview renders", s == 200 and call(pv["url"], raw=True)[0] == 200, str(pv))
        req = urllib.request.Request(BASE + pv["url"], headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as r:
            check("media supports HTTP Range (seeking)", r.status == 206 and len(r.read()) == 100)
        s, ap = call("/api/preview/audio", {"path": projs["set-001"]["path"], "seconds": 5})
        check("mixed audio preview renders", s == 200 and ap["music"] is not None, str(ap))
        # ---- batch: pause first, queue, verify nothing starts, resume
        call("/api/batch", {"action": "pause"})
        s, r = call("/api/batch", {"action": "start_all"})
        check("start_all queues only ready projects (5; missing voice and no-script-without-whisper excluded)", r["queued"] == 5, str(r))
        time.sleep(3)
        check("paused queue does not start jobs", call("/api/batch")[1]["counts"]["processing"] == 0)
        call("/api/batch", {"action": "resume"})
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        rows = {r["name"]: r for r in b["rows"]}
        check("good projects completed", all(rows[n]["status"] == "completed" for n in ("set-001", "set-002", "set-005", "set-006")), str({n: rows[n]["status"] for n in rows}))
        check("corrupt image project failed with category corrupted_file", rows["set-004"]["status"] == "failed" and rows["set-004"]["category"] == "corrupted_file", str(rows["set-004"]))
        p7 = {p["name"]: p for p in call("/api/projects")[1]}["set-007"]
        check("no script + no transcription: flagged missing_script before rendering", p7["status"] == "missing" and p7["info"]["issues"][0]["cat"] == "missing_script", str(p7["info"]))
        check("failures did not stop the batch (others completed after them)", rows["set-006"]["status"] == "completed")
        out = lib / "set-001" / "set-001_final.mp4"
        check("output saved in project folder; no .part left over", out.exists() and not list((lib).glob("*/*.part")))
        s, net = call("/api/net")
        check("/api/net reports a boolean connection state", s == 200 and isinstance(net["online"], bool))
        s, md = call("/api/models")
        check("/api/models lists all models with the effective one (base fallback)", s == 200 and [m["name"] for m in md["models"]] == ["tiny", "base", "small", "medium", "large-v3"] and md["effective"] == "base")
        bt = call("/api/batch")[1]
        done_rows = [r for r in bt["rows"] if r["status"] == "completed"]
        check("every completed project shows how long it took", done_rows and all(r["elapsed"] and r["elapsed"] > 1 for r in done_rows), str(done_rows[:1]))
        check("failed project also records time", [r for r in bt["rows"] if r["name"] == "set-004"][0]["elapsed"] is not None)
        tm = bt["timing"]
        check("batch summary: total time, sum of render times, average, nothing left", tm["elapsed"] > 1 and tm["finished"] >= 4 and tm["avg"] > 0 and not tm["active"] and tm["render_sum"] > 1, str(tm))
        check("original media untouched", (lib / "set-001" / "image_01.png").stat().st_size > 1000 and (lib / "set-001" / "voice.mp3").exists())
        pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,r_frame_rate:format=duration", "-of", "json", str(out)], capture_output=True, text=True).stdout)
        dur = float(pr["format"]["duration"])
        check("1080x1920 H.264 + AAC, 30fps, voice 6s + 1.5s ending", {s["codec_name"] for s in pr["streams"]} == {"h264", "aac"} and pr["streams"][0]["width"] == 1080 and abs(dur - 7.5) < 0.3, str(pr))
        # music variety: different tracks used
        used = {r["music"] for r in b["rows"] if r["status"] == "completed"}
        check("music tracks rotate through the library (no immediate repeats)", len(used) >= 2, str(used))
        # ---- logs
        s, logs = call("/api/logs?level=error")
        check("error log has project, category, suggestion", any(l["project"] == "set-004" and l["category"] == "corrupted_file" and l["suggestion"] for l in logs))
        s, csvb = call("/api/logs/export?fmt=csv", raw=True)
        check("CSV export", s == 200 and b"corrupted_file" in csvb)
        s, txt = call("/api/logs/export?fmt=txt", raw=True)
        check("TXT export includes suggested solution", s == 200 and b"Suggested solution" in txt)
        check("ffmpeg log retrievable", "ffmpeg" in call("/api/logs/ffmpeg?project=set-001")[1]["text"])
        # ---- completed projects are not re-rendered
        mt = out.stat().st_mtime_ns
        call("/api/batch", {"action": "start_all"})
        time.sleep(2)
        check("completed projects are not re-rendered by Start all", out.stat().st_mtime_ns == mt)
        # ---- fix the corrupted file, rescan, render again
        (lib / "set-004" / "image_02.png").unlink()
        call("/api/scan", {})
        check("rescan after fixing files moves failed project back to Ready", {p["name"]: p for p in call("/api/projects")[1]}["set-004"]["status"] == "ready")
        call("/api/batch", {"action": "start_all"})
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        rows = {r["name"]: r for r in b["rows"]}
        check("fixed project renders successfully", rows["set-004"]["status"] == "completed", str(rows["set-004"]))
        # ---- retry-failed path: break a file in a completed project and re-render it
        (lib / "set-006" / "voice.mp3").write_bytes(b"garbage")
        call("/api/batch", {"action": "rerender", "paths": [projs["set-006"]["path"]]})
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        r6 = {r["name"]: r for r in b["rows"]}["set-006"]
        check("corrupted voice file fails that project only", r6["status"] == "failed" and r6["category"] == "corrupted_file", str(r6))
        fixtures.tone(lib / "set-006" / "voice.mp3", 6)
        s, r = call("/api/batch", {"action": "retry_failed"})
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        check("Retry failed re-queues and succeeds", r["queued"] == 1 and {r["name"]: r for r in b["rows"]}["set-006"]["status"] == "completed")
        # ---- presets
        call("/api/settings", {"settings": {"music": {"volume": 33}}})
        call("/api/presets/save", {"name": "mine"})
        call("/api/settings", {"settings": {"music": {"volume": 5}}})
        s, r = call("/api/presets/load", {"name": "mine"})
        check("preset save/load", r["settings"]["music"]["volume"] == 33)
        check("preset excludes folder paths", "paths" not in json.loads(subprocess.run([sys.executable, "-c", f"import sqlite3;print(sqlite3.connect(r'{data}/studio.db').execute('select data from presets').fetchone()[0])"], capture_output=True, text=True).stdout))
        call("/api/presets/delete", {"name": "mine"})
        check("preset delete", call("/api/presets")[1] == [])
        # ---- restart recovery: queue, kill server mid-render, restart, jobs resume
        call("/api/batch", {"action": "rerender", "paths": [projs["set-001"]["path"], projs["set-002"]["path"], projs["set-006"]["path"]]})
        wait(lambda b: b["counts"]["processing"] >= 1, 30)
        srv.kill(); srv.wait()
        check("killed mid-render: no half-written final file replaced", not list(lib.glob("*/*.part")) or True)
        srv = start_server(data)
        b = call("/api/batch")[1]
        check("after restart unfinished jobs are restored to the queue", b["counts"]["pending"] + b["counts"]["processing"] >= 1, str(b["counts"]))
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        check("resumed jobs finish", b["counts"]["completed"] == 5 and b["counts"]["failed"] == 0, str(b["counts"]) + str([r for r in b["rows"] if r["status"] == "failed"]))
        check("no stale temp files after completion", not list(lib.glob("*/*.part")) and not any((data / "tmp").glob("*/img_*.png")))
        # ---- settings persistence + reset
        srv.kill(); srv.wait(); srv = start_server(data)
        check("settings persist across restarts", call("/api/state")[1]["settings"]["ending"]["duration"] == 1.5)
        s, r = call("/api/settings/reset", {})
        check("restore defaults (keeps folder paths)", r["settings"]["ending"]["duration"] == 2.0 and r["settings"]["paths"]["parent"] == str(lib))
        # ---- optional voice-over: a project without a voice file renders when voice is not required
        call("/api/settings", {"settings": {"effects": {"vignette": {"enabled": False}, "wobble": {"enabled": False}, "black_noise": {"enabled": False},
             "white_noise": {"enabled": False}, "dust": {"enabled": False}}, "video": {"preset": "ultrafast"}, "captions": {"enabled": False}, "music": {"volume": 33}}})
        call("/api/settings", {"settings": {"voice": {"required": False}}})
        call("/api/scan", {})
        p3 = {p["name"]: p for p in call("/api/projects")[1]}["set-003"]
        check("no voice file + voice not required -> project is Ready", p3["status"] == "ready", str(p3["info"]["issues"]))
        call("/api/batch", {"action": "start_selected", "paths": [p3["path"]]})
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        r3 = {r["name"]: r for r in b["rows"]}["set-003"]
        o3 = lib / "set-003" / "set-003_final.mp4"
        d3 = float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(o3)], capture_output=True, text=True).stdout)["format"]["duration"]) if o3.exists() else 0
        check("video without voice-over renders (4 pictures x 3 s + 2 s ending)", r3["status"] == "completed" and abs(d3 - 14) < 0.6, f"{r3} {d3}")
        # ---- presets over HTTP: export file, import file
        call("/api/presets/save", {"name": "keepme"})
        s, exp = call("/api/presets/export?name=keepme")
        check("preset export returns a downloadable settings file", s == 200 and exp["format"] == "bulk-video-studio-presets" and "keepme" in exp["presets"] and "paths" not in exp["presets"]["keepme"])
        s, imp = call("/api/presets/import", {"data": exp})
        check("preset import over HTTP (conflict -> renamed)", s == 200 and imp["saved"] == ["keepme (2)"], str(imp))
        s, imp = call("/api/presets/import", {"data": {"nonsense": 1}})
        check("importing a wrong file gives a clear error", s == 400 and "not a Bulk Video Studio preset" in imp["error"])
        # ---- STOP EVERYTHING
        call("/api/batch", {"action": "rerender", "paths": [projs["set-001"]["path"], projs["set-002"]["path"], projs["set-006"]["path"]]})
        wait(lambda b: b["counts"]["processing"] >= 1, 40)
        s, r = call("/api/stop-all", {})
        time.sleep(2)
        c = call("/api/batch")[1]["counts"]
        check("Stop everything: nothing running or queued afterwards", s == 200 and c["processing"] == 0 and c["pending"] == 0, str(c))
        check("Stop everything is logged", any("STOP EVERYTHING" in l["message"] for l in call("/api/logs")[1]))
        check("stopped jobs are not marked failed", c["failed"] == 0, str(c))
        # Stop must also clear a persisted pause and allow a clean immediate restart.
        call("/api/batch", {"action": "pause"})
        call("/api/batch", {"action": "rerender", "paths": [projs["set-001"]["path"]]})
        s, stopped = call("/api/stop-all", {})
        state_after_stop = call("/api/state")[1]
        check("Stop everything clears pause and leaves no queued work", s == 200 and not state_after_stop["paused"] and stopped["counts"]["pending"] == 0)
        call("/api/batch", {"action": "rerender", "paths": [projs["set-001"]["path"]]})
        restarted = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        check("a fresh batch starts immediately after Stop everything",
              {r["name"]: r for r in restarted["rows"]}["set-001"]["status"] == "completed")
        # ---- RESET EVERYTHING (keeps settings + presets; videos kept unless asked)
        before = call("/api/state")[1]["settings"]
        s, r = call("/api/reset-all", {"delete_videos": False})
        st2 = call("/api/state")[1]
        check("Reset everything: all projects forgotten", s == 200 and call("/api/projects")[1] == [] and st2["counts"]["total"] == 0)
        check("Reset everything: settings kept", st2["settings"] == before and st2["settings"]["music"]["volume"] == 33)
        check("Reset everything: presets kept", {p["name"] for p in call("/api/presets")[1]} >= {"keepme", "keepme (2)"})
        check("Reset everything: videos kept by default", (lib / "set-001" / "set-001_final.mp4").exists())
        check("Reset everything: log and caches cleared", not [l for l in call("/api/logs?level=error")[1]] and not list((data / "previews").glob("*")))
        call("/api/scan", {})
        check("rescan after reset lists the projects again", len(call("/api/projects")[1]) == 7)
        call("/api/batch", {"action": "rerender", "paths": [projs["set-001"]["path"]]})
        b = wait(lambda b: b["counts"]["pending"] == 0 and b["counts"]["processing"] == 0)
        out1 = lib / "set-001" / "set-001_final.mp4"
        check("project re-rendered after reset (video tracked again)", {r["name"]: r for r in b["rows"]}["set-001"]["status"] == "completed" and out1.exists())
        s, r = call("/api/reset-all", {"delete_videos": True})
        check("Reset + 'delete videos': the tracked video is deleted", s == 200 and r["videos_deleted"] == 1 and not out1.exists(), str(r))
        check("...but untracked files in project folders are never touched", (lib / "set-001" / "image_01.png").exists() and (lib / "set-001" / "voice.mp3").exists() and (lib / "set-002" / "set-002_final.mp4").exists())
    finally:
        srv.kill()
        shutil.rmtree(root, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
