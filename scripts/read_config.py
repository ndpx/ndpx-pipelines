#!/usr/bin/env python3
"""Read .ndpx/pipeline.yml, inspect the repository, and expose settings as GitHub Actions step outputs.

The pipeline does not require the ndpx template. It requires the platform contract:
  - .ndpx/pipeline.yml declaring the risk tier
  - npm with a committed package-lock.json
  - npm scripts: build, lint, test:coverage (format:check and typecheck run when present)
  - test:coverage writes coverage/coverage-summary.json (Istanbul json-summary; Vitest and Jest both support it)
  - apps that use Supabase keep their schema in supabase/migrations

Usage: read_config.py .ndpx/pipeline.yml [--root .]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

DEFAULTS = {
    "app_name": "",
    "risk_tier": 2,
    "node_version": "22",
    "coverage_lines": None,
    "build_dir": "dist",
    "src_dir": "src",
    "env_prefix": "VITE_",
}
COVERAGE_BY_TIER = {1: 0, 2: 70, 3: 80}
REQUIRED_SCRIPTS = ("build", "lint", "test:coverage")
OPTIONAL_SCRIPTS = ("format:check", "typecheck")
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
SAFE_PREFIX = re.compile(r"^[A-Z][A-Z0-9_]*_$|^$")


def resolve(raw: dict) -> dict[str, str]:
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    tier = int(cfg["risk_tier"])
    if tier not in (1, 2, 3):
        raise ValueError("risk_tier must be 1, 2 or 3")
    coverage = cfg["coverage_lines"] if cfg["coverage_lines"] is not None else COVERAGE_BY_TIER[tier]
    if int(coverage) < COVERAGE_BY_TIER[tier]:
        raise ValueError(f"coverage_lines cannot be below the Tier {tier} minimum of {COVERAGE_BY_TIER[tier]}")
    for key in ("build_dir", "src_dir"):
        val = str(cfg[key]).strip("/")
        if not val or not SAFE_PATH.match(val) or ".." in val.split("/"):
            raise ValueError(f"{key} must be a relative path inside the repository")
        cfg[key] = val
    if not SAFE_PREFIX.match(str(cfg["env_prefix"])):
        raise ValueError("env_prefix must be upper case and end with '_' (e.g. VITE_, NEXT_PUBLIC_, REACT_APP_)")
    return {
        "app_name": str(cfg["app_name"]),
        "risk_tier": str(tier),
        "node_version": str(cfg["node_version"]),
        "coverage_lines": str(int(coverage)),
        "build_dir": str(cfg["build_dir"]),
        "src_dir": str(cfg["src_dir"]),
        "env_prefix": str(cfg["env_prefix"]),
    }


def inspect_repo(root: str) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (outputs, errors, warnings) describing how the pipeline should treat this repository."""
    errors: list[str] = []
    warnings: list[str] = []
    pkg_path = os.path.join(root, "package.json")
    if not os.path.exists(pkg_path):
        return {}, ["package.json not found. The pipeline supports npm-based web apps."], warnings
    if not os.path.exists(os.path.join(root, "package-lock.json")):
        errors.append("package-lock.json not committed. Run `npm install` and commit the lockfile.")
    with open(pkg_path, encoding="utf-8") as fh:
        pkg = json.load(fh)
    scripts = pkg.get("scripts") or {}
    for name in REQUIRED_SCRIPTS:
        if name not in scripts:
            errors.append(f"npm script '{name}' is missing from package.json.")
    for name in OPTIONAL_SCRIPTS:
        if name not in scripts:
            warnings.append(f"npm script '{name}' is not defined; that check is skipped.")
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    uses_supabase = any(d.startswith("@supabase/") for d in deps)
    has_supabase = os.path.exists(os.path.join(root, "supabase", "config.toml"))
    if uses_supabase and not has_supabase:
        errors.append(
            "The app uses Supabase but has no supabase/ folder. Capture the schema as migrations: "
            "`npx supabase init`, `npx supabase link --project-ref <dev-ref>`, `npx supabase db pull`."
        )
    if has_supabase and not os.path.isdir(os.path.join(root, "supabase", "tests")):
        errors.append("supabase/tests/ is missing. Add pgTAP tests proving the access rules for every table.")
    outputs = {
        "has_supabase": "true" if has_supabase else "false",
        "has_format_check": "true" if "format:check" in scripts else "false",
        "has_typecheck": "true" if "typecheck" in scripts else "false",
    }
    return outputs, errors, warnings


def main(argv: list[str] | None = None) -> int:
    import yaml  # type: ignore

    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default=".ndpx/pipeline.yml")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    path = os.path.join(args.root, args.config) if not os.path.isabs(args.config) else args.config
    if not os.path.exists(path):
        print(f"::error::{args.config} not found. Every repository must declare its risk tier. Copy it from ndpx-webapp-template.")
        return 1
    with open(path, encoding="utf-8") as fh:
        try:
            out = resolve(yaml.safe_load(fh) or {})
        except ValueError as exc:
            print(f"::error::{args.config}: {exc}")
            return 1
    repo_out, errors, warnings = inspect_repo(args.root)
    out.update(repo_out)
    for w in warnings:
        print(f"::warning::{w}")
    for e in errors:
        print(f"::error::{e}")
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "\n".join(f"{k}={v}" for k, v in out.items())
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(lines + "\n")
    print(lines)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
