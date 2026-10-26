#!/usr/bin/env python3
"""Create or update a single pipeline comment on a pull request, identified by a marker.

Usage: pr_comment.py --marker ndpx-ai-review --body-file review.md
Env:   GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def gh(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method, data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read()
        return json.loads(raw) if raw else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marker", required=True)
    ap.add_argument("--body-file", required=True)
    args = ap.parse_args(argv)
    repo, pr = os.environ["GITHUB_REPOSITORY"], os.environ["PR_NUMBER"]
    tag = f"<!-- {args.marker} -->"
    with open(args.body_file, encoding="utf-8") as fh:
        body = fh.read()
    if tag not in body:
        body = f"{tag}\n{body}"
    page, existing = 1, None
    while existing is None:
        batch = gh("GET", f"/repos/{repo}/issues/{pr}/comments?per_page=100&page={page}") or []
        existing = next((c for c in batch if tag in c.get("body", "")
                         and c.get("user", {}).get("type") == "Bot"), None)
        if len(batch) < 100:
            break
        page += 1
    if existing:
        gh("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}", {"body": body})
    else:
        gh("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
    return 0


if __name__ == "__main__":
    sys.exit(main())
