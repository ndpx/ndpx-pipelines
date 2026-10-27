#!/usr/bin/env python3
"""Detect what a repository contains, so the universal pipeline runs only the relevant checks.

Looks at files tracked by git (falls back to walking the tree). Reads optional settings
from .ndpx/pipeline.yml. Writes GitHub Actions step outputs.

Usage: detect_stack.py [--root .] [--config .ndpx/pipeline.yml]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SQL_DIALECTS = {"ansi", "oracle", "sparksql", "postgres", "athena", "mysql", "tsql", "bigquery", "snowflake", "redshift"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "coverage", "__pycache__", ".ndpx-pipelines"}
DOCKERFILE_RE = re.compile(r"(^|/)(Dockerfile|Containerfile)(\.[\w.-]+)?$|\.dockerfile$", re.I)
COMPOSE_RE = re.compile(r"(^|/)(docker-)?compose(\.[\w.-]+)?\.ya?ml$", re.I)


def list_files(root: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], check=True, capture_output=True, text=True).stdout
        files = [f for f in out.splitlines() if f]
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            found.append(os.path.relpath(os.path.join(base, n), root).replace(os.sep, "/"))
    return found


def detect(files: list[str], cfg: dict) -> dict[str, str]:
    files = [f for f in files if not any(part in SKIP_DIRS for part in f.split("/"))]
    py = [f for f in files if f.endswith(".py")]
    reqs = [f for f in files if re.search(r"(^|/)requirements[\w.-]*\.txt$", f)]
    pyproject = [f for f in files if f.endswith("pyproject.toml")]
    sql = [f for f in files if f.lower().endswith((".sql", ".pks", ".pkb", ".prc", ".fnc", ".trg"))]
    dockerfiles = [f for f in files if DOCKERFILE_RE.search(f)]
    compose = [f for f in files if COMPOSE_RE.search(f)]
    npm = "package.json" in files
    has_tests = any(re.match(r"(.*/)?tests?/", f) and f.endswith(".py") for f in files) or any(
        re.search(r"(^|/)test_[\w]+\.py$|_test\.py$", f) for f in files)

    has_supabase = any(f.startswith("supabase/migrations/") for f in files)
    default_dialect = "postgres" if has_supabase else "ansi"
    dialect = str((cfg.get("sql") or {}).get("dialect") or cfg.get("sql_dialect") or default_dialect).lower()
    if dialect not in SQL_DIALECTS:
        raise ValueError(f"sql dialect '{dialect}' is not supported; use one of {sorted(SQL_DIALECTS)}")
    sql_mode = str((cfg.get("sql") or {}).get("lint") or "advisory").lower()
    if sql_mode not in ("advisory", "blocking"):
        raise ValueError("sql.lint must be 'advisory' or 'blocking'")
    tier = int(cfg.get("risk_tier", 2))
    if tier not in (1, 2, 3):
        raise ValueError("risk_tier must be 1, 2 or 3")
    design_gate = str((cfg.get("design") or {}).get("gate") or "enforce").lower()
    if design_gate not in ("enforce", "warn"):
        raise ValueError("design.gate must be 'enforce' or 'warn'")

    return {
        "risk_tier": str(tier),
        "design_gate": design_gate,
        "has_python": str(bool(py)).lower(),
        "has_python_tests": str(bool(py) and has_tests).lower(),
        "python_version": str((cfg.get("python") or {}).get("version") or "3.12"),
        "python_requirements": json.dumps(reqs),
        "has_pyproject": str(bool(pyproject)).lower(),
        "has_sql": str(bool(sql)).lower(),
        "sql_dialect": dialect,
        "sql_lint": sql_mode,
        "has_docker": str(bool(dockerfiles or compose)).lower(),
        "dockerfiles": json.dumps(dockerfiles),
        "has_npm": str(npm).lower(),
        "summary": ", ".join(
            name for name, present in (("Python", py), ("SQL", sql), ("Docker", dockerfiles or compose), ("npm", npm)) if present
        ) or "no language-specific content",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--config", default=".ndpx/pipeline.yml")
    args = ap.parse_args(argv)
    cfg_path = os.path.join(args.root, args.config)
    cfg: dict = {}
    if os.path.exists(cfg_path):
        import yaml  # type: ignore

        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    else:
        print(f"::error::{args.config} not found. Every repository must declare its risk tier and owner.")
        return 1
    try:
        out = detect(list_files(args.root), cfg)
    except ValueError as exc:
        print(f"::error::{args.config}: {exc}")
        return 1
    lines = "\n".join(f"{k}={v}" for k, v in out.items())
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(lines + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Repository content\n\nDetected: **{out['summary']}**. Matching checks will run.\n")
    print(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
