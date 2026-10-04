// SPDX-License-Identifier: AGPL-3.0-only
import * as THREE from "three";
import { mulberry32 } from "./flightState";

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

function finish(c: HTMLCanvasElement): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.needsUpdate = true;
  return t;
}

/** Soft radial disc — used for halos, sun glow and the landing ring. */
export function makeRadialTexture(stops: Array<[number, string]>, size = 128): THREE.CanvasTexture {
  const c = canvas(size, size);
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [o, col] of stops) g.addColorStop(o, col);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return finish(c);
}

/** Puffy cumulus: several overlapping soft blobs, slightly warmer at the base. */
export function makeCloudTexture(seed = 7): THREE.CanvasTexture {
  const size = 256;
  const c = canvas(size, size);
  const ctx = c.getContext("2d")!;
  const rnd = mulberry32(seed);
  const blobs = 9;
  for (let i = 0; i < blobs; i++) {
    const x = size * (0.28 + rnd() * 0.44);
    const y = size * (0.32 + rnd() * 0.36);
    const r = size * (0.16 + rnd() * 0.17);
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    const warm = y > size * 0.5;
    g.addColorStop(0, warm ? "rgba(255,250,242,0.55)" : "rgba(255,255,255,0.62)");
    g.addColorStop(0.55, "rgba(255,255,255,0.28)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
  }
  return finish(c);
}

/** Label sprite: main text (name) + small caption (count), ink on transparent. */
export function makeLabelTexture(text: string, caption: string): THREE.CanvasTexture {
  const w = 768;
  const h = 192;
  const c = canvas(w, h);
  const ctx = c.getContext("2d")!;
  ctx.clearRect(0, 0, w, h);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(20,32,44,0.88)";
  ctx.font = '500 58px "IBM Plex Sans KR", "Noto Sans KR", "Rubik", sans-serif';
  ctx.fillText(text, w / 2, h / 2 - 22, w - 40);
  ctx.fillStyle = "rgba(20,32,44,0.55)";
  ctx.font = '400 30px "Space Mono", "Nanum Gothic Coding", monospace';
  ctx.fillText(caption, w / 2, h / 2 + 42, w - 40);
  return finish(c);
}

/** Radiating light streaks around the sun — soft god rays. */
export function makeRaysTexture(seed = 5): THREE.CanvasTexture {
  const size = 512;
  const c = canvas(size, size);
  const ctx = c.getContext("2d")!;
  const rnd = mulberry32(seed);
  const cx = size / 2;
  const cy = size / 2;
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 44; i++) {
    const a = rnd() * Math.PI * 2;
    const half = 0.012 + rnd() * 0.05;
    const len = size * (0.32 + rnd() * 0.18);
    const alpha = 0.05 + rnd() * 0.11;
    const g = ctx.createLinearGradient(cx, cy, cx + Math.cos(a) * len, cy + Math.sin(a) * len);
    g.addColorStop(0, `rgba(255,236,205,${alpha})`);
    g.addColorStop(0.45, `rgba(255,236,205,${alpha * 0.55})`);
    g.addColorStop(1, "rgba(255,236,205,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(a - half) * len, cy + Math.sin(a - half) * len);
    ctx.lineTo(cx + Math.cos(a + half) * len, cy + Math.sin(a + half) * len);
    ctx.closePath();
    ctx.fill();
  }
  return finish(c);
}
