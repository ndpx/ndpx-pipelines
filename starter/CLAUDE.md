# CLAUDE.md

The rules below also apply to Claude Code. (Same content as AGENTS.md.)


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
