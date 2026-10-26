#!/usr/bin/env python3
"""Netlify helpers for rollback.

current  : print the currently published deploy id (captured before a production deploy)
restore  : republish a previous deploy (rollback)

Usage: netlify_site.py current --site <site id>
       netlify_site.py restore --site <site id> --deploy <deploy id>
Env:   NETLIFY_AUTH_TOKEN
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

API = "https://api.netlify.com/api/v1"


def call(method: str, path: str) -> dict:
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 headers={"Authorization": f"Bearer {os.environ['NETLIFY_AUTH_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["current", "restore"])
    ap.add_argument("--site", required=True)
    ap.add_argument("--deploy")
    args = ap.parse_args(argv)
    if args.action == "current":
        site = call("GET", f"/sites/{args.site}")
        print((site.get("published_deploy") or {}).get("id", ""))
        return 0
    if not args.deploy:
        print("::error::No previous deploy id; cannot roll back the frontend automatically.")
        return 1
    call("POST", f"/sites/{args.site}/deploys/{args.deploy}/restore")
    print(f"Restored deploy {args.deploy}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
