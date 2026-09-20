# Security Audit Playbook (reusable)

A repeatable, multi-phase process to take any repo from "I think it's fine" to
"a client under NDA can trust it." Run it per-repo. Each phase produces
artifacts and is **gated** — don't move on until the prior phase's blockers are
cleared. Hand any phase to an AI agent (Claude Code `/security-review`, or
Nexus) or run the commands yourself.

> Honest framing: nothing is "unhackable." The goal is to **close the holes
> attackers actually use, prove tenant data is isolated, and be able to show a
> client your controls.** That's what unblocks NDA deals — not a buzzword.

**Tailored default stack:** Next.js + Supabase (Postgres + RLS) + Vercel.
Adapt the tools per repo; the phases don't change.

---

## Phase 0 — Scope & threat model (30 min)

Fill this in once per repo. It decides what "secure enough" means.

- **What data does it hold?** (NDA bid data, PII, pricing, client lists → HIGH sensitivity)
- **Tenancy:** multi-tenant SaaS, single-tenant, or self-hosted? (drives the isolation requirement)
- **Who can access it?** users, admins, you, contractors, CI, third parties
- **Deployment:** Vercel prod + previews? Supabase project(s)? secrets where?
- **Worst case:** "Client A's confidential bid is visible to Client B / the public / a hacker." Everything below exists to make that impossible.

**Artifact:** `SECURITY_SCOPE.md` in the repo with the answers above.

---

## Phase 1 — Automated scans ("the whole 9 yards")

Run every category. For each: the tool, the command, and what it catches.
Capture all output into `security/findings-raw/`.

### 1. Secrets in code AND git history  ⭐ (the #1 real-world breach cause)
```bash
# gitleaks — scans working tree + full history
docker run -v "$PWD:/repo" zricethezav/gitleaks:latest detect --source=/repo -v
# or: brew install gitleaks && gitleaks detect -v
# trufflehog — verified live secrets
docker run -v "$PWD:/repo" trufflesecurity/trufflehog:latest filesystem /repo --only-verified
```
Catches: API keys, Supabase service-role keys, tokens committed now OR ever.
**Fix:** rotate any exposed key immediately, remove from history (`git filter-repo`),
move to env/secret manager, add to `.gitignore`.

### 2. Dependency vulnerabilities (SCA)
```bash
npm audit --omit=dev            # or: pnpm audit / yarn npm audit
npx osv-scanner@latest scan .   # cross-ecosystem CVE scan
```
Catches: known-vulnerable packages. **Fix:** `npm audit fix`, bump majors
deliberately, enable Dependabot/Renovate so it stays current.

### 3. Static analysis (SAST)
```bash
docker run --rm -v "$PWD:/src" returntocorp/semgrep semgrep \
  --config=p/owasp-top-ten --config=p/javascript --config=p/nextjs --config=p/typescript
# Best free option for GitHub repos: enable CodeQL (Settings → Code security)
```
Catches: injection, XSS, unsafe patterns, dangerous APIs. **Fix:** per finding.

### 4. Supabase / Postgres RLS audit  ⭐ (this is the NDA isolation guarantee)
For every table:
- Is **Row-Level Security ENABLED**? (`ALTER TABLE x ENABLE ROW LEVEL SECURITY;`)
- Are there policies that scope rows to the **owning user/org/branch**?
- Test as the **anon key** and as a **second user**: can they read another tenant's rows? They must NOT.
```sql
-- list tables WITHOUT rls (these are holes)
select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r' and c.relrowsecurity=false;
```
**Fix:** enable RLS on every table holding tenant data; write `using`/`with check`
policies keyed to `auth.uid()` / org id; never expose the service-role key to the
client; verify with a real second-tenant test.

### 5. Auth & session
Review: password policy, JWT/session expiry & rotation, MFA option, OAuth scopes,
"forgot password" flow, account-enumeration. **Fix:** short-lived tokens, secure
cookie flags (`HttpOnly; Secure; SameSite`), rate-limit auth endpoints.

### 6. Secrets management / env
- `.env*` in `.gitignore`? committed `.env`? (grep the repo + history)
- Vercel env vars scoped correctly (Production vs Preview vs Development)?
- Service-role / admin keys ONLY server-side, never in `NEXT_PUBLIC_*`.
**Fix:** move all secrets to Vercel/Supabase env; rotate; least-privilege keys.

### 7. Web/app hardening (headers, CORS, cookies)
Check security headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options),
CORS allowlist (no `*` with credentials), cookie flags.
```bash
npx @vercel/security-headers   # or check next.config headers()
curl -sI https://yourapp.com | grep -iE 'content-security|strict-transport|x-frame|x-content'
```
**Fix:** add a strict CSP + HSTS in `next.config` headers / middleware.

### 8. Infra / Vercel
- Branch protection on `main`; required reviews.
- **Preview deployments**: are they public? Preview builds can leak data — gate with Vercel Auth/password.
- No secrets in build logs.

### 9. Injection & input validation
SQL (use Supabase params / never string-concat SQL), XSS (escape output,
no `dangerouslySetInnerHTML` with user input), SSRF (validate outbound URLs),
file-upload validation. Covered partly by semgrep #3 — confirm manually.

### 10. Broken access control / IDOR
Can a user fetch `/api/bid/123` for a bid they don't own? Test object-level
authorization on every endpoint that takes an id. **Fix:** check ownership on
every read/write, server-side — never trust the client.

### 11. Logging & audit
Sensitive actions (login, export, data access) logged with who/when; **no
secrets or full PII in logs**. NDA work often wants an access audit trail.

### 12. Supply chain
Lockfile committed & integrity-checked; pin GitHub Actions to SHAs; minimal CI
permissions (`permissions: contents: read`).

**Phase 1 artifact:** `security/findings.md` — one row per finding:
`| id | category | severity | file:line | description | status |`

---

## Phase 2 — Manual review (what scanners can't see)

Scanners miss *logic*. A human/agent reviews:
- **Tenant-isolation logic** end to end — does every data path enforce the boundary?
- **Authorization** on each endpoint (the IDOR check, done by reading code).
- **Business-logic flaws** (e.g., can a user escalate role, see another org's bid via a shared link?).
- **Sensitive data handling** — where bid data flows, who can export it.

**Artifact:** append manual findings to `security/findings.md`.

---

## Phase 3 — Triage & prioritize

Score each finding: **Severity (Critical / High / Medium / Low) × Exploitability.**
- **Critical/High** = exposed secret, missing RLS on tenant data, auth bypass, IDOR → fix before anything else.
- **Medium** = missing headers, weak session config, outdated dep with no known exploit path.
- **Low** = hardening nice-to-haves.

**Artifact:** `security/remediation-plan.md` — findings ordered by priority, each
with an owner and a recommended fix.

---

## Phase 4 — Remediate

Fix top-down (Critical → Low). For each fix:
1. Implement the recommended change.
2. Re-run the scan that found it → confirm it's gone.
3. Mark `status: fixed` in `findings.md` with the commit.

Do NOT skip the re-scan — "fixed" means re-verified clean, not "I edited a file."

---

## Phase 5 — Verify & make it stay secure

- **Re-run ALL of Phase 1** → everything Critical/High is clean.
- **Wire scans into CI** so regressions are caught automatically:
  - gitleaks + `npm audit` + semgrep on every PR (GitHub Actions).
  - Enable Dependabot/CodeQL in repo settings.
- Add the hardening: strict CSP+HSTS, rate limiting, locked-down preview deploys.
- **Single-tenant / self-hosted path** (if NDA clients need it): document how to
  deploy an isolated instance (separate Supabase project + Vercel project per
  client, or a self-host bundle). This is often the real deal-unlock.

**Artifact:** green CI security workflow committed; `security/findings.md` all `fixed`.

---

## Phase 6 — Security one-pager (client-facing) — ONLY after Phase 5 is clean

Generate this from the VERIFIED state. **Only claim what's actually true now.**
Template below — fill the brackets, delete what doesn't apply.

```markdown
# [App Name] — Security Overview

**Purpose:** how [App Name] protects your confidential data.

## Data protection
- All data encrypted in transit (TLS 1.2+) and at rest (AES-256 via Supabase/Postgres).
- Secrets stored in a managed secret store — never in source code.

## Tenant isolation
- Each organization's data is isolated at the database level via Postgres
  Row-Level Security. A user can only ever access their own organization's
  records; this is enforced server-side and verified by automated tests.
- [If offered] Dedicated single-tenant / self-hosted deployment available for
  clients who require their data on an isolated instance.

## Access control
- Authentication via [provider]; sessions are short-lived and rotated.
- Role-based access; object-level authorization on every record.
- Sensitive actions are logged with an audit trail (who / what / when).

## Secure development
- Automated security scanning on every change: secret detection, dependency
  CVE scanning, and static analysis (SAST) run in CI.
- Dependencies kept patched; least-privilege keys and CI permissions.

## Operations
- Hosted on [Vercel / your infra]; production access restricted and logged.
- Incident response: [how you'd notify + remediate].

## NDA handling
- [How you contractually + technically honor NDAs: access limits, data
  segregation, deletion-on-request, no training/secondary use.]

_Last reviewed: [date]. Contact: [you] for a deeper security questionnaire._
```

---

## Quick-start checklist (tl;dr per repo)
1. Phase 0 scope doc.
2. Run: gitleaks · npm audit/osv · semgrep · RLS-enabled check.  ← these 4 catch ~80%.
3. Triage; fix Critical/High (secrets, RLS, IDOR, auth) first; re-scan.
4. Add gitleaks+audit+semgrep to CI.
5. Generate the one-pager from the clean state.

> Don't chase SOC 2 or "unhackable" until the basics above are green and you've
> landed a couple NDA clients. Basics + a single-tenant option + this one-pager
> unblock far more deals than a certificate does at this stage.
