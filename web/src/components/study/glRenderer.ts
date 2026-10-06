// SPDX-License-Identifier: AGPL-3.0-only
/* WebGL2 painter for the heavy geometry of the home canvas (ArchivalStudy).

   Why: on Chrome/Windows (Skia Ganesh over ANGLE) every Canvas 2D draw call costs enough that
   the thousands of strokes and fills the study issues per frame stutter. Here the same marks are
   recorded through a small stand-in for the 2D context (GLRec) and painted with instanced
   draws: one primitive texture per frame, one instance list, and per frame a few to a few
   hundred draw calls instead of thousands.

   What is drawn is meant to be what Chrome's Canvas 2D draws on the GPU (Skia Ganesh, the
   backend of desktop Chrome on Windows and macOS), mark for mark:
   - The same geometry: the drawing code is unchanged; it only talks to GLRec instead of a
     CanvasRenderingContext2D. Coordinates are device pixels (the 2D transform, dpr, is applied
     while recording), so line widths match at any devicePixelRatio.
   - The same colours, faint ones included. Fitted on Chrome's GPU Canvas 2D (strokes of 80
     alphas, with and without globalAlpha, single and repeated): Ganesh hands the GPU the call's
     alpha (colour alpha times globalAlpha) rounded to 8 bits and each colour channel
     premultiplied by it and rounded to 8 bits; the hardware then blends source-over into the
     8-bit target. Painted that way, 918 of the 960 channel values measured match exactly; the
     rest are one level off per call (where one channel rounds the other way). An alpha that rounds to 0 paints nothing, as on Ganesh. (The CPU rasterizer of
     headless Chrome rounds differently and drops more faint marks; it is not the reference.)
   - The same order: calls are blended in the order the 2D calls were made.
   - The same overlap rule inside one call. A 2D stroke() or fill() paints the union of its
     subpaths once: where two marks of one call overlap, the pixel is not darkened twice, at
     their ends or where they cross. The study relies on this (batched dots, lanes of threads,
     polylines with round joins). Each stroke()/fill() is a call. A call of one mark is drawn
     straight onto the canvas. A call of several marks goes through a coverage pass: its marks
     write their coverage into a slot of an offscreen target with MAX blending (the union per
     pixel), and one composite then paints every pixel of the call once, with the call's colour
     times that coverage. Consecutive calls share a pass (eight slots, two RGBA8 targets; the
     call's number in a second pair of targets picks its colour from the frame's colour table):
     a call takes the lowest slot above every call of the pass whose box it overlaps, and the
     composite lays the slots over each other in order, so overlapping calls keep their 2D
     order. Passes are cut to their box by the scissor.
   - Edge antialiasing is analytic: a pixel's coverage is the mean over its four half-pixel cells
     of the area each cell has inside the mark, computed for the local edge direction. Ganesh
     instead samples coverage (in steps of 1/16 on the GPU measured) and tessellates batched
     circles slightly inside their radius, so edge pixels differ by antialiasing levels. The
     union inside a call is the per-pixel maximum, which can leave a pixel where two partly
     covering marks meet a little lighter than Skia's exact union.

   Only what the study draws in its heavy layers is supported: line segments (round caps), full
   circles (stroked, optionally dashed, or filled) and quadratic curves (stroked). Anything else
   sets `unsupported`, and the study falls back to Canvas 2D. */

export type RendererKind = "gl" | "2d";

/** Whether this browser can give the home canvas a WebGL2 context. */
function webgl2Available(): boolean {
  try {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl2");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

/**
 * The one switch that picks the renderer of the home canvas. By capability, not by OS: WebGL2
 * when the browser offers it, otherwise Canvas 2D. `?renderer=2d` or `?renderer=gl` in the
 * address overrides it for comparison. To restrict WebGL to some platforms later, change only
 * this function.
 */
export function chooseHomeRenderer(): RendererKind {
  if (typeof window === "undefined") return "2d";
  let forced: string | null = null;
  try {
    forced = new URLSearchParams(window.location.search).get("renderer");
  } catch {
    forced = null;
  }
  if (forced === "2d") return "2d";
  return webgl2Available() ? "gl" : "2d";
}

/* ------------------------------------------------------------------ */
/* colours                                                              */
/* ------------------------------------------------------------------ */

let probe: CanvasRenderingContext2D | null = null;
const baseColour = new Map<string, number>();
/** sRGB of an opaque CSS colour as 0xRRGGBB, resolved by the browser's own parser. */
function resolveBase(css: string): number {
  let v = baseColour.get(css);
  if (v !== undefined) return v;
  if (!probe) {
    const c = document.createElement("canvas");
    c.width = 1;
    c.height = 1;
    probe = c.getContext("2d", { willReadFrequently: true });
  }
  v = 0;
  if (probe) {
    probe.clearRect(0, 0, 1, 1);
    probe.fillStyle = "#000";
    probe.fillStyle = css;
    probe.fillRect(0, 0, 1, 1);
    const d = probe.getImageData(0, 0, 1, 1).data;
    v = (d[0]! << 16) | (d[1]! << 8) | d[2]!;
  }
  baseColour.set(css, v);
  return v;
}

type Paint = { rgb: number; a: number };
const paints = new Map<string, Paint>();
/** A fill or stroke style string (rgba(), oklch( / a), #hex, …) as rgb and alpha. */
function parsePaint(style: unknown): Paint | null {
  if (typeof style !== "string") return null;
  let p = paints.get(style);
  if (p) return p;
  const s = style.trim();
  let a = 1;
  let base = s;
  const open = s.indexOf("(");
  if (s.startsWith("rgba(") || s.startsWith("rgb(")) {
    const parts = s.slice(open + 1, s.lastIndexOf(")")).split(/[\s,/]+/).filter(Boolean);
    if (parts.length >= 4) a = parseAlpha(parts[3]!);
    base = `rgb(${parts[0]},${parts[1]},${parts[2]})`;
  } else if (open > 0 && s.includes("/")) {
    const slash = s.lastIndexOf("/");
    a = parseAlpha(s.slice(slash + 1, s.lastIndexOf(")")).trim());
    base = `${s.slice(0, slash).trim()})`;
  } else if (s.startsWith("#") && (s.length === 9 || s.length === 5)) {
    const hex = s.length === 5 ? s.replace(/[^#]/g, (ch) => ch + ch) : s;
    a = parseInt(hex.slice(7, 9), 16) / 255;
    base = hex.slice(0, 7);
  }
  p = { rgb: resolveBase(base), a: Math.min(1, Math.max(0, a)) };
  if (paints.size > 20000) paints.clear();
  paints.set(style, p);
  return p;
}
function parseAlpha(t: string): number {
  return t.endsWith("%") ? Number(t.slice(0, -1)) / 100 : Number(t);
}

/* ------------------------------------------------------------------ */
/* shaders                                                              */
/* ------------------------------------------------------------------ */

const T_SEG = 0;
const T_RING = 1;
const T_BEZ = 2;
const T_ELL = 3; // axis-aligned ellipse, stroked
const T_ELLF = 4; // axis-aligned ellipse, filled
/** Words per primitive in the primitive texture (four RGBA32UI texels). */
const WORDS = 16;
const TEX_W = 4096; // texels per row: 1024 primitives
const BEZ_N = 12; // sub-quads that cover one quadratic curve
const PASS_MAX = 255; // calls sharing one coverage pass at most (numbered 1..255)
const SLOTS = 8; // coverage slots of a pass (two RGBA8 targets)
const COL_W = 1024; // texels per row of the colour table

const COMMON = /* glsl */ `#version 300 es
precision highp float;
precision highp int;
precision highp usampler2D;
uniform usampler2D uPrims;
uniform vec2 uView;
uvec4 fetchW(uint i, int k) {
  int t = int(i) * 4 + k;
  return texelFetch(uPrims, ivec2(t % ${TEX_W}, t / ${TEX_W}), 0);
}
vec2 bez(vec2 a, vec2 q, vec2 b, float t) {
  float s = 1.0 - t;
  return a * (s * s) + q * (2.0 * s * t) + b * (t * t);
}
`;

const FRAG = /* glsl */ `${COMMON}
flat in uint vPrim;
flat in uint vSlot;
flat in uint vId;
uniform int uPass;
layout(location = 0) out vec4 o0;
layout(location = 1) out vec4 o1;
layout(location = 2) out vec4 o2;
layout(location = 3) out vec4 o3;

// Area of the unit pixel square on the inner side of a straight edge whose signed distance from
// the pixel centre is t (positive: centre inside); a >= b are the absolute normal components.
float hp(float t, float a, float b) {
  float s = 0.5 * (a + b);
  if (t >= s) return 1.0;
  if (t <= -s) return 0.0;
  float m = 0.5 * (a - b);
  if (t > m) { float x = s - t; return 1.0 - x * x / (2.0 * a * b); }
  if (t < -m) { float x = t + s; return x * x / (2.0 * a * b); }
  return 0.5 + t / a;
}
// Coverage of a band of half width hw around a centre line at distance d, normal n.
float band(float d, float hw, vec2 n) {
  vec2 an = abs(n);
  float a = max(an.x, an.y);
  float b = max(min(an.x, an.y), 1e-4);
  return clamp(hp(hw - d, a, b) - hp(-hw - d, a, b), 0.0, 1.0);
}
float dot2(vec2 v) { return dot(v, v); }
// Closest parameter on a quadratic curve (exact, after Inigo Quilez), in coordinates relative to A.
float bezT(vec2 A, vec2 B, vec2 C, vec2 pos) {
  vec2 a = B - A;
  vec2 b = A - 2.0 * B + C;
  vec2 c = a * 2.0;
  vec2 d = A - pos;
  float bb = dot(b, b);
  if (bb < 1e-8) {
    vec2 ac = C - A;
    float l = dot(ac, ac);
    return l > 0.0 ? clamp(dot(pos - A, ac) / l, 0.0, 1.0) : 0.0;
  }
  float kk = 1.0 / bb;
  float kx = kk * dot(a, b);
  float ky = kk * (2.0 * dot(a, a) + dot(d, b)) / 3.0;
  float kz = kk * dot(d, a);
  float p = ky - kx * kx;
  float p3 = p * p * p;
  float q = kx * (2.0 * kx * kx - 3.0 * ky) + kz;
  float h = q * q + 4.0 * p3;
  if (h >= 0.0) {
    h = sqrt(h);
    vec2 x = (vec2(h, -h) - q) / 2.0;
    vec2 uv = sign(x) * pow(abs(x), vec2(1.0 / 3.0));
    return clamp(uv.x + uv.y - kx, 0.0, 1.0);
  }
  float z = sqrt(-p);
  float v = acos(clamp(q / (p * z * 2.0), -1.0, 1.0)) / 3.0;
  float m = cos(v);
  float n = sin(v) * 1.732050808;
  vec2 t = clamp(vec2(m + m, -n - m) * z - kx, 0.0, 1.0);
  float d0 = dot2(d + (c + b * t.x) * t.x);
  float d1 = dot2(d + (c + b * t.y) * t.y);
  return d0 < d1 ? t.x : t.y;
}

// Coverage of a cell of size 1/S at p by a round-capped segment a..a+ba of half width hw/S
// (hw is already scaled by S).
float segBand(vec2 a, vec2 ba, float l2, vec2 p, float hw, float S) {
  vec2 pa = p - a;
  float h = l2 > 1e-12 ? clamp(dot(pa, ba) / l2, 0.0, 1.0) : 0.0;
  vec2 q = pa - ba * h;
  float d = length(q);
  vec2 n;
  if (h > 0.0 && h < 1.0) n = vec2(-ba.y, ba.x) / sqrt(l2);
  else n = d > 1e-6 ? q / d : vec2(1.0, 0.0);
  return band(d * S, hw, n);
}

// Coverage of the pixel (S = 1) or of a 1/S-sized sub-pixel cell centred at p by one primitive;
// for a curve, t is the parameter of the closest point.
float coverage(uint prim, vec2 p, float S, out float t) {
  vec4 g0 = uintBitsToFloat(fetchW(prim, 0));
  vec4 g1 = uintBitsToFloat(fetchW(prim, 1));
  uvec4 w2 = fetchW(prim, 2);
  uvec4 w3 = fetchW(prim, 3);
  uint type = w2.x >> 24u;
  float hw = g1.w * S;
  t = 0.0;
  if (type == ${T_RING}u) {
    vec2 v = p - g0.xy;
    float dist = length(v);
    vec2 n = dist > 1e-6 ? v / dist : vec2(1.0, 0.0);
    float d = abs(dist - g1.z);
    float on = uintBitsToFloat(w3.z);
    if (on > 0.0) {
      // dashes along the arc, starting at angle 0 like Canvas; round caps
      float r = g1.z;
      float ang = atan(v.y, v.x);
      if (ang < 0.0) ang += 6.283185307;
      float s = ang * r;
      float period = on + uintBitsToFloat(w3.w);
      float u = mod(s, period);
      float e = u <= on ? 0.0 : min(u - on, period - u);
      float circ = 6.283185307 * r;
      // the last partial dash ends where the circle closes
      if (s > circ - mod(circ, period) && u > on) e = min(e, circ - s);
      float D = sqrt(d * d + e * e);
      vec2 tg = vec2(-n.y, n.x);
      vec2 nn = D > 1e-6 ? n * (d / D) + tg * (e / D) : n;
      return band(D * S, hw, nn);
    }
    return band(d * S, hw, n);
  }
  if (type >= ${T_ELL}u) {
    // distance to an ellipse (radii g1.z, g1.x), first-order estimate f/|grad f|
    vec2 r = vec2(g1.z, g1.x);
    vec2 v = p - g0.xy;
    float k0 = length(v / r);
    float k1 = length(v / (r * r));
    float sd = k1 > 1e-9 ? k0 * (k0 - 1.0) / k1 : -min(r.x, r.y);
    vec2 gr = v / (r * r);
    vec2 n = length(gr) > 1e-9 ? normalize(gr) : vec2(1.0, 0.0);
    if (type == ${T_ELLF}u) {
      vec2 an = abs(n);
      float a = max(an.x, an.y);
      float b = max(min(an.x, an.y), 1e-4);
      return clamp(hp(-sd * S, a, b), 0.0, 1.0);
    }
    return band(abs(sd) * S, hw, n);
  }
  if (type == ${T_BEZ}u) {
    vec2 A = g0.xy;
    vec2 Q = g1.xy;
    vec2 B = g0.zw;
    t = bezT(vec2(0.0), Q - A, B - A, p - A);
    vec2 c = bez(vec2(0.0), Q - A, B - A, t);
    vec2 v = (p - A) - c;
    float d = length(v);
    vec2 n;
    if (d > 1e-5) n = v / d;
    else {
      vec2 tg = (Q - A) * (2.0 * (1.0 - t)) + (B - Q) * (2.0 * t);
      n = length(tg) > 1e-6 ? vec2(-tg.y, tg.x) / length(tg) : vec2(1.0, 0.0);
    }
    return band(d * S, hw, n);
  }
  vec2 a = g0.xy;
  vec2 ba = g0.zw - a;
  float l2 = dot(ba, ba);
  float c = segBand(a, ba, l2, p, hw, S);
  // A round end (or a small disc) is curved within the pixel, where a straight-edge estimate
  // overshoots; there the pixel is split into 4x4 cells, each estimated on its own.
  if (g1.w * S < 3.0 && c > 0.0) {
    vec2 pa = p - a;
    float h = l2 > 1e-12 ? dot(pa, ba) / l2 : 0.0;
    if (h <= 0.0 || h >= 1.0) {
      float acc = 0.0;
      for (int k = 0; k < 16; k++) {
        vec2 o = (vec2(float(k & 3), float(k >> 2)) - 1.5) / (4.0 * S);
        acc += segBand(a, ba, l2, p + o, hw * 4.0, S * 4.0);
      }
      c = acc / 16.0;
    }
  }
  return c;
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uView.y - gl_FragCoord.y);
  float t;
  // coverage of the four 1/2-pixel cells of this pixel (see the header)
  vec4 c4;
  for (int q = 0; q < 4; q++) {
    vec2 c = p + vec2((q & 1) == 0 ? -0.25 : 0.25, q < 2 ? -0.25 : 0.25);
    c4[q] = coverage(vPrim, c, 2.0, t);
  }
  float cov = dot(c4, vec4(0.25));
  if (cov <= 0.0) discard;
  if (uPass == 1) {
    // coverage pass: this mark's coverage and its call's number in the call's slot; blended
    // with MAX, the slot keeps the union (max) of the call's marks (see the header)
    vec4 one = vec4(equal(uvec4(vSlot & 3u), uvec4(0u, 1u, 2u, 3u)));
    vec4 lo = vSlot < 4u ? one : vec4(0.0);
    vec4 hi = vSlot < 4u ? vec4(0.0) : one;
    float id = float(vId) / 255.0;
    o0 = lo * cov;
    o1 = hi * cov;
    o2 = lo * id;
    o3 = hi * id;
  } else {
    // the call's colour as Ganesh hands it to the GPU: alpha in 8 bits, premultiplied 8-bit rgb
    uvec4 w2 = fetchW(vPrim, 2);
    vec4 col = vec4(float((w2.x >> 16u) & 255u), float((w2.x >> 8u) & 255u), float(w2.x & 255u),
                    float(w2.y)) / 255.0;
    o0 = col * cov;
  }
}
`;

/* The vertex stage builds a quad (or a strip of BEZ_N quads for a curve) that covers the mark
   plus its antialiased fringe. */
const VERT = /* glsl */ `${COMMON}
layout(location = 0) in uint aInst;
uniform int uBez;
flat out uint vPrim;
flat out uint vSlot;
flat out uint vId;
void main() {
  // instance word: primitive (20 bits), call number in its pass (8), slot (3); see plan()
  uint prim = aInst & 0xFFFFFu;
  vPrim = prim;
  vId = (aInst >> 20u) & 255u;
  vSlot = aInst >> 28u;
  vec4 g0 = uintBitsToFloat(fetchW(prim, 0));
  vec4 g1 = uintBitsToFloat(fetchW(prim, 1));
  uvec4 w2 = fetchW(prim, 2);
  uint type = w2.x >> 24u;
  int vid = gl_VertexID % 6;
  vec2 c = vec2((vid == 1 || vid == 4 || vid == 5) ? 1.0 : -1.0,
                (vid == 2 || vid == 3 || vid == 5) ? 1.0 : -1.0);
  float hw = g1.w;
  float m = hw + 1.0;
  vec2 pos;
  if (uBez == 1) {
    int k = gl_VertexID / 6;
    vec2 A = g0.xy, Q = g1.xy, B = g0.zw;
    float t0 = float(k) / ${BEZ_N}.0;
    float t1 = float(k + 1) / ${BEZ_N}.0;
    vec2 P0 = bez(A, Q, B, t0);
    vec2 P1 = bez(A, Q, B, t1);
    vec2 d = P1 - P0;
    float L = length(d);
    vec2 tg = L > 1e-6 ? d / L : vec2(1.0, 0.0);
    vec2 n = vec2(-tg.y, tg.x);
    float sag = length(A - 2.0 * Q + B) * 2.0 / (8.0 * ${BEZ_N}.0 * ${BEZ_N}.0);
    pos = (c.x < 0.0 ? P0 - tg * m : P1 + tg * m) + n * (c.y * (m + sag));
  } else if (type == ${T_RING}u) {
    pos = g0.xy + c * (g1.z + m);
  } else if (type >= ${T_ELL}u) {
    pos = g0.xy + c * (vec2(g1.z, g1.x) + m);
  } else {
    vec2 a = g0.xy, b = g0.zw;
    vec2 d = b - a;
    float L = length(d);
    vec2 tg = L > 1e-6 ? d / L : vec2(1.0, 0.0);
    vec2 n = vec2(-tg.y, tg.x);
    pos = (c.x < 0.0 ? a - tg * m : b + tg * m) + n * (c.y * m);
  }
  gl_Position = vec4(pos.x / uView.x * 2.0 - 1.0, 1.0 - pos.y / uView.y * 2.0, 0.0, 1.0);
}
`;

const STAMP_VERT = /* glsl */ `#version 300 es
precision highp float;
uniform vec2 uView;
uniform vec2 uSize;
uniform vec3 uM0;
uniform vec3 uM1;
out vec2 vUV;
void main() {
  int vid = gl_VertexID;
  vec2 c = vec2((vid == 1 || vid == 4 || vid == 5) ? 1.0 : 0.0,
                (vid == 2 || vid == 3 || vid == 5) ? 1.0 : 0.0);
  vec2 px = c * uSize;
  vec2 pos = vec2(dot(uM0, vec3(px, 1.0)), dot(uM1, vec3(px, 1.0)));
  vUV = vec2(c.x, 1.0 - c.y);
  gl_Position = vec4(pos.x / uView.x * 2.0 - 1.0, 1.0 - pos.y / uView.y * 2.0, 0.0, 1.0);
}
`;
const STAMP_FRAG = /* glsl */ `#version 300 es
precision highp float;
uniform sampler2D uTex;
in vec2 vUV;
out vec4 outColor;
void main() { outColor = texture(uTex, vUV); }
`;

/* Composite of a coverage pass: each pixel once. The slots are laid over each other in order,
   each with its call's colour (from the frame's colour table) times its coverage, and the
   result is blended source-over onto the target. A full-screen triangle, cut to the pass's box
   by the scissor. */
const COMP_VERT = /* glsl */ `#version 300 es
void main() {
  vec2 c = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
  gl_Position = vec4(c, 0.0, 1.0);
}
`;
const COMP_FRAG = /* glsl */ `#version 300 es
precision highp float;
precision highp int;
uniform sampler2D uCov0;
uniform sampler2D uCov1;
uniform sampler2D uId0;
uniform sampler2D uId1;
uniform sampler2D uCol;
uniform int uBase;
out vec4 outColor;
void main() {
  ivec2 q = ivec2(gl_FragCoord.xy);
  vec4 c0 = texelFetch(uCov0, q, 0);
  vec4 c1 = texelFetch(uCov1, q, 0);
  if (max(max(max(c0.x, c0.y), max(c0.z, c0.w)), max(max(c1.x, c1.y), max(c1.z, c1.w))) <= 0.0) discard;
  vec4 i0 = texelFetch(uId0, q, 0);
  vec4 i1 = texelFetch(uId1, q, 0);
  vec4 acc = vec4(0.0);
  for (int k = 0; k < 8; k++) {
    float cov = k < 4 ? c0[k] : c1[k - 4];
    if (cov <= 0.0) continue;
    int id = int((k < 4 ? i0[k] : i1[k - 4]) * 255.0 + 0.5);
    int t = uBase + id;
    vec4 col = texelFetch(uCol, ivec2(t % ${COL_W}, t / ${COL_W}), 0) * cov;
    acc = col + acc * (1.0 - col.a);
  }
  outColor = acc;
}
`;

/* ------------------------------------------------------------------ */
/* painter                                                              */
/* ------------------------------------------------------------------ */

type Op =
  | { k: "call"; first: number; last: number; bez: boolean; g: number }
  | { k: "target"; fbo: boolean }
  | { k: "clear" }
  | { k: "stamp"; m: [number, number, number, number, number, number] };

type Step =
  | { k: "direct"; bez: boolean; first: number; count: number }
  | {
      k: "pass";
      first: number;
      count: number;
      bFirst: number;
      bCount: number;
      box: [number, number, number, number];
      base: number;
    }
  | { k: "target"; fbo: boolean }
  | { k: "clear" }
  | { k: "stamp"; m: [number, number, number, number, number, number] };

/** One WebGL2 canvas. Recorded marks are kept until flush(), which paints them in order. */
export class GLPainter {
  readonly canvas: HTMLCanvasElement;
  gl: WebGL2RenderingContext | null = null;
  lost = false;
  /** set when the study asked for something this painter cannot draw; the study falls back */
  unsupported = "";
  /** bumped whenever the context is (re)created: offscreen content from before is gone */
  epoch = 0;
  /** draw calls issued by the last flush (for the measurement harness) */
  lastDrawCalls = 0;
  /** coverage passes run by the last flush (for the measurement harness) */
  lastPasses = 0;
  /** main-thread ms of the last flush (for the measurement harness) */
  lastFlushMs = 0;
  private prog: WebGLProgram | null = null;
  private stampProg: WebGLProgram | null = null;
  private compProg: WebGLProgram | null = null;
  private primTex: WebGLTexture | null = null;
  private primRows = 0;
  private instBuf: WebGLBuffer | null = null;
  private vao: WebGLVertexArrayObject | null = null;
  private emptyVao: WebGLVertexArrayObject | null = null;
  /** offscreen layer (the study's settled-ring cache) */
  private fbo: WebGLFramebuffer | null = null;
  private fboTex: WebGLTexture | null = null;
  private fboW = 0;
  private fboH = 0;
  /** coverage pass target: slot coverages (two textures) and slot call numbers (two) */
  private covFbo: WebGLFramebuffer | null = null;
  private covTex: (WebGLTexture | null)[] = [];
  private covW = 0;
  private covH = 0;
  /** the frame's colour table: one texel per call in a pass (premultiplied, 8-bit) */
  private colTex: WebGLTexture | null = null;
  private colRows = 0;
  private colours = new Uint8Array(4 * COL_W * 4);
  private nCol = 0;
  private u: Record<string, WebGLUniformLocation | null> = {};
  private us: Record<string, WebGLUniformLocation | null> = {};
  private uc: Record<string, WebGLUniformLocation | null> = {};
  // frame state
  private words = new Uint32Array(WORDS * 4096);
  private floats = new Float32Array(this.words.buffer);
  /** primitives recorded since begin() */
  count = 0;
  private inst = new Uint32Array(16384);
  private nInst = 0;
  private ops: Op[] = [];
  private group = 0;
  /** bounding box of each call (group), device px, antialiasing fringe included */
  private gBox = new Float32Array(4 * 1024);
  private W = 1;
  private H = 1;
  // pass planning scratch: boxes and slots of the calls in the open pass
  private passBox = new Float32Array(4 * PASS_MAX);
  private passSlot = new Uint8Array(PASS_MAX);

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      this.lost = true;
      this.gl = null;
    });
    canvas.addEventListener("webglcontextrestored", () => {
      this.lost = false;
      this.init();
    });
    this.init();
  }

  get ok(): boolean {
    return !!this.gl && !this.lost && !this.unsupported;
  }

  private init() {
    this.epoch++;
    this.fbo = null;
    this.fboTex = null;
    this.fboW = 0;
    this.fboH = 0;
    this.covFbo = null;
    this.covTex = [];
    this.covW = 0;
    this.covH = 0;
    this.colTex = null;
    this.colRows = 0;
    this.primRows = 0;
    const gl = this.canvas.getContext("webgl2", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
    });
    if (!gl) {
      this.gl = null;
      return;
    }
    this.gl = gl;
    try {
      this.prog = link(gl, VERT, FRAG);
      this.stampProg = link(gl, STAMP_VERT, STAMP_FRAG);
      this.compProg = link(gl, COMP_VERT, COMP_FRAG);
    } catch (err) {
      this.unsupported = String(err);
      this.gl = null;
      return;
    }
    for (const n of ["uPrims", "uView", "uBez", "uPass"]) this.u[n] = gl.getUniformLocation(this.prog, n);
    for (const n of ["uView", "uSize", "uM0", "uM1", "uTex"])
      this.us[n] = gl.getUniformLocation(this.stampProg, n);
    for (const n of ["uCov0", "uCov1", "uId0", "uId1", "uCol", "uBase"])
      this.uc[n] = gl.getUniformLocation(this.compProg, n);
    // the coverage pass writes four targets at once
    if ((gl.getParameter(gl.MAX_DRAW_BUFFERS) as number) < 4) {
      this.unsupported = "draw buffers";
      return;
    }
    this.primTex = gl.createTexture();
    this.colTex = gl.createTexture();
    this.instBuf = gl.createBuffer();
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instBuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribIPointer(0, 1, gl.UNSIGNED_INT, 4, 0);
    gl.vertexAttribDivisor(0, 1);
    this.emptyVao = gl.createVertexArray();
    gl.bindVertexArray(null);
  }

  /** Start a frame of wPx × hPx device pixels. */
  begin(wPx: number, hPx: number) {
    if (this.canvas.width !== wPx) this.canvas.width = wPx;
    if (this.canvas.height !== hPx) this.canvas.height = hPx;
    this.W = wPx;
    this.H = hPx;
    this.count = 0;
    this.nInst = 0;
    this.ops = [{ k: "target", fbo: false }, { k: "clear" }];
    this.group = 0;
  }

  /** Further marks go to the offscreen layer (fbo) or to the canvas. */
  target(fbo: boolean) {
    this.ops.push({ k: "target", fbo });
  }
  /** Clear the current target to transparent if asked (kept otherwise). */
  clear(color: boolean) {
    if (color) this.ops.push({ k: "clear" });
  }
  /** Paint the offscreen layer onto the current target through m (layer px → device px). */
  stamp(m: [number, number, number, number, number, number]) {
    this.ops.push({ k: "stamp", m });
  }

  /** Open a new call (one 2D stroke() or fill()); returns its number. */
  newGroup(): number {
    const g = ++this.group;
    if ((g + 1) * 4 > this.gBox.length) {
      const b = new Float32Array(this.gBox.length * 2);
      b.set(this.gBox);
      this.gBox = b;
    }
    const o = g * 4;
    this.gBox[o] = Infinity;
    this.gBox[o + 1] = Infinity;
    this.gBox[o + 2] = -Infinity;
    this.gBox[o + 3] = -Infinity;
    return g;
  }

  /** Append one primitive of group g; coordinates and widths in device px, rgb straight
   *  0xRRGGBB, a the call's alpha (colour alpha times globalAlpha). */
  prim(
    type: number,
    x0: number,
    y0: number,
    x1: number,
    y1: number,
    qx: number,
    qy: number,
    r: number,
    hw: number,
    rgb: number,
    a: number,
    group: number,
    dashOn = 0,
    dashOff = 0,
  ) {
    // the instance word holds the primitive in 20 bits (see plan())
    if (this.count >= 1 << 20) {
      if (!this.unsupported) this.unsupported = "marks per frame";
      return;
    }
    const i = this.count++;
    if ((i + 1) * WORDS > this.words.length) {
      const next = new Uint32Array(this.words.length * 2);
      next.set(this.words);
      this.words = next;
      this.floats = new Float32Array(next.buffer);
    }
    const o = i * WORDS;
    const f = this.floats;
    f[o] = x0;
    f[o + 1] = y0;
    f[o + 2] = x1;
    f[o + 3] = y1;
    f[o + 4] = qx;
    f[o + 5] = qy;
    f[o + 6] = r;
    f[o + 7] = hw;
    // colour as Ganesh passes it (fitted on Chrome's GPU Canvas 2D, see the header): the alpha
    // rounded to 8 bits, then each channel premultiplied by it and rounded to 8 bits
    const a8 = alpha8(a);
    const pr = Math.round((((rgb >> 16) & 255) * a8) / 255);
    const pg = Math.round((((rgb >> 8) & 255) * a8) / 255);
    const pb = Math.round(((rgb & 255) * a8) / 255);
    this.words[o + 8] = ((type << 24) | (pr << 16) | (pg << 8) | pb) >>> 0;
    this.words[o + 9] = a8;
    this.words[o + 10] = group;
    this.words[o + 11] = 0;
    this.words[o + 12] = 0;
    this.words[o + 13] = 0;
    f[o + 14] = dashOn;
    f[o + 15] = dashOff;
    // the call's box
    let bx0: number, by0: number, bx1: number, by1: number;
    if (type === T_RING) {
      const e = Math.abs(r) + hw;
      bx0 = x0 - e;
      bx1 = x0 + e;
      by0 = y0 - e;
      by1 = y0 + e;
    } else if (type >= T_ELL) {
      const ex = Math.abs(r) + hw;
      const ey = Math.abs(qx) + hw;
      bx0 = x0 - ex;
      bx1 = x0 + ex;
      by0 = y0 - ey;
      by1 = y0 + ey;
    } else if (type === T_BEZ) {
      bx0 = Math.min(x0, x1, qx) - hw;
      bx1 = Math.max(x0, x1, qx) + hw;
      by0 = Math.min(y0, y1, qy) - hw;
      by1 = Math.max(y0, y1, qy) + hw;
    } else {
      bx0 = Math.min(x0, x1) - hw;
      bx1 = Math.max(x0, x1) + hw;
      by0 = Math.min(y0, y1) - hw;
      by1 = Math.max(y0, y1) + hw;
    }
    const B = this.gBox;
    const gb = group * 4;
    if (bx0 - 2 < B[gb]!) B[gb] = bx0 - 2;
    if (by0 - 2 < B[gb + 1]!) B[gb + 1] = by0 - 2;
    if (bx1 + 2 > B[gb + 2]!) B[gb + 2] = bx1 + 2;
    if (by1 + 2 > B[gb + 3]!) B[gb + 3] = by1 + 2;
  }

  /** Close call g, whose primitives are first..last-1. */
  queue(first: number, last: number, bez: boolean, g: number) {
    if (last > first) this.ops.push({ k: "call", first, last, bez, g });
  }

  /* Plan (see the header). A call of one mark is painted directly, in order, like any 2D draw;
     runs of such calls share one draw. A call of several marks goes through a coverage pass.
     Consecutive calls share a pass: each takes the lowest slot above the slots of the calls in
     the pass that it overlaps (boxes), so the composite lays them in their 2D order wherever
     they meet; a pass ends when a call would need a ninth slot or a 256th number. A one-mark
     call joins an open pass when it fits, else it is painted directly after the pass. */
  private plan(): Step[] {
    const steps: Step[] = [];
    const B = this.gBox;
    const PB = this.passBox;
    const PS = this.passSlot;
    let mode: "none" | "direct" | "pass" = "none";
    let dBez = false;
    let dFirst = 0;
    let pFirst = 0;
    let pBez: number[] = [];
    let nPass = 0;
    let box: [number, number, number, number] = [0, 0, 0, 0];
    this.nCol = 0;
    let base = 0;
    const push = (i0: number, i1: number, tag: number) => {
      const need = this.nInst + (i1 - i0);
      if (need > this.inst.length) {
        const next = new Uint32Array(Math.max(need, this.inst.length * 2));
        next.set(this.inst);
        this.inst = next;
      }
      for (let i = i0; i < i1; i++) this.inst[this.nInst++] = (i | tag) >>> 0;
    };
    const close = () => {
      if (mode === "direct") {
        steps.push({ k: "direct", bez: dBez, first: dFirst, count: this.nInst - dFirst });
      } else if (mode === "pass") {
        const count = this.nInst - pFirst;
        const bFirst = this.nInst;
        for (let k = 0; k < pBez.length; k += 3) push(pBez[k]!, pBez[k + 1]!, pBez[k + 2]!);
        steps.push({ k: "pass", first: pFirst, count, bFirst, bCount: this.nInst - bFirst, box, base });
      }
      mode = "none";
    };
    /** the slot call g would take in the open pass, or -1 if it does not fit */
    const slotFor = (g: number) => {
      if (nPass >= PASS_MAX) return -1;
      const o = g * 4;
      let slot = 0;
      for (let k = 0; k < nPass; k++) {
        const q = k * 4;
        if (B[o]! < PB[q + 2]! && B[o + 2]! > PB[q]! && B[o + 1]! < PB[q + 3]! && B[o + 3]! > PB[q + 1]!)
          if (PS[k]! + 1 > slot) slot = PS[k]! + 1;
      }
      return slot < SLOTS ? slot : -1;
    };
    const join = (op: { first: number; last: number; bez: boolean; g: number }, slot: number) => {
      const o = op.g * 4;
      const q = nPass * 4;
      PB[q] = B[o]!;
      PB[q + 1] = B[o + 1]!;
      PB[q + 2] = B[o + 2]!;
      PB[q + 3] = B[o + 3]!;
      PS[nPass] = slot;
      nPass++;
      const id = nPass; // 1..255
      box = [Math.min(box[0], B[o]!), Math.min(box[1], B[o + 1]!), Math.max(box[2], B[o + 2]!), Math.max(box[3], B[o + 3]!)];
      // the call's colour into the table (every mark of a call has the same)
      const t = base + id;
      if ((t + 1) * 4 > this.colours.length) {
        const next = new Uint8Array(this.colours.length * 2);
        next.set(this.colours);
        this.colours = next;
      }
      const w = this.words[op.first * WORDS + 8]!;
      this.colours[t * 4] = (w >>> 16) & 255;
      this.colours[t * 4 + 1] = (w >>> 8) & 255;
      this.colours[t * 4 + 2] = w & 255;
      this.colours[t * 4 + 3] = this.words[op.first * WORDS + 9]!;
      this.nCol = Math.max(this.nCol, t + 1);
      const tag = (id << 20) | (slot << 28);
      if (op.bez) pBez.push(op.first, op.last, tag);
      else push(op.first, op.last, tag);
    };
    for (const op of this.ops) {
      if (op.k !== "call") {
        close();
        steps.push(op);
        continue;
      }
      const multi = op.last - op.first > 1;
      const slot = mode === "pass" ? slotFor(op.g) : -1;
      if (slot >= 0) {
        join(op, slot);
      } else if (!multi) {
        if (mode !== "direct" || dBez !== op.bez) {
          close();
          mode = "direct";
          dBez = op.bez;
          dFirst = this.nInst;
        }
        push(op.first, op.last, 0);
      } else {
        close();
        mode = "pass";
        pFirst = this.nInst;
        pBez = [];
        nPass = 0;
        base = this.nCol;
        box = [Infinity, Infinity, -Infinity, -Infinity];
        join(op, 0);
      }
    }
    close();
    return steps;
  }

  /** Paint everything recorded since begin(). */
  flush() {
    const gl = this.gl;
    this.lastDrawCalls = 0;
    this.lastPasses = 0;
    if (!gl || this.lost || gl.isContextLost()) return;
    const t0 = performance.now();
    this.nInst = 0;
    const steps = this.plan();
    const perRow = TEX_W / 4;
    const rows = Math.max(1, Math.ceil(this.count / perRow));
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.primTex);
    if (rows > this.primRows) {
      this.primRows = Math.max(rows, this.primRows * 2, 16);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32UI, TEX_W, this.primRows, 0, gl.RGBA_INTEGER, gl.UNSIGNED_INT, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    }
    const needWords = rows * TEX_W * 4;
    if (this.words.length < needWords) {
      const next = new Uint32Array(needWords);
      next.set(this.words);
      this.words = next;
      this.floats = new Float32Array(next.buffer);
    }
    if (this.count > 0)
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, TEX_W, rows, gl.RGBA_INTEGER, gl.UNSIGNED_INT, this.words, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instBuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.inst.subarray(0, Math.max(1, this.nInst)), gl.STREAM_DRAW);
    if (this.nCol > 0) {
      const cRows = Math.ceil(this.nCol / COL_W);
      gl.activeTexture(gl.TEXTURE5);
      gl.bindTexture(gl.TEXTURE_2D, this.colTex);
      if (cRows > this.colRows) {
        this.colRows = Math.max(cRows, this.colRows * 2, 4);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, COL_W, this.colRows, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      }
      if (this.colours.length < cRows * COL_W * 4) {
        const next = new Uint8Array(cRows * COL_W * 4);
        next.set(this.colours);
        this.colours = next;
      }
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, COL_W, cRows, gl.RGBA, gl.UNSIGNED_BYTE, this.colours, 0);
    }

    gl.enable(gl.BLEND);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.SCISSOR_TEST);
    let fboOn = false;
    const bindTarget = () => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, fboOn ? this.fbo : null);
      gl.viewport(0, 0, this.W, this.H);
    };
    const marks = (pass: boolean, bez: boolean, first: number, count: number) => {
      if (count <= 0) return;
      gl.useProgram(this.prog);
      gl.bindVertexArray(this.vao);
      gl.vertexAttribIPointer(0, 1, gl.UNSIGNED_INT, 4, first * 4);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this.primTex);
      gl.uniform1i(this.u.uPrims!, 0);
      gl.uniform2f(this.u.uView!, this.W, this.H);
      gl.uniform1i(this.u.uBez!, bez ? 1 : 0);
      gl.uniform1i(this.u.uPass!, pass ? 1 : 0);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, bez ? 6 * BEZ_N : 6, count);
      this.lastDrawCalls++;
    };
    for (const st of steps) {
      if (st.k === "target") {
        fboOn = st.fbo;
        if (fboOn) this.ensureFbo();
        bindTarget();
      } else if (st.k === "clear") {
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
      } else if (st.k === "direct") {
        gl.blendEquation(gl.FUNC_ADD);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        marks(false, st.bez, st.first, st.count);
      } else if (st.k === "pass") {
        // scissor box in GL window coordinates (origin bottom left)
        const x0 = Math.max(0, Math.floor(st.box[0]));
        const x1 = Math.min(this.W, Math.ceil(st.box[2]));
        const y0 = Math.max(0, Math.floor(st.box[1]));
        const y1 = Math.min(this.H, Math.ceil(st.box[3]));
        if (x1 <= x0 || y1 <= y0) continue;
        this.lastPasses++;
        this.ensureCov();
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.covFbo);
        gl.viewport(0, 0, this.W, this.H);
        gl.enable(gl.SCISSOR_TEST);
        gl.scissor(x0, this.H - y1, x1 - x0, y1 - y0);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.blendEquation(gl.MAX);
        marks(true, false, st.first, st.count);
        marks(true, true, st.bFirst, st.bCount);
        bindTarget();
        gl.blendEquation(gl.FUNC_ADD);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.useProgram(this.compProg);
        gl.bindVertexArray(this.emptyVao);
        const names = ["uCov0", "uCov1", "uId0", "uId1"];
        for (let k = 0; k < 4; k++) {
          gl.activeTexture(gl.TEXTURE1 + k);
          gl.bindTexture(gl.TEXTURE_2D, this.covTex[k]!);
          gl.uniform1i(this.uc[names[k]!]!, 1 + k);
        }
        gl.activeTexture(gl.TEXTURE5);
        gl.bindTexture(gl.TEXTURE_2D, this.colTex);
        gl.uniform1i(this.uc.uCol!, 5);
        gl.uniform1i(this.uc.uBase!, st.base);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        this.lastDrawCalls++;
        gl.disable(gl.SCISSOR_TEST);
      } else if (st.k === "stamp") {
        if (!this.fboTex || fboOn) continue;
        gl.blendEquation(gl.FUNC_ADD);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.useProgram(this.stampProg);
        gl.bindVertexArray(this.emptyVao);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, this.fboTex);
        gl.uniform1i(this.us.uTex!, 0);
        gl.uniform2f(this.us.uView!, this.W, this.H);
        gl.uniform2f(this.us.uSize!, this.fboW, this.fboH);
        const m = st.m;
        gl.uniform3f(this.us.uM0!, m[0], m[2], m[4]);
        gl.uniform3f(this.us.uM1!, m[1], m[3], m[5]);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        this.lastDrawCalls++;
      }
    }
    gl.bindVertexArray(null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.lastFlushMs = performance.now() - t0;
  }

  private ensureFbo() {
    const gl = this.gl!;
    if (this.fbo && this.fboW === this.W && this.fboH === this.H) return;
    if (!this.fbo) {
      this.fbo = gl.createFramebuffer();
      this.fboTex = gl.createTexture();
    }
    this.fboW = this.W;
    this.fboH = this.H;
    gl.bindTexture(gl.TEXTURE_2D, this.fboTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, this.W, this.H, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.fboTex, 0);
    // a fresh layer starts transparent, as a resized 2D canvas does
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }

  /** The coverage pass target, the size of the canvas. */
  private ensureCov() {
    const gl = this.gl!;
    if (this.covFbo && this.covW === this.W && this.covH === this.H) return;
    if (!this.covFbo) {
      this.covFbo = gl.createFramebuffer();
      this.covTex = [0, 1, 2, 3].map(() => gl.createTexture());
    }
    this.covW = this.W;
    this.covH = this.H;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.covFbo);
    this.covTex.forEach((t, k) => {
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, this.W, this.H, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + k, gl.TEXTURE_2D, t, 0);
    });
    gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1, gl.COLOR_ATTACHMENT2, gl.COLOR_ATTACHMENT3]);
  }
}

/** A call's alpha as Ganesh uses it: rounded to 8 bits (0..255). */
export function alpha8(a: number): number {
  const v = Math.round(a * 255);
  return v < 0 ? 0 : v > 255 ? 255 : v;
}

function link(gl: WebGL2RenderingContext, vs: string, fs: string): WebGLProgram {
  const make = (type: number, src: string) => {
    const s = gl.createShader(type)!;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "shader");
    return s;
  };
  const p = gl.createProgram()!;
  gl.attachShader(p, make(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, make(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? "link");
  return p;
}

/* ------------------------------------------------------------------ */
/* recorder: the subset of the 2D context the heavy layers use          */
/* ------------------------------------------------------------------ */

const M = 0;
const L = 1;
const A = 2;
const Q = 3;
const Z = 4;
const E = 5;
const TAU = Math.PI * 2;

/** Stand-in for Path2D: records moveTo/lineTo/arc/quadraticCurveTo/closePath. */
export class GLPath {
  c: number[] = [];
  moveTo(x: number, y: number) {
    this.c.push(M, x, y);
  }
  lineTo(x: number, y: number) {
    this.c.push(L, x, y);
  }
  arc(x: number, y: number, r: number, a0: number, a1: number, ccw = false) {
    this.c.push(A, x, y, r, a0, a1, ccw ? 1 : 0);
  }
  quadraticCurveTo(qx: number, qy: number, x: number, y: number) {
    this.c.push(Q, qx, qy, x, y);
  }
  closePath() {
    this.c.push(Z);
  }
  ellipse(x: number, y: number, rx: number, ry: number, rot: number, a0: number, a1: number) {
    this.c.push(E, x, y, rx, ry, rot, a0, a1);
  }
}

type RecState = {
  lineWidth: number;
  strokeStyle: unknown;
  fillStyle: unknown;
  globalAlpha: number;
  lineCap: CanvasLineCap;
  lineJoin: CanvasLineJoin;
  font: string;
  textAlign: CanvasTextAlign;
  textBaseline: CanvasTextBaseline;
  letterSpacing: string;
  dash: number[];
  m: [number, number, number, number, number, number];
};

/**
 * Records strokes and fills for a GLPainter. Exposes the same names as the 2D context for the
 * calls the heavy layers make, so the drawing code can be pointed at it unchanged. Text and
 * other unsupported calls set painter.unsupported (the study then draws with Canvas 2D).
 */
export class GLRec {
  painter: GLPainter;
  private s: RecState = {
    lineWidth: 1,
    strokeStyle: "#000",
    fillStyle: "#000",
    globalAlpha: 1,
    lineCap: "butt",
    lineJoin: "miter",
    font: "10px sans-serif",
    textAlign: "start",
    textBaseline: "alphabetic",
    letterSpacing: "0px",
    dash: [],
    m: [1, 0, 0, 1, 0, 0],
  };
  private stack: RecState[] = [];
  private cur = new GLPath();

  constructor(painter: GLPainter) {
    this.painter = painter;
  }

  get lineWidth() { return this.s.lineWidth; }
  set lineWidth(v: number) { if (v > 0 && Number.isFinite(v)) this.s.lineWidth = v; }
  get strokeStyle() { return this.s.strokeStyle as string; }
  set strokeStyle(v: unknown) { this.s.strokeStyle = v; }
  get fillStyle() { return this.s.fillStyle as string; }
  set fillStyle(v: unknown) { this.s.fillStyle = v; }
  get globalAlpha() { return this.s.globalAlpha; }
  set globalAlpha(v: number) { if (v >= 0 && v <= 1) this.s.globalAlpha = v; }
  get lineCap() { return this.s.lineCap; }
  set lineCap(v: CanvasLineCap) { this.s.lineCap = v; }
  get lineJoin() { return this.s.lineJoin; }
  set lineJoin(v: CanvasLineJoin) { this.s.lineJoin = v; }
  get font() { return this.s.font; }
  set font(v: string) { this.s.font = v; }
  get textAlign() { return this.s.textAlign; }
  set textAlign(v: CanvasTextAlign) { this.s.textAlign = v; }
  get textBaseline() { return this.s.textBaseline; }
  set textBaseline(v: CanvasTextBaseline) { this.s.textBaseline = v; }
  get letterSpacing() { return this.s.letterSpacing; }
  set letterSpacing(v: string) { this.s.letterSpacing = v; }
  setLineDash(d: number[]) { this.s.dash = d.slice(); }
  getLineDash() { return this.s.dash.slice(); }
  setTransform(a: number, b: number, c: number, d: number, e: number, f: number) {
    this.s.m = [a, b, c, d, e, f];
  }
  save() {
    this.stack.push({ ...this.s, dash: this.s.dash.slice(), m: [...this.s.m] as RecState["m"] });
  }
  restore() {
    const t = this.stack.pop();
    if (t) this.s = t;
  }
  beginPath() {
    this.cur = new GLPath();
  }
  moveTo(x: number, y: number) { this.cur.moveTo(x, y); }
  lineTo(x: number, y: number) { this.cur.lineTo(x, y); }
  arc(x: number, y: number, r: number, a0: number, a1: number, ccw = false) { this.cur.arc(x, y, r, a0, a1, ccw); }
  quadraticCurveTo(qx: number, qy: number, x: number, y: number) { this.cur.quadraticCurveTo(qx, qy, x, y); }
  closePath() { this.cur.closePath(); }
  fillText() { this.fail("fillText"); }
  fillRect() { this.fail("fillRect"); }
  strokeRect() { this.fail("strokeRect"); }
  ellipse(x: number, y: number, rx: number, ry: number, rot: number, a0: number, a1: number) {
    this.cur.ellipse(x, y, rx, ry, rot, a0, a1);
  }
  drawImage() { this.fail("drawImage"); }
  measureText(): TextMetrics { this.fail("measureText"); return { width: 0 } as TextMetrics; }
  translate() { this.fail("translate"); }
  rotate() { this.fail("rotate"); }

  private fail(what: string) {
    if (!this.painter.unsupported) this.painter.unsupported = what;
  }

  stroke(path?: GLPath | Path2D) {
    this.emit(path instanceof GLPath ? path : path ? null : this.cur, true);
  }
  fill(path?: GLPath | Path2D) {
    this.emit(path instanceof GLPath ? path : path ? null : this.cur, false);
  }

  private emit(path: GLPath | null, stroke: boolean) {
    if (!path) return this.fail("Path2D");
    const paint = parsePaint(stroke ? this.s.strokeStyle : this.s.fillStyle);
    if (!paint) return this.fail("non-colour style");
    const alpha = paint.a * this.s.globalAlpha;
    const c = path.c;
    // an alpha that rounds to 0 in 8 bits paints nothing on Ganesh either
    if (!c.length || alpha8(alpha) === 0) return;
    const P = this.painter;
    const [ma, mb, mc, md, me, mf] = this.s.m;
    const scale = Math.sqrt(Math.abs(ma * md - mb * mc));
    const tx = (x: number, y: number) => ma * x + mc * y + me;
    const ty = (x: number, y: number) => mb * x + md * y + mf;
    const hw = (this.s.lineWidth * scale) / 2;
    const dash = this.s.dash;
    let dashOn = 0;
    let dashOff = 0;
    if (stroke && dash.length) {
      if (dash.length !== 2) return this.fail("dash pattern");
      dashOn = dash[0]! * scale;
      dashOff = dash[1]! * scale;
    }
    if (stroke && this.s.lineCap !== "round") return this.fail("line cap");
    const g = P.newGroup();
    const first = P.count;
    let hasBez = false;
    let hasOther = false;
    let cx = 0;
    let cy = 0;
    let sx = 0;
    let sy = 0;
    let has = false;
    const seg = (x0: number, y0: number, x1: number, y1: number) => {
      if (dashOn) return this.fail("dashed line");
      hasOther = true;
      P.prim(T_SEG, tx(x0, y0), ty(x0, y0), tx(x1, y1), ty(x1, y1), 0, 0, 0, hw, paint.rgb, alpha, g);
    };
    for (let i = 0; i < c.length; ) {
      const op = c[i]!;
      if (op === M) {
        cx = sx = c[i + 1]!;
        cy = sy = c[i + 2]!;
        has = true;
        i += 3;
      } else if (op === L) {
        const x = c[i + 1]!;
        const y = c[i + 2]!;
        if (has && stroke) seg(cx, cy, x, y);
        else if (!has) {
          sx = x;
          sy = y;
        } else if (!stroke) return this.fail("filled polygon");
        cx = x;
        cy = y;
        has = true;
        i += 3;
      } else if (op === A) {
        const x = c[i + 1]!;
        const y = c[i + 2]!;
        const r = c[i + 3]!;
        const a0 = c[i + 4]!;
        const a1 = c[i + 5]!;
        const ax = x + r * Math.cos(a0);
        const ay = y + r * Math.sin(a0);
        if (has && (Math.abs(cx - ax) > 1e-9 || Math.abs(cy - ay) > 1e-9)) {
          if (stroke) seg(cx, cy, ax, ay);
          else return this.fail("filled polygon");
        }
        if (!has) {
          sx = ax;
          sy = ay;
        }
        if (Math.abs(a1 - a0) < TAU - 1e-9) return this.fail("partial arc");
        hasOther = true;
        const X = tx(x, y);
        const Y = ty(x, y);
        if (stroke) P.prim(T_RING, X, Y, X, Y, 0, 0, r * scale, hw, paint.rgb, alpha, g, dashOn, dashOff);
        // a filled circle is a zero-length band of half width r
        else P.prim(T_SEG, X, Y, X, Y, 0, 0, 0, r * scale, paint.rgb, alpha, g);
        cx = x + r * Math.cos(a1);
        cy = y + r * Math.sin(a1);
        has = true;
        i += 7;
      } else if (op === Q) {
        const qx = c[i + 1]!;
        const qy = c[i + 2]!;
        const x = c[i + 3]!;
        const y = c[i + 4]!;
        if (!stroke) return this.fail("filled curve");
        if (dashOn) return this.fail("dashed curve");
        if (!has) {
          cx = sx = qx;
          cy = sy = qy;
        }
        hasBez = true;
        P.prim(T_BEZ, tx(cx, cy), ty(cx, cy), tx(x, y), ty(x, y), tx(qx, qy), ty(qx, qy), 0, hw, paint.rgb, alpha, g);
        cx = x;
        cy = y;
        has = true;
        i += 5;
      } else if (op === E) {
        const x = c[i + 1]!;
        const y = c[i + 2]!;
        const rx = c[i + 3]!;
        const ry = c[i + 4]!;
        if (c[i + 5] !== 0 || Math.abs(c[i + 7]! - c[i + 6]!) < TAU - 1e-9 || has || mb !== 0 || mc !== 0)
          return this.fail("ellipse form");
        hasOther = true;
        P.prim(stroke ? T_ELL : T_ELLF, tx(x, y), ty(x, y), tx(x, y), ty(x, y), ry * md, 0, rx * ma, hw, paint.rgb, alpha, g);
        cx = x + rx * Math.cos(c[i + 7]!);
        cy = y;
        has = true;
        i += 8;
      } else {
        if (has && stroke && (cx !== sx || cy !== sy)) seg(cx, cy, sx, sy);
        cx = sx;
        cy = sy;
        i += 1;
      }
    }
    const last = P.count;
    if (hasBez && hasOther) return this.fail("mixed path");
    P.queue(first, last, hasBez, g);
  }
}
