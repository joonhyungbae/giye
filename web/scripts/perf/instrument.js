// SPDX-License-Identifier: AGPL-3.0-only
/* Injected before any page script by scripts/perf/harness.mjs (measurement only, never shipped).

   window.__perfCfg (set by the harness just before this file) chooses what is wrapped:
     count: wrap the Canvas 2D API and the WebGL2 draw/upload calls and count them per animation
            frame (WebGL calls are counted as "gl:<name>")
     det:   deterministic clock. performance.now and the rAF timestamp advance exactly
            1000/60 ms per animation frame, Math.random is seeded, and the clock can be held
            at a target time (window.__holdAt) so the harness can screenshot that exact frame.

   Per frame it records { v, dt, work, calls, phase } into window.__frames. */
(() => {
  const cfg = window.__perfCfg || {};
  const realNow = performance.now.bind(performance);
  const realRaf = window.requestAnimationFrame.bind(window);
  const STEP = 1000 / 60;

  window.__phase = "load";
  window.__frames = [];
  window.__longTasks = [];
  window.__firstDrawV = -1;

  /* ---- deterministic clock ---- */
  let V = 0;
  let fontsReady = !cfg.det;
  window.__holdAt = Infinity;
  window.__held = false;
  if (cfg.det) {
    // seeded Math.random (mulberry32)
    let seed = 0x9e3779b9;
    Math.random = () => {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    performance.now = () => V;
  }

  /* ---- Canvas 2D call counter ---- */
  let calls = Object.create(null);
  let callTotal = 0;
  let drewMain = false;
  const bump = (name) => {
    calls[name] = (calls[name] || 0) + 1;
    callTotal++;
  };
  if (cfg.count) {
    const METHODS = [
      "beginPath", "stroke", "fill", "fillText", "strokeText", "drawImage", "arc", "ellipse",
      "lineTo", "moveTo", "quadraticCurveTo", "save", "restore", "fillRect", "strokeRect",
      "clearRect", "setTransform", "translate", "rotate", "measureText", "setLineDash",
      "putImageData", "getImageData", "createRadialGradient", "createPattern", "closePath",
    ];
    const PROPS = [
      "font", "fillStyle", "strokeStyle", "globalAlpha", "lineWidth", "textAlign",
      "textBaseline", "letterSpacing", "lineCap", "lineJoin",
    ];
    const protos = [window.CanvasRenderingContext2D, window.OffscreenCanvasRenderingContext2D]
      .filter(Boolean)
      .map((c) => c.prototype);
    for (const P of protos) {
      for (const m of METHODS) {
        const orig = P[m];
        if (typeof orig !== "function") continue;
        P[m] = function (...args) {
          bump(m);
          if (!drewMain && this.canvas && this.canvas.isConnected) drewMain = true;
          return orig.apply(this, args);
        };
      }
      for (const p of PROPS) {
        const d = Object.getOwnPropertyDescriptor(P, p);
        if (!d || !d.set) continue;
        Object.defineProperty(P, p, {
          configurable: true,
          enumerable: d.enumerable,
          get: d.get,
          set(v) {
            bump("set:" + p);
            d.set.call(this, v);
          },
        });
      }
    }
    // WebGL2: draw calls and uploads (the home canvas's GL layers, glRenderer.ts)
    const GLP = window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype;
    if (GLP) {
      for (const m of [
        "drawArrays", "drawArraysInstanced", "drawElements", "drawElementsInstanced",
        "texSubImage2D", "texImage2D", "bufferData", "bufferSubData", "clear",
      ]) {
        const orig = GLP[m];
        if (typeof orig !== "function") continue;
        GLP[m] = function (...args) {
          bump("gl:" + m);
          return orig.apply(this, args);
        };
      }
    }
    // Path2D building is CPU work but not a context call; counted separately.
    const PP = window.Path2D && window.Path2D.prototype;
    if (PP) {
      for (const m of ["arc", "lineTo", "moveTo", "quadraticCurveTo", "ellipse", "rect"]) {
        const orig = PP[m];
        PP[m] = function (...args) {
          bump("path:" + m);
          return orig.apply(this, args);
        };
      }
    }
  } else {
    // still detect the first frame that draws on the page canvas
    const P = window.CanvasRenderingContext2D.prototype;
    const orig = P.fillRect;
    P.fillRect = function (...a) {
      if (!drewMain && this.canvas && this.canvas.isConnected) drewMain = true;
      return orig.apply(this, a);
    };
  }

  /* ---- rAF: frame boundaries, frame intervals, held clock ---- */
  let queue = [];
  let lastReal = -1;
  let ticking = false;
  const tick = (realT) => {
    ticking = false;
    if (cfg.det) {
      if (!fontsReady || V >= window.__holdAt) {
        window.__held = fontsReady && V >= window.__holdAt;
        schedule();
        return;
      }
      window.__held = false;
      V += STEP;
    }
    const cbs = queue;
    queue = [];
    const t0 = realNow();
    calls = Object.create(null);
    callTotal = 0;
    drewMain = false;
    const stamp = cfg.det ? V : realT;
    for (const cb of cbs) {
      try {
        cb(stamp);
      } catch (e) {
        console.error(e);
      }
    }
    const work = realNow() - t0;
    // main-thread ms of the WebGL flushes and their coverage passes in this frame (glRenderer.ts)
    const glMs = window.__glFlushMs;
    const passes = window.__glPasses;
    window.__glFlushMs = undefined;
    window.__glPasses = undefined;
    if (drewMain && window.__firstDrawV < 0) {
      window.__firstDrawV = cfg.det ? V : realT;
      // det: stop on the first drawn frame; the harness schedules every hold from here
      if (cfg.det) window.__holdAt = V;
    }
    if (cbs.length) {
      window.__frames.push({
        v: cfg.det ? V : realT,
        dt: lastReal < 0 ? 0 : realT - lastReal,
        work,
        total: callTotal,
        glMs,
        passes,
        calls: cfg.count ? calls : undefined,
        phase: window.__phase,
      });
      if (window.__frames.length > 20000) window.__frames.splice(0, 10000);
    }
    lastReal = realT;
    if (queue.length || cfg.det) schedule();
  };
  const schedule = () => {
    if (ticking) return;
    ticking = true;
    realRaf(tick);
  };
  window.requestAnimationFrame = (cb) => {
    queue.push(cb);
    schedule();
    return queue.length;
  };
  window.cancelAnimationFrame = () => {
    /* the study cancels only on unmount; ignore */
  };

  if (cfg.det) {
    const want = [
      '400 10px "Space Mono"',
      '700 10px "Space Mono"',
      '400 10px "IBM Plex Sans KR"',
      '500 10px "IBM Plex Sans KR"',
      '600 10px "IBM Plex Sans KR"',
    ];
    const go = () =>
      Promise.all(want.map((f) => document.fonts.load(f).catch(() => null))).then(() => {
        fontsReady = true;
      });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go);
    else go();
  }

  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries())
        window.__longTasks.push({ start: e.startTime, dur: e.duration, phase: window.__phase });
    }).observe({ type: "longtask", buffered: true });
  } catch {
    /* not supported */
  }
})();
