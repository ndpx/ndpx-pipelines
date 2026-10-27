# ndpx-pipelines

Central CI/CD for ndpx code. Two profiles:

- **Web apps** (npm + Supabase + Netlify): `pr-checks.yml` and `deploy.yml`. Full checks, previews and deployment.
- **Any other code** (SQL scripts, Python and AWS Glue jobs, Docker-based internal tools): `code-checks.yml`.
  Checks only; deployment of these stays with the existing process and the CAB.

`pr-checks.yml` calls `code-checks.yml` first, so every repository gets the same baseline; web apps add their own stages on top.
Repositories call these workflows. They do not copy them. A fix here applies to every app at its next run.

This repository contains no secrets and no ndpx data. It is public so that private app repositories can load
its workflows and scripts without extra credentials.

## Platform contract

Any npm-based web app can use this pipeline. The ndpx template is the easiest starting point, not a requirement.
The `config` job checks the contract first and fails with a clear message when something is missing.

| Requirement | Detail |
|---|---|
| `.ndpx/pipeline.yml` | `risk_tier` (1–3). Optional: `build_dir` (default `dist`), `src_dir` (`src`), `env_prefix` (`VITE_`), `node_version`, `coverage_lines`, `database.*`. |
| npm + committed `package-lock.json` | `npm ci` must work. |
| npm scripts | Required: `build`, `lint`, `test:coverage`. Run when present: `format:check`, `typecheck`. |
| Coverage summary | `test:coverage` writes `coverage/coverage-summary.json` (Vitest reporter `json-summary`; Jest `--coverageReporters=json-summary`). Minimum by tier: 0 / 70 / 80 %. |
| Supabase | Apps that depend on `@supabase/*` must have `supabase/config.toml`, migrations in `supabase/migrations/` and pgTAP tests in `supabase/tests/`. Apps without Supabase skip the database job. |
| Browser variables | The pipeline sets `<env_prefix>SUPABASE_URL` and `<env_prefix>SUPABASE_PUBLISHABLE_KEY` at build time. |
| Netlify headers | `netlify.toml` (or `_headers`) must set HSTS, CSP with `frame-ancestors`, `X-Content-Type-Options` and `Referrer-Policy`; checked on every preview and deploy. |

### Bringing an existing app onto the platform

1. Create a private repository in the `ndpx` organisation and push the code.
2. Copy from the template: `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `.github/CODEOWNERS`,
   `.github/pull_request_template.md`, `.ndpx/`, `netlify.toml` (headers section), `AGENTS.md`, `CLAUDE.md`.
3. Set `build_dir`, `src_dir` and `env_prefix` in `.ndpx/pipeline.yml` for the framework.
4. Add the npm scripts above.
5. If it uses Supabase: `npx supabase init`, `npx supabase link --project-ref <dev-ref>`, `npx supabase db pull`
   to capture the schema as a baseline migration, then write pgTAP access tests.
6. Open a pull request. The first run lists everything to fix. Fix security blockers first.

## Any other code: universal checks

`code-checks.yml` detects what a repository contains and runs only the matching checks. Single required check: **`result`**.

| Content | Blocking | Reported for the Code Reviewer |
|---|---|---|
| Everything | Secrets (gitleaks), Semgrep findings, AI review High/Critical (when enabled), design approval for Tier 2–3 (C47) | AI review Medium/Low; SBOM artifact (C19) |
| Python / Glue | Syntax errors and undefined names (ruff), Bandit medium+ severity, vulnerable pinned packages (pip-audit), failing tests | Unpinned requirements |
| SQL | Passwords written into SQL, unparseable SQL when `sql.lint: blocking` | DROP, TRUNCATE, DELETE/UPDATE without WHERE, GRANT/REVOKE, user/role changes, dynamic SQL; style (sqlfluff) |
| Docker | `latest`/untagged base images (hadolint), misconfigurations (trivy config), HIGH/CRITICAL fixable image vulnerabilities, build failure | Other hadolint warnings |
| npm (not a web app) | `npm audit` high+ | |

### Bringing code into GitHub

Only organisation owners create repositories. Full steps: Platform Administration Runbook, Section 4.

| Where the code is | Command (Git Bash) |
|---|---|
| Laptop, OneDrive, personal hosting | Create an empty private repo, then `bash tools/onboard.sh import "<folder>" <repo> <tier>` |
| Already a repo in `ndpx` | `bash tools/onboard.sh existing <repo> <tier>` |
| A personal GitHub repo | Transfer it to `ndpx`, then `existing` |

`import` copies current files only onto `import/initial`, leaving out secret files and build output, and asks for
confirmation if anything looks like a secret. `existing` adds the ndpx files on `ndpx/add-pipeline` without overwriting,
merges `.gitignore`, and its first pull request also scans the full Git history for secrets (report only).

New repository by hand: copy everything in `starter/` and edit `.ndpx/pipeline.yml`.

### Design approval (C47)

Tier 2 and 3 repositories need `docs/DESIGN.md` (template in `starter/docs/`) with a `Threat model` section and without the
`ndpx:template` marker. A pull request that adds or changes it needs the `design-approved` label from a Code Reviewer who is
not the author. Imported code gets `design: { gate: warn }` in `.ndpx/pipeline.yml` as a visible temporary waiver.

### Weekly rescan (C33)

`checks.yml` (starter) and `ci.yml` (template) also run every Monday on `main`. A failed scheduled run opens or updates
the issue "ndpx weekly scan found problems on main" through `scheduled-report.yml`.

### External applications

Publish `templates/security.txt` at `/.well-known/security.txt` (Secure Coding Standard 14.3).

## Workflows

| Workflow | Called by | Does |
|---|---|---|
| `.github/workflows/pr-checks.yml` | app `ci.yml` on every pull request | Common checks (`code-checks.yml`: secrets, Semgrep with TypeScript/React rules, SQL risk report on migrations, AI review with the web rubric), then format, lint, types, tests + coverage, build, browser-bundle secret scan, dependency audit, licences, database migrations + RLS tests + database security checks on a local Supabase, AI security review, Netlify preview + header check + OWASP ZAP baseline. Single required check: **`result`**. |
| `.github/workflows/code-checks.yml` | `checks.yml` from `starter/`, and `pr-checks.yml` (`profile: web`) | Universal checks above. |
| `.github/workflows/scheduled-report.yml` | `checks.yml` / `ci.yml`, failed scheduled runs only | Opens or updates the weekly scan issue. |
| `.github/workflows/deploy.yml` | app `deploy.yml` | Staging on merge to `main`. Release SBOM artifact. Production only by manual run with a Change Request key; refuses unless Jira Service Management shows it approved for the exact commit. Applies migrations, deploys to Netlify, smoke-tests, rolls back the frontend on failure, records the outcome on the Change Request. |

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
| `read_config.py` | Checks the platform contract and reads `.ndpx/pipeline.yml` (risk tier sets the coverage minimum). |
| `detect_stack.py` | Detects Python, SQL, Docker and npm content and reads `.ndpx/pipeline.yml` for the universal checks. |
| `design_gate.py` | Design note and `design-approved` label check for Tier 2–3 (C47). |
| `sql_risk.py` | Blocks passwords in SQL; reports destructive and privilege-changing statements. |
| `check_coverage.py` | Enforces the tier's line-coverage minimum from the Istanbul summary (any test runner). |

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
   Breaking changes get `v2`; apps move when ready. `pr-checks.yml` calls `code-checks.yml@v1`: change that ref with the major version.
3. Apps that pin a commit SHA receive the update through Dependabot.

## Tests

```bash
pip install pytest pyyaml
python -m pytest tests
```
