// SPDX-License-Identifier: AGPL-3.0-only
/** Drawing colours for the canvases: the sheet (paper), the line (ink) and the one accent. */

// The canvases ask for the same few colours tens of thousands of times a frame. Alpha is
// quantised to 1/256 (one 8-bit step) and each (colour, step) string is built once.
const ALPHA_STEPS = 256;
const alphaTables = new Map<string, Array<string | undefined>>();

export function withAlpha(color: string, a: number): string {
  let table = alphaTables.get(color);
  if (table === undefined) {
    table = new Array(ALPHA_STEPS + 1);
    alphaTables.set(color, table);
  }
  const q = a <= 0 ? 0 : a >= 1 ? ALPHA_STEPS : (a * ALPHA_STEPS + 0.5) | 0;
  const hit = table[q];
  if (hit !== undefined) return hit;
  const out = buildAlpha(color, q / ALPHA_STEPS);
  table[q] = out;
  return out;
}

function buildAlpha(color: string, a: number): string {
  const c = color.trim();
  if (c.startsWith("oklch(") || c.startsWith("rgb(") || c.startsWith("hsl(")) {
    const inner = c.slice(c.indexOf("(") + 1, c.lastIndexOf(")"));
    const base = inner.split("/")[0]!.trim();
    return `${c.slice(0, c.indexOf("("))}(${base} / ${a})`;
  }
  if (c.startsWith("#") && (c.length === 7 || c.length === 4)) {
    const hex = c.length === 4 ? c.replace(/./g, (ch, i) => (i ? ch + ch : ch)) : c;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
  return c;
}

export function readTheme(): { paper: string; ink: string; accent: string; dark: boolean } {
  const cs = getComputedStyle(document.documentElement);
  const dark =
    document.documentElement.classList.contains("dark") ||
    document.documentElement.getAttribute("data-theme") === "dark";
  if (dark) {
    // The drawing sheet becomes a blueprint after dark.
    return { paper: "#10233d", ink: "#e9f1fb", accent: "#ffd27a", dark: true };
  }
  return {
    paper: cs.getPropertyValue("--background").trim() || "#f1f2ef",
    ink: cs.getPropertyValue("--foreground").trim() || "#181a1c",
    accent: cs.getPropertyValue("--primary").trim() || "#5b3fd6",
    dark: false,
  };
}
