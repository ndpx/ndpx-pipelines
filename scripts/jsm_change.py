#!/usr/bin/env python3
"""Jira Service Management change gate (check C29) and outcome recording.

verify: fails unless the Change Request
  - exists and is a change issue type,
  - is in an approved status,
  - names the exact commit being deployed, and
  - (if window fields are configured) the current time is inside the approved window.

record: adds a comment to the Change Request with the deployment outcome.

Usage:
  jsm_change.py verify --issue CHG-123 --commit <sha>
  jsm_change.py record --issue CHG-123 --commit <sha> --result success|failed|rolled-back --url <deploy url> --run <run url>

Env:
  JSM_BASE_URL            e.g. https://ndpx.atlassian.net
  JSM_EMAIL, JSM_API_TOKEN  service account credentials
  JSM_APPROVED_STATUSES   comma list, default "Awaiting implementation,Implementing,Approved"
  JSM_COMMIT_FIELD        optional custom field id holding the commit SHA (e.g. customfield_10120).
                          If unset, the SHA must appear in the summary or description.
  JSM_WINDOW_START_FIELD, JSM_WINDOW_END_FIELD  optional custom field ids for the planned window
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def api(method: str, path: str, body: dict | None = None) -> dict:
    base = os.environ["JSM_BASE_URL"].rstrip("/")
    auth = base64.b64encode(f"{os.environ['JSM_EMAIL']}:{os.environ['JSM_API_TOKEN']}".encode()).decode()
    req = urllib.request.Request(
        f"{base}{path}", method=method, data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read()
        return json.loads(raw) if raw else {}


def text_of(value) -> str:
    """Flatten a string or Atlassian Document Format value to plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(text_of(v) for k, v in value.items() if k in ("text", "content"))
    if isinstance(value, list):
        return " ".join(text_of(v) for v in value)
    return str(value)


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
    return dt.datetime.fromisoformat(v)


def evaluate(issue: dict, commit: str, approved: set[str], commit_field: str | None,
             start_field: str | None, end_field: str | None, now: dt.datetime) -> list[str]:
    fields = issue.get("fields", {})
    problems = []
    itype = (fields.get("issuetype") or {}).get("name", "")
    if "change" not in itype.lower():
        problems.append(f"issue type is '{itype}', not a Change")
    status = (fields.get("status") or {}).get("name", "")
    if status.lower() not in approved:
        problems.append(f"status is '{status}', not approved ({', '.join(sorted(approved))})")
    short = commit[:7].lower()
    if commit_field:
        recorded = text_of(fields.get(commit_field)).strip().lower()
        if not recorded or not (commit.lower().startswith(recorded) and len(recorded) >= 7):
            problems.append(f"commit field {commit_field} is '{recorded or 'empty'}', expected {commit}")
    else:
        haystack = (text_of(fields.get("summary")) + " " + text_of(fields.get("description"))).lower()
        if short not in haystack:
            problems.append(f"commit {short} is not named in the Change Request summary or description")
    if start_field and end_field:
        start, end = parse_time(fields.get(start_field)), parse_time(fields.get(end_field))
        if not start or not end:
            problems.append("planned start or end is not set")
        elif not (start <= now <= end):
            problems.append(f"outside the approved window {start.isoformat()} to {end.isoformat()}")
    return problems


def cmd_verify(args) -> int:
    if not ISSUE_RE.match(args.issue):
        print(f"::error::'{args.issue}' is not a valid Jira issue key.")
        return 1
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        print("::error::commit must be a full 40-character SHA.")
        return 1
    approved = {s.strip().lower() for s in os.environ.get(
        "JSM_APPROVED_STATUSES", "Awaiting implementation,Implementing,Approved").split(",") if s.strip()}
    try:
        issue = api("GET", f"/rest/api/2/issue/{args.issue}")
    except urllib.error.HTTPError as exc:
        print(f"::error::Could not read {args.issue}: HTTP {exc.code}")
        return 1
    problems = evaluate(issue, args.commit, approved, os.environ.get("JSM_COMMIT_FIELD") or None,
                        os.environ.get("JSM_WINDOW_START_FIELD") or None, os.environ.get("JSM_WINDOW_END_FIELD") or None,
                        dt.datetime.now(dt.timezone.utc))
    if problems:
        for p in problems:
            print(f"::error::{args.issue}: {p}")
        print("Production deployment blocked: the Change Request is not approved for this release.")
        return 1
    print(f"{args.issue} approved for commit {args.commit[:7]}.")
    return 0


def cmd_record(args) -> int:
    body = (f"Deployment {args.result.upper()} via pipeline.\n"
            f"Commit: {args.commit}\nSite: {args.url or 'n/a'}\nPipeline run: {args.run or 'n/a'}\n"
            f"Recorded: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}")
    try:
        api("POST", f"/rest/api/2/issue/{args.issue}/comment", {"body": body})
    except urllib.error.HTTPError as exc:
        print(f"::warning::Could not record outcome on {args.issue}: HTTP {exc.code}")
        return 1
    print(f"Outcome recorded on {args.issue}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--issue", required=True)
    v.add_argument("--commit", required=True)
    r = sub.add_parser("record")
    r.add_argument("--issue", required=True)
    r.add_argument("--commit", required=True)
    r.add_argument("--result", required=True, choices=["success", "failed", "rolled-back"])
    r.add_argument("--url")
    r.add_argument("--run")
    args = ap.parse_args(argv)
    return cmd_verify(args) if args.cmd == "verify" else cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
