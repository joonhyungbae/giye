#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
/* Home canvas measurement harness (measurement only; not part of the site build).

   What: drives google-chrome over the DevTools protocol against a locally built server and
   records, per animation frame, the Canvas 2D calls the home visualization makes, the
   script time of the frame and the frame interval, labelled by phase (intro, settled idle,
   hover, selection, stages 1–4). In --det mode the page clock is virtual (exactly 1/60 s per
   frame, seeded Math.random, held at fixed times) and the canvas is captured at fixed times,
   so two builds can be compared pixel for pixel with diff.py.
   Why: the canvas stutters on Chrome/Windows (Skia Ganesh), where per-draw-call overhead
   dominates; the call count per frame is the number to drive down without changing a pixel.

   Usage (from web/, after `bun run build`):
     node scripts/perf/harness.mjs --out /tmp/run-a [--det] [--count] [--gpu]
          [--output .output] [--site ../data/site] [--port 4710]
   --count wraps the 2D API (adds overhead: use a separate run without it for timings).
   --det   virtual clock + screenshots into <out>/shots/*.png (fonts from the network are
           blocked so text renders the same in every run). */
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const flag = (k) => args.includes(k);
const opt = (k, d) => {
  const i = args.indexOf(k);
  return i >= 0 ? args[i + 1] : d;
};
const OUT = resolve(opt("--out", "/tmp/giye-perf"));
const OUTPUT = resolve(opt("--output", join(here, "../../.output")));
const SITE = resolve(opt("--site", join(here, "../../../data/site")));
const PORT = Number(opt("--port", "4710"));
const CDP_PORT = PORT + 1;
const DET = flag("--det");
const COUNT = flag("--count");
const GPU = flag("--gpu");
const PROFILE = flag("--profile");
const W = 1600;
const H = 1000;
const DPR = Number(opt("--dpr", "1.5"));
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36";

mkdirSync(join(OUT, "shots"), { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Timeline, in ms after the first drawn frame. Shots are taken in --det mode only. */
const CX = W / 2;
const CY = (H - 96) / 2 + 58;
const TIMELINE = flag("--load-only") ? [{ t: 3000, phase: "end" }] : [
  { t: 0, phase: "intro" },
  ...[300, 800, 1500, 2500, 3500, 4500, 5500, 6300, 7000, 7600].map((t) => ({ t, shot: `intro-${t}` })),
  { t: 8000, phase: "idle" },
  ...[9000, 12000, 13000].map((t) => ({ t, shot: `idle-${t}` })),
  { t: 14000, phase: "hover", mouse: { type: "mouseMoved", x: CX + 150, y: CY + 100 } },
  { t: 14200, mouse: { type: "mouseMoved", x: CX + 152, y: CY + 101 } },
  { t: 15000, shot: "hover-15000" },
  { t: 16000, phase: "select", mouse: { type: "mousePressed", x: CX + 152, y: CY + 101 } },
  { t: 16050, mouse: { type: "mouseReleased", x: CX + 152, y: CY + 101 } },
  ...[16500, 17500, 19000].map((t) => ({ t, shot: `select-${t}` })),
  { t: 19500, mouse: { type: "mouseMoved", x: 40, y: H - 40 } },
  { t: 19600, key: "Escape" },
  { t: 19900, shot: "select-19900" },
  ...[1, 2, 3, 4].flatMap((k, i) => {
    const t0 = 20000 + i * 5000;
    return [
      { t: t0, phase: `stage${k}`, key: String(k) },
      ...[600, 1500, 3000, 4800].map((d) => ({ t: t0 + d, shot: `stage${k}-${d}` })),
    ];
  }),
  { t: 40000, phase: "stage0", key: "0" },
  ...[1000, 3000, 5500].map((d) => ({ t: 40000 + d, shot: `back-${d}` })),
  { t: 46000, phase: "end" },
];

/* ---- server ---- */
const server = spawn("node", [join(OUTPUT, "server/index.mjs")], {
  env: { ...process.env, GIYE_SITE_DIR: SITE, PORT: String(PORT), HOST: "127.0.0.1" },
  stdio: ["ignore", "pipe", "pipe"],
});
server.stderr.on("data", (d) => process.stderr.write(`[server] ${d}`));

/* ---- chrome ---- */
const profile = join(OUT, "profile");
rmSync(profile, { recursive: true, force: true });
const chromeArgs = [
  "--headless=new",
  `--remote-debugging-port=${CDP_PORT}`,
  `--user-data-dir=${profile}`,
  `--window-size=${W},${H}`,
  "--no-first-run",
  "--no-default-browser-check",
  "--hide-scrollbars",
  "--mute-audio",
  "--disable-background-timer-throttling",
  "--disable-renderer-backgrounding",
  "--disable-backgrounding-occluded-windows",
  ...(GPU ? [] : ["--disable-gpu"]),
  "about:blank",
];
const chrome = spawn("google-chrome", chromeArgs, { stdio: ["ignore", "ignore", "pipe"] });
const cleanup = () => {
  try { chrome.kill("SIGKILL"); } catch { /* gone */ }
  try { server.kill("SIGKILL"); } catch { /* gone */ }
};
process.on("exit", cleanup);
process.on("SIGINT", () => process.exit(130));

async function getJson(url, tries = 100) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url);
      if (r.ok) return await r.json();
    } catch { /* not up yet */ }
    await sleep(200);
  }
  throw new Error(`no answer from ${url}`);
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.handlers = [];
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) rej(new Error(JSON.stringify(m.error)));
        else res(m.result);
      } else if (m.method) for (const h of this.handlers) h(m);
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.pending.set(id, { res, rej }));
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
}

async function main() {
  // server up?
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/robots.txt`, { headers: { "user-agent": UA } });
      if (r.status < 500) break;
    } catch { /* starting */ }
    if (i > 150) throw new Error("server did not start");
    await sleep(200);
  }
  await getJson(`http://127.0.0.1:${CDP_PORT}/json/version`);
  const list = await getJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
  const page = list.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => {
    ws.addEventListener("open", r);
    ws.addEventListener("error", j);
  });
  const cdp = new CDP(ws);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Network.enable");
  await cdp.send("Network.setUserAgentOverride", { userAgent: UA });
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: W, height: H, deviceScaleFactor: DPR, mobile: false,
  });
  if (DET) await cdp.send("Network.setBlockedURLs", { urls: ["*fonts.googleapis.com*", "*fonts.gstatic.com*"] });

  const loaded = () =>
    new Promise((r) => {
      const h = (m) => {
        if (m.method === "Page.loadEventFired") {
          cdp.handlers = cdp.handlers.filter((x) => x !== h);
          r();
        }
      };
      cdp.handlers.push(h);
    });

  // Warm-up: this machine's headless Chrome stalls on its first request.
  const t0w = Date.now();
  let l = loaded();
  await cdp.send("Page.navigate", { url: `http://127.0.0.1:${PORT}/about` });
  await l;
  console.error(`warm-up navigation ${Date.now() - t0w} ms`);

  const cfg = { det: DET, count: COUNT };
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
    source: `window.__perfCfg=${JSON.stringify(cfg)};\n${readFileSync(join(here, "instrument.js"), "utf8")}`,
  });

  if (flag("--warm-home")) {
    // a repeat visit: the home page has been opened once in this profile
    l = loaded();
    await cdp.send("Page.navigate", { url: `http://127.0.0.1:${PORT}/` });
    await l;
    for (let i = 0; i < 600 && !(await cdp.eval("window.__firstDrawV >= 0")); i++) await sleep(50);
    await sleep(1000);
  }
  if (PROFILE) {
    await cdp.send("Profiler.enable");
    await cdp.send("Profiler.setSamplingInterval", { interval: 500 });
    await cdp.send("Profiler.start");
  }
  const tNav = Date.now();
  l = loaded();
  await cdp.send("Page.navigate", { url: `http://127.0.0.1:${PORT}/` });
  await l;
  // wait for the first drawn frame
  for (;;) {
    const v = await cdp.eval("window.__firstDrawV");
    if (v >= 0) break;
    if (Date.now() - tNav > 120000) throw new Error("no canvas frame within 120 s");
    await sleep(50);
  }
  const loadMs = Date.now() - tNav;
  console.error(`first canvas frame ${loadMs} ms after navigation`);
  if (DET) {
    await cdp.eval(`(() => { const s = document.createElement('style');
      s.textContent = '*{visibility:hidden!important;caret-color:transparent!important} canvas{visibility:visible!important}';
      document.head.appendChild(s); })()`);
  }

  const firstV = await cdp.eval("window.__firstDrawV");
  const tReal0 = Date.now();
  for (const ev of TIMELINE) {
    if (DET) {
      await cdp.eval(`window.__holdAt = ${firstV + ev.t}`);
      for (let i = 0; ; i++) {
        const ok = await cdp.eval(`window.__held && performance.now() >= ${firstV + ev.t}`);
        if (ok) break;
        if (i > 20000) throw new Error(`hold at ${ev.t} never reached`);
        await sleep(5);
      }
    } else {
      const wait = tReal0 + ev.t - Date.now();
      if (wait > 0) await sleep(wait);
    }
    if (ev.phase) await cdp.eval(`window.__phase = ${JSON.stringify(ev.phase)}`);
    if (ev.mouse) await cdp.send("Input.dispatchMouseEvent", { button: "left", clickCount: 1, ...ev.mouse, buttons: ev.mouse.type === "mousePressed" ? 1 : 0 });
    if (ev.key) {
      const key = ev.key;
      const code = /^\d$/.test(key) ? `Digit${key}` : key;
      const kc = key === "Escape" ? 27 : key.charCodeAt(0);
      await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: kc, text: key.length === 1 ? key : undefined });
      await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: kc });
    }
    if (ev.shot && DET) {
      // two real frames so the compositor has the held frame
      await cdp.eval("new Promise(r => setTimeout(r, 60))");
      const s = await cdp.send("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(OUT, "shots", `${ev.shot}.png`), Buffer.from(s.data, "base64"));
    }
  }
  if (PROFILE) {
    const { profile } = await cdp.send("Profiler.stop");
    writeFileSync(join(OUT, "profile.cpuprofile"), JSON.stringify(profile));
    const self = new Map();
    const byId = new Map(profile.nodes.map((n) => [n.id, n]));
    const dts = profile.timeDeltas;
    profile.samples.forEach((id, i) => {
      const n = byId.get(id);
      const k = `${n.callFrame.functionName || "(anon)"} ${n.callFrame.url.split("/").pop()}:${n.callFrame.lineNumber}`;
      self.set(k, (self.get(k) || 0) + (dts[i] || 0) / 1000);
    });
    console.log("profile self time (ms):");
    for (const [k, v] of [...self].sort((a, b) => b[1] - a[1]).slice(0, 25)) console.log(`  ${v.toFixed(0).padStart(7)}  ${k}`);
  }
  const frames = await cdp.eval("window.__frames");
  const longTasks = await cdp.eval("window.__longTasks");
  writeFileSync(join(OUT, "frames.json"), JSON.stringify({ loadMs, firstV, frames, longTasks }));
  summarise(frames, longTasks, firstV, loadMs);
}

function pct(a, p) {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.floor(p * s.length))];
}

function summarise(frames, longTasks, firstV, loadMs) {
  const phases = [...new Set(frames.map((f) => f.phase))];
  const rows = [];
  for (const ph of phases) {
    const fs = frames.filter((f) => f.phase === ph);
    const painted = fs.filter((f) => f.total > 0);
    const tot = painted.map((f) => f.total);
    const dts = fs.map((f) => f.dt).filter((d) => d > 0);
    const work = fs.map((f) => f.work);
    const lts = longTasks.filter((x) => x.phase === ph);
    const callAvg = {};
    if (COUNT) {
      for (const f of painted) for (const [k, v] of Object.entries(f.calls)) callAvg[k] = (callAvg[k] || 0) + v;
      for (const k of Object.keys(callAvg)) callAvg[k] = Math.round(callAvg[k] / Math.max(1, painted.length));
    }
    rows.push({
      phase: ph,
      frames: fs.length,
      painted: painted.length,
      calls_per_frame: Math.round(fs.reduce((a, f) => a + f.total, 0) / Math.max(1, fs.length)),
      calls_mean: Math.round(tot.reduce((a, b) => a + b, 0) / Math.max(1, tot.length)),
      calls_p95: pct(tot, 0.95),
      calls_max: Math.max(0, ...tot),
      work_mean: +(work.reduce((a, b) => a + b, 0) / Math.max(1, work.length)).toFixed(2),
      work_p95: +pct(work, 0.95).toFixed(2),
      dt_p50: +pct(dts, 0.5).toFixed(1),
      dt_p95: +pct(dts, 0.95).toFixed(1),
      dt_max: +Math.max(0, ...dts).toFixed(1),
      over20: dts.filter((d) => d > 20).length,
      longtasks: lts.length,
      longtask_max: Math.round(Math.max(0, ...lts.map((x) => x.dur))),
      top: COUNT
        ? Object.entries(callAvg).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([k, v]) => `${k}=${v}`).join(" ")
        : "",
    });
  }
  writeFileSync(join(OUT, "summary.json"), JSON.stringify({ loadMs, rows }, null, 1));
  console.log(`load→first frame ${loadMs} ms`);
  console.table(rows.map(({ top, ...r }) => r));
  if (COUNT) for (const r of rows) console.log(`${r.phase}: ${r.top}`);
}

main()
  .then(() => process.exit(0))
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
