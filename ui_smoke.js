// Browser smoke test (Playwright). Usage: node tests/ui_smoke.js http://localhost:8792 /path/to/projects /path/to/music
const { chromium } = require("playwright");
const [base, parent, music] = process.argv.slice(2);
const results = [];
const check = (n, c, x = "") => { results.push(!!c); console.log((c ? "PASS " : "FAIL ") + n + (c ? "" : "  [" + x + "]")); };
(async () => {
  const b = await chromium.launch(); const page = await b.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = []; page.on("pageerror", e => errors.push("pageerror: " + e.message)); page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  const api = (p, body) => page.evaluate(([p, body]) => fetch(p, body ? { method: "POST", headers: { "X-Studio": "1", "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}).then(r => r.json()), [p, body]);
  await page.goto(base); await page.waitForSelector("#nav a");
  await api("/api/settings", { settings: { paths: { parent, music_dir: music }, effects: { vignette: { enabled: false }, black_noise: { enabled: false }, white_noise: { enabled: false }, wobble: { enabled: false }, dust: { enabled: false } }, video: { preset: "ultrafast" } } });
  const pages = ["dashboard", "projects", "voice", "video", "effects", "captions", "batch", "settings", "logs"];
  for (const p of pages) {
    await page.click(`#nav a[data-page=${p}]`); await page.waitForTimeout(500);
    const txt = await page.textContent("#main");
    check(`page '${p}' renders`, txt.length > 80 && !/Loading…/.test(txt), txt.slice(0, 100));
  }
  // ---- tooltips: every labelled control has a working "?" note
  for (const p of pages.concat(["start"])) {
    await page.click(`#nav a[data-page=${p}]`); await page.waitForTimeout(400);
    const missing = await page.$$eval("#main .row > label", ls => ls.filter(l => !l.querySelector(".tip") || (l.querySelector(".tip").dataset.tip || "").length < 8).map(l => l.textContent.trim()));
    check(`page '${p}': every control label has a '?' note`, missing.length === 0, missing.join(", "));
    await page.setViewportSize({ width: 1360, height: 3400 }); await page.waitForTimeout(150);
    const covered = await page.$$eval("#main button, #main a.btn, #main input, #main select, #main textarea", els => els.filter(el => { const r = el.getBoundingClientRect(); if (!r.width || !r.height || el.type === "hidden" || el.hidden) return false; const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return t && !(el.contains(t) || t.contains(el)); }).map(el => (el.id || el.dataset.key || el.dataset.pexp || el.textContent.trim().slice(0, 20))));
    check(`page '${p}': no control is covered by another element`, covered.length === 0, covered.join(", "));
    await page.setViewportSize({ width: 1360, height: 900 });
  }
  await page.click("#nav a[data-page=voice]"); await page.waitForSelector("#main .tip");
  await page.hover("#main .tip"); await page.waitForSelector(".tipbox");
  check("hovering a '?' shows its note", (await page.textContent(".tipbox")).length > 10);
  // ---- online status (no longer hard-coded 'Offline')
  const shown = await page.textContent("#netstat"), net = await api("/api/net");
  check("top-left status reflects the real connection state", shown.includes(net.online ? "Online" : "Offline"), shown);
  check("Stop-everything button is always visible in the sidebar", !!(await page.$("#stopAll")));
  // Projects: scan via UI
  await page.click("#nav a[data-page=projects]"); await page.waitForSelector("#scanBtn");
  await page.click("#scanBtn"); await page.waitForSelector("#ptb tr.click", { timeout: 20000 });
  check("scan via UI lists 3 projects", (await page.$$("#ptb tr.click")).length === 3);
  await page.screenshot({ path: "/tmp/ui_projects.png" });
  // settings control -> backend
  await page.click("#nav a[data-page=video]"); await page.waitForSelector("[data-key='video.crf']");
  await page.$eval("[data-key='video.crf']", el => { el.value = 27; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.selectOption("[data-key='video.fit']", "crop"); await page.waitForTimeout(900);
  let s = await api("/api/state"); check("slider + select persisted to backend", s.settings.video.crf === 27 && s.settings.video.fit === "crop");
  await page.click("[data-reset^='video.aspect']"); await page.click("[data-reset^='video.fit']"); await page.waitForTimeout(900);
  s = await api("/api/state"); check("section Default buttons restore defaults", s.settings.video.crf === 20 && s.settings.video.fit === "blur");
  // ---- GPU / CPU / automatic choice + whisper model manager
  const hwOpts = await page.$$eval("[data-key='video.hwaccel'] option", o => o.map(x => x.value));
  check("hardware choice offers Automatic / GPU / CPU", JSON.stringify(hwOpts) === JSON.stringify(["auto", "gpu", "cpu"]), hwOpts.join());
  await page.selectOption("[data-key='video.hwaccel']", "cpu"); await page.waitForTimeout(800);
  s = await api("/api/state"); check("hardware choice saved", s.settings.video.hwaccel === "cpu");
  await page.waitForSelector("#models table"); const mtxt = await page.textContent("#models");
  check("model manager lists models, sizes and the model in use", /base/.test(mtxt) && /large-v3/.test(mtxt) && /Currently used/.test(mtxt));
  check("a not-downloaded model offers Download (and base stays in use)", /Download/.test(mtxt) || /not installed/i.test(mtxt));
  // ---- image synchronisation: two modes, voice-based is the default
  const meth = await page.$eval("[data-key='sync.method']", e => ({ v: e.value, o: [...e.options].map(x => x.value) }));
  check("Image sync: two methods, voice-based is the default", meth.v === "voice" && JSON.stringify(meth.o) === JSON.stringify(["voice", "manual"]), JSON.stringify(meth));
  check("voice mode: confidence/hold/lead/unmatched enabled, manual settings greyed out", !(await page.$eval("[data-key='sync.match_threshold']", e => e.disabled)) && await page.$eval("[data-key='sync.mode']", e => e.disabled));
  await page.selectOption("[data-key='sync.method']", "manual"); await page.waitForTimeout(700);
  check("manual mode keeps all existing settings (distribution, sentences per image, mismatch threshold/action)", ["sync.mode", "sync.group_size", "sync.mismatch_threshold", "sync.mismatch_action"].length === (await page.$$("[data-key='sync.mode']:not([disabled]),[data-key='sync.group_size']:not([disabled]),[data-key='sync.mismatch_threshold']:not([disabled]),[data-key='sync.mismatch_action']:not([disabled])")).length && await page.$eval("[data-key='sync.match_threshold']", e => e.disabled));
  s = await api("/api/state"); check("method choice saved", s.settings.sync.method === "manual");
  await page.selectOption("[data-key='sync.method']", "voice"); await page.$eval("[data-key='sync.match_threshold']", el => { el.value = 65; el.dispatchEvent(new Event("input", { bubbles: true })); }); await page.waitForTimeout(800);
  s = await api("/api/state"); check("voice-sync confidence threshold saved", s.settings.sync.method === "voice" && s.settings.sync.match_threshold === 65);
  // effects page: disabled transitions + toggle disables group
  await page.click("#nav a[data-page=effects]"); await page.waitForSelector("[data-key='transition.name']");
  const zo = await page.$eval("[data-key='transition.name'] option[value=zoomout]", o => ({ d: o.disabled, t: o.textContent }));
  check("Zoom Out shown as unavailable", zo.d && /unavailable/.test(zo.t));
  check("Smooth zoom + extended black-noise controls are present", !!(await page.$("[data-key='effects.zoom.probability']")) && !!(await page.$("[data-key='effects.black_noise.softness']")) && !!(await page.$("[data-key='effects.black_noise.flicker']")));
  check("zoom controls are greyed out while zoom is disabled", await page.$eval("[data-key='effects.zoom.probability']", el => el.disabled));
  await page.check("[data-key='effects.zoom.enabled']"); await page.waitForTimeout(300);
  check("enabling zoom un-greys its controls", !(await page.$eval("[data-key='effects.zoom.probability']", el => el.disabled)));
  await page.selectOption("[data-key='effects.zoom.focus']", "top_left"); await page.selectOption("[data-key='effects.zoom.direction']", "random");
  for (const [k, v] of [["probability", 55], ["speed", 60], ["smoothness", 90], ["amount_min", 5], ["amount_max", 70]])
    await page.$eval(`[data-key='effects.zoom.${k}']`, (el, v) => { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); }, v);
  await page.check("[data-key='effects.black_noise.enabled']");
  await page.selectOption("[data-key='effects.black_noise.speed']", "static");
  await page.$eval("[data-key='effects.black_noise.size']", el => { el.value = 9; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.waitForTimeout(1000);
  s = await api("/api/state"); const z = s.settings.effects.zoom, bn = s.settings.effects.black_noise;
  check("zoom options persisted to backend", z.enabled && z.focus === "top_left" && z.direction === "random" && z.probability === 55 && z.speed === 60 && z.smoothness === 90 && z.amount_min === 5 && z.amount_max === 70, JSON.stringify(z));
  check("black-noise options persisted to backend", bn.speed === "static" && bn.size === 9, JSON.stringify(bn));
  await page.click("[data-reset='effects.zoom']"); await page.waitForTimeout(700);
  s = await api("/api/state"); check("zoom Default button restores defaults", s.settings.effects.zoom.enabled === false && s.settings.effects.zoom.probability === 70);
  await page.screenshot({ path: "/tmp/ui_effects.png", fullPage: true });
  // captions: preset button fills fields; live preview
  await page.click("#nav a[data-page=captions]"); await page.waitForSelector("[data-preset=social]");
  await page.click("[data-preset=social]"); await page.waitForTimeout(800);
  s = await api("/api/state"); check("caption preset applied to fields", s.settings.captions.preset === "social" && s.settings.captions.uppercase === true && s.settings.captions.max_words === 3);
  check("live caption preview shows uppercase text", /SOMETIMES/.test(await page.textContent("#ph-captions")));
  await page.$eval("[data-key='captions.size']", el => { el.value = 90; el.dispatchEvent(new Event("input", { bubbles: true })); }); await page.waitForTimeout(800);
  s = await api("/api/state"); check("editing a field switches preset to custom", s.settings.captions.preset === "custom" && s.settings.captions.size === 90);
  await page.screenshot({ path: "/tmp/ui_captions.png", fullPage: true });
  // ---- caption synchronisation checkbox (separate from image sync, on by default)
  await page.click("#nav a[data-page=captions]"); await page.waitForSelector("[data-key='captions.voice_sync']");
  check("'Synchronize Video Captions with Image and Voice' exists and is checked by default", await page.$eval("[data-key='captions.voice_sync']", e => e.checked) && /Synchronize Video Captions with Image and Voice/.test(await page.textContent("#main")));
  await page.uncheck("[data-key='captions.voice_sync']"); await page.waitForTimeout(800);
  s = await api("/api/state"); check("caption sync is independent of image sync (saved separately)", s.settings.captions.voice_sync === false && s.settings.sync.method === "voice");
  await page.check("[data-key='captions.voice_sync']"); await page.waitForTimeout(700);
  // ---- free caption placement
  await page.click("#nav a[data-page=captions]"); await page.waitForSelector("#ph-captions .ptxt");
  check("caption has X/Y sliders and quick positions (no fixed top/middle/bottom list)", !!(await page.$("[data-key='captions.pos_x']")) && !!(await page.$("[data-key='captions.pos_y']")) && !(await page.$("[data-key='captions.position']")) && (await page.$$("[data-quick^='captions:']")).length === 5);
  await page.click("[data-quick='captions:50:12']"); await page.waitForTimeout(900);
  s = await api("/api/state"); check("quick button moves the caption (Y=12%)", s.settings.captions.pos_y === 12);
  check("a spot under the app interface warns but is allowed", /covered|cut off|interface/.test(await page.textContent("#pw-captions")));
  await page.uncheck("[data-key='captions.uppercase']"); await page.$eval("[data-key='captions.size']", el => { el.value = 40; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.click("[data-quick='captions:50:50']"); await page.waitForTimeout(300);
  check("a safe spot shows the all-clear", /safe area/.test(await page.textContent("#pw-captions")));
  const box = await page.$eval("#ph-captions", e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await page.mouse.move(box.x + box.w * 0.5, box.y + box.h * 0.5); await page.mouse.down(); await page.mouse.move(box.x + box.w * 0.25, box.y + box.h * 0.9, { steps: 6 }); await page.mouse.up(); await page.waitForTimeout(900);
  s = await api("/api/state"); check("dragging the caption in the preview sets X/Y", Math.abs(s.settings.captions.pos_x - 25) <= 3 && Math.abs(s.settings.captions.pos_y - 90) <= 3, JSON.stringify([s.settings.captions.pos_x, s.settings.captions.pos_y]));
  await page.$eval("[data-key='captions.pos_x']", el => { el.value = 100; el.dispatchEvent(new Event("input", { bubbles: true })); }); await page.waitForTimeout(300);
  check("anchor at the far edge warns about cut-off or hidden text", /cut off|covered/.test(await page.textContent("#pw-captions")));
  await page.click("[data-quick='captions:50:78']"); await page.waitForTimeout(600);
  // ---- Start words page
  await page.click("#nav a[data-page=start]"); await page.waitForSelector("#ph-start .ptxt");
  check("Start Words page: file name, duration, rotation, X/Y, font controls", (await page.$$("[data-key='start.file'],[data-key='start.duration_ms'],[data-key='start.rotation'],[data-key='start.pos_x'],[data-key='start.pos_y'],[data-key='start.font'],[data-key='start.size']")).length === 7 && (await page.$$("[data-key^='start.']")).length >= 20);
  await page.check("[data-key='start.enabled']"); await page.fill("[data-key='start.file']", "hook.txt"); await page.$eval("[data-key='start.duration_ms']", el => { el.value = 700; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.$eval("[data-key='start.rotation']", el => { el.value = -25; el.dispatchEvent(new Event("input", { bubbles: true })); }); await page.waitForTimeout(900);
  s = await api("/api/state"); check("start words settings saved (file name, ms, rotation)", s.settings.start.file === "hook.txt" && s.settings.start.duration_ms === 700 && s.settings.start.rotation === -25);
  check("rotation is visible in the placement preview", /rotate\(-25deg\)/.test(await page.$eval("#pt-start", e => e.style.transform)));
  await page.fill("[data-key='start.file']", "start.txt"); await page.waitForTimeout(600);
  // drawer
  await page.click("#nav a[data-page=projects]"); await page.waitForSelector("#ptb tr.click");
  await page.click("#ptb a[data-open]"); await page.waitForSelector("#drawer.open .thumb img");
  check("drawer shows thumbnails", (await page.$$("#drawer .thumb")).length === 4);
  await page.waitForFunction(() => [...document.querySelectorAll("#drawer .thumb img")].every(i => i.complete && i.naturalWidth > 0), null, { timeout: 15000 }).then(() => check("thumbnails load", true)).catch(() => check("thumbnails load", false));
  await page.click("#dPlan"); await page.waitForSelector("#drawer .tl", { timeout: 30000 });
  check("timeline visual rendered", (await page.$$("#drawer .tl > div")).length >= 4);
  await page.screenshot({ path: "/tmp/ui_drawer.png" });
  await page.click("#dPrev"); await page.waitForSelector("#dprev video", { timeout: 120000 });
  check("preview video appears in drawer", true);
  await page.click("#dclose");
  // batch: start all through the UI, wait for completion
  await page.click("#nav a[data-page=batch]"); await page.waitForSelector("[data-act=start_all]");
  await page.click("[data-act=start_all]");
  await page.waitForFunction(() => /3 of 3|\b3\b.*completed/.test(document.querySelector("#bnote")?.textContent || "") || document.querySelectorAll("#btb .chip.completed").length === 3, null, { timeout: 240000, polling: 1000 }).then(() => check("batch completes from the UI", true)).catch(async () => check("batch completes from the UI", false, await page.textContent("#btb")));
  await page.screenshot({ path: "/tmp/ui_batch.png", fullPage: true });
  await page.click("#nav a[data-page=dashboard]"); await page.waitForTimeout(700);
  check("dashboard shows completed count", /3\s*Completed/.test((await page.textContent("#main")).replace(/\s+/g, " ")) || /Completed/.test(await page.textContent(".tiles")));
  await page.click("#nav a[data-page=logs]"); await page.waitForSelector("#ltb tr"); 
  check("logs page lists entries", (await page.$$("#ltb tr")).length >= 3);
  // ---- timing columns + batch summary
  await page.click("#nav a[data-page=batch]"); await page.waitForSelector("#btb tr"); await page.waitForTimeout(1500);
  const cells = await page.$$eval("#btb tr", rs => rs.map(r => r.children[5]?.textContent || ""));
  check("every finished video shows how long it took", cells.length === 3 && cells.every(c => /\d+(m \d\ds|s)/.test(c)), cells.join(" | "));
  const bt = await page.textContent("#btime"); check("batch summary shows total time, render time and average", /Total time/.test(bt) && /Average per video/.test(bt) && /Render time/.test(bt), bt);
  // ---- presets: export / import in the browser
  await page.click("#nav a[data-page=settings]"); await page.waitForSelector("#pSave");
  await page.fill("#pname", "ui-preset"); await page.click("#pSave"); await page.waitForSelector("a[data-pexp='ui-preset']"); await page.waitForTimeout(1200);
  const seen = []; page.on("response", r => { if (r.url().includes("export")) seen.push(`${r.status()} ${r.headers()["content-disposition"]}`); }); page.on("framenavigated", f => seen.push("nav " + f.url()));
  let dl; try { [dl] = await Promise.all([page.waitForEvent("download", { timeout: 10000 }), page.click("a[data-pexp='ui-preset']")]); } catch (e) { console.log("DEBUG no download event; saw:", JSON.stringify(seen), "| hit:", await page.$eval("a[data-pexp='ui-preset']", b => { const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, t = document.elementFromPoint(x, y); return JSON.stringify({ rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], hit: t && (t.tagName + "#" + t.id + "." + t.className), page: [innerWidth, innerHeight], drawerOpen: document.querySelector("#drawer").className, toasts: document.querySelectorAll(".toast").length }); }).catch(x => "gone")); throw e; }
  const fs = require("fs"), file = await dl.path(), exported = JSON.parse(fs.readFileSync(file, "utf8"));
  check("preset export downloads a JSON file", exported.format === "bulk-video-studio-presets" && !!exported.presets["ui-preset"]);
  await page.setInputFiles("#pfile", file); await page.waitForSelector("[data-pload='ui-preset (2)']", { timeout: 8000 }).then(() => check("preset import via file picker", true)).catch(() => check("preset import via file picker", false));
  // ---- Stop everything + Reset everything buttons
  let dialogs = []; page.on("dialog", d => { dialogs.push(d.message().slice(0, 30)); dialogs.length === 2 ? d.dismiss() : d.accept(); });
  await page.click("#stopAll"); await page.waitForTimeout(800); check("Stop everything asks for confirmation", dialogs.length === 1 && /Stop everything/.test(dialogs[0]));
  dialogs = []; await page.click("#resetAll"); await page.waitForTimeout(1500);
  s = await api("/api/state"); const pr = await api("/api/presets");
  check("Reset everything: asks twice (reset, then 'delete videos?')", dialogs.length === 2, dialogs.join(" / "));
  check("Reset everything: projects cleared, settings and presets kept", s.counts.total === 0 && s.settings.paths.parent === parent && pr.length >= 2);
  // reload keeps state
  await page.reload(); await page.waitForSelector("#nav a"); s = await api("/api/state"); check("state survives browser refresh (settings kept after reset)", s.settings.paths.parent === parent);
  check("no console/page errors", errors.length === 0, errors.join(" | "));
  await b.close();
  const bad = results.filter(x => !x).length; console.log(`\n${results.length - bad}/${results.length} UI checks passed`); process.exit(bad ? 1 : 0);
})().catch(e => { console.error("CRASH", e); process.exit(2); });
