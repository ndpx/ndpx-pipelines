#!/usr/bin/env python3
"""Check security headers on a deployed site, and optionally that it serves the app (smoke test).

Usage: check_headers.py --url https://example.netlify.app [--smoke]
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request

REQUIRED = {
    "strict-transport-security": lambda v: "max-age=" in v and int(v.split("max-age=")[1].split(";")[0]) >= 15552000,
    "content-security-policy": lambda v: "default-src" in v and "frame-ancestors" in v,
    "x-content-type-options": lambda v: v.lower() == "nosniff",
    "referrer-policy": lambda v: bool(v),
}


def fetch(url: str, attempts: int = 5) -> tuple[int, dict[str, str], str]:
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ndpx-pipeline-check"})
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (URL supplied by pipeline)
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, headers, resp.read(200_000).decode("utf-8", errors="ignore")
        except Exception as exc:  # retry while a fresh deploy propagates
            last = exc
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"Could not fetch {url}: {last}")


def evaluate(status: int, headers: dict[str, str], body: str, smoke: bool) -> list[str]:
    problems = []
    for name, ok in REQUIRED.items():
        value = headers.get(name)
        if value is None:
            problems.append(f"missing header {name}")
            continue
        try:
            if not ok(value):
                problems.append(f"weak header {name}: {value}")
        except (ValueError, IndexError):
            problems.append(f"unparseable header {name}: {value}")
    if smoke:
        if status != 200:
            problems.append(f"status {status}, expected 200")
        if 'id="root"' not in body:
            problems.append("page does not contain the application root element")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)
    status, headers, body = fetch(args.url)
    problems = evaluate(status, headers, body, args.smoke)
    for p in problems:
        print(f"::error::{args.url}: {p}")
    if not problems:
        print(f"{args.url}: headers{' and smoke test' if args.smoke else ''} passed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
