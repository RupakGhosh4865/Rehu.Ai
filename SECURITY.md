# Security — secret handling (Phase 0.1)

## 1. Git-history secret scan — findings

Scanned all 17 commits across all refs. **No real secret values are committed.**

| What was searched | Result |
|---|---|
| Any `.env` / `.env.local` / `.env.production` file ever committed | **None** (only `.env.example` is tracked) |
| Real key value prefixes in history (`sk-proj-…`, `sk_live_…`, `whsec_…`) | **None** — only documentation placeholders (`sk-proj-...`, `sk_...`) in `FRONTEND_DEPLOYMENT.md` and `.env.example` |
| Variable-name occurrences (`OPENAI_API_KEY`, `JWT_SECRET`, …) | Present only in `.env.example`, `config.py`, and docs (expected — no values) |

`.gitignore` correctly ignores `.env` and `*.env.local`. The live keys exist
**only** in the local, untracked `backend/.env`.

> The real API keys live in the local `.env`. Treat them as compromised anyway
> (they were shared in chat) and rotate them in each provider dashboard, then
> update the host's secret store (Railway/Render/Fly).

## 2. If a `.env` is ever found in history — purge commands (DO NOT run blindly)

Back up the repo first, coordinate with anyone who has clones, then:

```bash
# Option A — git filter-repo (recommended)
pip install git-filter-repo
git filter-repo --path backend/.env --invert-paths --force

# Option B — BFG
bfg --delete-files .env
git reflog expire --expire=now --all && git gc --prune=now --aggressive

# Then force-push the rewritten history (rewrites SHAs for everyone):
git push --force --all
git push --force --tags
```

Rewriting history changes every commit hash and requires all collaborators to
re-clone. **Rotate the leaked keys regardless** — purging history does not
un-leak a key that was already pushed.

## 3. Pre-commit secret scanning

A pre-commit hook (`gitleaks` + `detect-private-key`) blocks commits containing
secrets. Config: [`.pre-commit-config.yaml`](.pre-commit-config.yaml),
allowlist: [`.gitleaks.toml`](.gitleaks.toml).

```bash
make install-hooks     # once per clone
make scan-secrets      # manual full scan (repo + history)
```

## 4. Startup secret enforcement

`security.check_secrets()` is the single boot-time gate. When
`ENVIRONMENT=production` the app **refuses to boot** if any of these is missing
or a known default/placeholder: `SECRET_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD`,
`STRIPE_WEBHOOK_SECRET`, `FORCE_HTTPS`, a Postgres `DATABASE_URL`, explicit
`CORS_ORIGINS`, `OPENAI_API_KEY`, `LIVEAVATAR_API_KEY`. Tested in
`backend/tests/test_prod_hardening.py`.
