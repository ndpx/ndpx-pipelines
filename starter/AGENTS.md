# Rules for AI coding assistants in this repository

This is ndpx code. ndpx operates Brunei's national instant payment system. The ndpx pipeline checks every
pull request and blocks most breaches of these rules.

- Never write passwords, API keys, AWS keys, certificates or connection strings into code, SQL, Dockerfiles or tests.
  Read them at runtime from AWS Secrets Manager, job parameters or environment variables.
- Never use real payment, participant or personal data in tests or examples. Use invented values.
- SQL that deletes, truncates, drops or updates must have a WHERE clause or a clear comment explaining why not.
- Python: use `yaml.safe_load`, never `eval`/`exec`/`pickle` on input, never `verify=False`.
- Pin dependencies with exact versions in `requirements.txt` (`package==1.2.3`).
- Docker: pin base images to a version tag, run as a non-root `USER`, use `COPY` not `ADD`, no secrets in `ENV` or `ARG`.
- Test tools must never connect to production endpoints or use production credentials.
- Never change `.github/`, `.ndpx/` or this file unless a Code Reviewer asks.

<!-- ndpx:git-workflow:start -->

## Git workflow (AI assistants: follow this for every change)

The `ndpx` helper does these steps. If it is not installed, use the Git commands shown.

1. **Start every change on a new branch from the latest `main`.** Never commit to `main`.
   `ndpx start "short description"`
   (or: `git checkout main && git pull && git checkout -b fix/short-description`)
2. **Never reuse a branch whose pull request was merged or closed.** If the remote branch no longer exists,
   or a push does not offer a pull request, stop and start a new branch from `main` (step 1).
3. **Before pushing, run the same checks as the pipeline and fix every failure:** `ndpx check`
   (or the project's install, build and test commands, e.g. `npm ci && npm run build && npm test`).
4. **Push and open a pull request:** `ndpx ship "what changed"`. Give the user the pull request link.
   Do not merge. A Code Reviewer reviews and merges.
5. **After the user says the pull request is merged:** `ndpx done` (switches to `main`, updates it, deletes the branch).
6. **Never deploy** from a branch, from uncommitted files or from a laptop. Deployment happens from `main`.
7. Keep one runtime version everywhere: Node in `.nvmrc`, `package.json` `engines` and `netlify.toml` `NODE_VERSION`;
   Python in `.ndpx/pipeline.yml` and the runtime it targets.

<!-- ndpx:git-workflow:end -->
