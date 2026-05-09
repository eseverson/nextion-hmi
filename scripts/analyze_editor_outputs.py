#!/usr/bin/env python3
"""analyze_editor_outputs.py — run all configured diffs across
`tests/editor outputs/` and print a single combined report.

For each experiment subfolder that contains an `after.HMI`/`after.tft`
pair (or `iter*.HMI`/`iter*.tft` for the 13_save_six_times case),
compare against `00_baseline/baseline.*` using the existing diff
helpers and summarise.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXP_DIR = REPO_ROOT / "tests" / "editor outputs"
BASELINE_DIR = EXP_DIR / "00_baseline"


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        return f"[FAILED {e.returncode}]\n{e.output}"
    except FileNotFoundError as e:
        return f"[FILE NOT FOUND]: {e}"


def _baseline_files():
    bl_h = BASELINE_DIR / "baseline.HMI"
    bl_t = BASELINE_DIR / "baseline.tft"
    return bl_h, bl_t


def _experiment_pairs(exp_dir: Path):
    """Yield (label, hmi, tft) pairs found in an experiment folder."""
    # Standard pair
    a_h = exp_dir / "after.HMI"
    a_t = exp_dir / "after.tft"
    if a_h.exists() and a_t.exists():
        yield ("after", a_h, a_t)
        return
    # Iterations (13_save_six_times)
    for i in range(1, 20):
        ih = exp_dir / f"iter{i}.HMI"
        it = exp_dir / f"iter{i}.tft"
        if ih.exists() and it.exists():
            yield (f"iter{i}", ih, it)


def _analyze(exp_dir: Path) -> str:
    bl_h, bl_t = _baseline_files()
    if not (bl_h.exists() and bl_t.exists()):
        return f"  (no baseline at {BASELINE_DIR}; drop baseline.HMI/baseline.tft there first)"

    pairs = list(_experiment_pairs(exp_dir))
    if not pairs:
        return "  (no after.HMI/.tft yet)"

    out = []
    for label, h, t in pairs:
        out.append(f"  --- {label} ---")
        out.append("  TFT regions:")
        out.append(_run([sys.executable, str(REPO_ROOT / "scripts" / "diff_tft.py"),
                         str(bl_t), str(t)]))
        out.append("  HMI directory:")
        out.append(_run([sys.executable, str(REPO_ROOT / "scripts" / "diff_hmi.py"),
                         str(bl_h), str(h)]))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*",
                    help="Run only the named experiments (e.g. 01_orientation_flip).")
    ap.add_argument("--xor-h2", action="store_true",
                    help="Also dump full XOR(a_h2, b_h2) for each TFT pair.")
    args = ap.parse_args()

    if not EXP_DIR.exists():
        print(f"missing: {EXP_DIR}", file=sys.stderr)
        return 1

    selected = sorted(p for p in EXP_DIR.iterdir() if p.is_dir())
    selected = [p for p in selected if p.name != "00_baseline"]
    if args.only:
        selected = [p for p in selected if p.name in args.only]

    for exp in selected:
        print()
        print("=" * 78)
        print(f"# {exp.name}")
        print("=" * 78)
        instr = exp / "instructions.md"
        if instr.exists():
            # Print just the goal
            for line in instr.read_text().splitlines():
                if line.startswith("## Goal"):
                    continue
                if line.startswith("##"):
                    break
                if line.strip():
                    print(line.strip())
                    break
        print()
        print(_analyze(exp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
