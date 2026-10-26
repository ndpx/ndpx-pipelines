#!/usr/bin/env python3
"""Database security checks for Supabase projects (check C-DB).

Runs against a database with all migrations applied (local Supabase in CI).
Blocking findings fail the job. Warnings are reported only.

Usage: check_db_security.py --dsn postgresql://... [--config .ndpx/pipeline.yml]
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

EXPOSED_SCHEMAS = ("public", "graphql_public")


@dataclass
class Finding:
    level: str  # "BLOCK" or "WARN"
    check: str
    obj: str
    detail: str


@dataclass
class Config:
    allow_anon_access: bool = False
    open_read_tables: list[str] = field(default_factory=list)


def load_config(path: str | None) -> Config:
    if not path or not os.path.exists(path):
        return Config()
    import yaml  # type: ignore

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    db = raw.get("database") or {}
    return Config(
        allow_anon_access=bool(db.get("allow_anon_access", False)),
        open_read_tables=[str(t) for t in (db.get("open_read_tables") or [])],
    )


QUERIES: dict[str, str] = {
    "rls_disabled": """
        select n.nspname || '.' || c.relname
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = any(%(schemas)s) and c.relkind in ('r','p') and not c.relrowsecurity
          and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
        order by 1""",
    "rls_no_policy": """
        select n.nspname || '.' || c.relname
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = any(%(schemas)s) and c.relkind in ('r','p') and c.relrowsecurity
          and not exists (select 1 from pg_policy p where p.polrelid = c.oid)
          and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
        order by 1""",
    "permissive_true_policy": """
        select schemaname || '.' || tablename, policyname, cmd, roles::text
        from pg_policies
        where schemaname = any(%(schemas)s)
          and (coalesce(qual, '') in ('true', '(true)') or coalesce(with_check, '') in ('true', '(true)'))
        order by 1, 2""",
    "security_definer_view": """
        select n.nspname || '.' || c.relname
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = any(%(schemas)s) and c.relkind in ('v','m')
          and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
          and not coalesce(
            (select bool_or(opt in ('security_invoker=true','security_invoker=on'))
               from unnest(c.reloptions) opt), false)
        order by 1""",
    "definer_no_search_path": """
        select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where p.prosecdef
          and n.nspname not in ('pg_catalog','information_schema','auth','storage','extensions',
                                'graphql','graphql_public','realtime','supabase_functions','vault',
                                'pgsodium','pgbouncer','net','cron','supabase_migrations','_realtime','pgtle')
          and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) cfg where cfg like 'search_path=%%')
        order by 1""",
    "definer_exposed": """
        select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where p.prosecdef and n.nspname = any(%(schemas)s)
        order by 1""",
    "anon_table_grants": """
        select n.nspname || '.' || c.relname,
               concat_ws(',',
                 case when has_table_privilege('anon', c.oid, 'SELECT') then 'SELECT' end,
                 case when has_table_privilege('anon', c.oid, 'INSERT') then 'INSERT' end,
                 case when has_table_privilege('anon', c.oid, 'UPDATE') then 'UPDATE' end,
                 case when has_table_privilege('anon', c.oid, 'DELETE') then 'DELETE' end)
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = any(%(schemas)s) and c.relkind in ('r','p','v','m','f')
          and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
          and (has_table_privilege('anon', c.oid, 'SELECT') or has_table_privilege('anon', c.oid, 'INSERT')
               or has_table_privilege('anon', c.oid, 'UPDATE') or has_table_privilege('anon', c.oid, 'DELETE'))
        order by 1""",
    "no_primary_key": """
        select n.nspname || '.' || c.relname
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = any(%(schemas)s) and c.relkind in ('r','p')
          and not exists (select 1 from pg_constraint k where k.conrelid = c.oid and k.contype = 'p')
          and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
        order by 1""",
    "extension_in_public": """
        select e.extname from pg_extension e join pg_namespace n on n.oid = e.extnamespace
        where n.nspname = 'public' and e.extname not in ('plpgsql') order by 1""",
    "public_buckets": """
        select id from storage.buckets where public order by 1""",
}


def evaluate(results: dict[str, list[tuple]], cfg: Config) -> list[Finding]:
    f: list[Finding] = []
    for (obj,) in results.get("rls_disabled", []):
        f.append(Finding("BLOCK", "RLS disabled", obj, "Enable row level security and add policies."))
    for (obj,) in results.get("rls_no_policy", []):
        f.append(Finding("WARN", "RLS with no policy", obj, "Table denies all access through the API. Confirm this is intended."))
    for obj, pol, cmd, roles in results.get("permissive_true_policy", []):
        table = obj.split(".", 1)[1]
        open_read_ok = cmd == "SELECT" and table in cfg.open_read_tables and "anon" not in roles and "public" not in roles
        if not open_read_ok:
            f.append(Finding("BLOCK", "Policy allows every row", f"{obj} / {pol}",
                             f"{cmd} policy for {roles} uses 'true'. Restrict to the rows each user may access."))
    for (obj,) in results.get("security_definer_view", []):
        f.append(Finding("BLOCK", "View bypasses RLS", obj,
                         "Create the view WITH (security_invoker = true) or move it out of the exposed schema."))
    for (obj,) in results.get("definer_no_search_path", []):
        f.append(Finding("BLOCK", "SECURITY DEFINER without search_path", obj, "Add SET search_path = '' to the function."))
    for (obj,) in results.get("definer_exposed", []):
        f.append(Finding("WARN", "SECURITY DEFINER callable through the API", obj,
                         "Move to the private schema unless it must be called from the browser."))
    if not cfg.allow_anon_access:
        for obj, privs in results.get("anon_table_grants", []):
            f.append(Finding("BLOCK", "Anonymous role has table privileges", obj,
                             f"anon holds {privs}. Internal apps revoke all privileges from anon."))
    for (obj,) in results.get("no_primary_key", []):
        f.append(Finding("WARN", "No primary key", obj, "Add a primary key."))
    for (obj,) in results.get("extension_in_public", []):
        f.append(Finding("WARN", "Extension in public schema", obj, "Install extensions in the extensions schema."))
    for (obj,) in results.get("public_buckets", []):
        f.append(Finding("BLOCK", "Public storage bucket", obj, "Make the bucket private and use signed URLs."))
    return f


def run_queries(dsn: str) -> dict[str, list[tuple]]:
    import psycopg  # type: ignore

    out: dict[str, list[tuple]] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        has_storage = conn.execute(
            "select exists (select 1 from information_schema.tables where table_schema='storage' and table_name='buckets')"
        ).fetchone()[0]
        for name, sql in QUERIES.items():
            if name == "public_buckets" and not has_storage:
                continue
            out[name] = conn.execute(sql, {"schemas": list(EXPOSED_SCHEMAS)}).fetchall()
    return out


def render(findings: list[Finding]) -> str:
    if not findings:
        return "### Database security checks\n\nNo findings.\n"
    lines = ["### Database security checks", "", "| Level | Check | Object | Action |", "|---|---|---|---|"]
    for x in sorted(findings, key=lambda i: (i.level != "BLOCK", i.check, i.obj)):
        lines.append(f"| {x.level} | {x.check} | `{x.obj}` | {x.detail} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    findings = evaluate(run_queries(args.dsn), load_config(args.config))
    report = render(findings)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    blocking = [x for x in findings if x.level == "BLOCK"]
    if blocking:
        print(f"::error::{len(blocking)} blocking database finding(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
