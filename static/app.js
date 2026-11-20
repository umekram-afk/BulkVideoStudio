"use strict";
/* Local Bulk Video Production Studio - vanilla JS UI. Every control is bound to a backend setting or action. */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap = s => String(s).replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
function fmtDur(sec) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}
function timeCell(p) {
  if (p.status === "completed" && p.elapsed != null) return `<span class="ok" title="Time this video took to render">${fmtDur(p.elapsed)}</span>`;
  if (p.status === "failed" && p.elapsed != null) return `<span class="err">failed after ${fmtDur(p.elapsed)}</span>`;
  if (p.status === "processing") return `${fmtDur(p.elapsed)} elapsed · ${p.eta != null ? `<b title="Estimate based on this video's progress and earlier videos">~${fmtDur(p.eta)} left</b>` : '<span class="mute">estimating…</span>'}`;
  if (p.status === "pending") return p.eta != null ? `<span class="mute" title="Estimated render time, and when it should be finished from now">~${fmtDur(p.est)} · ready in ~${fmtDur(p.eta)}</span>` : '<span class="mute" title="The estimate appears after the first video of this batch has finished">estimating…</span>';
  return "";
}
let lastTiming = null;
function timingText() {
  const t = lastTiming; if (!t || t.started == null) return "No batch has run yet. Timing and estimates appear here once you start rendering.";
  const parts = [`Total time${t.active ? " so far" : ""}: <b>${fmtDur(t.elapsed)}</b>`];
  if (t.finished) parts.push(`Render time of ${t.finished} finished video(s): <b>${fmtDur(t.render_sum)}</b>`, `Average per video: <b>${fmtDur(t.avg)}</b>`);
  if (t.active) parts.push(t.eta != null ? `Estimated time remaining: <b>~${fmtDur(t.eta)}</b> (finish about ${new Date(Date.now() + t.eta * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })})` : "Estimated time remaining: <span class='mute'>estimating after the first video…</span>");
  return parts.join(" · ");
}
function updateTimingUI() { ["btime", "dtime"].forEach(id => { const el = $("#" + id); if (el) el.innerHTML = timingText(); }); }
const fmtT = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
let st = null, SCH = {}, projects = [], page = "dashboard", sel = new Set(), previewProject = "", pollKey = "";

// ------------------------------------------------------------------ helpers
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Studio": "1" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* non-JSON */ }
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "err" ? 8000 : 3500);
}
async function act(btn, fn) {  // run async action with loading indicator + error toast
  const old = btn && btn.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>' + btn.textContent; }
  try { return await fn(); } catch (e) { toast(e.message, "err"); } finally { if (btn) { btn.disabled = false; btn.innerHTML = old; } }
}
const val = path => path.split(".").reduce((o, k) => o?.[k], st.settings);
const LAB = { "9:16": "9:16 vertical (Reels / Shorts)", "16:9": "16:9 landscape", "1:1": "1:1 square", crop: "Crop to fill", blur: "Fit with blurred background", solid: "Fit with solid background",
  sentence: "One image per sentence", group: "One image for several sentences", even: "Even distribution across the voice", folder: "script.txt in each project folder",
  dir: "Separate directory (matching file names)", transcribe: "Always transcribe the voice", auto: "Auto (GPU if available)", cpu: "CPU only", estimate: "Estimate timing and warn", fail: "Fail the project",
  per_image: "New random transition per image", per_project: "One random transition per project" };

const TIPS = {
  "voice.fade_in": "Seconds over which the voice-over fades in at the very start.", "voice.fade_out": "Seconds over which the voice-over fades out at its end.",
  "music.min_start": "Earliest second of the track a random start may pick.", "music.fade_in": "Seconds over which the music fades in.", "music.fade_out": "Seconds over which the music fades out at the end of the video.",
  "music.duck_amount": "How strongly the music is lowered while the voice speaks (higher = quieter music under the voice).",
  "video.fps": "Frames per second of the finished video. 30 is standard for Reels/Shorts.", "video.preset": "Encoder speed preset. Faster = quicker rendering but slightly larger files at the same quality.",
  "video.fit": "How a picture that does not match the video shape is fitted. Pictures are never stretched.",
  "video.hwaccel": "Controls only the final H.264 video encoder. GPU uses NVENC/QSV/AMF when available; CPU uses libx264. Image scaling, transitions, filters, captions and much of the rendering pipeline still run on the CPU. Whisper may use the GPU independently.",
  "video.hw_encoder": "Which graphics-card encoder to use when GPU encoding is active. Automatic picks the first one that works. Only encoders detected on this computer can work.",
  "sync.group_size": "How many sentences share one picture in 'One image for several sentences' mode.",
  "whisper.language": "Spoken language code (en, es, fr, de, hi...) or 'auto' to detect it. A fixed language is slightly more accurate.",
  "sync.method": "Automatic: the real narration decides when each picture appears, using the description in its file name. Manual: pictures are spread over the script sentences (or evenly) - the previous behaviour.",
  "sync.match_threshold": "How sure the program must be that a picture's description is being spoken before it switches to that picture. Higher = fewer, safer switches; lower = more switches but a greater risk of a wrong one.",
  "sync.min_hold": "A picture stays on screen at least this long. Prevents rapid flicker and duplicate matches.",
  "sync.lead_ms": "Switch this many milliseconds before the matched word is spoken, so the picture is already there when the idea begins.",
  "sync.unmatched": "Keep: a picture with no confident match is skipped and the current picture stays. Between neighbours: it is still shown, placed between the pictures around it.",
  "captions.voice_sync": "On: captions are generated from the actual voice (Whisper timestamps, never from the script), shown progressively as words are spoken, and kept in step with picture changes. Off: the previous script-based caption behaviour.",
  "whisper.vad": "Skip silent parts before transcribing. Usually OFF is more accurate for clean voice-overs.",
  "effects.vignette.intensity": "How dark the edges become.", "effects.vignette.range": "How far the darkening reaches towards the centre.", "effects.vignette.feather": "Softness of the vignette edge.",
  "effects.white_noise.intensity": "How many bright specks of grain.", "effects.white_noise.opacity": "How visible each speck is.", "effects.white_noise.speed": "How fast the grain changes.",
  "effects.black_noise.speed": "How fast the grain changes. Static = frozen specks (fastest to render).",
  "effects.wobble.intensity": "How far the picture sways, within the available margin.", "effects.wobble.range": "How much the frame is enlarged to leave room for movement.", "effects.wobble.speed": "How quickly the picture sways.",
  "transition.enabled": "Off = hard cuts between pictures.", "transition.random_scope": "Random mode: a new random transition for every picture, or one random transition for each video.",
  "transition.duration": "Length of each transition in seconds (shortened automatically if a picture is shown only briefly).",
  "ending.enabled": "Add a black screen after the voice-over ends.", "ending.duration": "Seconds of black ending after the voice finishes.", "ending.fade_video": "Fade the last picture into black instead of cutting.", "ending.fade_audio": "Fade the sound out during the ending.",
  "captions.enabled": "Show captions generated from the voice-over (or your script).", "captions.font": "Font family. It must be installed on this computer (FFmpeg draws the text). Use 'Load all installed fonts' for the full list.",
  "captions.color": "Colour of the caption text.", "captions.bold": "Use the bold weight of the font.", "captions.uppercase": "Show all captions in capital letters.",
  "captions.outline": "Thickness of the outline around the letters.", "captions.outline_color": "Colour of the outline.", "captions.shadow": "Distance of the soft shadow behind the text.",
  "captions.bg_enabled": "Draw a coloured box behind the text.", "captions.bg_color": "Colour of the box.",
  "captions.highlight": "Colour the word that is being spoken right now.", "captions.highlight_color": "Colour of the currently spoken word.",
  "captions.highlight_lead": "Moves the highlight earlier (+) or later (-) in milliseconds. If the highlight feels late, raise it; if it jumps ahead, lower it.",
  "captions.align": "How lines are aligned inside the caption block. This also decides what the X position means: left edge, centre or right edge.",
  "captions.max_words": "Maximum words on one line.", "captions.max_lines": "Maximum lines shown at the same time.",
  "output.suffix": "Added to the project name: set-001 + _final = set-001_final.mp4.", "paths.prefix": "Only sub-folders whose name starts with this are treated as projects.",
  "voice.volume": "100% = original level (after loudness normalisation).", "batch.workers": "How many videos are rendered at the same time. 1 is safest on a normal computer.",
  "start.file": "Name of the text file inside each project folder that holds the start words. Change it if you use another name.",
  "start.pick": "If the file has several lines: use the first line, or pick one line at random for each video.",
  "start.max_words": "Only the first N words of the chosen line are shown (1-6).",
  "start.default_text": "Used for projects that have no start file. Leave empty to skip the start words for those projects.",
  "start.duration_ms": "How long the words stay on screen, in milliseconds (1000 ms = 1 second).", "start.delay_ms": "Wait this long after the video starts before showing the words.",
  "start.fade_in_ms": "Fade-in time in milliseconds (0 = appears instantly).", "start.fade_out_ms": "Fade-out time in milliseconds.",
  "start.font": "Font family. Use 'Load all installed fonts' to pick from everything installed on this computer.", "start.size": "Text size, defined for a 1080 px wide frame.",
  "start.color": "Colour of the text.", "start.bold": "Use the bold weight.", "start.italic": "Slanted text.", "start.uppercase": "Show in capital letters.", "start.spacing": "Extra space between letters (negative = tighter).",
  "start.outline": "Thickness of the outline.", "start.outline_color": "Colour of the outline.", "start.shadow": "Distance of the shadow.",
  "start.bg_enabled": "Draw a coloured box behind the words.", "start.bg_color": "Colour of the box.", "start.bg_opacity": "How solid the box is.", "start.padding": "Space between the words and the edge of the box.",
  "start.rotation": "Tilt in degrees. Positive = clockwise, negative = counter-clockwise.", "start.pos_x": "Horizontal position of the text centre: 0 = left edge, 100 = right edge. You can also drag the text in the preview.",
  "start.pos_y": "Vertical position of the text centre: 0 = top, 100 = bottom. You can also drag the text in the preview.", "start.enabled": "Show the start words at the beginning of every video in the batch.",
  "captions.pos_x": "Horizontal anchor: 0 = left edge, 100 = right edge of the video. With centred text this is the centre of the caption.",
  "captions.pos_y": "Vertical centre of the caption: 0 = top, 100 = bottom. You can also drag the caption in the preview.",
  "effects.dust.enabled": "Film dust overlay: tiny dark specks, hairs and grain that jump around at a choppy film rate.",
  "effects.dust.amount": "How many specks appear per frame (100% = like the reference design; 300% = very dusty).",
  "effects.dust.size": "Size of the specks (100% = reference size).", "effects.dust.min_opacity": "Faintest speck, in % opacity.", "effects.dust.max_opacity": "Darkest speck, in % opacity. Each speck gets a random value between min and max.",
  "effects.dust.opacity": "Overall strength of the whole dust layer.", "effects.dust.color": "Colour of the specks and grain. Black = dust on film; white = bright dust.",
  "effects.dust.softness": "Blur of the specks. 0 = hard edges; 40 = soft like film; 100+ = out-of-focus blobs.", "effects.dust.fps": "How many times per second the dust pattern changes. Low = choppy old-film look; 30 = smooth.",
  "effects.dust.jitter": "Random flicker of the whole layer's strength from frame to frame.",
  "effects.dust.hairs_enabled": "Occasional thin curved hairs/fibres.", "effects.dust.hairs_chance": "Chance that hairs appear in a given frame.", "effects.dust.hairs_max": "Maximum number of hairs in one frame.",
  "effects.dust.hairs_size": "Length and thickness of the hairs.", "effects.dust.hairs_color": "Colour of the hairs.",
  "effects.dust.grain_enabled": "Many tiny faint dots across the frame.", "effects.dust.grain_amount": "How many micro-grain dots per frame.", "effects.dust.grain_opacity": "Strongest micro-grain dot, in % opacity.",
  "effects.dust.scratches_enabled": "Long thin vertical film scratches (off by default).", "effects.dust.scratches_chance": "Chance that a scratch appears in a given frame.", "effects.dust.scratches_opacity": "How visible the scratches are.",
  "effects.dust.scratches_width": "Thickness of the scratches.", "effects.dust.scratches_color": "Colour of the scratches (old film scratches are usually light).",
  "effects.dust.random_per_video": "On: every video gets a different dust pattern. Off: the same pattern in every video.",
  "effects.vignette.enabled": "Darken the edges of every picture slightly for a cinematic look.", "effects.black_noise.enabled": "Fine black grain over the picture (pixel noise). For dust, hairs and scratches use 'Black noise - film dust'.",
  "effects.white_noise.enabled": "Fine white grain over the picture, like film texture.", "effects.wobble.enabled": "Gentle camera sway for a handheld cinematic feel.",
  "effects.zoom.enabled": "Slowly zoom into (or out of) your pictures.", "paths.parent": "Folder that contains all project folders (set-001, set-002, ...).",
  "paths.music_dir": "Folder with MP3/WAV/M4A/AAC background music.", "paths.script_dir": "Used when the script source is 'Separate directory'. Files are named like the project, e.g. set-001.txt.",
  "paths.voice_dir": "Used in 'Separate directory' mode. Files are named like the project, e.g. set-001.mp3. Matching is by name, never by position.", "paths.extra_output": "Optional: a copy of every finished video is also saved here.",
};
// ------------------------------------------------------------------ declarative form fields
function fld(path, label, tip = "", o = {}) {
  const r = SCH[path], v = val(path);
  tip = tip || TIPS[path] || "";
  if (!tip) console.warn("no tooltip for", path);
  let c = "";
  if (!r) return `<div class="row err">Unknown setting ${esc(path)}</div>`;
  if (r[0] === "bool") c = `<label class="sw"><input type="checkbox" data-key="${path}" ${v ? "checked" : ""}><span class="mute">${o.on || "Enabled"}</span></label>`;
  else if ((r[0] === "int" || r[0] === "float") && !o.num && r[2] - r[1] <= 300) {
    const step = o.step ?? (r[0] === "int" ? 1 : 0.1);
    c = `<input type="range" data-key="${path}" data-unit="${o.unit || ""}" min="${r[1]}" max="${r[2]}" step="${step}" value="${v}"><output>${v}${o.unit || ""}</output>`;
  } else if (r[0] === "int" || r[0] === "float") c = `<input type="number" data-key="${path}" min="${r[1]}" max="${r[2]}" step="${o.step ?? (r[0] === "int" ? 1 : 0.1)}" value="${v}">${o.unit ? `<span class="mute">${o.unit}</span>` : ""}`;
  else if (r[0] === "enum") c = `<select data-key="${path}">${r[1].map(x => `<option value="${x}" ${String(x) === String(v) ? "selected" : ""} ${(o.disabled || []).includes(x) ? "disabled" : ""}>${esc(o.labels?.[x] ?? LAB[x] ?? cap(x))}</option>`).join("")}</select>`;
  else if (r[0] === "color") c = `<input type="color" data-key="${path}" value="${v}">`;
  else c = `<input type="text" data-key="${path}" value="${esc(v)}" placeholder="${esc(o.ph || "")}" ${o.list ? `list="${o.list}"` : ""}>${o.browse ? `<button data-browse="${path}" title="Open the Windows folder picker">Browse…</button>` : ""}`;
  return `<div class="row"><label>${esc(label)}${tip ? `<i class="tip" tabindex="0" data-tip="${esc(tip)}">?</i>` : ""}</label><div class="ctl">${c}</div></div>`;
}
const tipI = t => `<i class="tip" tabindex="0" data-tip="${esc(t)}">?</i>`;
const card = (title, body, reset = "", extra = "") => `<div class="card"><div class="card-h"><h2>${title}</h2><span>${extra}${reset ? `<button class="sm" data-reset="${reset}" title="Restore the default values of this group">Default</button>` : ""}</span></div>${body}</div>`;
const head = (t, s) => `<h1>${t}</h1><p class="sub">${s}</p>`;

let pending = {}, saveTimer = null;
function queueSave(path, value) {
  pending[path] = value;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 350);
}
async function flushSave() {
  const patch = pending; pending = {};
  if (!Object.keys(patch).length) return;
  try {
    const r = await api("/api/settings", { settings: patch });
    st.settings = r.settings; afterSettings(Object.keys(patch));
  } catch (e) { toast(e.message, "err"); await reloadState(); render(); }
}
function afterSettings(keys) {
  if (page === "captions") capPreview();
  if (page === "start") drawPlacer("start");
  if (keys.some(k => k.startsWith("music.") || k.startsWith("voice.") || k.startsWith("effects.") || k.startsWith("captions.") || k.startsWith("transition."))) updateDisabled();
}
function updateWhen() {  // groups that only apply to one choice (data-when="path=value")
  document.querySelectorAll("[data-when]").forEach(g => {
    const [k, v] = g.dataset.when.split("="), on = String(val(k)) === v;
    g.style.opacity = on ? "" : ".45"; g.querySelectorAll("input,select").forEach(el => { el.disabled = !on; });
  });
}
function updateDisabled() {  // grey out sub-controls of disabled groups
  updateWhen();
  document.querySelectorAll("[data-group]").forEach(g => {
    const on = val(g.dataset.group + ".enabled");
    g.querySelectorAll("[data-key]").forEach(el => { if (!el.dataset.key.endsWith(".enabled")) el.disabled = !on; });
  });
}
document.addEventListener("input", e => {
  const el = e.target.closest("[data-key]"); if (!el) return;
  const k = el.dataset.key;
  let v = el.type === "checkbox" ? el.checked : (el.type === "range" || el.type === "number") ? (el.value === "" ? "" : Number(el.value)) : el.value;
  if (el.type === "range") el.nextElementSibling.textContent = v + (el.dataset.unit || "");
  if (v === "") return;
  const parts = k.split("."), last = parts.pop(); parts.reduce((o, p) => o[p], st.settings)[last] = v;  // optimistic local update
  if (el.type === "checkbox" || el.tagName === "SELECT") updateDisabled();
  queueSave(k, v);
  if (k.startsWith("start.")) drawPlacer("start");
  if (k.startsWith("captions.") && !["captions.preset", "captions.enabled"].includes(k)) queueSave("captions.preset", "custom");
  if (k.startsWith("captions.")) { st.settings.captions[k.split(".")[1]] = v; capPreview(); }
});
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.browse) {
    const path = b.dataset.browse;
    await act(b, async () => { const r = await api("/api/pick-folder", { title: "Select folder" }); await setSetting(path, r.path); render(); });
  } else if (b.dataset.reset) {
    const pre = b.dataset.reset.split(",");
    const d = {}; st.schema.forEach(([p, def]) => { if (pre.some(x => p.startsWith(x))) d[p] = def; });
    await act(b, async () => { const r = await api("/api/settings", { settings: d }); st.settings = r.settings; render(); toast("Defaults restored", "ok"); });
  }
});
async function setSetting(path, v) { const r = await api("/api/settings", { settings: { [path]: v } }); st.settings = r.settings; }
async function reloadState() { st = await api("/api/state"); SCH = {}; st.schema.forEach(([p, d, r]) => SCH[p] = r); }

// ------------------------------------------------------------------ navigation
const ICONS = { dashboard: "M3 12l9-9 9 9M5 10v10h14V10", projects: "M3 7h6l2 2h10v10H3z", voice: "M12 3v12M8 7v4M16 7v4M4 9v2M20 9v2M9 20h6", video: "M3 6h13v12H3zM16 10l5-3v10l-5-3", effects: "M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z", captions: "M3 5h18v14H3zM7 11h4M13 11h4M7 15h10", start: "M4 7V4h16v3M9 20h6M12 4v16", batch: "M4 6h16M4 12h16M4 18h10", settings: "M12 8a4 4 0 100 8 4 4 0 000-8zM3 12h3M18 12h3M12 3v3M12 18v3", logs: "M5 3h14v18H5zM9 8h6M9 12h6M9 16h4" };
const PAGES = [["dashboard", "Dashboard"], ["projects", "Projects"], ["voice", "Voice & Music"], ["video", "Video Settings"], ["effects", "Effects & Transitions"], ["captions", "Captions"], ["start", "Start Words"], ["batch", "Batch Processing"], ["settings", "Settings"], ["logs", "Activity & Error Logs"]];
function nav() {
  $("#nav").innerHTML = PAGES.map(([id, n]) => `<a data-page="${id}" class="${id === page ? "on" : ""}"><svg viewBox="0 0 24 24"><path d="${ICONS[id]}"/></svg>${n}</a>`).join("");
  const c = st.counts;
  $("#sidefoot").innerHTML = `<button class="dng" id="stopAll" style="width:100%;margin-bottom:8px" title="Immediately cancels every running render, empties the queue and cancels a running preview. Finished videos are kept.">■ Stop everything</button>${c.processing ? `<span class="warn">● Rendering ${c.processing}</span><br>` : ""}${c.pending} queued · ${c.completed}/${c.total} done${lastTiming?.active && lastTiming.eta != null ? `<br>~${fmtDur(lastTiming.eta)} left` : ""}`;
}
$("#nav").addEventListener("click", e => { const a = e.target.closest("[data-page]"); if (a) go(a.dataset.page); });
async function go(p) { page = p; location.hash = p; closeDrawer(); await render(); }

async function render() {
  nav();
  const m = $("#main");
  const fn = { dashboard, projects: projectsPage, voice: voicePage, video: videoPage, effects: effectsPage, captions: captionsPage, start: startPage, batch: batchPage, settings: settingsPage, logs: logsPage }[page];
  m.innerHTML = '<p class="mute"><span class="spin"></span>Loading…</p>';
  try { m.innerHTML = await fn(); } catch (e) { m.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  updateDisabled();
  if (page === "captions") capPreview();
  if (page === "start") drawPlacer("start");
  if (page === "video") loadModels();
  updateTimingUI();
  if (page === "projects" || page === "batch") { pollKey = ""; drawRows(); }
  if (page === "batch") batchTiles();
  if (page === "logs") loadLogs();
}

// ------------------------------------------------------------------ shared project helpers
async function loadProjects() { projects = await api("/api/projects"); if (!previewProject || !projects.find(p => p.path === previewProject)) previewProject = (projects.find(p => p.status !== "missing") || projects[0] || {}).path || ""; }
const projPicker = () => `<select id="pp" style="max-width:260px">${projects.length ? projects.map(p => `<option value="${esc(p.path)}" ${p.path === previewProject ? "selected" : ""}>${esc(p.name)}</option>`).join("") : '<option value="">(scan projects first)</option>'}</select>`;
document.addEventListener("change", e => { if (e.target.id === "pp") previewProject = e.target.value; });
function needProject() { if (!previewProject) { toast("Scan a parent folder in Projects first, then choose a project to preview.", "err"); return false; } return true; }
const media = p => "/api/media?path=" + encodeURIComponent(p);
const thumb = (p, w = 200) => `/api/thumb?w=${w}&path=${encodeURIComponent(p)}`;

// ------------------------------------------------------------------ Dashboard
async function dashboard() {
  await reloadState();
  try { lastTiming = (await api("/api/batch")).timing; } catch (e) { /* ignore */ }
  setTimeout(updateTimingUI, 0);
  const c = st.counts, caps = st.caps;
  const health = [
    [caps.ok, caps.ok ? `FFmpeg ready - ${esc(caps.version.split(" ").slice(0, 3).join(" "))}` : "FFmpeg not found. Install it and restart (see README)."],
    [caps.libass, caps.libass ? "Caption rendering (libass) available" : "FFmpeg lacks libass - captions unavailable"],
    [st.whisper, st.whisper ? "Faster-Whisper installed - local transcription available" : "Faster-Whisper not installed - scripts are required (timing is estimated) and captions use script timing"],
    [st.music_count > 0, st.music_count ? `${st.music_count} music track(s) found` : "No music folder/tracks selected (Voice & Music)"],
    [!!val("paths.parent"), val("paths.parent") ? `Parent folder: ${esc(val("paths.parent"))}` : "No parent folder selected (Projects)"],
    [true, caps.hw.length ? `Hardware encoder available: ${caps.hw.join(", ")}` : "No hardware encoder detected - using CPU (libx264)", "ok"],
  ];
  const done = c.completed + c.failed + c.skipped, queue = done + c.pending + c.processing;
  return head("Dashboard", "Overview of your batch and system health.") +
    `<div class="tiles">${[["total", "Total projects"], ["ready", "Ready"], ["missing", "Missing files"], ["pending", "Pending"], ["processing", "Processing"], ["completed", "Completed"], ["failed", "Failed"]].map(([k, n]) => `<div class="tile"><b class="${k === "failed" && c[k] ? "err" : ""}">${c[k]}</b><span>${n}</span></div>`).join("")}</div>` +
    card("Overall progress", `<div class="prog" style="height:10px"><i style="width:${c.total ? Math.round(c.completed / c.total * 100) : 0}%"></i></div><p id="dtime" class="note"></p><p class="note">${c.completed} of ${c.total} completed${c.failed ? `, ${c.failed} failed` : ""}${st.paused ? " · <b class='warn'>queue paused</b>" : ""}</p>
      <div class="bar"><button class="pri" data-go="projects">Scan projects</button><button data-act="start_all">Start all ready (${c.ready})</button><button data-go="batch">Open batch dashboard</button><button class="dng" id="stopAll2">■ Stop everything</button></div>`) +
    card("System health", health.map(([ok, t, k]) => `<div class="pill"><span class="dot ${k || (ok ? "ok" : "warn")}"></span><span>${t}</span></div>`).join(""));
}
document.addEventListener("click", async e => {
  const g = e.target.closest("[data-go]"); if (g) go(g.dataset.go);
  const a = e.target.closest("[data-act]");
  if (a) await act(a, async () => { const r = await api("/api/batch", { action: a.dataset.act }); toast(r.queued ? `${r.queued} project(s) queued` : "Done", "ok"); await refreshCounts(); render(); });
});
async function refreshCounts() { const b = await api("/api/batch"); st.counts = b.counts; st.paused = b.paused; return b; }

// ------------------------------------------------------------------ Projects
async function projectsPage() {
  await loadProjects();
  return head("Projects", "Each sub-folder with the configured prefix is one independent video project.") +
    card("Source folder", fld("paths.parent", "Parent folder", "Folder that contains all project folders (set-001, set-002, ...).", { browse: true, ph: "C:\\Videos\\Main Folder" }) +
      fld("paths.prefix", "Folder prefix", "Only sub-folders whose name starts with this prefix are projects.", {}) +
      `<div class="bar"><button class="pri" id="scanBtn">Scan / rescan projects</button><span class="mute" id="scanMsg"></span></div>`) +
    `<div class="card"><div class="bar"><input type="text" id="q" class="grow" placeholder="Search projects…"><select id="sf" style="width:160px"><option value="">All statuses</option>${["missing", "ready", "pending", "processing", "completed", "failed", "skipped"].map(s => `<option>${s}</option>`).join("")}</select>
      <button id="selAll">Select visible</button><button id="selNone">Clear</button><button class="pri" id="startSel">Start selected</button><button id="rerSel" title="Render again even if already completed">Re-render selected</button></div>
      <div class="tw"><table><thead><tr><th></th><th>Project</th><th>Images</th><th>Script</th><th>Voice</th><th>Readiness</th><th>Status</th><th>Time</th><th>Output</th></tr></thead><tbody id="ptb"></tbody></table></div></div>`;
}
function rowFilter() { const q = ($("#q")?.value || "").toLowerCase(), f = $("#sf")?.value || ""; return projects.filter(p => (!q || p.name.toLowerCase().includes(q)) && (!f || p.status === f)); }
function readiness(p) {
  const is = p.info.issues || [], bl = is.filter(i => i.blocking), nb = is.filter(i => !i.blocking && ["missing_voice", "missing_start"].includes(i.cat));
  return bl.length ? `<span class="warn" title="${esc(bl.map(i => i.msg).join("\n"))}">${esc(bl[0].msg)}</span>` : `<span class="ok">Ready</span>${nb.length ? ` <span class="mute" title="${esc(nb.map(i => i.msg).join("\n"))}">(${nb.map(i => i.cat === "missing_voice" ? "no voice" : "no start words").join(", ")})</span>` : ""}`;
}
function drawRows() {
  const key = JSON.stringify([projects.map(p => [p.status, p.progress, p.message, Math.round(p.elapsed || 0), Math.round(p.eta || 0)]), [...sel], $("#q")?.value, $("#sf")?.value, page]);
  if (key === pollKey) return; pollKey = key;
  const chip = p => `<span class="chip ${p.status}">${p.status}</span>`;
  if (page === "projects" && $("#ptb")) {
    $("#ptb").innerHTML = rowFilter().map(p => `<tr class="click"><td><input type="checkbox" data-sel="${esc(p.path)}" ${sel.has(p.path) ? "checked" : ""}></td>
      <td><a href="#" data-open="${esc(p.path)}" style="color:var(--acc)">${esc(p.name)}</a>${p.has_overrides ? ' <span class="chip" title="Has project-specific overrides">override</span>' : ""}</td>
      <td>${p.info.images}${p.info.total_images !== p.info.images ? `<span class="mute"> / ${p.info.total_images}</span>` : ""}</td>
      <td>${p.info.script ? '<span class="ok">✓</span>' : '<span class="mute" title="Will be transcribed">—</span>'}</td>
      <td>${p.info.voice ? `<span class="ok" title="${esc(p.info.voice)}">✓</span>` : p.info.voice_candidates.length > 1 ? '<span class="warn">choose</span>' : '<span class="err">✗</span>'}</td>
      <td>${readiness(p)}</td><td>${chip(p)}${p.status === "processing" ? `<div class="prog"><i style="width:${Math.round(p.progress * 100)}%"></i></div>` : ""}${p.status === "failed" ? `<div class="err" style="font-size:12px" title="${esc(p.message)}">${esc(p.category)}</div>` : ""}</td>
      <td style="white-space:nowrap;font-size:12.5px">${timeCell(p)}</td><td>${p.output ? `<a href="#" data-open="${esc(p.path)}" style="color:var(--ok)">Open</a>` : ""}</td></tr>`).join("") || '<tr><td colspan="9" class="mute">No projects. Choose a parent folder and click Scan.</td></tr>';
  }
  if (page === "batch" && $("#btb")) {
    $("#btb").innerHTML = projects.filter(p => ["pending", "processing", "completed", "failed", "skipped"].includes(p.status)).map(p => `<tr><td><input type="checkbox" data-sel="${esc(p.path)}" ${sel.has(p.path) ? "checked" : ""}></td>
      <td>${esc(p.name)}</td><td>${chip(p)}</td><td style="width:170px"><div class="prog"><i style="width:${Math.round((p.status === "completed" ? 1 : p.progress) * 100)}%"></i></div></td>
      <td class="${p.status === "failed" ? "err" : "mute"}" style="max-width:300px">${esc(p.status === "failed" ? `[${p.category}] ${p.message}` : p.message)}</td><td style="white-space:nowrap;font-size:12.5px">${timeCell(p)}</td><td>${p.music ? esc(p.music) : ""}</td>
      <td>${["pending", "processing"].includes(p.status) ? `<button class="sm" data-pact="cancel" data-p="${esc(p.path)}">Cancel</button> ` : ""}${["pending", "processing", "ready", "failed"].includes(p.status) ? `<button class="sm" data-pact="skip" data-p="${esc(p.path)}">Skip</button> ` : ""}${p.status === "failed" ? `<button class="sm" data-pact="retry" data-p="${esc(p.path)}">Retry</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="8" class="mute">Nothing queued yet. Start all ready projects or select some in Projects.</td></tr>';
  }
}
document.addEventListener("change", e => {
  const s = e.target.closest("[data-sel]"); if (s) { s.checked ? sel.add(s.dataset.sel) : sel.delete(s.dataset.sel); pollKey = ""; }
  if (e.target.id === "sf") { pollKey = ""; drawRows(); }
});
document.addEventListener("input", e => { if (e.target.id === "q") { pollKey = ""; drawRows(); } });
document.addEventListener("click", async e => {
  const t = e.target, b = t.closest("button");
  const op = t.closest("[data-open]"); if (op) { e.preventDefault(); openDrawer(op.dataset.open); return; }
  if (!b) return;
  if (b.id === "scanBtn") await act(b, async () => { await flushSave(); const r = await api("/api/scan", {}); toast(`${r.found} project(s) found`, "ok"); await loadProjects(); pollKey = ""; nav(); $("#scanMsg").textContent = `${r.found} found`; drawRows(); }).then(() => refreshCounts().then(nav));
  else if (b.id === "selAll") { rowFilter().forEach(p => sel.add(p.path)); pollKey = ""; drawRows(); }
  else if (b.id === "selNone") { sel.clear(); pollKey = ""; drawRows(); }
  else if (b.id === "startSel" || b.id === "rerSel") {
    if (!sel.size) return toast("Select projects first", "err");
    if (b.id === "rerSel" && !confirm(`Re-render ${sel.size} project(s)? Existing output files will be replaced.`)) return;
    await act(b, async () => { const r = await api("/api/batch", { action: b.id === "rerSel" ? "rerender" : "start_selected", paths: [...sel] }); toast(`${r.queued} project(s) queued`, r.queued ? "ok" : ""); await poll(true); });
  } else if (b.dataset.pact) {
    const a = b.dataset.pact;
    await act(b, async () => { await api("/api/batch", a === "retry" ? { action: "start_selected", paths: [b.dataset.p] } : { action: a, paths: [b.dataset.p] }); await poll(true); });
  }
});

// ------------------------------------------------------------------ Project drawer
let cur = null;
function closeDrawer() { $("#drawer").classList.remove("open"); cur = null; }
async function openDrawer(path) {
  const d = $("#drawer"); d.classList.add("open"); d.innerHTML = '<p class="mute"><span class="spin"></span>Loading…</p>';
  try { cur = await api("/api/project?path=" + encodeURIComponent(path)); } catch (e) { d.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  drawDrawer();
}
function drawDrawer() {
  const c = cur, p = c.project, ov = c.overrides, os = ov.settings || {}, d = $("#drawer"), ex = new Set(c.excluded);
  const g = (k, def) => os[k] ?? def;
  d.innerHTML = `<div class="card-h"><h2 style="font-size:18px">${esc(p.name)} <span class="chip ${p.status}">${p.status}</span></h2><button id="dclose">Close</button></div>
  ${c.issues.map(i => `<div class="pill"><span class="dot ${i.blocking ? "err" : "warn"}"></span><span>${esc(i.msg)}</span></div>`).join("")}
  ${p.status === "failed" ? `<div class="pill"><span class="dot err"></span><span><b>${esc(p.category)}</b>: ${esc(p.message)}<br><button class="sm" id="showlog">View FFmpeg log</button></span></div><pre id="logbox" hidden></pre>` : ""}
  ${p.elapsed != null && p.status === "completed" ? `<p class="note" style="margin-top:0">Rendered in <b>${fmtDur(p.elapsed)}</b>${p.video_seconds ? ` (${fmtDur(p.video_seconds)} of video)` : ""}</p>` : ""}<div class="bar"><button class="pri" id="dRender">${c.has_output ? "Re-render this project" : "Render this project"}</button><button id="dPlan">Show timeline</button><button id="dPrev">Preview first 8s</button><button id="dFolder">Open folder</button></div>
  ${c.has_output ? `<h2>Final video</h2><video controls preload="metadata" src="${media(p.output)}"></video>` : ""}
  <div id="dtl"></div><div id="dprev"></div>
  <h2 style="margin:16px 0 8px">Images <span class="mute">(untick to exclude · natural order)</span></h2>
  <div class="thumbs">${c.images.map(n => `<div class="thumb ${ex.has(n) ? "ex" : ""}"><img loading="lazy" src="${thumb(p.path + (p.path.includes("\\") ? "\\" : "/") + n, 200)}" alt=""><label><input type="checkbox" data-ex="${esc(n)}" ${ex.has(n) ? "" : "checked"}>${esc(n)}</label></div>`).join("") || '<span class="mute">No images</span>'}</div>
  <h2 style="margin:16px 0 8px">Voice-over</h2>
  ${c.voice_candidates.length > 1 ? `<div class="row"><label>Choose voice file <i class="tip" title="Several audio files match this project - pick the right one.">?</i></label><div class="ctl"><select id="dvoice"><option value="">(choose…)</option>${c.voice_candidates.map(n => `<option ${ov.voice_file === n ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div></div>` : ""}
  ${c.voice ? `<audio controls preload="none" src="${media(c.voice)}"></audio>` : '<p class="mute">No voice selected.</p>'}
  <h2 style="margin:16px 0 8px">Script</h2>${c.script ? `<pre>${esc(c.script_text)}</pre>` : '<p class="mute">No script file - the voice will be transcribed automatically.</p>'}
  <h2 style="margin:16px 0 8px">Project-specific overrides <span class="mute">(blank = use global setting)</span></h2>
  <div class="row"><label>Voice volume %</label><div class="ctl"><input type="number" id="ovVv" min="0" max="200" value="${g("voice.volume", "")}"></div></div>
  <div class="row"><label>Music volume %</label><div class="ctl"><input type="number" id="ovMv" min="0" max="100" value="${g("music.volume", "")}"></div></div>
  <div class="row"><label>Music start (seconds)</label><div class="ctl"><input type="number" id="ovMs" min="0" step="0.5" value="${os["music.random_start"] === false ? g("music.manual_start", "") : ""}"></div></div>
  <div class="row"><label>Music track</label><div class="ctl"><select id="ovMt"><option value="">Random from library</option></select></div></div>
  <div class="bar"><button class="pri" id="ovSave">Save overrides</button><button id="ovClear">Clear all overrides</button></div>`;
  api("/api/music").then(m => { const s = $("#ovMt"); if (s) s.innerHTML += m.map(x => `<option ${ov.music_file === x.name ? "selected" : ""}>${esc(x.name)}</option>`).join(""); });
}
async function saveOv(ov) { await api("/api/project/overrides", { path: cur.project.path, overrides: ov }); await openDrawer(cur.project.path); await loadProjects(); pollKey = ""; drawRows(); }
$("#drawer").addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b || !cur) return;
  const path = cur.project.path, ov = JSON.parse(JSON.stringify(cur.overrides));
  if (b.id === "dclose") closeDrawer();
  else if (b.id === "showlog") { const r = await api("/api/logs/ffmpeg?project=" + encodeURIComponent(cur.project.name)); const l = $("#logbox"); l.hidden = false; l.textContent = r.text; }
  else if (b.id === "dFolder") act(b, () => api("/api/open-folder", { path }));
  else if (b.id === "dRender") act(b, async () => { const r = await api("/api/batch", { action: "rerender", paths: [path] }); toast(r.queued ? "Queued" : "Cannot queue (project missing files or already queued)", r.queued ? "ok" : "err"); await poll(true); });
  else if (b.id === "dPrev") act(b, async () => { const r = await api("/api/preview", { path, seconds: 8 }); $("#dprev").innerHTML = `<h2 style="margin:12px 0 6px">Preview</h2><video controls autoplay src="${r.url}"></video>${r.warnings.map(w => `<p class="note warn">${esc(w)}</p>`).join("")}`; });
  else if (b.id === "dPlan") act(b, () => showTimeline(path));
  else if (b.id === "ovSave") {
    const s = {}, num = id => $(id).value === "" ? null : Number($(id).value);
    if (num("#ovVv") !== null) s["voice.volume"] = num("#ovVv");
    if (num("#ovMv") !== null) s["music.volume"] = num("#ovMv");
    if (num("#ovMs") !== null) { s["music.random_start"] = false; s["music.manual_start"] = num("#ovMs"); }
    ov.settings = s; $("#ovMt").value ? ov.music_file = $("#ovMt").value : delete ov.music_file;
    act(b, async () => { await saveOv(ov); toast("Overrides saved", "ok"); });
  } else if (b.id === "ovClear") { if (confirm("Remove all overrides for this project (including excluded images and manual timing)?")) act(b, () => saveOv({})); }
  else if (b.id === "tlSave") {
    const starts = [...document.querySelectorAll("[data-ms]")].map(i => Number(i.value));
    ov.manual_starts = starts; act(b, async () => { await saveOv(ov); toast("Manual timing saved - it is used for rendering", "ok"); });
  } else if (b.id === "tlClear") { delete ov.manual_starts; act(b, () => saveOv(ov)); }
});
$("#drawer").addEventListener("change", e => {
  if (!cur) return;
  const ov = JSON.parse(JSON.stringify(cur.overrides));
  if (e.target.dataset.ex !== undefined) {
    const ex = new Set(ov.excluded_images || []); e.target.checked ? ex.delete(e.target.dataset.ex) : ex.add(e.target.dataset.ex);
    ov.excluded_images = [...ex]; delete ov.manual_starts; saveOv(ov).catch(x => toast(x.message, "err"));
  } else if (e.target.id === "dvoice") { ov.voice_file = e.target.value; saveOv(ov).catch(x => toast(x.message, "err")); }
});
async function showTimeline(path) {
  const r = await api("/api/plan", { path }), box = $("#dtl"), total = r.duration + r.ending;
  const w = s => ((s.end - s.start) / total * 100).toFixed(2);
  box.innerHTML = `<h2 style="margin:12px 0 4px">Timeline <span class="mute">(${fmtT(r.duration)} voice${r.ending ? ` + ${r.ending}s ending` : ""} · mode: ${r.mode}${r.transcribed ? " · transcribed locally" : ""})</span></h2>
    ${r.estimated ? '<p class="note warn">⚠ Timing is estimated (not verified against the audio). Review it before rendering.</p>' : r.score != null ? `<p class="note ok">Script matched the speech: ${Math.round(r.score * 100)}%</p>` : ""}
    ${r.warnings.map(x => `<p class="note warn">⚠ ${esc(x)}</p>`).join("")}
    ${r.voice_captions ? '<p class="note ok">Captions: synchronised with the voice (word by word as spoken).</p>' : ""}
    <div class="tl">${r.segments.map(s => `<div style="width:${w(s)}%;background-image:url('${thumb(s.image, 160)}')"><span>${s.name.replace(/\.[^.]+$/, "")} · ${fmtT(s.start)}</span></div>`).join("")}${r.ending ? `<div class="end" style="width:${(r.ending / total * 100).toFixed(2)}%"><span>end</span></div>` : ""}</div>
    ${r.music ? `<p class="note">Music: ${esc(r.music.name)} from ${fmtT(r.music.start)} (random pick shown here; each render picks its own)</p>` : ""}
    ${r.sync && r.sync.length ? `<details open><summary class="mute">Voice matching - how each picture was placed</summary><table style="margin-top:6px"><tr><th>Picture</th><th>Caption from file name</th><th>Confidence</th><th>Result</th></tr>${r.sync.map(x => `<tr><td>${esc(x.name.slice(0, 28))}</td><td>${esc(x.caption)}</td><td>${x.matched ? Math.round(x.confidence * 100) + "%" : "—"}</td><td>${x.matched ? `<span class="ok">switches at ${fmtT(x.time)}</span>` : x.shown ? `<span class="mute">${x.time === 0 ? "shown from the start" : "placed between neighbours"}</span>` : '<span class="warn">no confident match - previous picture kept</span>'}</td></tr>`).join("")}</table></details>` : ""}
    <details><summary class="mute">Sentences & manual timing</summary>
    <table style="margin-top:8px"><tr><th>Image</th><th>Start (s)</th></tr>${r.segments.map((s, i) => `<tr><td>${esc(s.name)}</td><td><input type="number" step="0.1" min="0" style="width:100px" data-ms="${i}" value="${s.start.toFixed(2)}" ${i === 0 ? "readonly" : ""}></td></tr>`).join("")}</table>
    <div class="bar" style="margin-top:8px"><button id="tlSave">Use these start times</button><button id="tlClear">Back to automatic</button></div>
    <table>${r.sentences.map(s => `<tr><td class="mute" style="width:90px">${fmtT(s.start)}</td><td>${esc(s.text)}</td></tr>`).join("")}</table></details>`;
}

// ------------------------------------------------------------------ Voice & Music
async function voicePage() {
  await Promise.all([reloadState(), loadProjects()]);
  return head("Voice & Music", "Where scripts, voice-overs and background music come from, and how they are mixed.") +
    `<div class="grid">` +
    card("Sources", fld("sources.script_source", "Script source", "Where the script text is read from. If none is found the voice is transcribed automatically.") + fld("paths.script_dir", "Script directory", "Used when the source is 'Separate directory'. Files must be named like the project, e.g. set-001.txt.", { browse: true }) +
      fld("sources.voice_mode", "Voice source", "Voice file inside each project folder, or a separate folder with files named after the project.") + fld("paths.voice_dir", "Voice directory", "Files must be named like the project, e.g. set-001.mp3. Matching is by name, never by position.", { browse: true }), "sources,paths.script_dir,paths.voice_dir") +
    card("Voice", `<div data-group="voice">${fld("voice.enabled", "Use voice-over", "Off = never use a voice-over: videos are made without it (length = pictures × seconds per picture, set in Video Settings).")}${fld("voice.required", "Voice-over required", "On: a project without a voice file is flagged 'missing' and not rendered. Off: it is rendered without voice-over (music only or silent).", { on: "Required" })}${fld("voice.volume", "Volume", "100% = original level (after normalisation).", { unit: "%" })}${fld("voice.fade_in", "Fade-in", "", { unit: " s" })}${fld("voice.fade_out", "Fade-out", "", { unit: " s" })}${fld("voice.normalize", "Normalise loudness", "EBU R128 loudness normalisation so every voice has the same level.")}</div>`, "voice.") +
    card("Background music", `<div data-group="music">${fld("music.enabled", "Use background music", "Off = no background music in any video.")}${fld("music.required", "Music required", "On: rendering fails if no music file can be found. Off: videos are made without music when none is available.", { on: "Required" })}${fld("paths.music_dir", "Music folder", `MP3, WAV, M4A or AAC. ${st.music_count} track(s) found.`, { browse: true })}${fld("music.volume", "Music volume", "Independent from the voice volume.", { unit: "%" })}
      ${fld("music.allow_repeat", "Allow repeating tracks", "Off: every track is used once before any track is reused.")}${fld("music.random_start", "Random start position", "Pick a random valid start time inside the track.")}${fld("music.min_start", "Minimum start", "", { unit: " s", num: true })}${fld("music.max_start", "Maximum start", "Clamped to the real track length.", { unit: " s", num: true })}${fld("music.manual_start", "Manual start", "Used when random start is off.", { unit: " s", num: true })}
      ${fld("music.fade_in", "Fade-in", "", { unit: " s" })}${fld("music.fade_out", "Fade-out", "", { unit: " s" })}${fld("music.ducking", "Audio ducking", "Lower the music while the voice speaks.")}${fld("music.duck_amount", "Ducking strength", "", { unit: "%" })}</div>`, "music.,paths.music_dir") + `</div>` +
    card("Previews", `<div class="bar">${projPicker()}<button id="pvVoice">Voice</button><button id="pvMusic">Music</button><button class="pri" id="pvMix">Mixed audio (20 s)</button></div><div id="apv"></div><p class="note">Uses the voice of the selected project and the same audio chain as the final render.</p>`);
}
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "pvVoice" || b.id === "pvMusic" || b.id === "pvMix") {
    if (!needProject()) return;
    await act(b, async () => {
      await flushSave();
      if (b.id === "pvVoice") { const r = await api("/api/project?path=" + encodeURIComponent(previewProject)); if (!r.voice) throw new Error("This project has no voice file"); $("#apv").innerHTML = `<audio controls autoplay src="${media(r.voice)}"></audio>`; }
      else if (b.id === "pvMusic") { const r = await api("/api/plan", { path: previewProject }); if (!r.music) throw new Error("No music selected - check the music folder"); $("#apv").innerHTML = `<p class="note">${esc(r.music.name)} - starting at ${fmtT(r.music.start)}</p><audio controls id="ma" src="${media(r.music.file)}"></audio>`; const a = $("#ma"); a.onloadedmetadata = () => { a.currentTime = r.music.start; a.play(); }; }
      else { const r = await api("/api/preview/audio", { path: previewProject, seconds: 20 }); $("#apv").innerHTML = `<p class="note">Mixed: voice + ${r.music ? esc(r.music.name) + " from " + fmtT(r.music.start) : "no music"}</p><audio controls autoplay src="${r.url}"></audio>`; }
    });
  }
});

// ------------------------------------------------------------------ Video settings
async function videoPage() {
  await reloadState();
  const hw = st.caps.hw, encl = { auto: "Automatic", h264_nvenc: "NVIDIA (NVENC)", h264_qsv: "Intel (Quick Sync)", h264_amf: "AMD (AMF)" };
  const hwl = { auto: "Automatic - system decides", gpu: "GPU (graphics card)", cpu: "CPU only" };
  const hwNote = hw.length ? `<span class="ok">✓ Detected GPU encoder: ${hw.map(h => encl[h]).join(", ")}</span>` : '<span class="warn">No working GPU encoder detected on this computer - CPU will be used even if you choose GPU.</span>';
  return head("Video Settings", "Output format, hardware, image fitting, and how images are synchronised with the voice.") + `<div class="grid">` +
    card("Output", fld("video.aspect", "Aspect ratio", "9:16 = 1080×1920, 16:9 = 1920×1080, 1:1 = 1080×1080.") + fld("video.fps", "Frame rate", "", { unit: " fps" }) + fld("video.crf", "Quality (CRF)", "Lower = better quality and bigger files. 18-23 is typical.") +
      fld("video.preset", "Encoder speed preset", "") + `<p class="note">Container MP4 · H.264 · AAC · suitable for Facebook/Instagram Reels and YouTube Shorts.</p>`, "video.aspect,video.fps,video.crf,video.preset") +
    card("Rendering hardware (GPU / CPU)", fld("video.hwaccel", "Use", "", { labels: hwl }) + fld("video.hw_encoder", "GPU encoder", "", { labels: encl, disabled: ["h264_nvenc", "h264_qsv", "h264_amf"].filter(x => !hw.includes(x)) }) +
      `<p class="note">${hwNote}</p><p class="note">This controls only the final video encoder, not every processing stage. Image preparation, scaling, transitions, visual effects and subtitle rendering are CPU filters. GPU mode can therefore still show substantial CPU usage; CPU mode does not disable GPU use by Whisper or other applications. If hardware encoding fails, the video falls back to CPU.</p>`, "video.hwaccel,video.hw_encoder") +
    card("Image fitting", fld("video.fit", "Fit mode", "") + fld("video.bg_color", "Solid background colour", "Used by 'Fit with solid background'.") + fld("video.image_seconds", "Seconds per image (no voice-over)", "Used when a video has no voice-over: video length = number of pictures × this value.", { unit: " s" }), "video.fit,video.bg_color,video.image_seconds") +
    card("Image synchronisation", fld("sync.method", "Method", "", { labels: { voice: "Automatic (voice)", manual: "Manual distribution" } }) +
      `<div data-when="sync.method=voice"><p class="note" style="margin-top:0">The narration is transcribed locally (Whisper) and each picture appears when its idea is spoken. Each picture's <b>file name</b> is its description, e.g. <i>01_Why-do-certain-people-cross-your-path.png</i>. Wording does not have to match exactly. Pictures are used in numeric order; the first one is shown from the start.</p>
        ${fld("sync.match_threshold", "Confidence threshold", "", { unit: "%" })}${fld("sync.min_hold", "Minimum time on screen", "", { unit: " s" })}${fld("sync.lead_ms", "Switch slightly early", "", { unit: " ms", num: true })}${fld("sync.unmatched", "If a picture has no confident match", "", { labels: { keep: "Keep current picture", spread: "Place between neighbours" } })}</div>` +
      `<div data-when="sync.method=manual"><p class="note" style="margin-top:0">Pictures are spread over the script sentences (or evenly over the voice).</p>${fld("sync.mode", "Distribution", "How images are assigned to sentences. Image order is never changed.")}${fld("sync.group_size", "Sentences per image", "")}
        ${fld("sync.mismatch_threshold", "Mismatch threshold", "Minimum share of script words that must be found in the speech to trust the alignment.", { unit: "%" })}${fld("sync.mismatch_action", "On mismatch", "Estimate: continue with proportional timing and log a warning. Fail: stop that project.")}</div>`, "sync.") +
    card("Local transcription (Faster-Whisper)", fld("whisper.language", "Language code", "") + fld("whisper.vad", "Skip silence before transcribing", "") +
      `<h2 style="margin:12px 0 6px;font-size:13px">Models ${tipI("Bigger models are more accurate but slower. A model must be downloaded once (internet needed); after that it works offline. Until a chosen model is downloaded, the base model is used so rendering never breaks.")}</h2><div id="models"><span class="mute"><span class="spin"></span>Checking models…</span></div>`, "whisper.language,whisper.vad") + `</div>`;
}
let modelTimer = null;
async function loadModels() {
  const box = $("#models"); if (!box) return;
  let m; try { m = await api("/api/models"); } catch (e) { box.textContent = e.message; return; }
  const sel = st.settings.whisper.model, busy = m.models.some(x => x.state === "downloading");
  box.innerHTML = (!m.installed ? '<p class="warn">Faster-Whisper is not installed - run setup.bat.</p>' : "") +
    `<p class="note" style="margin-top:0">Currently used: <b>${esc(m.effective)}</b>${m.effective !== sel ? ` - you selected <b>${esc(sel)}</b>, but it is not downloaded yet, so the base model is used until it is.` : ""} ${m.online ? "" : '<span class="warn">No internet connection detected - downloads will fail.</span>'}</p>
    <table>${m.models.map(x => `<tr><td><b>${x.name}</b>${x.name === sel ? ' <span class="chip">selected</span>' : ""}</td><td class="mute">${x.size}</td>
      <td>${x.downloaded ? '<span class="ok">✓ downloaded (works offline)</span>' : x.state === "downloading" ? '<span class="warn"><span class="spin"></span>downloading…</span>' : x.state === "error" ? `<span class="err" title="${esc(x.error)}">download failed</span>` : '<span class="mute">not downloaded</span>'}</td>
      <td style="text-align:right">${x.downloaded ? (x.name === sel ? '<span class="mute">in use</span>' : `<button class="sm" data-muse="${x.name}">Use this model</button>`) :
        x.state === "downloading" ? "" : `<button class="sm" data-mdl="${x.name}" title="Selects this model and downloads it. The base model is used until the download finishes.">Download &amp; use</button>`}${!x.downloaded && x.name === sel && x.name !== "base" ? ` <button class="sm" data-muse="base">Back to base</button>` : ""}</td></tr>`).join("")}</table>`;
  clearTimeout(modelTimer); if (busy && page === "video") modelTimer = setTimeout(loadModels, 2500);
}
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.muse) await act(b, async () => { await setSetting("whisper.model", b.dataset.muse); toast("Model selected", "ok"); loadModels(); });
  else if (b.dataset.mdl) await act(b, async () => { await setSetting("whisper.model", b.dataset.mdl); await api("/api/models/download", { name: b.dataset.mdl }); toast("Download started - the base model is used until it finishes", "ok"); loadModels(); });
});

// ------------------------------------------------------------------ Effects & transitions
async function effectsPage() {
  await Promise.all([reloadState(), loadProjects()]);
  const tr = st.transitions, dis = tr.filter(t => !t.available).map(t => t.id), labels = Object.fromEntries(tr.map(t => [t.id, t.label + (t.available ? "" : " (unavailable)")]));
  const speed = { slow: "Slow", normal: "Normal", fast: "Fast" };
  const noise = k => `<div data-group="effects.${k}">${fld(`effects.${k}.enabled`, "Enable")}${fld(`effects.${k}.intensity`, "Intensity", "How many specks of grain.", { unit: "%" })}${fld(`effects.${k}.opacity`, "Opacity", "How visible each speck is.", { unit: "%" })}${fld(`effects.${k}.speed`, "Speed", "How fast the grain changes.", { labels: speed })}</div>`;
  const dust = `<div data-group="effects.dust">${fld("effects.dust.enabled", "Enable film dust")}${fld("effects.dust.amount", "Amount", "", { unit: "%", num: true })}${fld("effects.dust.size", "Speck size", "", { unit: "%", num: true })}
    ${fld("effects.dust.min_opacity", "Speck opacity - min", "", { unit: "%" })}${fld("effects.dust.max_opacity", "Speck opacity - max", "", { unit: "%" })}${fld("effects.dust.opacity", "Overall strength", "", { unit: "%" })}${fld("effects.dust.color", "Dust colour")}
    ${fld("effects.dust.softness", "Softness / blur", "", { unit: "%", num: true })}${fld("effects.dust.fps", "Change rate", "", { unit: " per second" })}${fld("effects.dust.jitter", "Flicker", "", { unit: "%" })}${fld("effects.dust.random_per_video", "Different pattern per video", "", { on: "Random per video" })}</div>`;
  const dust2 = `<div data-group="effects.dust"><p class="note" style="margin-top:0"><b>Hairs / fibres</b></p>${fld("effects.dust.hairs_enabled", "Hairs", "", { on: "Show hairs" })}${fld("effects.dust.hairs_chance", "How often", "", { unit: "%" })}${fld("effects.dust.hairs_max", "Max per frame")}${fld("effects.dust.hairs_size", "Size", "", { unit: "%", num: true })}${fld("effects.dust.hairs_color", "Hair colour")}
    <p class="note"><b>Micro grain</b></p>${fld("effects.dust.grain_enabled", "Micro grain", "", { on: "Show grain" })}${fld("effects.dust.grain_amount", "Amount", "", { unit: "%", num: true })}${fld("effects.dust.grain_opacity", "Strongest dot", "", { unit: "%" })}
    <p class="note"><b>Vertical film scratches</b></p>${fld("effects.dust.scratches_enabled", "Scratches", "", { on: "Show scratches" })}${fld("effects.dust.scratches_chance", "How often", "", { unit: "%" })}${fld("effects.dust.scratches_opacity", "Opacity", "", { unit: "%" })}${fld("effects.dust.scratches_width", "Thickness", "", { unit: "%", num: true })}${fld("effects.dust.scratches_color", "Scratch colour")}</div>`;
  const blackNoise = `<div data-group="effects.black_noise">${fld("effects.black_noise.enabled", "Enable")}${fld("effects.black_noise.intensity", "Density", "How many dark specks appear (0-100%).", { unit: "%" })}${fld("effects.black_noise.opacity", "Darkness / opacity", "How dark each speck is.", { unit: "%" })}
    ${fld("effects.black_noise.size", "Grain size", "Size of each speck in pixels. 1 = fine film grain, 12 = large dust blobs.", { unit: " px" })}${fld("effects.black_noise.softness", "Softness", "0 = sharp pixel specks, 100 = soft blurred blobs.", { unit: "%" })}
    ${fld("effects.black_noise.flicker", "Flicker", "Randomly dips the strength of the whole layer over time, like an old projector.", { unit: "%" })}${fld("effects.black_noise.speed", "Animation", "Slow / Normal / Fast = how quickly the specks change. Static = frozen specks, like dust on the lens (fastest to render).", { labels: { ...speed, static: "Static (frozen)" } })}
    <p class="note">Tip: size 8-12 + low density + soft = drifting dust. Size 1-2 + normal = fine film grain. Flicker adds render time.</p></div>`;
  const zoomCard = `<div data-group="effects.zoom">${fld("effects.zoom.enabled", "Enable smooth zoom", "Slowly zooms into (or out of) your pictures. Off by default.")}${fld("effects.zoom.first_image", "Always zoom the first image", "When enabled, the first image always receives a zoom even if the probability setting would skip it.")}${fld("effects.zoom.probability", "How often", "Chance that each picture gets a zoom. 100% = every picture, 50% = about half of them (random).", { unit: "%" })}
    ${fld("effects.zoom.direction", "Direction", "Zoom in, zoom out, or pick randomly for each picture.", { labels: { in: "Zoom in", out: "Zoom out", random: "Random (in or out)" } })}
    ${fld("effects.zoom.amount_min", "Zoom amount - minimum", "Smallest zoom, as % of extra size (1-100). Set min = max for a fixed amount.", { unit: "%" })}${fld("effects.zoom.amount_max", "Zoom amount - maximum", "Largest zoom (100% = twice the size). Each picture gets a random amount between min and max.", { unit: "%" })}
    ${fld("effects.zoom.speed", "Speed", "1 = the zoom takes the whole time the picture is shown (slow, gentle). 100 = the zoom finishes in about 15% of that time (fast).", { unit: "" })}${fld("effects.zoom.smoothness", "Smoothness", "0 = constant speed. 100 = gentle ease-in and ease-out (very smooth start and stop).", { unit: "%" })}
    ${fld("effects.zoom.focus", "Focus point", "Where the zoom is aimed. Random picks a different spot for every picture.", { labels: { center: "Center", top: "Top", bottom: "Bottom", left: "Left", right: "Right", top_left: "Top left", top_right: "Top right", bottom_left: "Bottom left", bottom_right: "Bottom right", custom: "Custom (use X / Y below)", random: "Random for each picture" } })}
    ${fld("effects.zoom.focus_x", "Custom focus X", "Used when Focus point = Custom. 0 = left edge, 100 = right edge.", { unit: "%" })}${fld("effects.zoom.focus_y", "Custom focus Y", "0 = top edge, 100 = bottom edge.", { unit: "%" })}
    ${fld("effects.zoom.quality", "Quality", "Standard is fast. High uses smoother resampling (about twice the render time for zoomed pictures).", { labels: { standard: "Standard (faster)", high: "High (slower)" } })}</div>`;
  return head("Effects & Transitions", "Applied globally to every project. Use Preview to check the real result on a project.") + `<div class="grid">` +
    card("Vignette", `<div data-group="effects.vignette">${fld("effects.vignette.enabled", "Enable")}${fld("effects.vignette.intensity", "Intensity", "", { unit: "%" })}${fld("effects.vignette.range", "Range", "How far the darkening reaches towards the centre.", { unit: "%" })}${fld("effects.vignette.feather", "Feathering", "Softness of the edge.", { unit: "%" })}</div>`, "effects.vignette") +
    card("Black noise - film dust (CapCut style)", dust, "effects.dust") + card("Dust details: hairs, grain, scratches", dust2) + card("Black noise - fine grain (pixel noise)", blackNoise, "effects.black_noise") + card("White noise", noise("white_noise"), "effects.white_noise") + card("Smooth zoom", zoomCard, "effects.zoom") +
    card("Subtle wobble", `<div data-group="effects.wobble">${fld("effects.wobble.enabled", "Enable")}${fld("effects.wobble.intensity", "Intensity", "Sway amount within the available margin.", { unit: "%" })}${fld("effects.wobble.range", "Movement range", "How much the frame is enlarged to allow movement.", { unit: "%" })}${fld("effects.wobble.speed", "Speed", "", { labels: speed })}</div>`, "effects.wobble") +
    card("Transitions", `<div data-group="transition">${fld("transition.enabled", "Enable transitions", "Off = hard cuts between images.")}${fld("transition.random", "Random transition", "Pick from all transitions your FFmpeg supports.")}${fld("transition.random_scope", "Random scope")}${fld("transition.name", "Transition", "Used when 'Random' is off. Unsupported ones are disabled - they are never silently replaced.", { labels, disabled: dis })}${fld("transition.duration", "Duration", "", { unit: " s" })}${fld("transition.timing", "Timing", "When the transition runs relative to the moment the next sentence starts.", { labels: { before: "Ends at the image change", center: "Centered on the image change", after: "Starts at the image change" } })}</div>${dis.length ? `<p class="note warn">${tr.filter(t => !t.available).map(t => `${t.label}: ${t.reason}`).join(" · ")}</p>` : ""}`, "transition") +
    card("Ending black screen", `<div data-group="ending">${fld("ending.enabled", "Enable")}${fld("ending.duration", "Duration", "Added after the voice ends.", { unit: " s" })}${fld("ending.fade_video", "Fade into black", "")}${fld("ending.fade_audio", "Fade out audio", "")}</div>`, "ending") + `</div>` +
    card("Preview", `<div class="bar">${projPicker()}<button class="pri" id="pvVid">Render 8-second preview</button></div><div id="vpv"></div><p class="note">Renders the first 8 seconds of the selected project with all current settings (effects, transitions, captions, audio).</p>`);
}
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b || b.id !== "pvVid") return;
  if (!needProject()) return;
  await act(b, async () => { await flushSave(); const r = await api("/api/preview", { path: previewProject, seconds: 8 }); $("#vpv").innerHTML = `<video controls autoplay loop src="${r.url}"></video>${r.warnings.map(w => `<p class="note warn">${esc(w)}</p>`).join("")}`; });
});

// ------------------------------------------------------------------ Captions, Start words, placement preview
const FONTS = ["Arial", "Segoe UI", "Impact", "Verdana", "Tahoma", "Georgia", "Trebuchet MS", "Calibri", "Montserrat", "Poppins", "Arial Black", "Times New Roman", "Courier New", "Comic Sans MS"];
const getFonts = () => { try { const f = JSON.parse(localStorage.getItem("studio_fonts") || "null"); if (f && f.length) return f; } catch (e) { /* ignore */ } return FONTS; };
const fontsBar = () => `<datalist id="fonts">${getFonts().map(f => `<option>${esc(f)}</option>`).join("")}</datalist><div class="bar" style="margin:4px 0 8px"><button id="loadFonts" title="Asks your browser for the list of every font installed on this computer (Chrome/Edge show a permission prompt).">Load all installed fonts</button><span class="note" id="fontMsg">${getFonts().length} fonts in the list. You can also type any installed font name.</span></div>`;
const QUICK = [["Top", 50, 12], ["Upper", 50, 28], ["Middle", 50, 50], ["Lower", 50, 66], ["Bottom", 50, 78]];
const placerBox = kind => `<div class="phone" id="ph-${kind}"></div><div class="note" id="pw-${kind}" style="text-align:center;min-height:36px"></div><div class="bar" style="justify-content:center">${QUICK.map(([n, x, y]) => `<button class="sm" data-quick="${kind}:${x}:${y}">${n}</button>`).join("")}</div>`;

async function captionsPage() {
  await Promise.all([reloadState(), loadProjects()]);
  const P = { classic: "Classic White", black_box: "White with Black Background", yellow: "Yellow Highlight", cinematic: "Cinematic Subtitle", social: "Bold Social Media", custom: "Custom" };
  return head("Captions", "Generated from the actual voice-over (local Faster-Whisper) or aligned to your script.") +
    card("Presets", `<div class="bar">${Object.keys(P).filter(k => k !== "custom").map(k => `<button data-preset="${k}" class="${val("captions.preset") === k ? "pri" : ""}">${P[k]}</button>`).join("")}${val("captions.preset") === "custom" ? '<span class="chip">Custom</span>' : ""}</div><p class="note">A preset fills in every field below - all of them stay editable.</p>`) +
    `<div class="grid"><div>` + card("Style", `<div data-group="captions">${fld("captions.enabled", "Enable captions")}${fld("captions.voice_sync", "Synchronize Video Captions with Image and Voice", "", { on: "Synchronize" })}${fld("captions.animation", "Caption animation", "Used with voice-synchronised captions. Highlight spoken word keeps the whole caption visible and moves only the highlight.", { labels: { progressive: "Progressive word reveal", word_highlight: "Full line with spoken-word highlight", static: "Static full caption" } })}<p class="note" style="margin-top:0">On: captions are timed from the actual voice and start fresh when the picture changes. Choose progressive reveal, a full line with spoken-word highlighting, or a static full caption. When Highlight spoken word is enabled, the complete caption remains visible while only the spoken word changes colour. Off: the previous script-based behaviour.</p>${fontsBar()}${fld("captions.font", "Font family", "", { list: "fonts" })}${fld("captions.size", "Font size", "Defined for a 1080 px wide frame.", { unit: " px" })}${fld("captions.color", "Font colour")}${fld("captions.bold", "Bold", "", { on: "Bold" })}${fld("captions.uppercase", "UPPERCASE", "", { on: "On" })}
      ${fld("captions.outline", "Text outline", "", { unit: " px" })}${fld("captions.outline_color", "Outline colour")}${fld("captions.shadow", "Text shadow", "", { unit: " px" })}
      ${fld("captions.bg_enabled", "Background box", "")}${fld("captions.bg_color", "Background colour")}${fld("captions.bg_opacity", "Background opacity", "How solid the box is.", { unit: "%" })}${fld("captions.padding", "Padding", "Space between the text and the edge of the box.", { unit: " px" })}
      <p class="note">Border radius: not available - FFmpeg's subtitle renderer (libass) cannot draw rounded boxes.</p>
      ${fld("captions.highlight", "Highlight spoken word", "")}${fld("captions.highlight_color", "Highlight colour")}${fld("captions.highlight_lead", "Highlight timing", "", { unit: " ms", num: true })}</div>`, "captions.") + `</div><div>` +
    card("Position & layout", `<div data-group="captions">${fld("captions.pos_x", "Position X", "", { unit: "%" })}${fld("captions.pos_y", "Position Y", "", { unit: "%" })}${fld("captions.align", "Text alignment", "", { labels: { left: "Left", center: "Center", right: "Right" } })}${fld("captions.max_words", "Max words per line")}${fld("captions.max_lines", "Max lines")}${fld("captions.timing_offset", "Timing adjustment", "Positive = captions appear later, negative = earlier.", { unit: " s", step: 0.05 })}</div>
      <p class="note">Place the caption anywhere: drag it in the preview, use the X/Y sliders or the quick buttons. The red areas are covered by the app interface on Reels/Shorts - you are warned, but never blocked.</p>`) +
    card("Placement preview", placerBox("captions")) + `</div></div>` +
    card("Real preview", `<div class="bar">${projPicker()}<button class="pri" id="pvCap">Render 8-second preview</button></div><div id="vpv"></div>`);
}

async function startPage() {
  await Promise.all([reloadState(), loadProjects()]);
  return head("Start Words", "1-6 words shown for a split second at the very start of every video (a hook / title). Applied to the whole batch.") +
    `<div class="grid"><div>` + card("Words & timing", `<div data-group="start">${fld("start.enabled", "Show start words")}${fld("start.file", "Text file name", "", { })}${fld("start.pick", "If the file has several lines", "", { labels: { first_line: "Use the first line", random_line: "Pick a random line for each video" } })}${fld("start.max_words", "Max words")}${fld("start.default_text", "Fallback text", "")}
      ${fld("start.duration_ms", "Stays on screen", "", { unit: " ms" })}${fld("start.delay_ms", "Delay before showing", "", { unit: " ms" })}${fld("start.fade_in_ms", "Fade in", "", { unit: " ms" })}${fld("start.fade_out_ms", "Fade out", "", { unit: " ms" })}</div>
      <p class="note">Put a text file named <b id="sfn">${esc(val("start.file"))}</b> with the words (for example <i>Wait for it</i>) in each project folder - the same folder as the pictures. The name can be changed above. Projects without the file use the fallback text, or show no start words.</p>`, "start.") +
    card("Style", `<div data-group="start">${fontsBar()}${fld("start.font", "Font family", "", { list: "fonts" })}${fld("start.size", "Size", "", { unit: " px" })}${fld("start.color", "Colour")}${fld("start.bold", "Bold", "", { on: "Bold" })}${fld("start.italic", "Italic", "", { on: "Italic" })}${fld("start.uppercase", "UPPERCASE", "", { on: "On" })}${fld("start.spacing", "Letter spacing", "", { unit: " px" })}
      ${fld("start.outline", "Outline", "", { unit: " px" })}${fld("start.outline_color", "Outline colour")}${fld("start.shadow", "Shadow", "", { unit: " px" })}${fld("start.bg_enabled", "Background box", "")}${fld("start.bg_color", "Box colour")}${fld("start.bg_opacity", "Box opacity", "", { unit: "%" })}${fld("start.padding", "Box padding", "", { unit: " px" })}</div>`) + `</div><div>` +
    card("Place & rotate", `<div data-group="start">${fld("start.rotation", "Rotation", "", { unit: "°", num: true })}${fld("start.pos_x", "Position X", "", { unit: "%" })}${fld("start.pos_y", "Position Y", "", { unit: "%" })}</div><p class="note">Drag the words in the preview to place them. Red areas are covered by the app interface on Reels/Shorts.</p>` + placerBox("start")) + `</div></div>` +
    card("Real preview", `<div class="bar">${projPicker()}<button class="pri" id="pvStart">Render 4-second preview</button></div><div id="vpv"></div>`);
}

const capPreview = () => drawPlacer("captions");
function drawPlacer(kind) {
  const ph = $("#ph-" + kind); if (!ph) return;
  const [pw, phh] = { "9:16": [270, 480], "16:9": [480, 270], "1:1": [360, 360] }[st.settings.video.aspect] || [270, 480];
  ph.style.width = pw + "px"; ph.style.height = phh + "px";
  const c = st.settings[kind], k = Math.min(pw, phh) / 1080, vertical = phh > pw;
  const rgba = (h, o) => { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${o / 100})`; };
  let txt, style, anchor = "-50%", rot = 0;
  if (kind === "captions") {
    const words = "Sometimes life doesn't go as planned".split(" "), lines = [];
    for (let i = 0; i < words.length && lines.length < c.max_lines; i += c.max_words) lines.push(words.slice(i, i + c.max_words));
    txt = lines.map((l, li) => l.map((w, wi) => { const W = c.uppercase ? w.toUpperCase() : w; return c.highlight && li === 0 && wi === 1 ? `<span style="color:${c.highlight_color}">${esc(W)}</span>` : esc(W); }).join(" ")).join("<br>");
    anchor = { left: "0%", center: "-50%", right: "-100%" }[c.align];
    style = [`font-family:'${c.font}',sans-serif`, `font-size:${c.size * k}px`, `font-weight:${c.bold ? 700 : 400}`, `color:${c.color}`, `text-align:${c.align}`,
      c.outline ? `-webkit-text-stroke:${c.outline * k * 1.6}px ${c.outline_color};paint-order:stroke fill` : "", c.shadow && !c.bg_enabled ? `text-shadow:${c.shadow * k}px ${c.shadow * k}px ${c.shadow * k * 1.5}px rgba(0,0,0,.7)` : "",
      c.bg_enabled ? `background:${rgba(c.bg_color, c.bg_opacity)};padding:${c.padding * k}px;border-radius:2px` : ""].join(";");
  } else {
    const t = (c.default_text || "Wait for it").split(/\s+/).slice(0, c.max_words).join(" ");
    txt = esc(c.uppercase ? t.toUpperCase() : t); rot = c.rotation;
    style = [`font-family:'${c.font}',sans-serif`, `font-size:${c.size * k}px`, `font-weight:${c.bold ? 700 : 400}`, `font-style:${c.italic ? "italic" : "normal"}`, `color:${c.color}`, `letter-spacing:${c.spacing * k}px`, "text-align:center",
      c.outline ? `-webkit-text-stroke:${c.outline * k * 1.6}px ${c.outline_color};paint-order:stroke fill` : "", c.shadow && !c.bg_enabled ? `text-shadow:${c.shadow * k}px ${c.shadow * k}px ${c.shadow * k * 1.5}px rgba(0,0,0,.7)` : "",
      c.bg_enabled ? `background:${rgba(c.bg_color, c.bg_opacity)};padding:${c.padding * k}px` : ""].join(";");
  }
  // areas covered by the Facebook / Instagram / YouTube interface on vertical video
  const zones = vertical ? [[0, 0, 100, 8, "top"], [0, 82, 100, 18, "bottom bar"], [86, 40, 14, 42, "buttons"]] : [[0, 0, 100, 4, ""], [0, 94, 100, 6, ""]];
  ph.innerHTML = zones.map(z => `<div class="zone" style="left:${z[0]}%;top:${z[1]}%;width:${z[2]}%;height:${z[3]}%">${z[4]}</div>`).join("") +
    `<div class="ptxt" id="pt-${kind}" style="left:${c.pos_x}%;top:${c.pos_y}%;transform:translate(${anchor},-50%) rotate(${rot}deg);${style}">${txt}</div>`;
  const el = $("#pt-" + kind), r = el.getBoundingClientRect(), p = ph.getBoundingClientRect();
  const outside = r.left < p.left - 1 || r.right > p.right + 1 || r.top < p.top - 1 || r.bottom > p.bottom + 1;
  const hit = zones.some(z => { const zl = p.left + z[0] / 100 * p.width, zt = p.top + z[1] / 100 * p.height, zr = zl + z[2] / 100 * p.width, zb = zt + z[3] / 100 * p.height; return r.left < zr && r.right > zl && r.top < zb && r.bottom > zt; });
  const w = $("#pw-" + kind);
  if (w) w.innerHTML = outside ? '<span class="err">⚠ Part of the text is outside the video and will be cut off.</span>' : hit ? '<span class="warn">⚠ This spot can be covered by the Facebook/Instagram/YouTube interface (red areas). It is allowed, but viewers may not see it.</span>' : '<span class="ok">✓ Inside the safe area.</span>';
}
let drag = null;
function placeFrom(ev, kind) {
  const ph = $("#ph-" + kind), p = ph.getBoundingClientRect();
  const x = Math.max(0, Math.min(100, Math.round((ev.clientX - p.left) / p.width * 100))), y = Math.max(0, Math.min(100, Math.round((ev.clientY - p.top) / p.height * 100)));
  setPos(kind, x, y);
}
function setPos(kind, x, y) {
  st.settings[kind].pos_x = x; st.settings[kind].pos_y = y;
  [["pos_x", x], ["pos_y", y]].forEach(([f, v]) => { const el = $(`[data-key='${kind}.${f}']`); if (el) { el.value = v; if (el.nextElementSibling?.tagName === "OUTPUT") el.nextElementSibling.textContent = v + "%"; } queueSave(`${kind}.${f}`, v); });
  drawPlacer(kind);
}
document.addEventListener("pointerdown", e => { const t = e.target.closest(".ptxt"); if (t) { drag = t.id.slice(3); e.preventDefault(); } else { const ph = e.target.closest(".phone"); if (ph) { drag = ph.id.slice(3); placeFrom(e, drag); } } });
document.addEventListener("pointermove", e => { if (drag) placeFrom(e, drag); });
document.addEventListener("pointerup", () => { drag = null; });
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.quick) { const [kind, x, y] = b.dataset.quick.split(":"); setPos(kind, Number(x), Number(y)); }
  else if (b.dataset.preset) { const v = st.presets_caption[b.dataset.preset], patch = { "captions.preset": b.dataset.preset }; Object.entries(v).forEach(([k, x]) => patch["captions." + k] = x); await act(b, async () => { const r = await api("/api/settings", { settings: patch }); st.settings = r.settings; render(); }); }
  else if (b.id === "pvCap" || b.id === "pvStart") { if (!needProject()) return; await act(b, async () => { await flushSave(); const r = await api("/api/preview", { path: previewProject, seconds: b.id === "pvStart" ? 4 : 8 }); $("#vpv").innerHTML = `<video controls autoplay loop src="${r.url}"></video>${r.warnings.map(w => `<p class="note warn">${esc(w)}</p>`).join("")}`; }); }
  else if (b.id === "loadFonts") {
    if (!window.queryLocalFonts) return toast("This browser cannot list installed fonts. Use Chrome or Edge, or type the font name.", "err");
    await act(b, async () => { const f = await window.queryLocalFonts(); const fam = [...new Set(f.map(x => x.family))].sort((a, z) => a.localeCompare(z)); localStorage.setItem("studio_fonts", JSON.stringify(fam)); $("#fonts").innerHTML = fam.map(x => `<option>${esc(x)}</option>`).join(""); $("#fontMsg").textContent = `${fam.length} installed fonts loaded - click the font box and start typing to search.`; });
  }
});

// ------------------------------------------------------------------ Batch
async function batchPage() {
  await Promise.all([reloadState(), loadProjects()]);
  const c = st.counts;
  return head("Batch Processing", "Projects render independently in the background. You can close or refresh this page at any time.") +
    `<div class="tiles" id="btiles"></div>` +
    card("Controls", `<div class="bar"><button class="pri" data-act="start_all" title="Queue every project that is ready">Start all</button><button id="bStartSel">Start selected</button>
      <button data-act="${st.paused ? "resume" : "pause"}" id="bPause">${st.paused ? "Resume" : "Pause"}</button><button class="dng" id="bStop">Stop</button><button data-act="retry_failed">Retry failed</button><button id="bClear" title="Return failed and skipped projects to Ready">Reset failed/skipped</button></div>
      <div class="prog" style="height:10px"><i id="ball" style="width:0"></i></div><p class="note" id="bnote"></p><p id="btime" style="margin:8px 0 0"></p>`) +
    `<div class="grid">` + card("Output", fld("output.suffix", "Filename suffix", "Final file: <project>_final.mp4 inside the project folder.", {}) + fld("output.overwrite", "Overwrite existing output", "Replace an existing final video.") + fld("output.skip_existing", "Skip existing output", "Projects that already have a final video are skipped (never re-rendered unless you choose Re-render).") + fld("paths.extra_output", "Additional output folder", "Optional: a copy of every finished video is also saved here.", { browse: true }), "output.,paths.extra_output") +
    card("Resources", fld("batch.workers", "Parallel render workers", "Default 1 protects limited computers. Each worker runs one FFmpeg process.", {}) + `<p class="note">Rendering uses a persistent queue: if the app or PC restarts, unfinished projects resume automatically.</p>`, "batch.") + `</div>` +
    `<div class="card"><div class="tw"><table><thead><tr><th></th><th>Project</th><th>Status</th><th>Progress</th><th>Message</th><th>Time</th><th>Music</th><th></th></tr></thead><tbody id="btb"></tbody></table></div></div>`;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "bStop") { if (confirm("Stop the batch? Running renders are cancelled and queued projects return to Ready.")) await act(b, async () => { await api("/api/batch", { action: "stop" }); await poll(true); }); }
  else if (b.id === "bStartSel") { if (!sel.size) return toast("Select projects in the table below or in Projects", "err"); await act(b, async () => { const r = await api("/api/batch", { action: "start_selected", paths: [...sel] }); toast(`${r.queued} queued`, "ok"); await poll(true); }); }
  else if (b.id === "bClear") await act(b, async () => { await api("/api/batch", { action: "clear_finished" }); await poll(true); });
});
function batchTiles() {
  const c = st.counts, el = $("#btiles"); if (!el) return;
  el.innerHTML = [["total", "Total"], ["ready", "Ready"], ["missing", "Missing files"], ["pending", "Pending"], ["processing", "Processing"], ["completed", "Completed"], ["failed", "Failed"], ["skipped", "Skipped"]].map(([k, n]) => `<div class="tile"><b>${c[k]}</b><span>${n}</span></div>`).join("");
  const done = c.completed + c.failed + c.skipped, tot = done + c.pending + c.processing;
  $("#ball").style.width = (tot ? Math.round(done / tot * 100) : 0) + "%";
  $("#bnote").textContent = `${done} of ${tot} in the current batch finished${st.paused ? " · PAUSED (running jobs finish, no new ones start)" : ""}`;
  const pb = $("#bPause"); if (pb) { pb.textContent = st.paused ? "Resume" : "Pause"; pb.dataset.act = st.paused ? "resume" : "pause"; }
}

// ------------------------------------------------------------------ Settings
async function settingsPage() {
  await reloadState();
  const presets = await api("/api/presets");
  return head("Settings", "Presets, defaults and diagnostics. All settings are saved automatically and restored on next start.") + `<div class="grid">` +
    card("Presets", `<p class="note" style="margin-top:0">A preset stores every video, audio, effect, transition, caption and start-words setting (not folder paths). Export a preset to a file to back it up or share it; import it on another computer.</p>
      <div class="bar"><input type="text" id="pname" class="grow" placeholder="Preset name"><button class="pri" id="pSave">Save / update</button></div>
      <div class="bar"><button id="pImport">Import from file…</button><input type="file" id="pfile" accept=".json,application/json" hidden>${presets.length ? '<a class="btn" id="pExportAll" href="/api/presets/export" download>Export all</a>' : '<button disabled>Export all</button>'}</div>
      ${presets.length ? presets.map(p => `<div class="prow"><div><b>${esc(p.name)}</b><div class="mute" style="font-size:12px">${new Date(p.updated * 1000).toLocaleString()}</div></div><div class="pbtns"><button class="sm" data-pload="${esc(p.name)}">Load</button><button class="sm" data-pupd="${esc(p.name)}" title="Overwrite this preset with the current settings">Update with current</button><a class="btn sm" data-pexp="${esc(p.name)}" href="/api/presets/export?name=${encodeURIComponent(p.name)}" download title="Download this preset as a file">Export</a><button class="sm dng" data-pdel="${esc(p.name)}">Delete</button></div></div>`).join("") : '<p class="mute">No presets yet.</p>'}`) +
    card("Defaults & maintenance", `<div class="bar"><button class="dng" id="sReset">Restore application defaults</button><button id="sCache">Clear caches (thumbnails, transcripts, previews)</button></div><p class="note">Defaults keep your folder paths. Clearing transcripts means voices will be transcribed again.</p>`) +
    card("Danger zone", `<p class="note" style="margin-top:0"><b>Stop everything</b> cancels all running renders and empties the queue (finished videos stay).<br><b>Reset everything</b> also forgets all projects, statuses, timings, logs, previews, transcripts and caches - as if you had just installed the app. <b>Your settings and presets are kept.</b></p>
      <div class="bar"><button class="dng" id="stopAll3">■ Stop everything</button><button class="dng" id="resetAll">Reset everything (keep my settings)</button></div>`) +
    card("Diagnostics", `<table><tr><td>FFmpeg</td><td>${st.caps.ok ? esc(st.caps.version) : '<span class="err">not found</span>'}</td></tr><tr><td>Captions (libass)</td><td>${st.caps.libass ? "yes" : "no"}</td></tr><tr><td>Hardware encoders</td><td>${st.caps.hw.join(", ") || "none"}</td></tr><tr><td>Faster-Whisper</td><td>${st.whisper ? "installed" : "not installed"}</td></tr><tr><td>Transitions</td><td>${st.transitions.filter(t => t.available).length}/${st.transitions.length} available</td></tr><tr><td>Data folder</td><td>${esc(st.data_dir)}</td></tr><tr><td>Server</td><td>localhost only</td></tr></table>`) + `</div>`;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  const post = (u, d) => api(u, d);
  if (b.id === "pSave" || b.dataset.pupd) { const name = b.dataset.pupd || $("#pname").value.trim(); if (!name) return toast("Enter a preset name", "err"); await act(b, async () => { await flushSave(); await post("/api/presets/save", { name }); toast("Preset saved", "ok"); render(); }); }
  else if (b.dataset.pload) await act(b, async () => { const r = await post("/api/presets/load", { name: b.dataset.pload }); st.settings = r.settings; toast("Preset loaded", "ok"); render(); });
  else if (b.dataset.pdel) { if (confirm(`Delete preset "${b.dataset.pdel}"?`)) await act(b, async () => { await post("/api/presets/delete", { name: b.dataset.pdel }); render(); }); }
  else if (b.id === "pImport") $("#pfile").click();
  else if (b.id === "sReset") { if (confirm("Restore ALL settings to application defaults? (Folder paths are kept.)")) await act(b, async () => { const r = await post("/api/settings/reset", {}); st.settings = r.settings; toast("Defaults restored", "ok"); render(); }); }
  else if (b.id === "sCache") { if (confirm("Delete cached thumbnails, transcripts and previews?")) await act(b, async () => { const r = await post("/api/clear-cache", {}); toast(`${r.removed} file(s) removed`, "ok"); }); }
  else if (b.id === "resetAll") {
    if (!confirm("RESET EVERYTHING?\n\nThis cancels all running work and forgets all projects, statuses, timings, logs, previews and transcripts.\nYour settings and presets are kept.\n\nContinue?")) return;
    const del = confirm("Also DELETE the finished videos that this app made (the *_final.mp4 files)?\n\nOK = delete them too\nCancel = keep the videos");
    await act(b, async () => { const r = await post("/api/reset-all", { delete_videos: del }); sel.clear(); projects = []; pollKey = ""; await reloadState(); toast(`Reset done${del ? ` - ${r.videos_deleted} video(s) deleted` : ""}. Settings kept.`, "ok"); render(); });
  }
});
document.addEventListener("change", async e => {
  if (e.target.id !== "pfile" || !e.target.files[0]) return;
  try {
    const data = JSON.parse(await e.target.files[0].text()), r = await api("/api/presets/import", { data });
    toast(`Imported: ${r.saved.join(", ") || "nothing"}${r.skipped.length ? " · Skipped: " + r.skipped.join("; ") : ""}`, r.skipped.length ? "err" : "ok"); render();
  } catch (x) { toast("Import failed: " + x.message, "err"); }
  e.target.value = "";
});

// ------------------------------------------------------------------ global: tooltips, stop/reset, online status
let tipEl = null;
function hideTip() { tipEl?.remove(); tipEl = null; }
function showTip(t) {
  hideTip(); tipEl = document.createElement("div"); tipEl.className = "tipbox"; tipEl.textContent = t.dataset.tip; document.body.appendChild(tipEl);
  const r = t.getBoundingClientRect(), h = tipEl.offsetHeight;
  tipEl.style.left = Math.max(8, Math.min(innerWidth - 320, r.left - 10)) + "px";
  tipEl.style.top = (r.bottom + 8 + h > innerHeight ? r.top - h - 8 : r.bottom + 8) + "px";
}
document.addEventListener("mouseover", e => { const t = e.target.closest(".tip"); if (t) showTip(t); });
document.addEventListener("mouseout", e => { if (e.target.closest(".tip")) hideTip(); });
document.addEventListener("focusin", e => { const t = e.target.closest(".tip"); if (t) showTip(t); });
document.addEventListener("focusout", e => { if (e.target.closest(".tip")) hideTip(); });
document.addEventListener("click", e => { const t = e.target.closest(".tip"); if (t) { e.preventDefault(); tipEl ? hideTip() : showTip(t); } else hideTip(); });
document.addEventListener("click", async e => {
  const b = e.target.closest("#stopAll, #stopAll2, #stopAll3"); if (!b) return;
  if (!confirm("Stop everything?\n\nRunning renders are cancelled, the queue is emptied and a running preview is cancelled.\nFinished videos are kept.")) return;
  await act(b, async () => { const r = await api("/api/stop-all", {}); st.counts = r.counts; toast("Everything stopped", "ok"); await poll(true); });
});
function setNet(online) {
  const el = $("#netstat"); if (!el) return;
  el.innerHTML = online === null ? '<span class="dot err" style="display:inline-block;margin:0 5px 1px 0"></span>Server not responding' : `Local · <span class="dot ${online ? "ok" : "warn"}" style="display:inline-block;margin:0 4px 1px 0"></span>${online ? "Online" : "Offline"}`;
  el.title = online === null ? "The local server is not responding." : "Everything runs on this computer and works without internet. The internet is only used to download transcription models. This shows whether an internet connection is currently available.";
}
async function netStatus() { try { setNet((await api("/api/net")).online); } catch (e) { setNet(null); } }
setInterval(netStatus, 30000);

// ------------------------------------------------------------------ Logs
let logFilter = { q: "", level: "", project: "" };
async function logsPage() {
  await loadProjects();
  return head("Activity & Error Logs", "Every error shows the project, category, detail and a suggested solution.") +
    `<div class="card"><div class="bar"><input type="text" id="lq" class="grow" placeholder="Search logs…" value="${esc(logFilter.q)}"><select id="ll" style="width:130px">${["", "error", "warn", "info"].map(l => `<option value="${l}" ${logFilter.level === l ? "selected" : ""}>${l ? cap(l) : "All levels"}</option>`).join("")}</select>
    <select id="lp" style="width:170px"><option value="">All projects</option>${projects.map(p => `<option ${logFilter.project === p.name ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
    <button id="lRetry">Retry failed jobs</button><button id="lTxt">Export TXT</button><button id="lCsv">Export CSV</button><button class="dng" id="lClear">Clear log</button></div>
    <div class="tw"><table><thead><tr><th>Time</th><th>Project</th><th>Level</th><th>Category</th><th>Message</th></tr></thead><tbody id="ltb"></tbody></table></div></div><div id="ldet"></div>`;
}
let logRows = [];
async function loadLogs() {
  const f = logFilter; logRows = await api(`/api/logs?q=${encodeURIComponent(f.q)}&level=${f.level}&project=${encodeURIComponent(f.project)}`);
  const tb = $("#ltb"); if (!tb) return;
  tb.innerHTML = logRows.map((r, i) => `<tr class="click" data-log="${i}"><td class="mute" style="white-space:nowrap">${new Date(r.ts * 1000).toLocaleString()}</td><td>${esc(r.project)}</td><td class="${r.level === "error" ? "err" : r.level === "warn" ? "warn" : "mute"}">${r.level}</td><td>${esc(r.category)}</td><td>${esc(r.message)}</td></tr>`).join("") || '<tr><td colspan="5" class="mute">No log entries.</td></tr>';
}
document.addEventListener("input", e => { if (e.target.id === "lq") { logFilter.q = e.target.value; clearTimeout(window._lt); window._lt = setTimeout(loadLogs, 250); } });
document.addEventListener("change", e => { if (e.target.id === "ll") { logFilter.level = e.target.value; loadLogs(); } if (e.target.id === "lp") { logFilter.project = e.target.value; loadLogs(); } });
document.addEventListener("click", async e => {
  const tr = e.target.closest("[data-log]");
  if (tr) { const r = logRows[tr.dataset.log]; $("#ldet").innerHTML = card("Details", `<p><b>${esc(r.project || "System")}</b> · ${esc(r.category || r.level)}<br>${esc(r.message)}</p>${r.suggestion ? `<p class="ok">Suggested solution: ${esc(r.suggestion)}</p>` : ""}${r.detail ? `<pre>${esc(r.detail)}</pre>` : ""}${r.project ? `<button class="sm" id="lFf" data-p="${esc(r.project)}">Show FFmpeg log</button><pre id="lFfb" hidden></pre>` : ""}`); return; }
  const b = e.target.closest("button"); if (!b) return;
  const qs = `q=${encodeURIComponent(logFilter.q)}&level=${logFilter.level}&project=${encodeURIComponent(logFilter.project)}`;
  if (b.id === "lTxt") location.href = `/api/logs/export?fmt=txt&${qs}`;
  else if (b.id === "lCsv") location.href = `/api/logs/export?fmt=csv&${qs}`;
  else if (b.id === "lRetry") await act(b, async () => { const r = await api("/api/batch", { action: "retry_failed" }); toast(`${r.queued} failed project(s) re-queued`, "ok"); });
  else if (b.id === "lClear") { if (confirm("Delete all log entries?")) await act(b, async () => { await api("/api/logs/clear", {}); loadLogs(); }); }
  else if (b.id === "lFf") { const r = await api("/api/logs/ffmpeg?project=" + encodeURIComponent(b.dataset.p)); const p = $("#lFfb"); p.hidden = false; p.textContent = r.text; }
});

// ------------------------------------------------------------------ polling (UI only observes - rendering never depends on the browser)
let polling = false;
async function poll(force) {
  if (polling && !force) return; polling = true;
  try {
    const b = await api("/api/batch"); st.counts = b.counts; st.paused = b.paused;
    const by = Object.fromEntries(b.rows.map(r => [r.path, r]));
    if (projects.length !== b.rows.length) await loadProjects(); else projects.forEach(p => { const r = by[p.path]; if (r) Object.assign(p, { status: r.status, progress: r.progress, message: r.message, category: r.category, output: r.output, music: r.music, elapsed: r.elapsed, eta: r.eta, est: r.est }); });
    lastTiming = b.timing; nav(); batchTiles(); drawRows(); updateTimingUI();
    const ck = JSON.stringify(st.counts); if (page === "dashboard" && ck !== window._ck) { window._ck = ck; render(); }
    if (page === "logs") loadLogs();
  } catch (e) { /* server restarting */ } finally { polling = false; }
}
setInterval(() => { if (st && ["dashboard", "projects", "batch", "logs"].includes(page)) poll(); }, 1800);

(async function init() {
  try { await reloadState(); } catch (e) { $("#main").innerHTML = `<p class="err">Cannot reach the local server: ${esc(e.message)}</p>`; return; }
  netStatus();
  const h = location.hash.slice(1); if (PAGES.find(p => p[0] === h)) page = h;
  await render(); if (page === "batch") await poll(true);
})();
window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (h !== page && PAGES.find(p => p[0] === h)) go(h); });
