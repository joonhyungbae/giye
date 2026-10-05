// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Network mode — the archive read as who shared an event with whom.
 *
 * A force-directed drawing on one canvas: an artist is a dot sized by tie strength, a line is at
 * least one shared event, grey territories are Louvain communities. Filters (evidence, period,
 * minimum shared events) rebuild the graph; the simulation warms up again from where the dots are.
 */
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { SITE_NAV } from "@/components/SiteChrome";
import { mulberry32 } from "@/components/flight/flightState";
import { readTheme, withAlpha } from "@/components/study/theme";
import { useLang } from "@/lib/i18n";
import type { NetworkData } from "@/lib/giye.network";
import {
  buildGraph,
  eventMembers,
  graphBetweenness,
  yearSpan,
  type Graph,
  type NetFilter,
} from "./graph";

/** brokerage before it has been computed */
const EMPTY_BROKER = new Float64Array(0);

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];
const roman = (k: number) => ROMAN[k] ?? String(k + 1);
const SIDE_W = 360;

type Focus =
  | { kind: "artist"; idx: number }
  | { kind: "community"; idx: number }
  | { kind: "event"; idx: number }
  | null;

export function NetworkStudy({ data, modeSwitch }: { data: NetworkData; modeSwitch: ReactNode }) {
  const { lang, setLang, t } = useLang();
  const n = data.artists.length;
  const [span0, span1] = useMemo(() => yearSpan(data), [data]);
  const [filter, setFilter] = useState<NetFilter>({
    roster: true,
    cv: true,
    y0: span0,
    y1: span1,
    minShared: 1,
  });
  const graph = useMemo<Graph>(() => buildGraph(data, filter), [data, filter]);
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<Focus>(null);
  const [hoverCom, setHoverCom] = useState<number | null>(null);
  const [panel, setPanel] = useState(false);
  const [menu, setMenu] = useState(false);

  const nameOf = (i: number) => {
    const a = data.artists[i]!;
    return lang === "ko" ? a.name_ko : (a.name_en ?? a.name_ko);
  };
  const eventLabel = (ei: number) => {
    const e = data.events[ei]!;
    return lang === "ko" ? e.label_ko : e.label_en;
  };

  /* ---- ranks ---- */
  // Brokerage (Brandes betweenness) costs about a second on this graph, so it is computed after
  // the picture is on screen; until it lands the panel shows nothing and node labels omit the rank.
  const [broker, setBroker] = useState<Float64Array>(EMPTY_BROKER);
  useEffect(() => {
    setBroker(EMPTY_BROKER);
    let cancelled = false;
    const run = () => {
      if (cancelled) return;
      setBroker(graphBetweenness(graph));
    };
    const w = window as unknown as {
      requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    const id = w.requestIdleCallback
      ? w.requestIdleCallback(run, { timeout: 1200 })
      : window.setTimeout(run, 200);
    return () => {
      cancelled = true;
      if (w.cancelIdleCallback) w.cancelIdleCallback(id);
      else window.clearTimeout(id);
    };
  }, [graph]);
  const brokers = useMemo(
    () =>
      [...broker]
        .map((b, i) => [b, i] as const)
        .filter(([b]) => b > 0)
        .sort((p, q) => q[0] - p[0])
        .map(([, i]) => i),
    [broker],
  );
  const brokerRank = useMemo(() => {
    const r = new Int32Array(n).fill(-1);
    brokers.forEach((i, k) => (r[i] = k));
    return r;
  }, [brokers, n]);

  /* ---- canvas + simulation state (never React state) ---- */
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [st] = useState(() => {
    // seeded start: a loose disc, so a reload draws the same picture
    const rnd = mulberry32(20260916);
    const x = new Float32Array(n);
    const y = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const a = rnd() * Math.PI * 2;
      const r = 40 + Math.sqrt(rnd()) * 260;
      x[i] = Math.cos(a) * r;
      y[i] = Math.sin(a) * r;
    }
    return {
      w: 0,
      h: 0,
      dpr: 1,
      x,
      y,
      vx: new Float32Array(n),
      vy: new Float32Array(n),
      alpha: 1,
      zoom: 1,
      /** screen position of the world origin */
      ox: 0,
      oy: 0,
      /** world bounding box of every dot: minX, minY, maxX, maxY */
      box: [-300, -300, 300, 300] as [number, number, number, number],
      /** where the untied artists are parked, for its caption */
      band: null as { x: number; y: number; count: number } | null,
      side: 0,
      userView: false,
      hover: -1,
      dirty: true,
      graph,
      selected: null as number | null,
      focusSet: null as Set<number> | null,
      focusEdges: null as Set<number> | null,
      labelSet: new Set<number>(),
    };
  });

  // what the drawing emphasises: an artist and their ties, a community, or one event's participants
  const focusSet = useMemo(() => {
    if (focus?.kind === "artist") {
      const s = new Set<number>([focus.idx]);
      for (const k of graph.adj[focus.idx]!) {
        const e = graph.edges[k]!;
        s.add(e.a);
        s.add(e.b);
      }
      return s;
    }
    if (focus?.kind === "community") return new Set(graph.communities[focus.idx]?.members ?? []);
    if (focus?.kind === "event") return new Set(eventMembers(data.events[focus.idx]!, filter));
    if (hoverCom != null) return new Set(graph.communities[hoverCom]?.members ?? []);
    return null;
  }, [focus, hoverCom, graph, data.events, filter]);

  useEffect(() => {
    st.graph = graph;
    st.alpha = Math.max(st.alpha, 0.6);
    st.dirty = true;
  }, [graph, st]);

  useEffect(() => {
    st.selected = selected;
    st.focusSet = focusSet;
    let edges: Set<number> | null = null;
    if (focus?.kind === "artist") edges = new Set(graph.adj[focus.idx]);
    else if (focusSet) {
      edges = new Set();
      graph.edges.forEach((e, k) => {
        if (focusSet.has(e.a) && focusSet.has(e.b)) edges!.add(k);
      });
    }
    st.focusEdges = edges;
    // labels: the strongest ties overall, the brokers, and whoever is in focus
    const labels = new Set<number>();
    [...graph.strength]
      .map((s, i) => [s, i] as const)
      .sort((p, q) => q[0] - p[0])
      .slice(0, 14)
      .forEach(([, i]) => labels.add(i));
    brokers.slice(0, 6).forEach((i) => labels.add(i));
    if (focusSet && focusSet.size <= 60) focusSet.forEach((i) => labels.add(i));
    st.labelSet = labels;
    st.dirty = true;
  }, [selected, focus, focusSet, graph, brokers, st]);

  const select = (i: number | null) => {
    setSelected(i);
    setFocus(i == null ? null : { kind: "artist", idx: i });
    if (i != null) setPanel(false);
  };

  /* ---- frame loop ---- */
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let theme = readTheme();
    let themeAt = 0;

    const resize = () => {
      const r = wrap.getBoundingClientRect();
      st.w = r.width;
      st.h = r.height;
      st.dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.floor(st.w * st.dpr);
      canvas.height = Math.floor(st.h * st.dpr);
      canvas.style.width = `${st.w}px`;
      canvas.style.height = `${st.h}px`;
      st.dirty = true;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    const radius = (g: Graph, i: number) => 2 + Math.sqrt(g.strength[i]!) * 1.9;

    const tick = () => {
      const g = st.graph;
      const { x, y, vx, vy } = st;
      const a = st.alpha;
      const on: number[] = [];
      for (let i = 0; i < n; i++) if (g.degree[i]! > 0) on.push(i);
      // repulsion between every pair of tied artists
      for (let p = 0; p < on.length; p++) {
        const i = on[p]!;
        for (let q = p + 1; q < on.length; q++) {
          const j = on[q]!;
          let dx = x[j]! - x[i]!;
          let dy = y[j]! - y[i]!;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) {
            dx = 0.1 * (((i * 7 + j) % 5) - 2);
            dy = 0.1;
            d2 = 0.02;
          }
          if (d2 > 250000) continue;
          const d = Math.sqrt(d2);
          let f = 520 / d2;
          const min = radius(g, i) + radius(g, j) + 3;
          if (d < min) f += (min - d) * 0.25;
          const fx = (dx / d) * f * a;
          const fy = (dy / d) * f * a;
          vx[i]! -= fx;
          vy[i]! -= fy;
          vx[j]! += fx;
          vy[j]! += fy;
        }
      }
      // ties pull; a stronger tie is shorter and stiffer
      for (const e of g.edges) {
        const dx = x[e.b]! - x[e.a]!;
        const dy = y[e.b]! - y[e.a]!;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const wn = Math.min(1, e.w);
        const L = 22 + 30 * (1 - wn);
        const k = 0.012 + 0.08 * Math.sqrt(wn);
        const f = ((d - L) / d) * k * a;
        vx[e.a]! += dx * f;
        vy[e.a]! += dy * f;
        vx[e.b]! -= dx * f;
        vy[e.b]! -= dy * f;
      }
      // communities gather a little; everything drifts to the centre
      const cx = new Float64Array(g.communities.length);
      const cy = new Float64Array(g.communities.length);
      g.communities.forEach((c, k) => {
        for (const i of c.members) {
          cx[k]! += x[i]!;
          cy[k]! += y[i]!;
        }
        cx[k]! /= c.members.length;
        cy[k]! /= c.members.length;
      });
      const box: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
      for (const i of on) {
        const c = g.community[i]!;
        if (c >= 0) {
          vx[i]! += (cx[c]! - x[i]!) * 0.004 * a;
          vy[i]! += (cy[c]! - y[i]!) * 0.004 * a;
        }
        vx[i]! -= x[i]! * 0.006 * a;
        vy[i]! -= y[i]! * 0.006 * a;
        vx[i]! *= 0.62;
        vy[i]! *= 0.62;
        x[i]! += vx[i]!;
        y[i]! += vy[i]!;
        box[0] = Math.min(box[0], x[i]!);
        box[1] = Math.min(box[1], y[i]!);
        box[2] = Math.max(box[2], x[i]!);
        box[3] = Math.max(box[3], y[i]!);
      }
      if (!on.length) box.splice(0, 4, -100, -100, 100, 100);
      // artists without a shared event under this filter wait in a band under the drawing
      const off: number[] = [];
      for (let i = 0; i < n; i++) if (g.degree[i] === 0) off.push(i);
      const GAP = 9;
      const cols = Math.max(12, Math.floor((box[2] - box[0]) / GAP));
      const bx = (box[0] + box[2]) / 2 - ((Math.min(cols, off.length) - 1) * GAP) / 2;
      const by = box[3] + 56;
      off.forEach((i, k) => {
        x[i]! += (bx + (k % cols) * GAP - x[i]!) * 0.15;
        y[i]! += (by + Math.floor(k / cols) * GAP - y[i]!) * 0.15;
        vx[i] = 0;
        vy[i] = 0;
      });
      st.band = off.length ? { x: (box[0] + box[2]) / 2, y: by, count: off.length } : null;
      if (off.length) box[3] = by + Math.ceil(off.length / cols) * GAP;
      st.box = box;
      st.alpha *= 0.985;
    };

    let raf = 0;
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const moving = st.alpha > 0.004;
      if (moving) tick();
      // the view frames the whole drawing, between the panels, until the viewer zooms or pans
      const wide = st.w >= 768;
      const sideTarget = st.selected != null && wide ? SIDE_W : 0;
      let settling = Math.abs(st.side - sideTarget) > 0.5;
      st.side += (sideTarget - st.side) * 0.18;
      if (!st.userView) {
        const left = wide ? 290 : 16;
        const top = wide ? 80 : 110;
        const availW = Math.max(80, st.w - left - st.side - 16);
        const availH = Math.max(80, st.h - top - 44);
        const [x0, y0, x1, y1] = st.box;
        const z = clamp(Math.min(availW / (x1 - x0 + 60), availH / (y1 - y0 + 60)), 0.15, 3);
        const tox = left + availW / 2 - ((x0 + x1) / 2) * z;
        const toy = top + availH / 2 - ((y0 + y1) / 2) * z;
        if (Math.abs(z - st.zoom) > 0.001 || Math.abs(tox - st.ox) + Math.abs(toy - st.oy) > 0.5)
          settling = true;
        st.zoom += (z - st.zoom) * 0.12;
        st.ox += (tox - st.ox) * 0.12;
        st.oy += (toy - st.oy) * 0.12;
      }
      if (!moving && !st.dirty && !settling) return;
      st.dirty = false;
      if (now - themeAt > 600) {
        theme = readTheme();
        themeAt = now;
      }

      const g = st.graph;
      const { x, y } = st;
      const Z = st.zoom;
      ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
      ctx.fillStyle = theme.paper;
      ctx.fillRect(0, 0, st.w, st.h);
      const { ox, oy } = st;
      ctx.setTransform(st.dpr * Z, 0, 0, st.dpr * Z, st.dpr * ox, st.dpr * oy);

      const fs = st.focusSet;
      const hv = st.hover;

      // community territories: one path per community, so overlaps inside it do not darken
      g.communities.forEach((c) => {
        if (c.members.length < 3) return;
        const lit = fs != null && c.members.every((i) => fs.has(i));
        ctx.beginPath();
        for (const i of c.members) {
          ctx.moveTo(x[i]! + 20, y[i]!);
          ctx.arc(x[i]!, y[i]!, 20, 0, Math.PI * 2);
        }
        ctx.fillStyle = withAlpha(theme.ink, lit ? 0.075 : fs ? 0.02 : 0.045);
        ctx.fill();
      });

      // ties, bucketed by weight so thousands of lines stay a handful of strokes
      const lw = 1 / Z;
      const buckets: number[][] = [[], [], [], []];
      g.edges.forEach((e, k) => {
        const b = e.w >= 1 ? 3 : e.w >= 0.34 ? 2 : e.w >= 0.1 ? 1 : 0;
        buckets[b]!.push(k);
      });
      const baseA = [0.05, 0.11, 0.22, 0.4];
      buckets.forEach((list, b) => {
        ctx.beginPath();
        for (const k of list) {
          const e = g.edges[k]!;
          ctx.moveTo(x[e.a]!, y[e.a]!);
          ctx.lineTo(x[e.b]!, y[e.b]!);
        }
        ctx.strokeStyle = withAlpha(theme.ink, fs ? baseA[b]! * 0.3 : baseA[b]!);
        ctx.lineWidth = lw * (0.6 + b * 0.35);
        ctx.stroke();
      });
      if (st.focusEdges) {
        for (const k of st.focusEdges) {
          const e = g.edges[k]!;
          ctx.beginPath();
          ctx.moveTo(x[e.a]!, y[e.a]!);
          ctx.lineTo(x[e.b]!, y[e.b]!);
          ctx.strokeStyle = withAlpha(theme.ink, 0.25 + 0.5 * Math.min(1, e.w));
          ctx.lineWidth = lw * (0.8 + 2.2 * Math.min(1, e.w));
          ctx.stroke();
        }
      }

      // artists
      for (let i = 0; i < n; i++) {
        const r = radius(g, i);
        const dim = fs != null && !fs.has(i);
        if (g.degree[i] === 0) {
          ctx.beginPath();
          ctx.arc(x[i]!, y[i]!, 2.2, 0, Math.PI * 2);
          ctx.strokeStyle = withAlpha(theme.ink, dim ? 0.12 : 0.4);
          ctx.lineWidth = lw;
          ctx.stroke();
          continue;
        }
        ctx.beginPath();
        ctx.arc(x[i]!, y[i]!, r, 0, Math.PI * 2);
        ctx.fillStyle = dim ? withAlpha(theme.ink, 0.18) : theme.ink;
        ctx.fill();
        if (i === st.selected || i === hv) {
          ctx.beginPath();
          ctx.arc(x[i]!, y[i]!, r + 4 / Z, 0, Math.PI * 2);
          ctx.strokeStyle = i === st.selected ? theme.accent : theme.ink;
          ctx.lineWidth = 1.4 / Z;
          ctx.stroke();
        }
      }

      // labels in screen pixels; a label that would overlap one already placed is left out
      ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
      ctx.textBaseline = "middle";
      const placed: Array<[number, number, number, number]> = [];
      const free = (x0: number, y0: number, x1: number, y1: number) => {
        for (const [a0, b0, a1, b1] of placed)
          if (x0 < a1 && x1 > a0 && y0 < b1 && y1 > b0) return false;
        placed.push([x0, y0, x1, y1]);
        return true;
      };
      const drawLabel = (i: number, strong: boolean) => {
        const sx = ox + x[i]! * Z;
        const sy = oy + y[i]! * Z;
        if (sx < -40 || sy < -20 || sx > st.w + 40 || sy > st.h + 20) return;
        const label = nameOf(i);
        ctx.font = `${strong ? 600 : 400} ${strong ? 12 : 10.5}px ui-sans-serif, system-ui, sans-serif`;
        const tx = sx + radius(g, i) * Z + 5;
        const tw = ctx.measureText(label).width;
        if (!free(tx - 2, sy - 7, tx + tw + 2, sy + 7) && !strong) return;
        ctx.lineWidth = 3;
        ctx.strokeStyle = withAlpha(theme.paper, 0.9);
        ctx.strokeText(label, tx, sy);
        ctx.fillStyle = withAlpha(theme.ink, strong ? 1 : fs && !fs.has(i) ? 0.35 : 0.8);
        ctx.fillText(label, tx, sy);
      };
      if (st.selected != null) drawLabel(st.selected, true);
      if (hv >= 0 && hv !== st.selected) drawLabel(hv, true);
      for (const i of st.labelSet) if (i !== hv && i !== st.selected) drawLabel(i, false);

      if (st.band) {
        ctx.font = "400 10px ui-monospace, SFMono-Regular, Menlo, monospace";
        ctx.textAlign = "center";
        ctx.fillStyle = withAlpha(theme.ink, 0.5);
        const cap = t(
          `이 조건에서 함께한 행사가 기록되지 않은 작가 ${st.band.count}`,
          `${st.band.count} artists with no shared event recorded under these conditions`,
        );
        const bx = ox + st.band.x * Z;
        const by = oy + st.band.y * Z - 14;
        free(bx - 160, by - 7, bx + 160, by + 7);
        ctx.fillText(cap, bx, by);
        ctx.textAlign = "left";
      }

      // community numerals just outside each community, on the side facing away from the centre
      ctx.font = "500 11px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      g.communities.forEach((c, k) => {
        if (c.members.length < 3) return;
        let cx = 0;
        let cy = 0;
        for (const i of c.members) {
          cx += x[i]!;
          cy += y[i]!;
        }
        cx /= c.members.length;
        cy /= c.members.length;
        const len = Math.hypot(cx, cy);
        const ux = len > 1 ? cx / len : 0;
        const uy = len > 1 ? cy / len : -1;
        let reach = 0;
        for (const i of c.members) reach = Math.max(reach, x[i]! * ux + y[i]! * uy);
        const sx = ox + (ux * (reach + 30) + (cx - ux * (cx * ux + cy * uy))) * Z;
        const sy = oy + (uy * (reach + 30) + (cy - uy * (cx * ux + cy * uy))) * Z;
        if (!free(sx - 12, sy - 8, sx + 12, sy + 8)) return;
        ctx.fillStyle = withAlpha(theme.ink, fs ? 0.3 : 0.6);
        ctx.fillText(roman(k), sx, sy);
      });
      ctx.textAlign = "left";
    };
    raf = requestAnimationFrame(draw);

    /* ---- pointer ---- */
    const toWorld = (clientX: number, clientY: number) => {
      const r = canvas.getBoundingClientRect();
      return {
        wx: (clientX - r.left - st.ox) / st.zoom,
        wy: (clientY - r.top - st.oy) / st.zoom,
      };
    };
    const pick = (clientX: number, clientY: number) => {
      const { wx, wy } = toWorld(clientX, clientY);
      const g = st.graph;
      let best = -1;
      let bestD = Infinity;
      for (let i = 0; i < n; i++) {
        const d = Math.hypot(st.x[i]! - wx, st.y[i]! - wy);
        const reach = (g.degree[i] ? radius(g, i) : 2.2) + 6 / st.zoom;
        if (d < reach && d < bestD) {
          best = i;
          bestD = d;
        }
      }
      return best;
    };
    const pointers = new Map<number, { x: number; y: number }>();
    let drag: {
      node: number;
      sx: number;
      sy: number;
      moved: boolean;
      pinch: number;
    } | null = null;
    const onDown = (e: PointerEvent) => {
      canvas.setPointerCapture?.(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 2) {
        const [p, q] = [...pointers.values()];
        drag = { node: -1, sx: 0, sy: 0, moved: true, pinch: Math.hypot(p!.x - q!.x, p!.y - q!.y) };
        return;
      }
      drag = {
        node: pick(e.clientX, e.clientY),
        sx: e.clientX,
        sy: e.clientY,
        moved: false,
        pinch: 0,
      };
    };
    const onMove = (e: PointerEvent) => {
      const prev = pointers.get(e.pointerId);
      if (prev) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (!drag) {
        const h = pick(e.clientX, e.clientY);
        if (h !== st.hover) {
          st.hover = h;
          st.dirty = true;
          canvas.style.cursor = h >= 0 ? "pointer" : "grab";
        }
        return;
      }
      if (drag.pinch && pointers.size === 2) {
        const [p, q] = [...pointers.values()];
        const d = Math.hypot(p!.x - q!.x, p!.y - q!.y);
        const before = st.zoom;
        st.zoom = clamp(st.zoom * (d / drag.pinch), 0.15, 6);
        const r = canvas.getBoundingClientRect();
        const mx = (p!.x + q!.x) / 2 - r.left;
        const my = (p!.y + q!.y) / 2 - r.top;
        st.ox = mx - (mx - st.ox) * (st.zoom / before);
        st.oy = my - (my - st.oy) * (st.zoom / before);
        drag.pinch = d;
        st.userView = true;
        st.dirty = true;
        return;
      }
      if (!prev) return;
      if (!drag.moved && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 4) return;
      drag.moved = true;
      if (drag.node >= 0 && st.graph.degree[drag.node]! > 0) {
        const { wx, wy } = toWorld(e.clientX, e.clientY);
        st.x[drag.node] = wx;
        st.y[drag.node] = wy;
        st.vx[drag.node] = 0;
        st.vy[drag.node] = 0;
        st.alpha = Math.max(st.alpha, 0.25);
      } else {
        st.ox += e.clientX - prev.x;
        st.oy += e.clientY - prev.y;
        st.userView = true;
        canvas.style.cursor = "grabbing";
      }
      st.dirty = true;
    };
    const onUp = (e: PointerEvent) => {
      pointers.delete(e.pointerId);
      canvas.releasePointerCapture?.(e.pointerId);
      if (drag && !drag.moved && !drag.pinch) select(drag.node >= 0 ? drag.node : null);
      if (pointers.size === 0) drag = null;
      canvas.style.cursor = "grab";
    };
    const onLeave = () => {
      if (st.hover !== -1) {
        st.hover = -1;
        st.dirty = true;
      }
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      const before = st.zoom;
      st.zoom = clamp(st.zoom * Math.exp(-e.deltaY * 0.0012), 0.15, 6);
      const k = st.zoom / before;
      const cx = e.clientX - r.left - st.ox;
      const cy = e.clientY - r.top - st.oy;
      st.ox -= cx * (k - 1);
      st.oy -= cy * (k - 1);
      st.userView = true;
      st.dirty = true;
    };
    const onDbl = () => {
      st.userView = false;
      st.dirty = true;
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "Escape") select(null);
      else if (e.key === "/") document.getElementById("net-q")?.focus();
      else if (e.key === "0") onDbl();
      else return;
      e.preventDefault();
    };
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", onDbl);
    window.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("dblclick", onDbl);
      window.removeEventListener("keydown", onKey);
    };
    // nameOf follows the language; the loop reads everything else from refs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n, st, lang]);

  const years = useMemo(() => {
    const out: number[] = [];
    for (let y = span0; y <= span1; y++) out.push(y);
    return out;
  }, [span0, span1]);

  const setF = (patch: Partial<NetFilter>) => setFilter((f) => ({ ...f, ...patch }));
  const box =
    "border border-input px-1.5 py-0.5 transition hover:border-primary hover:text-primary";
  const pressed = "aria-pressed:bg-foreground aria-pressed:text-background";

  const controls = (
    <div className="space-y-4">
      <section>
        <h2 className="mb-1.5 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("같은 행사로 보는 근거", "What counts as the same event")}
        </h2>
        <div className="flex flex-wrap gap-1">
          {(
            [
              ["roster", t("표집틀 회차 명단", "Frame edition rosters")],
              ["cv", t("CV의 같은 활동", "Matching CV lines")],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              aria-pressed={filter[k]}
              onClick={() => {
                const next = { ...filter, [k]: !filter[k] };
                if (!next.roster && !next.cv) return;
                setFilter(next);
              }}
              className={`${box} ${pressed}`}
            >
              {label}
            </button>
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-1.5 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("선을 긋는 최소 공동 행사", "Minimum shared events per tie")}
        </h2>
        <div className="flex gap-1">
          {[1, 2, 3].map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={filter.minShared === m}
              onClick={() => setF({ minShared: m })}
              className={`${box} ${pressed} w-8 tabular-nums`}
            >
              {m}+
            </button>
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-1.5 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("기간", "Period")}
        </h2>
        <div className="flex items-center gap-1.5 tabular-nums">
          <label className="sr-only" htmlFor="net-y0">
            {t("시작 연도", "From")}
          </label>
          <select
            id="net-y0"
            value={filter.y0}
            onChange={(e) => {
              const v = Number(e.target.value);
              setF({ y0: v, y1: Math.max(v, filter.y1) });
            }}
            className="border border-input bg-background px-1 py-0.5"
          >
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
          <span>–</span>
          <label className="sr-only" htmlFor="net-y1">
            {t("끝 연도", "To")}
          </label>
          <select
            id="net-y1"
            value={filter.y1}
            onChange={(e) => {
              const v = Number(e.target.value);
              setF({ y1: v, y0: Math.min(v, filter.y0) });
            }}
            className="border border-input bg-background px-1 py-0.5"
          >
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
          {filter.y0 !== span0 || filter.y1 !== span1 ? (
            <button
              type="button"
              onClick={() => setF({ y0: span0, y1: span1 })}
              className="text-muted-foreground underline underline-offset-2 hover:text-primary"
            >
              {t("전체", "all")}
            </button>
          ) : null}
        </div>
      </section>

      <section>
        <h2 className="mb-1 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("요약", "Summary")}
        </h2>
        <table className="w-full border-collapse tabular-nums">
          <tbody>
            {(
              [
                [t("연결된 작가", "Tied artists"), `${graph.connected} / ${n}`],
                [t("공동 행사", "Shared events"), graph.events.length],
                [t("연결선", "Ties"), graph.edges.length],
                [t("밀도", "Density"), graph.density.toFixed(3)],
                [t("구성요소", "Components"), graph.components],
                [
                  t("커뮤니티", "Communities"),
                  `${graph.communities.filter((c) => c.members.length >= 3).length} · Q ${graph.modularity.toFixed(2)}`,
                ],
              ] as const
            ).map(([k, v]) => (
              <tr key={k}>
                <td className="py-px pr-2 text-muted-foreground">{k}</td>
                <td className="py-px text-right">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2 className="mb-1 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("커뮤니티", "Communities")}
        </h2>
        <ul className="space-y-0.5">
          {graph.communities
            .filter((c) => c.members.length >= 3)
            .map((c) => {
              const on = focus?.kind === "community" && focus.idx === c.id;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onMouseEnter={() => setHoverCom(c.id)}
                    onMouseLeave={() => setHoverCom(null)}
                    onFocus={() => setHoverCom(c.id)}
                    onBlur={() => setHoverCom(null)}
                    onClick={() => {
                      setSelected(null);
                      setFocus(on ? null : { kind: "community", idx: c.id });
                    }}
                    className={`block w-full px-1 py-0.5 text-left hover:bg-foreground/[0.06] ${on ? "bg-foreground/[0.08]" : ""}`}
                  >
                    <span className="inline-block w-8 text-muted-foreground">{roman(c.id)}</span>
                    <span className="tabular-nums">
                      {t(`${c.members.length}명`, `${c.members.length}`)}
                    </span>
                    <span className="block truncate pl-8 text-[10px] text-muted-foreground">
                      {c.events.map(([ei]) => eventLabel(ei)).join(" · ") || "—"}
                    </span>
                  </button>
                </li>
              );
            })}
        </ul>
      </section>

      <section>
        <h2 className="mb-1 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("매개 중심성 상위", "Brokers (betweenness)")}
        </h2>
        <ol className="space-y-px">
          {brokers.slice(0, 8).map((i, k) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => select(i)}
                className="flex w-full items-baseline px-1 text-left hover:bg-foreground/[0.06]"
              >
                <span className="w-5 text-muted-foreground tabular-nums">{k + 1}</span>
                <span className="flex-1 truncate">{nameOf(i)}</span>
                <span className="text-muted-foreground tabular-nums">
                  {(broker[i] ?? 0).toFixed(3)}
                </span>
              </button>
            </li>
          ))}
        </ol>
      </section>

      <details className="text-[10px] leading-4 text-muted-foreground">
        <summary className="cursor-pointer text-[9.5px] uppercase tracking-[0.16em]">
          {t("읽는 법", "How to read")}
        </summary>
        <div className="mt-1.5 space-y-1.5">
          <p>
            {t(
              "점은 작가, 선은 같은 행사에 함께 이름을 올린 기록입니다. 행사는 표집틀 회차의 공개 명단이거나, 여러 작가의 CV에서 제목과 연도가 일치하는 활동입니다. 표집틀 회차를 가리키는 CV 항목은 그 회차로 합쳐 두 번 세지 않습니다. 기획·퍼실리테이터·심사·멘토처럼 행사를 운영한 역할은 참여로 세지 않습니다.",
              "A dot is an artist; a line means both were listed in the same event. An event is a frame edition's published roster, or an activity whose title and year match across several CVs. CV lines naming a frame edition are folded into it, never counted twice. Running roles (organiser, facilitator, juror, mentor) are not counted as taking part.",
            )}
          </p>
          <p>
            {t(
              "선의 무게는 행사마다 1/(참여자−1)을 더합니다(Newman 2001). 큰 캠프와 두 사람의 전시가 한 작가에게 같은 무게로 남습니다. 회색 영역은 Louvain 커뮤니티, 매개 중심성은 Brandes 알고리즘(무가중)입니다.",
              "Each event adds 1/(participants−1) to a tie (Newman 2001), so a large camp and a two-person show weigh the same on one artist. Grey territories are Louvain communities; betweenness uses Brandes (unweighted).",
            )}
          </p>
          <p>
            {t(
              "CV가 모인 작가일수록 선이 많습니다. 선이 없다는 것은 관계가 없다는 뜻이 아니라, 아직 기록되지 않았다는 뜻입니다.",
              "Artists whose CVs are on file carry more lines. A missing line means unrecorded, not unrelated.",
            )}
          </p>
        </div>
      </details>
    </div>
  );

  return (
    <div
      ref={wrapRef}
      className="relative h-dvh min-h-[34rem] w-full select-none overflow-hidden bg-background"
    >
      <canvas
        ref={canvasRef}
        className="block h-full w-full cursor-grab touch-none"
        aria-label={t("공동 참여 네트워크", "Co-participation network")}
      />

      {/* top: wordmark · mode · find · language */}
      <div className="pointer-events-none absolute inset-x-0 top-0 p-[26px] pb-0">
        <div className="flex items-start justify-between gap-3">
          <div className="pointer-events-auto flex items-center gap-3">
            <p className="whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.22em] text-foreground/70">
              기예 Giye
            </p>
            {modeSwitch}
          </div>
          <div className="pointer-events-auto flex items-center gap-3 whitespace-nowrap font-mono text-[10px] text-muted-foreground">
            <nav
              aria-label={t("사이트 메뉴", "Site navigation")}
              aria-hidden={!menu}
              className={`hidden flex-wrap justify-end gap-x-3 gap-y-1 transition-opacity duration-300 sm:flex ${menu ? "opacity-100" : "pointer-events-none opacity-0"}`}
            >
              {SITE_NAV.map((l) => (
                <Link
                  key={l.to}
                  to={l.to}
                  tabIndex={menu ? 0 : -1}
                  className="text-foreground/70 no-underline hover:text-primary hover:underline"
                >
                  {t(l.ko, l.en)}
                </Link>
              ))}
            </nav>
            <button
              type="button"
              className="text-foreground/60"
              onClick={() => setMenu((m) => !m)}
              aria-expanded={menu}
            >
              {t("메뉴", "menu")}
            </button>
            <button
              type="button"
              onClick={() => setLang(lang === "ko" ? "en" : "ko")}
              className="border border-input px-1.5 py-0.5 text-[10px] text-foreground/70 transition hover:border-primary hover:text-primary"
              aria-label={t("언어 전환", "Switch language")}
            >
              {lang === "ko" ? "EN" : "KO"}
            </button>
          </div>
        </div>
        <div className="pointer-events-auto mt-3 w-full sm:absolute sm:left-1/2 sm:top-[19px] sm:mt-0 sm:w-[min(24rem,34vw)] sm:-translate-x-1/2">
          <ArtistFind data={data} graph={graph} nameOf={nameOf} t={t} onPick={select} />
        </div>
      </div>

      {/* left: conditions, summary, communities, brokers */}
      <aside
        className={`pointer-events-auto absolute left-5 top-[76px] z-10 w-64 overflow-auto bg-background/85 font-mono text-[10.5px] leading-5 text-foreground/85 backdrop-blur-[2px] max-md:inset-x-4 max-md:bottom-14 max-md:top-auto max-md:max-h-[60vh] max-md:w-auto max-md:border max-md:border-foreground/30 max-md:p-3 md:max-h-[calc(100dvh-140px)] ${panel ? "" : "max-md:hidden"}`}
        aria-label={t("네트워크 조건과 요약", "Network conditions and summary")}
      >
        {controls}
      </aside>

      {/* right: the selected artist's ties */}
      {selected != null ? (
        <ArtistTies
          key={selected}
          idx={selected}
          data={data}
          graph={graph}
          filter={filter}
          brokerRank={brokerRank[selected]!}
          broker={broker}
          nameOf={nameOf}
          eventLabel={eventLabel}
          focus={focus}
          t={t}
          onSelect={select}
          onFocusEvent={(ei) =>
            setFocus(
              ei == null || (focus?.kind === "event" && focus.idx === ei)
                ? { kind: "artist", idx: selected }
                : { kind: "event", idx: ei },
            )
          }
          onClose={() => select(null)}
        />
      ) : null}

      {/* bottom */}
      <div className="pointer-events-none absolute inset-x-4 bottom-4 flex items-end justify-between gap-3 font-mono text-[9.5px] text-muted-foreground">
        <button
          type="button"
          onClick={() => setPanel((p) => !p)}
          aria-expanded={panel}
          className="pointer-events-auto border border-input bg-background px-2 py-1 text-foreground/80 md:hidden"
        >
          {t("조건 · 요약", "Conditions · summary")}
        </button>
        <p className="hidden leading-4 md:block md:pl-[17rem]">
          {t(
            "점을 누르면 함께한 작가와 행사 · 끌어서 이동, 휠로 확대 · 두 번 누르면 전체 보기",
            "Click a dot for shared artists and events · drag to pan, wheel to zoom · double-click to fit",
          )}
        </p>
        <span className="tabular-nums">v{data.version}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

type T = (ko: string, en: string) => string;

function ArtistFind({
  data,
  graph,
  nameOf,
  t,
  onPick,
}: {
  data: NetworkData;
  graph: Graph;
  nameOf: (i: number) => string;
  t: T;
  onPick: (i: number) => void;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/\s+/g, "");
    if (!s) return [];
    return data.artists
      .map((a, i) => ({ a, i }))
      .filter(({ a }) =>
        [a.name_ko, a.name_en ?? "", a.id].some((v) =>
          v.toLowerCase().replace(/\s+/g, "").includes(s),
        ),
      )
      .sort((p, r) => graph.strength[r.i]! - graph.strength[p.i]!)
      .slice(0, 8)
      .map(({ i }) => i);
  }, [q, data.artists, graph]);
  const choose = (i: number | undefined) => {
    if (i == null) return;
    onPick(i);
    setQ("");
    setOpen(false);
    (document.activeElement as HTMLElement | null)?.blur();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // The Enter that finishes an IME syllable (Korean) is not a choice; see StudySearch.
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (hits.length ? (a + 1) % hits.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (hits.length ? (a - 1 + hits.length) % hits.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(hits[active]);
    } else if (e.key === "Escape") {
      setQ("");
      setOpen(false);
    }
  };
  const show = open && q.trim().length > 0;
  return (
    <div className="relative">
      <div className="flex items-center gap-2 border-b border-foreground/30 py-1 focus-within:border-foreground">
        <label htmlFor="net-q" className="sr-only">
          {t("작가 찾기", "Find an artist")}
        </label>
        <input
          id="net-q"
          role="combobox"
          aria-expanded={show}
          aria-controls="net-q-list"
          aria-autocomplete="list"
          aria-activedescendant={show && hits.length ? `net-q-${active}` : undefined}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          placeholder={t("네트워크에서 작가 찾기", "Find an artist in the network")}
          autoComplete="off"
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent py-1 text-[13px] text-foreground outline-none placeholder:text-muted-foreground/70"
        />
        <span aria-hidden="true" className="font-mono text-[10px] text-muted-foreground/70">
          /
        </span>
      </div>
      {show ? (
        <ul
          id="net-q-list"
          role="listbox"
          className="absolute inset-x-0 top-full z-30 mt-1 max-h-[min(60vh,24rem)] overflow-y-auto border border-foreground/40 bg-background py-1 shadow-[0_6px_24px_rgba(0,0,0,0.1)]"
        >
          {hits.length === 0 ? (
            <li className="px-3 py-2 text-[12px] text-muted-foreground">
              {t("찾는 작가가 없습니다", "No matching artist")}
            </li>
          ) : null}
          {hits.map((i, k) => (
            <li
              key={i}
              id={`net-q-${k}`}
              role="option"
              aria-selected={k === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(k)}
              onClick={() => choose(i)}
              className={`cursor-pointer px-3 py-1.5 ${k === active ? "bg-foreground/[0.07]" : ""}`}
            >
              <span className="block truncate text-[12.5px]">{nameOf(i)}</span>
              <span className="block font-mono text-[10px] text-muted-foreground">
                {t(`함께한 작가 ${graph.degree[i]}`, `${graph.degree[i]} ties`)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ArtistTies({
  idx,
  data,
  graph,
  filter,
  brokerRank,
  broker,
  nameOf,
  eventLabel,
  focus,
  t,
  onSelect,
  onFocusEvent,
  onClose,
}: {
  idx: number;
  data: NetworkData;
  graph: Graph;
  filter: NetFilter;
  brokerRank: number;
  /** brokerage scores, empty until they have been computed */
  broker: Float64Array;
  nameOf: (i: number) => string;
  eventLabel: (ei: number) => string;
  focus: Focus;
  t: T;
  onSelect: (i: number) => void;
  onFocusEvent: (ei: number | null) => void;
  onClose: () => void;
}) {
  const [more, setMore] = useState(false);
  const a = data.artists[idx]!;
  const ties = useMemo(
    () =>
      graph.adj[idx]!.map((k) => graph.edges[k]!)
        .map((e) => ({ other: e.a === idx ? e.b : e.a, e }))
        .sort((p, q) => q.e.events.length - p.e.events.length || q.e.w - p.e.w),
    [graph, idx],
  );
  const events = useMemo(
    () =>
      [...graph.eventsOf[idx]!].sort(
        (p, q) => (data.events[q]!.year ?? 0) - (data.events[p]!.year ?? 0),
      ),
    [graph, idx, data.events],
  );
  const shown = more ? ties : ties.slice(0, 24);
  const evidence = (ei: number) => {
    const e = data.events[ei]!;
    const r = filter.roster && e.roster.includes(idx);
    const c = filter.cv && e.cv.includes(idx);
    return r && c ? t("명단·CV", "roster·CV") : r ? t("명단", "roster") : t("CV", "CV");
  };
  const other =
    a.name_en && a.name_en !== a.name_ko
      ? nameOf(idx) === a.name_ko
        ? a.name_en
        : a.name_ko
      : null;
  const h2 =
    "mb-1 mt-4 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground";

  return (
    <aside
      className="pointer-events-auto absolute z-20 overflow-auto border border-foreground/60 bg-background font-mono text-[10.5px] leading-5 text-foreground/85 shadow-[0_6px_24px_rgba(0,0,0,0.08)] max-md:inset-x-4 max-md:bottom-14 max-md:max-h-[55vh] md:bottom-14 md:right-5 md:top-[76px] md:w-[340px]"
      aria-label={t("선택한 작가의 연결", "Ties of the selected artist")}
    >
      <div className="sticky top-0 flex items-start justify-between gap-2 border-b border-foreground/30 bg-background px-3 py-2">
        <div className="min-w-0">
          <p className="truncate font-sans text-[15px] font-semibold text-foreground">
            {nameOf(idx)}
          </p>
          {other ? <p className="truncate text-[10px] text-muted-foreground">{other}</p> : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("닫기", "Close")}
          className="grid size-6 shrink-0 place-items-center border border-input hover:border-primary hover:text-primary"
        >
          ×
        </button>
      </div>
      <div className="px-3 pb-3">
        <table className="mt-2 w-full tabular-nums">
          <tbody>
            {(
              [
                [t("함께한 작가", "Artists tied"), graph.degree[idx]],
                [t("함께한 행사", "Shared events"), events.length],
                [t("연결 강도", "Tie strength"), graph.strength[idx]!.toFixed(2)],
                [
                  t("매개 중심성", "Betweenness"),
                  brokerRank >= 0
                    ? `${(broker[idx] ?? 0).toFixed(3)} · ${t(`${brokerRank + 1}위`, `#${brokerRank + 1}`)}`
                    : "0",
                ],
                [
                  t("커뮤니티", "Community"),
                  graph.community[idx]! >= 0 ? roman(graph.community[idx]!) : "—",
                ],
                [t("기록", "Records"), a.records],
              ] as const
            ).map(([k, v]) => (
              <tr key={k}>
                <td className="pr-2 text-muted-foreground">{k}</td>
                <td className="text-right">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Link
          to="/artist/$id"
          params={{ id: a.id }}
          className="mt-2 inline-block text-foreground underline underline-offset-2 hover:text-primary"
        >
          {t("기록 전체 보기 →", "Open full record →")}
        </Link>

        <h3 className={h2}>{t("함께한 행사", "Shared events")}</h3>
        {events.length === 0 ? (
          <p className="text-muted-foreground">
            {t("이 조건에서 함께한 행사가 없습니다.", "No shared events under these conditions.")}
          </p>
        ) : (
          <ul>
            {events.map((ei) => {
              const e = data.events[ei]!;
              const on = focus?.kind === "event" && focus.idx === ei;
              return (
                <li key={ei}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => onFocusEvent(ei)}
                    className={`block w-full px-1 text-left hover:bg-foreground/[0.06] ${on ? "bg-foreground/[0.1]" : ""}`}
                  >
                    <span className="flex items-baseline gap-2">
                      <span className="w-9 shrink-0 text-muted-foreground tabular-nums">
                        {e.year ?? "—"}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{eventLabel(ei)}</span>
                      <span className="shrink-0 text-muted-foreground tabular-nums">
                        {eventMembers(e, filter).length}
                      </span>
                    </span>
                    <span className="block truncate pl-11 text-[9.5px] text-muted-foreground">
                      {[e.detail, evidence(ei)].filter(Boolean).join(" · ")}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <h3 className={h2}>{t("함께한 작가", "Artists tied")}</h3>
        <ul>
          {shown.map(({ other: o, e }) => (
            <li key={o}>
              <button
                type="button"
                onClick={() => onSelect(o)}
                className="block w-full px-1 text-left hover:bg-foreground/[0.06]"
              >
                <span className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate">{nameOf(o)}</span>
                  <span className="shrink-0 text-muted-foreground tabular-nums">
                    {t(`행사 ${e.events.length}`, `${e.events.length} events`)}
                  </span>
                </span>
                <span className="block truncate text-[9.5px] text-muted-foreground">
                  {e.events.map(eventLabel).join(" · ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {ties.length > shown.length ? (
          <button
            type="button"
            onClick={() => setMore(true)}
            className="mt-1 text-muted-foreground underline underline-offset-2 hover:text-primary"
          >
            {t(
              `${ties.length - shown.length}명 더 보기`,
              `Show ${ties.length - shown.length} more`,
            )}
          </button>
        ) : null}
      </div>
    </aside>
  );
}
