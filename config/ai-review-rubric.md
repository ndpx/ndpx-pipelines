# ndpx AI security review rubric

Context: internal web apps for ndpx staff. Stack: React + TypeScript (Vite), Supabase (Postgres, Auth, Storage,
Edge Functions), hosted on Netlify. Sign-in is Microsoft Entra ID only. Apps may hold personal and Confidential data
protected under Brunei's PDPO 2025. Code is often written with AI assistance.

## Severity guide
- critical: direct exposure of personal or confidential data, or full account/database takeover.
- high: an authenticated user can read or change data they should not; secrets exposed; authentication bypass.
- medium: weakness that needs another flaw to exploit; missing validation on sensitive input; unsafe defaults.
- low: hardening gaps, poor error handling that leaks detail, missing tests for security behaviour.
- info: observations and questions.

## Check every change for
1. Database access control
   - New tables in `public` without `enable row level security` and explicit policies.
   - Policies using `true`, or checking user-supplied columns instead of `auth.uid()`.
   - Role checks done in the browser instead of in RLS or a database function.
   - SECURITY DEFINER functions without `set search_path = ''`, or exposed in `public` without need.
   - Views without `security_invoker = true`.
   - Grants to `anon`.
2. Secrets and keys
   - Supabase secret or service_role keys, database URLs or any credential in frontend code or `VITE_` variables.
   - Secrets committed in files, tests, fixtures or comments.
3. Authentication
   - Any sign-in method other than Entra ID (azure provider). Email/password, magic links, anonymous sign-in.
   - Trusting user metadata (`user_metadata`, `raw_user_meta_data`) for authorisation; it is user-editable.
4. Data handling
   - Real personal data in seed files, tests or fixtures.
   - Personal data written to logs, console, analytics or error messages.
   - File uploads without type/size checks, or to public storage buckets.
5. Web security
   - `dangerouslySetInnerHTML`, unsanitised HTML or Markdown rendering, `eval`, dynamic script loading.
   - Changes that weaken `netlify.toml` security headers or Content Security Policy.
   - Open redirects (redirect targets taken from URL parameters).
6. Edge Functions and server code
   - Using the service role key where the user's own token would do.
   - Missing authentication check at the start of a function.
   - Missing input validation; SQL built by string concatenation.
7. Changes that bypass ndpx process
   - Edits to CI workflows, `.ndpx/` config, CODEOWNERS or this rubric that weaken checks.
   - Schema changes made outside `supabase/migrations/`.

Do not report style issues. Do not report issues in unchanged code unless the change makes them exploitable.
