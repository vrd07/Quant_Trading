---
description: Audit, document, and manage environment variable usage across the project.
---

## Instructions

When invoked, scan the codebase for environment variable usage and produce a report + updated `.env.example`.

### Step 1 — Discovery
Search for all environment variable references across the project:
- `process.env.VARIABLE_NAME` (Node.js)
- `import.meta.env.VARIABLE_NAME` (Vite)
- `os.environ.get('VARIABLE_NAME')` (Python)
- Any other env access patterns relevant to the project stack

### Step 2 — Audit Report

For each variable found, report:

| Variable | Location(s) | Required? | Has Default? | Documented in .env.example? |
|----------|-------------|-----------|--------------|------------------------------|
| `DATABASE_URL` | `src/db/client.ts:12` | ✅ Yes | ❌ No | ✅ Yes |
| `DEBUG_MODE` | `src/logger.ts:5` | ❌ No | ✅ `false` | ❌ Missing |

Flag:
- 🚨 **Missing from .env.example** — undocumented variables
- ⚠️ **Used without a default** — will crash if not set
- ✅ **Has a sensible default** — safe to omit in development

### Step 3 — Generate / Update .env.example

Produce a clean `.env.example` file:

```dotenv
# ─── Database ────────────────────────────────────────────────
DATABASE_URL=postgres://user:password@localhost:5432/antigravity

# ─── Auth ────────────────────────────────────────────────────
JWT_SECRET=your-secret-here
JWT_EXPIRES_IN=15m

# ─── Server ──────────────────────────────────────────────────
API_PORT=3000
NODE_ENV=development

# ─── External Services ───────────────────────────────────────
# STRIPE_SECRET_KEY=sk_test_...
# SENDGRID_API_KEY=SG....
```

Rules:
- Group related variables with comments.
- Use placeholder values that communicate the expected format (e.g. `postgres://user:password@host/db`).
- Comment out optional variables with a `#`.
- Never include real values — not even from development environments.