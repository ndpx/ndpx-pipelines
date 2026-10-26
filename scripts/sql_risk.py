#!/usr/bin/env python3
"""Report risky statements in SQL scripts for the Code Reviewer.

Blocking: credentials written into SQL (IDENTIFIED BY '...', PASSWORD '...').
Review required (reported, not blocking): DROP, TRUNCATE, DELETE/UPDATE without WHERE,
GRANT/REVOKE, user and role changes, dynamic SQL, and statements that disable constraints or triggers.

Usage: sql_risk.py [files...]   (defaults to all tracked .sql files)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

BLOCK = [
    (re.compile(r"\bidentified\s+by\s+(?!values\b)(\"[^\"]+\"|'[^']+'|[A-Za-z0-9_#$!@%^&*]{4,})", re.I),
     "Password written into SQL (IDENTIFIED BY). Use a secrets store; rotate this password."),
    (re.compile(r"\bpassword\s*(=|\s)\s*'[^']{4,}'", re.I),
     "Password literal in SQL. Use a secrets store; rotate this password."),
]
REVIEW = [
    (re.compile(r"^\s*drop\s+(table|database|schema|user|tablespace|view|index|materialized\s+view|function|procedure|package)\b", re.I), "DROP"),
    (re.compile(r"^\s*truncate\b", re.I), "TRUNCATE"),
    (re.compile(r"^\s*(grant|revoke)\b", re.I), "GRANT/REVOKE"),
    (re.compile(r"^\s*(create|alter|drop)\s+(user|role)\b", re.I), "User or role change"),
    (re.compile(r"\bexecute\s+immediate\b|\bexec(ute)?\s*\(|\bsp_executesql\b", re.I), "Dynamic SQL"),
    (re.compile(r"\b(disable|novalidate)\b.*\b(constraint|trigger)\b|\balter\s+table\b.*\bdisable\b", re.I), "Disables constraint or trigger"),
]
DELETE_NO_WHERE = re.compile(r"^\s*delete\s+(from\s+)?[\w.\"$#]+\s*$", re.I)
UPDATE_NO_WHERE = re.compile(r"^\s*update\s+[\w.\"$#]+\s+set\b(?!.*\bwhere\b)", re.I | re.S)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def statements(sql: str) -> list[tuple[int, str]]:
    """Split on semicolons and '/' lines, keeping the starting line number of each statement."""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 1
    for i, line in enumerate(sql.splitlines(), 1):
        if not buf and line.strip():
            start = i
        if line.strip() == "/":
            if buf:
                out.append((start, "\n".join(buf)))
                buf = []
            continue
        parts = line.split(";")
        for j, part in enumerate(parts):
            if part.strip() or buf:
                buf.append(part)
            if j < len(parts) - 1:
                text = "\n".join(buf).strip()
                if text:
                    out.append((start, text))
                buf = []
                start = i
    if "".join(buf).strip():
        out.append((start, "\n".join(buf)))
    return out


def scan(path: str, text: str) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    reviews: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        code = re.sub(r"--.*$", "", raw)
        for rx, msg in BLOCK:
            if rx.search(code):
                blocks.append(f"{path}:{lineno}: {msg}")
    for lineno, stmt in statements(strip_comments(text)):
        flat = " ".join(stmt.split())
        for rx, label in REVIEW:
            if rx.search(flat):
                reviews.append(f"{path}:{lineno}: {label}: `{flat[:100]}`")
        if DELETE_NO_WHERE.match(flat):
            reviews.append(f"{path}:{lineno}: DELETE without WHERE: `{flat[:100]}`")
        if UPDATE_NO_WHERE.match(flat):
            reviews.append(f"{path}:{lineno}: UPDATE without WHERE: `{flat[:100]}`")
    return blocks, reviews


def tracked_sql() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "*.sql", "*.SQL", "*.pks", "*.pkb", "*.prc", "*.fnc", "*.trg"],
                             check=True, capture_output=True, text=True).stdout
        return [f for f in out.splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def main(argv: list[str]) -> int:
    files = argv or tracked_sql()
    all_blocks: list[str] = []
    all_reviews: list[str] = []
    for f in files:
        with open(f, encoding="utf-8", errors="ignore") as fh:
            b, r = scan(f, fh.read())
        all_blocks += b
        all_reviews += r
    lines = ["### SQL risk report", ""]
    if not all_blocks and not all_reviews:
        lines.append(f"{len(files)} SQL file(s) scanned. No risky statements found.")
    if all_blocks:
        lines += ["**Blocking**", ""] + [f"- {b}" for b in all_blocks] + [""]
    if all_reviews:
        lines += ["**Code Reviewer must confirm these are intended**", ""] + [f"- {r}" for r in all_reviews]
    report = "\n".join(lines) + "\n"
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    for b in all_blocks:
        print(f"::error::{b}")
    for r in all_reviews:
        print(f"::warning::{r}")
    return 1 if all_blocks else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
