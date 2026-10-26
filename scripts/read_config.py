#!/usr/bin/env python3
"""Read .ndpx/pipeline.yml and expose values as GitHub Actions step outputs.

Usage: read_config.py .ndpx/pipeline.yml
"""
from __future__ import annotations

import os
import sys

DEFAULTS = {"app_name": "", "risk_tier": 2, "node_version": "22", "coverage_lines": None}
COVERAGE_BY_TIER = {1: 0, 2: 70, 3: 80}


def resolve(raw: dict) -> dict[str, str]:
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    tier = int(cfg["risk_tier"])
    if tier not in (1, 2, 3):
        raise ValueError("risk_tier must be 1, 2 or 3")
    coverage = cfg["coverage_lines"] if cfg["coverage_lines"] is not None else COVERAGE_BY_TIER[tier]
    if int(coverage) < COVERAGE_BY_TIER[tier]:
        raise ValueError(f"coverage_lines cannot be below the Tier {tier} minimum of {COVERAGE_BY_TIER[tier]}")
    return {
        "app_name": str(cfg["app_name"]),
        "risk_tier": str(tier),
        "node_version": str(cfg["node_version"]),
        "coverage_lines": str(int(coverage)),
    }


def main(argv: list[str]) -> int:
    import yaml  # type: ignore

    path = argv[1] if len(argv) > 1 else ".ndpx/pipeline.yml"
    if not os.path.exists(path):
        print(f"::error::{path} not found. Every repository must declare its risk tier.")
        return 1
    with open(path, encoding="utf-8") as fh:
        out = resolve(yaml.safe_load(fh) or {})
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "\n".join(f"{k}={v}" for k, v in out.items())
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(lines + "\n")
    print(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
