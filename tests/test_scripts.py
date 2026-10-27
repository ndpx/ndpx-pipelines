"""Unit tests for ndpx pipeline scripts. Run: python -m pytest tests"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import ai_gate  # noqa: E402
import ai_review  # noqa: E402
import check_coverage  # noqa: E402
import check_db_security as dbsec  # noqa: E402
import check_headers  # noqa: E402
import check_licenses  # noqa: E402
import design_gate  # noqa: E402
import detect_stack  # noqa: E402
import jsm_change  # noqa: E402
import read_config  # noqa: E402
import scan_bundle  # noqa: E402
import sql_risk  # noqa: E402


def jwt(payload: dict) -> str:
    enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc(payload)}.c2lnbmF0dXJlc2lnbmF0dXJl"


# ---------------------------------------------------------------- scan_bundle
class TestScanBundle:
    def test_flags_service_role_jwt(self):
        assert scan_bundle.scan_text(f'const k="{jwt({"role": "service_role"})}"', "a.js", False)

    def test_allows_anon_jwt(self):
        assert not scan_bundle.scan_text(f'const k="{jwt({"role": "anon"})}"', "a.js", False)

    def test_flags_secret_key_and_dsn(self):
        hits = scan_bundle.scan_text('x="sb_secret_ABCDEFGHIJKLMN" y="postgresql://postgres:pw@db.x.co:5432/postgres"', "a.js", False)
        assert len(hits) == 2

    def test_allows_publishable_key(self):
        assert not scan_bundle.scan_text('x="sb_publishable_ABCDEFGHIJKLMN"', "a.js", False)

    def test_flags_secret_like_vite_variable_in_source(self):
        assert scan_bundle.scan_text("import.meta.env.VITE_SUPABASE_SERVICE_ROLE_KEY", "src/a.ts", True)
        assert not scan_bundle.scan_text("import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY", "src/a.ts", True)

    def test_scan_paths(self, tmp_path):
        d = tmp_path / "dist"
        d.mkdir()
        (d / "index.js").write_text(f'"{jwt({"role": "service_role"})}"')
        assert scan_bundle.scan_paths(str(d), None)


# ---------------------------------------------------------------- licences
class TestLicences:
    allowed = {"MIT", "Apache-2.0", "ISC"}

    def test_or_expression(self):
        assert check_licenses.licence_ok("(MIT OR GPL-3.0)", self.allowed)

    def test_and_expression_requires_all(self):
        assert not check_licenses.licence_ok("MIT AND GPL-3.0", self.allowed)

    def test_check_lock(self):
        lock = {"packages": {
            "": {"name": "app"},
            "node_modules/a": {"version": "1.0.0", "license": "MIT"},
            "node_modules/b": {"version": "2.0.0", "license": "GPL-3.0"},
            "node_modules/c": {"version": "3.0.0"},
            "node_modules/d": {"version": "1.0.0", "license": "AGPL-3.0"},
        }}
        problems = check_licenses.check(lock, self.allowed, {"d"})
        assert problems == ["b@2.0.0: GPL-3.0", "c@3.0.0: no licence declared"]


# ---------------------------------------------------------------- db security evaluation
class TestDbSecurity:
    def test_blocking_rules(self):
        results = {
            "rls_disabled": [("public.payroll",)],
            "permissive_true_policy": [("public.lookup", "read_all", "SELECT", "{authenticated}")],
            "security_definer_view": [("public.v_staff",)],
            "definer_no_search_path": [("public.f()",)],
            "anon_table_grants": [("public.employees", "SELECT")],
            "public_buckets": [("payslips",)],
            "rls_no_policy": [("public.internal",)],
        }
        findings = dbsec.evaluate(results, dbsec.Config())
        blocks = {f.check for f in findings if f.level == "BLOCK"}
        assert blocks == {"RLS disabled", "Policy allows every row", "View bypasses RLS",
                          "SECURITY DEFINER without search_path", "Anonymous role has table privileges",
                          "Public storage bucket"}
        assert any(f.level == "WARN" and f.check == "RLS with no policy" for f in findings)

    def test_open_read_allowlist(self):
        results = {"permissive_true_policy": [("public.lookup", "read_all", "SELECT", "{authenticated}")]}
        cfg = dbsec.Config(open_read_tables=["lookup"])
        assert not [f for f in dbsec.evaluate(results, cfg) if f.level == "BLOCK"]

    def test_allowlist_never_covers_anon_or_writes(self):
        cfg = dbsec.Config(open_read_tables=["lookup"])
        anon = {"permissive_true_policy": [("public.lookup", "r", "SELECT", "{anon}")]}
        write = {"permissive_true_policy": [("public.lookup", "w", "UPDATE", "{authenticated}")]}
        assert [f for f in dbsec.evaluate(anon, cfg) if f.level == "BLOCK"]
        assert [f for f in dbsec.evaluate(write, cfg) if f.level == "BLOCK"]


# ---------------------------------------------------------------- headers
class TestHeaders:
    good = {
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
    }

    def test_good(self):
        assert check_headers.evaluate(200, self.good, '<div id="root"></div>', True) == []

    def test_missing_and_weak(self):
        bad = dict(self.good)
        del bad["content-security-policy"]
        bad["strict-transport-security"] = "max-age=60"
        problems = check_headers.evaluate(200, bad, "", True)
        assert len(problems) == 3  # missing CSP, weak HSTS, missing root


# ---------------------------------------------------------------- AI review parsing and gate
class TestAiReview:
    def test_parse_tolerates_surrounding_text(self):
        text = 'Here you go:\n{"summary":"s","findings":[{"severity":"HIGH","title":"t","line":"12"}]}\nthanks'
        out = ai_review.parse_findings(text)
        assert out["findings"][0]["severity"] == "high"
        assert out["findings"][0]["line"] == 12

    def test_unknown_severity_becomes_info(self):
        out = ai_review.parse_findings('{"findings":[{"severity":"urgent"}]}')
        assert out["findings"][0]["severity"] == "info"

    def test_prompt_wraps_diff_as_data(self):
        p = ai_review.build_prompt("rubric", "diff text", ["a.ts"])
        assert "<diff>\ndiff text\n</diff>" in p

    def test_missing_key_skips_or_fails(self, tmp_path, monkeypatch):
        out = tmp_path / "f.json"
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("REQUIRE_AI_REVIEW", raising=False)
        assert ai_review.main(["--base", "a", "--head", "b", "--rubric", "x", "--out", str(out)]) == 0
        assert json.loads(out.read_text())["skipped"] is True
        monkeypatch.setenv("REQUIRE_AI_REVIEW", "true")
        assert ai_review.main(["--base", "a", "--head", "b", "--rubric", "x", "--out", str(out)]) == 1


class TestAiGate:
    findings = [{"severity": "high"}, {"severity": "low"}]

    def test_blocking_threshold(self):
        assert len(ai_gate.blocking(self.findings, "high")) == 1
        assert len(ai_gate.blocking(self.findings, "critical")) == 0

    def test_label_by_reviewer_counts(self):
        ev = [{"event": "labeled", "label": {"name": "ai-findings-accepted"}, "actor": {"login": "Reviewer1"}}]
        assert ai_gate.accepted_by(ev, "ai-findings-accepted", {"reviewer1"}, "builder") == "Reviewer1"

    def test_label_by_author_or_non_reviewer_ignored(self):
        own = [{"event": "labeled", "label": {"name": "ai-findings-accepted"}, "actor": {"login": "reviewer1"}}]
        assert ai_gate.accepted_by(own, "ai-findings-accepted", {"reviewer1"}, "reviewer1") is None
        other = [{"event": "labeled", "label": {"name": "ai-findings-accepted"}, "actor": {"login": "builder"}}]
        assert ai_gate.accepted_by(other, "ai-findings-accepted", {"reviewer1"}, "author") is None

    def test_unlabel_revokes(self):
        ev = [
            {"event": "labeled", "label": {"name": "ai-findings-accepted"}, "actor": {"login": "r"}},
            {"event": "unlabeled", "label": {"name": "ai-findings-accepted"}, "actor": {"login": "builder"}},
        ]
        assert ai_gate.accepted_by(ev, "ai-findings-accepted", {"r"}, "builder") is None


# ---------------------------------------------------------------- JSM change gate
SHA = "a" * 40
NOW = dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc)
APPROVED = {"awaiting implementation", "implementing"}


def issue(status="Awaiting implementation", itype="Change", summary=f"Release {SHA[:7]}", **extra):
    return {"fields": {"issuetype": {"name": itype}, "status": {"name": status}, "summary": summary, "description": "", **extra}}


class TestJsm:
    def test_approved(self):
        assert jsm_change.evaluate(issue(), SHA, APPROVED, None, None, None, NOW) == []

    def test_not_approved(self):
        assert jsm_change.evaluate(issue(status="Awaiting approval"), SHA, APPROVED, None, None, None, NOW)

    def test_wrong_type(self):
        assert jsm_change.evaluate(issue(itype="Service request"), SHA, APPROVED, None, None, None, NOW)

    def test_commit_must_match(self):
        assert jsm_change.evaluate(issue(summary="Release bbbbbbb"), SHA, APPROVED, None, None, None, NOW)

    def test_commit_field(self):
        ok = issue(customfield_1=SHA[:12])
        bad = issue(customfield_1="b" * 12)
        assert jsm_change.evaluate(ok, SHA, APPROVED, "customfield_1", None, None, NOW) == []
        assert jsm_change.evaluate(bad, SHA, APPROVED, "customfield_1", None, None, NOW)

    def test_window(self):
        inside = issue(cs="2026-11-02T09:00:00.000+0800", ce="2026-11-02T20:00:00.000+0800")
        outside = issue(cs="2026-11-03T09:00:00.000+0800", ce="2026-11-03T12:00:00.000+0800")
        assert jsm_change.evaluate(inside, SHA, APPROVED, None, "cs", "ce", NOW) == []
        assert jsm_change.evaluate(outside, SHA, APPROVED, None, "cs", "ce", NOW)

    def test_rejects_bad_inputs(self):
        assert jsm_change.main(["verify", "--issue", "not a key", "--commit", SHA]) == 1
        assert jsm_change.main(["verify", "--issue", "CHG-1", "--commit", "abc"]) == 1


# ---------------------------------------------------------------- config and platform contract
class TestConfig:
    def test_tier_minimum_coverage(self):
        assert read_config.resolve({"risk_tier": 2})["coverage_lines"] == "70"
        assert read_config.resolve({"risk_tier": 3, "coverage_lines": 90})["coverage_lines"] == "90"

    def test_cannot_lower_coverage(self):
        with pytest.raises(ValueError):
            read_config.resolve({"risk_tier": 3, "coverage_lines": 10})

    def test_invalid_tier(self):
        with pytest.raises(ValueError):
            read_config.resolve({"risk_tier": 5})

    def test_framework_settings(self):
        out = read_config.resolve({"build_dir": "out/", "src_dir": "app", "env_prefix": "NEXT_PUBLIC_"})
        assert (out["build_dir"], out["src_dir"], out["env_prefix"]) == ("out", "app", "NEXT_PUBLIC_")

    @pytest.mark.parametrize("bad", [{"build_dir": "../etc"}, {"build_dir": "a b"}, {"env_prefix": "vite_"}, {"env_prefix": "X;rm"}])
    def test_rejects_unsafe_settings(self, bad):
        with pytest.raises(ValueError):
            read_config.resolve(bad)


def make_repo(tmp_path, scripts, deps=None, supabase=False, tests=True, lock=True):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts, "dependencies": deps or {}}))
    if lock:
        (tmp_path / "package-lock.json").write_text("{}")
    if supabase:
        (tmp_path / "supabase").mkdir()
        (tmp_path / "supabase" / "config.toml").write_text("")
        if tests:
            (tmp_path / "supabase" / "tests").mkdir()
    return str(tmp_path)


FULL = {"build": "x", "lint": "x", "test:coverage": "x", "format:check": "x", "typecheck": "x"}


class TestContract:
    def test_template_shape_passes(self, tmp_path):
        out, errors, warnings = read_config.inspect_repo(make_repo(tmp_path, FULL, {"@supabase/supabase-js": "2"}, supabase=True))
        assert errors == [] and warnings == []
        assert out["has_supabase"] == "true"

    def test_non_template_app_without_optional_scripts(self, tmp_path):
        scripts = {"build": "next build", "lint": "next lint", "test:coverage": "jest --coverage"}
        out, errors, warnings = read_config.inspect_repo(make_repo(tmp_path, scripts))
        assert errors == []
        assert len(warnings) == 2 and out["has_typecheck"] == "false" and out["has_supabase"] == "false"

    def test_missing_required_scripts_and_lockfile(self, tmp_path):
        _, errors, _ = read_config.inspect_repo(make_repo(tmp_path, {"build": "x"}, lock=False))
        assert len(errors) == 3  # lockfile, lint, test:coverage

    def test_supabase_used_without_migrations_fails(self, tmp_path):
        _, errors, _ = read_config.inspect_repo(make_repo(tmp_path, FULL, {"@supabase/supabase-js": "2"}))
        assert any("supabase/ folder" in e for e in errors)

    def test_supabase_without_tests_fails(self, tmp_path):
        _, errors, _ = read_config.inspect_repo(make_repo(tmp_path, FULL, {}, supabase=True, tests=False))
        assert any("supabase/tests" in e for e in errors)


class TestCoverage:
    def test_pct(self):
        assert check_coverage.line_pct({"total": {"lines": {"pct": 81.5}}}) == 81.5
        assert check_coverage.line_pct({"total": {"lines": {"pct": "Unknown"}}}) == 100.0

    def test_threshold(self, tmp_path):
        f = tmp_path / "s.json"
        f.write_text(json.dumps({"total": {"lines": {"pct": 65}}}))
        assert check_coverage.main(["--summary", str(f), "--min", "70"]) == 1
        assert check_coverage.main(["--summary", str(f), "--min", "60"]) == 0
        assert check_coverage.main(["--summary", str(tmp_path / "missing.json"), "--min", "0"]) == 1


class TestEnvNames:
    @pytest.mark.parametrize("name", ["NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY", "REACT_APP_DB_PASSWORD", "VITE_STRIPE_SECRET"])
    def test_flags_secret_like_public_vars(self, name):
        assert scan_bundle.scan_text(f"process.env.{name}", "src/a.ts", True)

    def test_allows_public_key_names(self):
        assert not scan_bundle.scan_text("process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "src/a.ts", True)


# ---------------------------------------------------------------- detect_stack
def test_detect_mixed_repo():
    files = ["jobs/a.py", "tests/test_a.py", "requirements.txt", "sql/001.sql", "wallet/Dockerfile",
             "docker-compose.yml", "node_modules/x/y.py"]
    out = detect_stack.detect(files, {"risk_tier": 3, "sql": {"dialect": "oracle", "lint": "blocking"}})
    assert out["risk_tier"] == "3"
    assert out["has_python"] == "true" and out["has_python_tests"] == "true"
    assert json.loads(out["python_requirements"]) == ["requirements.txt"]
    assert out["has_sql"] == "true" and out["sql_dialect"] == "oracle" and out["sql_lint"] == "blocking"
    assert out["has_docker"] == "true" and json.loads(out["dockerfiles"]) == ["wallet/Dockerfile"]
    assert out["has_npm"] == "false"


def test_detect_sql_only_defaults():
    out = detect_stack.detect(["001_setup.sql", "README.md"], {})
    assert out["has_python"] == "false" and out["has_docker"] == "false"
    assert out["sql_dialect"] == "ansi" and out["sql_lint"] == "advisory" and out["risk_tier"] == "2"
    assert out["summary"] == "SQL"


@pytest.mark.parametrize("cfg", [{"risk_tier": 5}, {"sql": {"dialect": "cobol"}}, {"sql": {"lint": "sometimes"}}])
def test_detect_rejects_bad_config(cfg):
    with pytest.raises(ValueError):
        detect_stack.detect(["a.sql"], cfg)


def test_detect_requires_config(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert detect_stack.main(["--root", str(tmp_path)]) == 1


# ---------------------------------------------------------------- sql_risk
def test_sql_risk_blocks_password():
    blocks, _ = sql_risk.scan("a.sql", "CREATE USER r IDENTIFIED BY \"Sup3rSecret!\";\n")
    assert blocks and "IDENTIFIED BY" in blocks[0]


def test_sql_risk_ignores_commented_password():
    blocks, reviews = sql_risk.scan("a.sql", "-- CREATE USER r IDENTIFIED BY secret1;\nSELECT 1;\n")
    assert blocks == [] and reviews == []


def test_sql_risk_flags_risky_statements():
    sql = "DROP TABLE t;\nDELETE FROM t;\nUPDATE t SET a = 1;\nGRANT SELECT ON t TO r;\nTRUNCATE TABLE t;\n"
    _, reviews = sql_risk.scan("a.sql", sql)
    text = "\n".join(reviews)
    for label in ("DROP", "DELETE without WHERE", "UPDATE without WHERE", "GRANT/REVOKE", "TRUNCATE"):
        assert label in text


def test_sql_risk_allows_safe_changes():
    sql = "DELETE FROM t WHERE id = 1;\nUPDATE t\n  SET a = 1\n  WHERE id = 2;\nSELECT * FROM t;\n"
    blocks, reviews = sql_risk.scan("a.sql", sql)
    assert blocks == [] and reviews == []


def test_sql_risk_exit_code(tmp_path):
    bad = tmp_path / "bad.sql"
    bad.write_text("ALTER USER app IDENTIFIED BY 'abcd1234';\n")
    good = tmp_path / "good.sql"
    good.write_text("DROP VIEW v;\n")
    assert sql_risk.main([str(bad)]) == 1
    assert sql_risk.main([str(good)]) == 0


def test_detect_supabase_defaults_to_postgres():
    out = detect_stack.detect(["package.json", "supabase/migrations/001.sql"], {"risk_tier": 2})
    assert out["sql_dialect"] == "postgres" and out["has_npm"] == "true"


# ---------------------------------------------------------------- design_gate
GOOD_DESIGN = "# Design\n## Threat model (STRIDE)\n| Spoofing | x | y | z |\n"


def test_design_tier1_not_applied():
    problems, _ = design_gate.evaluate(1, None, "", False, None)
    assert problems == []


def test_design_missing_blocks_tier2():
    problems, _ = design_gate.evaluate(2, None, "", False, None)
    assert problems and "No design note" in problems[0]


def test_design_template_and_no_threat_model():
    problems, _ = design_gate.evaluate(2, "docs/DESIGN.md", "<!-- ndpx:template -->\n# Design\n", False, None)
    assert len(problems) == 2


def test_design_change_needs_approval():
    problems, _ = design_gate.evaluate(3, "docs/DESIGN.md", GOOD_DESIGN, True, None)
    assert problems and "design-approved" in problems[0]
    problems, notes = design_gate.evaluate(3, "docs/DESIGN.md", GOOD_DESIGN, True, "reviewer1")
    assert problems == [] and "reviewer1" in notes[0]


def test_design_unchanged_good_passes():
    problems, _ = design_gate.evaluate(2, "docs/DESIGN.md", GOOD_DESIGN, False, None)
    assert problems == []


def test_detect_design_gate_option():
    assert detect_stack.detect(["a.sql"], {"design": {"gate": "warn"}})["design_gate"] == "warn"
    with pytest.raises(ValueError):
        detect_stack.detect(["a.sql"], {"design": {"gate": "off"}})
