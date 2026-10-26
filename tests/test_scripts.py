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
import check_db_security as dbsec  # noqa: E402
import check_headers  # noqa: E402
import check_licenses  # noqa: E402
import jsm_change  # noqa: E402
import read_config  # noqa: E402
import scan_bundle  # noqa: E402


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


# ---------------------------------------------------------------- config
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
