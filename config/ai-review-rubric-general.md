# ndpx AI security review rubric: general code

Context: internal code at ndpx, the operator of Brunei's national instant payment system (tarus).
Repositories include AWS Glue jobs (Python/PySpark), SQL scripts (Oracle, Athena/Spark SQL, Postgres),
internal tools in Docker (including payment testing tools that send and receive test transactions),
and automation scripts. Data handled may include payment transactions and personal data protected
under Brunei's PDPO 2025. Code is often written with AI assistance.

## Severity guide
- critical: credentials or keys exposed; real payment or personal data exposed; code that can move money or alter production data unintentionally.
- high: missing authentication or authorisation; injection; unsafe deserialisation; secrets in images or logs; destructive SQL without safeguards.
- medium: weak error handling around data changes; missing input validation; overly broad IAM or database privileges; unpinned base images.
- low: hardening gaps; missing tests for data-changing logic.
- info: observations.

## Check every change for
1. Secrets and configuration
   - Passwords, API keys, AWS keys, connection strings or certificates in code, SQL, Dockerfiles, compose files or tests.
   - Credentials passed as Docker build arguments or baked into images.
   - Secrets read from environment and then logged.
2. AWS and Glue jobs
   - Hard-coded account IDs, bucket names or regions where job parameters should be used.
   - IAM permissions broader than the job needs (wildcards on actions or resources).
   - Writing to production buckets or tables from code that looks like a test.
   - Disabling encryption, or writing data unencrypted.
   - Logging or printing rows that contain personal or payment data.
3. SQL
   - DROP, TRUNCATE, DELETE or UPDATE that could affect more rows than intended; missing WHERE clauses.
   - Dynamic SQL built from input (injection).
   - GRANT of broad privileges; changes to users or roles.
   - Scripts that are not re-runnable where they should be (missing IF EXISTS / MERGE logic).
4. Docker and services
   - Containers running as root; unpinned or untrusted base images; `latest` tags.
   - Ports exposed on all interfaces without need; debug modes enabled.
   - Services without authentication on internal endpoints.
5. Payment testing tools
   - Any path by which test tooling could connect to production endpoints, use production credentials, or send real transactions.
   - Hard-coded participant identifiers, account numbers or certificates.
   - Missing clear separation between test and production configuration.
6. General
   - Injection (SQL, command, path), unsafe deserialisation (pickle, yaml.load), `eval`/`exec`.
   - TLS verification disabled (`verify=False`), weak cryptography.
   - Changes to CI workflows, `.ndpx/` config or CODEOWNERS that weaken checks.

Do not report style issues. Report only real, specific problems in the changed code.
