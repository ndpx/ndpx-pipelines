#!/usr/bin/env python3
"""Design approval gate (check C47).

Tier 2 and 3 repositories must have a filled-in design note with a threat model.
A pull request that adds or changes the design note needs the `design-approved` label,
applied by a Code Reviewer who is not the pull request author.

Usage: design_gate.py --tier 2 --mode enforce --base SHA --head SHA
Env:   GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, PR_AUTHOR, CODE_REVIEWERS (comma-separated logins)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

from ai_gate import accepted_by, label_events

CANDIDATES = ["docs/DESIGN.md", "docs/design.md", "DESIGN.md", "design.md"]
TEMPLATE_MARKER = "ndpx:template"
THREAT_HEADING = re.compile(r"^#{1,4}\s*threat model", re.I | re.M)
LABEL = "design-approved"


def find_design(root: str = ".") -> str | None:
    for c in CANDIDATES:
        if os.path.isfile(os.path.join(root, c)):
            return c
    return None


def content_problems(path: str | None, text: str) -> list[str]:
    if path is None:
        return ["No design note. Add docs/DESIGN.md (copy the starter template) covering purpose, data, "
                "security requirements and a threat model."]
    problems = []
    if TEMPLATE_MARKER in text:
        problems.append(f"{path} is still the unfilled template. Complete it and remove the `{TEMPLATE_MARKER}` line.")
    if not THREAT_HEADING.search(text):
        problems.append(f"{path} has no 'Threat model' section.")
    return problems


def design_changed(path: str | None, base: str, head: str) -> bool:
    if not path or not base or not head:
        return False
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}", "--", path],
                         capture_output=True, text=True, check=False).stdout
    return bool(out.strip())


def evaluate(tier: int, path: str | None, text: str, changed: bool, approver: str | None) -> tuple[list[str], list[str]]:
    """Return (problems, notes)."""
    if tier < 2:
        return [], ["Tier 1: design note optional; gate not applied."]
    problems = content_problems(path, text)
    notes = []
    if changed:
        if approver:
            notes.append(f"Design change approved by @{approver}.")
        else:
            problems.append(f"This pull request changes {path}. A Code Reviewer who is not the author must review the "
                            f"design and add the `{LABEL}` label.")
    elif not problems:
        notes.append(f"{path} present with a threat model; design not changed in this pull request.")
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, required=True)
    ap.add_argument("--mode", default="enforce", choices=["enforce", "warn"])
    ap.add_argument("--base", default="")
    ap.add_argument("--head", default="")
    args = ap.parse_args(argv)

    path = find_design()
    text = open(path, encoding="utf-8", errors="ignore").read() if path else ""
    changed = design_changed(path, args.base, args.head)
    approver = None
    if changed and args.tier >= 2:
        reviewers = {r.strip().lower() for r in os.environ.get("CODE_REVIEWERS", "").split(",") if r.strip()}
        events = label_events(os.environ["GITHUB_REPOSITORY"], os.environ["PR_NUMBER"], os.environ["GITHUB_TOKEN"])
        approver = accepted_by(events, LABEL, reviewers, os.environ.get("PR_AUTHOR", ""))

    problems, notes = evaluate(args.tier, path, text, changed, approver)
    lines = ["### Design approval (C47)", ""] + [f"- {n}" for n in notes] + [f"- **{p}**" for p in problems]
    if problems and args.mode == "warn":
        lines.append("\n_Gate set to `warn` in .ndpx/pipeline.yml (temporary waiver). Not blocking._")
    report = "\n".join(lines) + "\n"
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    level = "error" if args.mode == "enforce" else "warning"
    for p in problems:
        print(f"::{level}::{p}")
    return 1 if problems and args.mode == "enforce" else 0


if __name__ == "__main__":
    sys.exit(main())
