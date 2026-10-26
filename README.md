# ndpx-pipelines

Central CI/CD for ndpx internal web apps (React + Supabase + Netlify).
App repositories call these workflows. They do not copy them. A fix here applies to every app at its next run.

This repository contains no secrets and no ndpx data. It is public so that private app repositories can load
its workflows and scripts without extra credentials.

## Workflows

| Workflow | Called by | Does |
|---|---|---|
| `.github/workflows/pr-checks.yml` | app `ci.yml` on every pull request | Format, lint, types, tests + coverage, build, browser-bundle secret scan, dependency audit, licences, secret scanning (gitleaks), static analysis (Semgrep), database migrations + RLS tests + database security checks on a local Supabase, AI security review, Netlify preview + header check + OWASP ZAP baseline. Single required check: **`result`**. |
| `.github/workflows/deploy.yml` | app `deploy.yml` | Staging on merge to `main`. Production only by manual run with a Change Request key; refuses unless Jira Service Management shows it approved for the exact commit. Applies migrations, deploys to Netlify, smoke-tests, rolls back the frontend on failure, records the outcome on the Change Request. |

## Scripts

| Script | Purpose |
|---|---|
| `check_db_security.py` | Blocks: tables without RLS, `using (true)` policies, views that bypass RLS, SECURITY DEFINER without `search_path`, anonymous grants, public storage buckets. |
| `scan_bundle.py` | Blocks Supabase secret/service_role keys, DB connection strings and private keys in the built site; blocks secret-like `VITE_` variable names in source. |
| `check_licenses.py` | Licence allowlist (`config/licenses.txt`) with per-repo reviewed exceptions. |
| `ai_review.py` / `ai_gate.py` | Tool-less Claude review of the diff against `config/ai-review-rubric.md`; gate fails on High/Critical unless a Code Reviewer (not the author) applies `ai-findings-accepted`. |
| `jsm_change.py` | Verifies the Change Request (type, approved status, commit, window); records the outcome. |
| `check_headers.py` | Security headers and smoke test on a deployed URL. |
| `netlify_site.py` | Captures the published deploy and restores it on rollback. |
| `pr_comment.py` | Upserts pipeline comments on the pull request. |
| `read_config.py` | Reads the app's `.ndpx/pipeline.yml` (risk tier sets the coverage minimum). |

## Configuration expected in each app repository

Secrets (repository): `ANTHROPIC_API_KEY`, `NETLIFY_AUTH_TOKEN`, `NETLIFY_DEV_SITE_ID`
Variables (repository): `CODE_REVIEWERS`, `DEV_SUPABASE_URL`, `DEV_SUPABASE_PUBLISHABLE_KEY`, optional `AI_REVIEW_MODEL`,
`REQUIRE_AI_REVIEW=true` and `REQUIRE_PREVIEW=true` once those services are set up.

Environment `staging` and `production` (GitHub Team plan):
- Secrets: `NETLIFY_AUTH_TOKEN`, `NETLIFY_SITE_ID`, `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `JSM_API_TOKEN` (production)
- Variables: `SUPABASE_PROJECT_REF`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `APP_NAME`,
  `JSM_BASE_URL`, `JSM_EMAIL`, `JSM_APPROVED_STATUSES`, `JSM_COMMIT_FIELD`, `JSM_WINDOW_START_FIELD`, `JSM_WINDOW_END_FIELD` (production)

## Releasing a change to the pipeline

1. Pull request here, reviewed by a Code Reviewer. Run `python -m pytest tests` locally first.
2. After merge, move the `v1` tag only for backward-compatible changes: `git tag -f v1 && git push -f origin v1`.
   Breaking changes get `v2`; apps move when ready.
3. Apps that pin a commit SHA receive the update through Dependabot.

## Tests

```bash
pip install pytest pyyaml
python -m pytest tests
```
