#!/usr/bin/env python3
"""Pixel diff of two harness --det runs (measurement only; not part of the site build).

What: compares every PNG in <a>/shots with the same name in <b>/shots and prints, per shot,
the maximum and mean per-channel difference (0-255) and how many pixels differ by more than
0, 2 and 8 levels. With --heat DIR it writes an amplified difference image for each shot
that is not identical.
Why: an optimization of how the home canvas is drawn must not change what it draws.
Usage: python3 scripts/perf/diff.py <run-a> <run-b> [--heat DIR]
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
    a, b = Path(args[0]) / "shots", Path(args[1]) / "shots"
    worst = 0
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
    print(f"worst max difference: {worst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
