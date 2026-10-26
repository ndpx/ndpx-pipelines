#!/usr/bin/env python3
"""Enforce the line-coverage minimum for the app's risk tier (checks C04/C05).

Tool-agnostic: reads the Istanbul json-summary that Vitest and Jest both produce
(`coverage/coverage-summary.json`). The app's `test:coverage` script must write it.

Usage: check_coverage.py --summary coverage/coverage-summary.json --min 70
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def line_pct(summary: dict) -> float:
    total = summary.get("total", {}).get("lines", {})
    if "pct" not in total:
        raise ValueError("coverage summary has no total.lines.pct")
    pct = total["pct"]
    return 100.0 if pct == "Unknown" else float(pct)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default="coverage/coverage-summary.json")
    ap.add_argument("--min", type=float, required=True)
    args = ap.parse_args(argv)
    if not os.path.exists(args.summary):
        print(f"::error::{args.summary} not found. The 'test:coverage' script must write an Istanbul json-summary "
              "(Vitest: coverage.reporter includes 'json-summary'; Jest: --coverageReporters=json-summary).")
        return 1
    with open(args.summary, encoding="utf-8") as fh:
        pct = line_pct(json.load(fh))
    print(f"Line coverage {pct:.2f}% (minimum {args.min:.0f}%).")
    if pct < args.min:
        print(f"::error::Line coverage {pct:.2f}% is below the minimum of {args.min:.0f}% for this risk tier.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
