#!/usr/bin/env python3
"""Gate for the AI security review (check C16).

Fails when findings at or above the blocking severity exist, unless a Code Reviewer has
applied the acceptance label. The label only counts when applied by someone on the
Code Reviewer list, and not by the PR author. This stops a builder from accepting their own findings.

Writes a markdown report for the PR comment and step summary.

Usage: ai_gate.py --findings findings.json --report review.md
Env:   GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, PR_AUTHOR, CODE_REVIEWERS (comma-separated logins)
       ACCEPT_LABEL (default ai-findings-accepted), BLOCK_AT (default high)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ORDER = ["info", "low", "medium", "high", "critical"]


def label_events(repo: str, pr: str, token: str) -> list[dict]:
    events: list[dict] = []
    page = 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/issues/{pr}/events?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            batch = json.loads(resp.read())
        events.extend(batch)
        if len(batch) < 100:
            return events
        page += 1


def accepted_by(events: list[dict], label: str, reviewers: set[str], author: str) -> str | None:
    """Return the login of the reviewer whose label application is still in force, if any."""
    current: str | None = None
    for ev in events:
        if (ev.get("label") or {}).get("name") != label:
            continue
        actor = (ev.get("actor") or {}).get("login", "")
        if ev.get("event") == "labeled":
            current = actor if (actor.lower() in reviewers and actor.lower() != author.lower()) else None
        elif ev.get("event") == "unlabeled":
            current = None
    return current


def blocking(findings: list[dict], block_at: str) -> list[dict]:
    threshold = ORDER.index(block_at)
    return [f for f in findings if ORDER.index(f.get("severity", "info")) >= threshold]


def render(result: dict, blockers: list[dict], accepted: str | None) -> str:
    lines = ["<!-- ndpx-ai-review -->", "### AI security review", ""]
    if result.get("skipped"):
        lines.append(f"_{result.get('summary')}_")
        return "\n".join(lines) + "\n"
    lines.append(result.get("summary") or "")
    lines.append("")
    findings = sorted(result.get("findings", []), key=lambda f: -ORDER.index(f["severity"]))
    if not findings:
        lines.append("No findings.")
    else:
        lines += ["| Severity | Finding | Location | Fix |", "|---|---|---|---|"]
        for f in findings:
            loc = f"`{f['file']}:{f['line']}`" if f.get("file") else ""
            desc = f"**{f['title']}** — {f['description']}".replace("\n", " ").replace("|", "\\|")
            fix = f["recommendation"].replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {f['severity'].upper()} | {desc} | {loc} | {fix} |")
    lines.append("")
    if blockers and accepted:
        lines.append(f"Blocking findings accepted by Code Reviewer @{accepted}.")
    elif blockers:
        lines.append(f"**{len(blockers)} blocking finding(s).** Fix them, or a Code Reviewer may accept them by adding the "
                     "`ai-findings-accepted` label with a comment giving the reason.")
    lines.append("\n_AI review is advisory input to human review. It can miss issues and report false positives._")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", default="findings.json")
    ap.add_argument("--report", default="review.md")
    args = ap.parse_args(argv)
    with open(args.findings, encoding="utf-8") as fh:
        result = json.load(fh)

    block_at = os.environ.get("BLOCK_AT", "high").lower()
    blockers = blocking(result.get("findings", []), block_at)
    accepted = None
    if blockers:
        reviewers = {r.strip().lower() for r in os.environ.get("CODE_REVIEWERS", "").split(",") if r.strip()}
        events = label_events(os.environ["GITHUB_REPOSITORY"], os.environ["PR_NUMBER"], os.environ["GITHUB_TOKEN"])
        accepted = accepted_by(events, os.environ.get("ACCEPT_LABEL", "ai-findings-accepted"), reviewers,
                               os.environ.get("PR_AUTHOR", ""))

    report = render(result, blockers, accepted)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    if blockers and not accepted:
        print(f"::error::{len(blockers)} AI security finding(s) at or above '{block_at}' are unresolved.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
