#!/usr/bin/env python3
"""Pixel diff of two harness --det runs (measurement only; not part of the site build).

What: compares every PNG in <a>/shots with the same name in <b>/shots and prints, per shot,
the maximum and mean per-channel difference (0-255) and how many pixels differ by more than
0, 2 and 8 levels. With --heat DIR it writes an amplified difference image for each shot
that is not identical. With --worst N it also writes, for the N shots with the most pixels
off by more than 8 levels, a side-by-side image (a | b | difference x8, enlarged) of the
240 px square that holds the most of those pixels (into the --heat directory).
Why: an optimization of how the home canvas is drawn, or another renderer (Canvas 2D against
WebGL), must not change what it draws.
Usage: python3 scripts/perf/diff.py <run-a> <run-b> [--heat DIR] [--worst N]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> int:
    args = sys.argv[1:]
    heat = None
    if "--heat" in args:
        i = args.index("--heat")
        heat = Path(args[i + 1])
        heat.mkdir(parents=True, exist_ok=True)
        del args[i : i + 2]
    nworst = 0
    if "--worst" in args:
        i = args.index("--worst")
        nworst = int(args[i + 1])
        del args[i : i + 2]
    a, b = Path(args[0]) / "shots", Path(args[1]) / "shots"
    worst = 0
    ranked = []
    print(f"{'shot':<22}{'max':>5}{'mean':>10}{'>0':>9}{'>2':>8}{'>8':>8}")
    for pa in sorted(a.glob("*.png")):
        pb = b / pa.name
        if not pb.exists():
            print(f"{pa.stem:<22} missing in b")
            continue
        x = np.asarray(Image.open(pa).convert("RGB"), dtype=np.int16)
        y = np.asarray(Image.open(pb).convert("RGB"), dtype=np.int16)
        if x.shape != y.shape:
            print(f"{pa.stem:<22} size differs {x.shape} {y.shape}")
            continue
        d = np.abs(x - y).max(axis=2)
        mx = int(d.max())
        worst = max(worst, mx)
        print(
            f"{pa.stem:<22}{mx:>5}{d.mean():>10.5f}{int((d > 0).sum()):>9}"
            f"{int((d > 2).sum()):>8}{int((d > 8).sum()):>8}"
        )
        if heat and mx > 0:
            img = np.clip(d * 32, 0, 255).astype(np.uint8)
            Image.fromarray(img).save(heat / pa.name)
        ranked.append((int((d > 8).sum()), pa.stem, x, y, d))
    print(f"worst max difference: {worst}")
    if heat and nworst:
        for n8, stem, x, y, d in sorted(ranked, key=lambda r: -r[0])[:nworst]:
            if n8 == 0:
                continue
            k = 240
            m = (d > 8).astype(np.int64).cumsum(0).cumsum(1)
            m = np.pad(m, ((1, 0), (1, 0)))
            win = m[k:, k:] - m[:-k, k:] - m[k:, :-k] + m[:-k, :-k]
            y0, x0 = np.unravel_index(np.argmax(win), win.shape)
            tiles = [x[y0 : y0 + k, x0 : x0 + k], y[y0 : y0 + k, x0 : x0 + k]]
            hd = np.clip(d[y0 : y0 + k, x0 : x0 + k] * 8, 0, 255)
            tiles.append(np.stack([hd] * 3, axis=2))
            row = np.concatenate([np.pad(t, ((0, 0), (0, 8), (0, 0)), constant_values=255) for t in tiles], axis=1)
            im = Image.fromarray(row.astype(np.uint8))
            im = im.resize((im.width * 3, im.height * 3), Image.NEAREST)
            im.save(heat / f"worst-{stem}-x{x0}-y{y0}.png")
            print(f"worst region of {stem}: x={x0} y={y0} ({n8} px > 8)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
