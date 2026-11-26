# Verity

AI customer support in one workspace: a knowledge base (RAG), an embeddable chatbot and a ticketing system, with email built in. The full product plan is in [`docs/product-plan.md`](docs/product-plan.md).

This repository holds **Phase 1**: accounts, workspaces, roles, plans, subscriptions and the AI token quota that later phases build on.

| Phase | Scope | State |
|---|---|---|
| 1 | Registration, email verification, auth, 2FA, workspaces, RBAC, plans, subscriptions, token quota, customer dashboard, system admin | **Done** |
| 2 | Knowledge base, RAG, document processing, crawling, chatbot, live token tracking | Next |
| 3 | Form builder, ticket categories, ticket management | |
| 4 | Email integration, spam detection, automatic replies, threading | |
| 5 | External API tools, routing, analytics, real payments (Stripe) | |

## Layout

```
apps/api   FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL · Redis
apps/web   Next.js 15 (App Router) · TypeScript · the Verity design system
docs/      product plan
```

### API (`apps/api`)

- `app/routers/` — `auth`, `me`, `workspaces` (members, roles, invitations, audit), `billing`, `usage`, `admin`
- `app/services/` — `quota` (token reserve/settle), `subscriptions` (periods, renewals), `payments` (provider interface + mock), `permissions` (role catalogue), `email`, `audit`
- `app/core/deps.py` — session auth, `workspace_context` (tenant resolution) and `require(...)` (permission and subscription checks)
- `app/worker.py` — renewals, releasing stale token holds, audit retention (every 5 minutes)

### Web (`apps/web`)

- `src/design/tokens.json` is copied from the **Verity design system** artifact and is the single source of truth; `npm run tokens` generates `tokens.css` (light and dark).
- `src/design/verity.css` is the design system's component stylesheet, unchanged. `src/components/ui.tsx` ports its components (Button, Badge, Field, Segmented, Switch, Icon…) to typed React with the same `vr-` classes.
- `src/design/app.css` adds what the design system doesn't have yet (plan cards, usage meters, banners, tables, the app shell), built only from its tokens and rules: teal acts, lime marks evidence, no gradients, status always has a word.
- The browser only talks to Next; `/api/*` is proxied to FastAPI so the session cookie stays first-party.

## How the important parts work

**Sign-up.** Register → a single-use, hashed, 24-hour link is emailed → verifying it creates a workspace with the user as Workspace Owner and signs them in. Unverified users can sign in but every workspace route returns `email_not_verified`. Register, resend and forgot-password give the same answer whether or not an account exists.

**Sessions.** Opaque random token in an `httpOnly`, `SameSite=Lax` cookie; only its SHA-256 is stored. Password reset revokes every session; a password change revokes all but the current one. Writes from foreign origins are refused. Passwords use Argon2id; 2FA secrets are Fernet-encrypted; recovery codes are hashed.

**Rate limits.** Redis sliding windows on login (per IP), register, verification and reset emails (per IP and per address), and 2FA. Five failed logins lock the address for 15 minutes.

**Tenancy and roles.** Every workspace route resolves the workspace through the caller's membership and filters on that ID, never on an ID from the request. System roles: Owner, Administrator, Support Agent, Viewer; workspaces can add custom roles. Nobody can grant a role or permission above their own; only the Owner can transfer ownership. Tests cover cross-tenant access.

**Plans and subscriptions.** Admins define price, cycle, token allowance and limits (documents, domains, crawled pages, chatbot domains, email accounts, members, storage). A subscription snapshots those terms each period, so plan edits reach existing customers at renewal. Upgrades start a new period at once; downgrades are scheduled for renewal. Without a plan, settings, plans and billing stay open and everything else is gated (`subscription_required`).

**Payments.** `PaymentProvider` has a mock implementation: checkout → confirm → activation, idempotent like a webhook. Stripe replaces it in Phase 5 without touching the activation logic.

**Token quota.** Each AI call will `reserve` an estimate, call the model, then `settle` the real cost (or `release` on failure). `reserve` is one conditional `UPDATE … WHERE allowance + bonus − reserved − consumed ≥ n`, so concurrent requests can't overrun the allowance (tested with 50 parallel reservations). Weighted tokens = input × weight + output × weight, per model and optionally per operation; customers see the weights. Alerts go out once each at 80%, 90% and 100%. Allowances reset each period with no rollover. Admins can add or remove tokens, and every adjustment is audit-logged.

## Run it

### With Docker

```bash
docker compose up --build
```

App on http://localhost:3000, caught emails on http://localhost:8025 (Mailpit). The first admin is `admin@example.com` / `Admin-pass-123` unless you set `VERITY_ADMIN_EMAIL` and `VERITY_ADMIN_PASSWORD`.

### Without Docker

Needs Python 3.12+, Node 22, PostgreSQL 16 and Redis, plus [uv](https://docs.astral.sh/uv/).

```bash
make setup
createdb verity && createdb verity_test
make migrate
VERITY_ADMIN_EMAIL=admin@example.com VERITY_ADMIN_PASSWORD='Admin-pass-123' make seed
make api      # terminal 1
make web      # terminal 2
```

`make api` uses the in-memory email backend: open the newest messages at http://localhost:8000/api/dev/outbox. Set `VERITY_EMAIL_BACKEND=smtp` to send through Mailpit or a real server.

In development, `POST /api/dev/simulate-usage` runs the real reserve → settle path without calling a model, so the usage pages have data.

## Tests

```bash
make test-api   # 45 API tests: auth, 2FA, rate limits, RBAC, tenant isolation, billing, renewals, quota concurrency, admin
make test-web   # lint, typecheck, production build
make e2e        # Playwright: sign up → verify → buy a plan → usage → invite → teammate accepts; admin; mobile
```

## Decisions worth knowing

- **Opaque server-side sessions instead of JWTs** — instant revocation on reset, suspension and sign-out everywhere.
- **Plain CSS from the design system instead of Tailwind** — the design system ships `vr-` component styles; reusing them unchanged keeps the app identical to it.
- **Background jobs in a small asyncio worker.** Phase 2 adds a real queue for ingestion and crawling.
- **Mock payments** until Phase 5, behind the provider interface.
