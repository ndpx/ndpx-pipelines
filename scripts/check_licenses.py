#!/usr/bin/env python3
"""Check npm dependency licences against the ndpx allowlist (check C10).

Reads package-lock.json (lockfile v2/v3). Production and development dependencies
are both checked, because both are redistributed or executed by ndpx.

Usage: check_licenses.py --lockfile package-lock.json --allow config/licenses.txt [--exceptions .ndpx/license-exceptions.txt]
"""
from __future__ import annotations

import argparse
import json
import re
import sys


def read_list(path: str | None) -> set[str]:
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as fh:
            return {ln.split("#", 1)[0].strip() for ln in fh if ln.split("#", 1)[0].strip()}
    except FileNotFoundError:
        return set()


def licence_ok(expr: str, allowed: set[str]) -> bool:
    """Accept an SPDX expression if any OR-branch is fully allowed (AND requires all parts)."""
    expr = expr.strip().strip("()")
    for branch in re.split(r"\s+OR\s+", expr, flags=re.I):
        parts = [p.strip("() ") for p in re.split(r"\s+AND\s+", branch, flags=re.I)]
        if all(p in allowed for p in parts if p):
            return True
    return False


def check(lock: dict, allowed: set[str], exceptions: set[str]) -> list[str]:
    problems: list[str] = []
    for path, meta in (lock.get("packages") or {}).items():
        if not path:  # root project
            continue
        name = path.split("node_modules/")[-1]
        version = meta.get("version", "?")
        if name in exceptions or f"{name}@{version}" in exceptions:
            continue
        lic = meta.get("license")
        if isinstance(lic, dict):
            lic = lic.get("type")
        if not lic:
            problems.append(f"{name}@{version}: no licence declared")
        elif not licence_ok(str(lic), allowed):
            problems.append(f"{name}@{version}: {lic}")
    return sorted(set(problems))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lockfile", default="package-lock.json")
    ap.add_argument("--allow", required=True)
    ap.add_argument("--exceptions")
    args = ap.parse_args(argv)
    with open(args.lockfile, encoding="utf-8") as fh:
        lock = json.load(fh)
    problems = check(lock, read_list(args.allow), read_list(args.exceptions))
    if problems:
        for p in problems:
            print(f"::error::Licence not on allowlist: {p}")
        print("Add a reviewed exception to .ndpx/license-exceptions.txt only with IT Manager approval.")
        return 1
    print("Licence check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
