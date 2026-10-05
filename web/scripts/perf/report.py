#!/usr/bin/env python3
"""Per-phase Canvas 2D call report for one or two harness runs (measurement only).

What: reads <run>/frames.json written by harness.mjs --count and prints, per phase, the mean
per painted frame and per animation frame of draw calls (stroke, fill, fillText, strokeText,
drawImage, fillRect, strokeRect, clearRect), all context calls (draws, path commands and state
sets on the context) and Path2D building commands, plus the worst frame. With two runs it
prints them side by side with the ratio a/b.
Why: the stutter on Chrome/Ganesh tracks the number of draw calls per frame.
Usage: python3 scripts/perf/report.py <run-a> [<run-b>]
"""
import json
import sys
from pathlib import Path

DRAWS = {"stroke", "fill", "fillText", "strokeText", "drawImage", "fillRect", "strokeRect", "clearRect"}
ORDER = ["intro", "idle", "hover", "select", "stage1", "stage2", "stage3", "stage4", "stage0"]


def phases(run: str):
    d = json.loads((Path(run) / "frames.json").read_text())
    out = {}
    for ph in ORDER:
        fs = [f for f in d["frames"] if f["phase"] == ph]
        if not fs:
            continue
        rows = []
        for f in fs:
            c = f.get("calls") or {}
            draws = sum(v for k, v in c.items() if k in DRAWS)
            ctx = sum(v for k, v in c.items() if not k.startswith("path:"))
            path = sum(v for k, v in c.items() if k.startswith("path:"))
            rows.append((draws, ctx, path, f["total"] > 0))
        painted = [r for r in rows if r[3]] or [(0, 0, 0, False)]
        n, p = len(rows), len(painted)
        out[ph] = {
            "frames": n,
            "painted": sum(1 for r in rows if r[3]),
            "draws/painted": sum(r[0] for r in painted) / p,
            "ctx/painted": sum(r[1] for r in painted) / p,
            "path/painted": sum(r[2] for r in painted) / p,
            "draws/frame": sum(r[0] for r in rows) / n,
            "ctx/frame": sum(r[1] for r in rows) / n,
            "max draws": max(r[0] for r in rows),
            "max ctx": max(r[1] for r in rows),
        }
    return out


def main() -> int:
    a = phases(sys.argv[1])
    b = phases(sys.argv[2]) if len(sys.argv) > 2 else None
    keys = ["painted", "draws/painted", "ctx/painted", "path/painted", "draws/frame", "ctx/frame", "max draws", "max ctx"]
    for ph, ra in a.items():
        print(f"== {ph} ({ra['frames']} frames)")
        for k in keys:
            if b and ph in b:
                rb = b[ph][k]
                ratio = f"{ra[k] / rb:8.2f}x" if rb else "        -"
                print(f"  {k:<15}{ra[k]:>12.0f}{rb:>12.0f}{ratio}")
            else:
                print(f"  {k:<15}{ra[k]:>12.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
