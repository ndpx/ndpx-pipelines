#!/usr/bin/env python3
"""Scan a built frontend and its source for secrets that must never reach the browser.

Blocks on:
  - Supabase secret keys (sb_secret_...) or JWTs whose role is service_role
  - Postgres connection strings with credentials
  - Private key blocks
  - Environment variables exposed to the browser (VITE_, NEXT_PUBLIC_, REACT_APP_ and similar) whose names suggest secrets

Usage: scan_bundle.py --dist dist [--src src]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
SB_SECRET_RE = re.compile(r"sb_secret_[A-Za-z0-9_-]{10,}")
PG_DSN_RE = re.compile(r"postgres(?:ql)?://[^\s:'\"/]+:[^\s@'\"]+@[^\s'\"]+")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")
SECRET_ENV_RE = re.compile(r"\b(?:VITE_|NEXT_PUBLIC_|REACT_APP_|PUBLIC_|NUXT_PUBLIC_|EXPO_PUBLIC_|GATSBY_)[A-Z0-9_]*(?:SECRET|SERVICE_ROLE|SERVICE_KEY|PRIVATE|PASSWORD|DB_URL)[A-Z0-9_]*\b")
TEXT_EXT = {".js", ".mjs", ".cjs", ".html", ".css", ".json", ".map", ".txt", ".ts", ".tsx", ".jsx", ".env", ".toml", ".yml", ".yaml"}


def jwt_role(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def scan_text(text: str, where: str, check_env_names: bool) -> list[str]:
    hits: list[str] = []
    for m in SB_SECRET_RE.finditer(text):
        hits.append(f"{where}: Supabase secret key ({m.group(0)[:14]}…)")
    for m in JWT_RE.finditer(text):
        if jwt_role(m.group(0)) == "service_role":
            hits.append(f"{where}: Supabase service_role JWT")
    for m in PG_DSN_RE.finditer(text):
        hits.append(f"{where}: database connection string with credentials")
    if PRIVATE_KEY_RE.search(text):
        hits.append(f"{where}: private key block")
    if check_env_names:
        for m in set(SECRET_ENV_RE.findall(text)):
            hits.append(f"{where}: browser-exposed variable with secret-like name {m}")
    return hits


def walk(root: str):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".git"}]
        for name in files:
            if os.path.splitext(name)[1].lower() in TEXT_EXT or name.startswith(".env"):
                yield os.path.join(base, name)


def scan_paths(dist: str | None, src: str | None) -> list[str]:
    hits: list[str] = []
    for root, env_names in ((dist, False), (src, True)):
        if not root or not os.path.isdir(root):
            continue
        for path in walk(root):
            with open(path, encoding="utf-8", errors="ignore") as fh:
                hits.extend(scan_text(fh.read(), path, env_names))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--src", default="src")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.dist):
        print(f"::error::Build output '{args.dist}' not found. Build before scanning.")
        return 1
    hits = scan_paths(args.dist, args.src)
    if hits:
        for h in hits:
            print(f"::error::{h}")
        return 1
    print("Bundle scan: no secrets found in build output or browser-exposed variables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
