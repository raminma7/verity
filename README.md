# Verity

AI customer support in one workspace: a knowledge base (RAG), an embeddable chatbot and a ticketing system, with email built in. The full product plan is in [`docs/product-plan.md`](docs/product-plan.md).

This repository holds **Phases 1 to 3**: accounts, workspaces, roles, plans, subscriptions and the AI token quota (Phase 1); the knowledge base, retrieval-augmented answers with citations, the embeddable chatbot with live handoff to people, and the Redis job queue (Phase 2); and ticket categories, tickets, the form builder, and chatbot forms that the AI offers and prefills and that open tickets (Phase 3).

| Phase | Scope | State |
|---|---|---|
| 1 | Registration, email verification, auth, 2FA, workspaces, RBAC, plans, subscriptions, token quota, customer dashboard, system admin | **Done** |
| 2 | Knowledge base, RAG, document processing, crawling, chatbot, live handoff, live token tracking | **Done** |
| 3 | Form builder, chatbot forms that open tickets, ticket categories, ticket management | **Done** |
| 4 | Email integration, spam detection, automatic replies, threading | Next |
| 5 | External API tools, routing, analytics, real payments (Stripe) | |

## Layout

```
apps/api   FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL · Redis
apps/web   Next.js 15 (App Router) · TypeScript · the Verity design system
docs/      product plan
```

### API (`apps/api`)

- `app/routers/` — `auth`, `me`, `workspaces` (members, roles, invitations, audit), `billing`, `usage`, `admin`, `knowledge` (sources, retrieval settings, Test retrieval), `chatbots` (settings, conversations, replies, Ask, conversation → ticket), `widget` (the public chatbot API, including forms), `tickets` (categories, tickets, notes, replies), `forms` (the form builder)
- `app/services/` — `quota` (token reserve/settle), `subscriptions` (periods, renewals), `payments` (provider interface + mock), `permissions` (role catalogue), `email`, `audit`, `ingest/` (parsers, chunker, pipeline), `crawler`, `ai/` (Voyage embeddings, Claude generation, the `Meter` every AI call goes through), `rag` (hybrid retrieval, cited answers), `conversations` (answers and the handoff), `tickets` (numbering, timeline, status, reopen on reply), `forms` (field definitions and value checks), `forms_ai` (form activation and prefill, and the handoff timeout), `storage`
- `app/core/deps.py` — session auth, `workspace_context` (tenant resolution) and `require(...)` (permission and subscription checks)
- `app/jobs/` + `app/worker.py` — the SAQ job queue on Redis: ingestion, crawls and deletions, the handoff check scheduled when a conversation is handed over, plus cron jobs for website re-sync (every 15 minutes), renewals, stale token holds and audit retention (every 5 minutes) and lost handoff checks (every minute)

### Web (`apps/web`)

- `src/design/tokens.json` is copied from the **Verity design system** artifact and is the single source of truth; `npm run tokens` generates `tokens.css` (light and dark).
- `src/design/verity.css` is the design system's component stylesheet, unchanged. `src/components/ui.tsx` ports its components (Button, Badge, Field, Segmented, Switch, Icon…) to typed React with the same `vr-` classes.
- `src/design/app.css` adds what the design system doesn't have yet (plan cards, usage meters, banners, tables, the app shell), built only from its tokens and rules: teal acts, lime marks evidence, no gradients, status always has a word.
- `src/components/rag.tsx` ports the design system's Ask and Library components (QueryBar, AnswerCard, Citation, SourceCard, RelevanceMeter, PipelineTrace, DocumentRow, FileTag); the screens follow its AppLibrary, AppUpload, AppDocument, AppAsk, AppHistory and AppSettings layouts.
- `src/components/chat-form.tsx` renders a chatbot form exactly as customers see it: the widget uses it, and so does the form builder's live preview. Tickets, the ticket detail, Forms and Settings → Tickets aren't in the design system yet, so they are built only from its tokens and rules (`app.css`, Phase 3 section).
- `public/widget.js` is the one-line embed; `src/app/widget/[key]` is the chat that runs inside its iframe, and `src/middleware.ts` sets that page's `frame-ancestors` from the chatbot's authorized domains.
- The browser only talks to Next; `/api/*` is proxied to FastAPI so the session cookie stays first-party.

## How the important parts work

**Sign-up.** Register → a single-use, hashed, 24-hour link is emailed → verifying it creates a workspace with the user as Workspace Owner and signs them in. Unverified users can sign in but every workspace route returns `email_not_verified`. Register, resend and forgot-password give the same answer whether or not an account exists.

**Sessions.** Opaque random token in an `httpOnly`, `SameSite=Lax` cookie; only its SHA-256 is stored. Password reset revokes every session; a password change revokes all but the current one. Writes from foreign origins are refused. Passwords use Argon2id; 2FA secrets are Fernet-encrypted; recovery codes are hashed.

**Rate limits.** Redis sliding windows on login (per IP), register, verification and reset emails (per IP and per address), and 2FA. Five failed logins lock the address for 15 minutes.

**Tenancy and roles.** Every workspace route resolves the workspace through the caller's membership and filters on that ID, never on an ID from the request. System roles: Owner, Administrator, Support Agent, Viewer; workspaces can add custom roles. Nobody can grant a role or permission above their own; only the Owner can transfer ownership. Tests cover cross-tenant access.

**Plans and subscriptions.** Admins define price, cycle, token allowance and limits (documents, domains, crawled pages, chatbot domains, email accounts, members, storage). A subscription snapshots those terms each period, so plan edits reach existing customers at renewal. Upgrades start a new period at once; downgrades are scheduled for renewal. Without a plan, settings, plans and billing stay open and everything else is gated (`subscription_required`).

**Payments.** `PaymentProvider` has a mock implementation: checkout → confirm → activation, idempotent like a webhook. Stripe replaces it in Phase 5 without touching the activation logic.

**Token quota.** Each AI call will `reserve` an estimate, call the model, then `settle` the real cost (or `release` on failure). `reserve` is one conditional `UPDATE … WHERE allowance + bonus − reserved − consumed ≥ n`, so concurrent requests can't overrun the allowance (tested with 50 parallel reservations). Weighted tokens = input × weight + output × weight, per model and optionally per operation; customers see the weights. Alerts go out once each at 80%, 90% and 100%. Allowances reset each period with no rollover. Admins can add or remove tokens, and every adjustment is audit-logged.

**Knowledge base.** Upload PDF, DOCX, TXT, CSV and XLSX files (checked by content, not just extension; archives that would expand too far are refused) or add a website. A job parses each file into sections that remember where they came from (page, heading path, sheet and rows), splits them into ~500-token passages with overlap, embeds them with Voyage (`voyage-4`, 1024 dimensions) and swaps the new passages in atomically. Failures explain themselves ("No text layer found. This PDF looks scanned…"). Reprocessing bumps a version, so a late job can't overwrite a newer run. Plan limits (`documents`, `domains`, `crawled_pages`, `storage_mb`) are checked on the backend under a per-workspace lock, so parallel uploads can't pass them.

**Crawling.** Breadth-first from the start address and its sitemaps, on the same site only, honouring robots.txt (including crawl-delay), path filters, depth and the plan's page limit. trafilatura keeps each page's main text. Pages are hashed, so a re-sync (daily, weekly or on demand) only re-embeds pages that changed and drops pages that disappeared. Every URL and redirect is resolved and refused if it points at a private, loopback, link-local (cloud metadata) or reserved address; direct connections are pinned to the address that was checked.

**Retrieval and answers.** Retrieval is hybrid and exact inside one workspace: pgvector cosine similarity and Postgres full-text search each return their top 40, fused with weighted reciprocal-rank fusion, then cut to top-k above a minimum relevance (a passage can also qualify on a strong keyword match, such as a product name or error code). Workspaces tune top-k, cut-off and hybrid weight, and can test a question without generating. Passages go to Claude as `search_result` blocks with citations on, so the answer's citations come back from the API pointing at exact passages; we number them, map them to chunks and drop any that don't match. No passage above the cut-off means no model call: the chatbot says it couldn't find it and offers a person. Passages are framed as data, never instructions. Every answer stores its trace (embed, retrieve, generate, verify citations) for the "How it was answered" tab.

**Chatbot and widget.** Each chatbot has a name, logo, colour, position, theme, language, tone, welcome message, behaviour instructions, model (Claude Opus 5.5 by default; Sonnet 5.5 and Haiku 5.5 available), a no-answer message and a fallback message for when AI is unavailable (allowance used up, subscription lapsed, provider down or refusing). Sites embed one script tag; the chat runs in an iframe that only renders inside the chatbot's authorized domains (`frame-ancestors`), and the API refuses any call whose embedding origin isn't authorized. The plan's `chatbot_domains` limit counts domains across all chatbots. Visitors are anonymous: each conversation has a random token whose hash is stored. Widget calls are rate limited per visitor, IP and chatbot. Answers stream over server-sent events.

**Live handoff.** A visitor can ask for a person at any time, and is offered one when the assistant can't answer. The conversation moves to "Needs a person", members who can reply are notified in the app and by email, and the assistant stays silent: new messages wait for the team. Agents reply from the dashboard (replying to an open conversation takes it over), the widget picks replies up within seconds, and resolving hands the conversation back to the assistant. `services/conversations.escalate()` is the single entry point, ready for Phase 3 to open a ticket. See "Human Handoff and Chatbot Forms" in the product plan.

**Tickets.** Tickets come from chatbot forms, from agents turning a chat into a ticket, and from the team (manual); each has a `source`, and `email` is reserved for Phase 4. Numbers are per workspace (#1, #2…), taken with one `UPDATE … RETURNING`, so parallel tickets never share one. Statuses New → Open → Pending → Resolved → Closed follow a transition table on the server; assigning a new ticket or replying to it opens it. Only members whose role can work tickets can be assigned. Internal notes and every change go on the ticket's timeline; the customer never sees notes. Search covers subject, description, requester, form values and `#number`, with filters for status, priority, category, assignee and source. A customer's message in the chat lands on its ticket: Pending moves to Open, Resolved reopens when the workspace's `reopen_on_reply` is on, and Closed stays closed. Categories are created, renamed, archived and ordered per workspace (`tickets.configure`: Owner and Administrator); a category can't be archived while forms still use it.

**Forms.** Forms have text, email, number, select, multi-select, date, long-text and checkbox fields, each required or optional with validation (lengths, patterns, ranges, number of choices, date limits). Definitions are checked when saved; submitted values are checked again on the server, and patterns run with a timeout. Each form maps to one ticket category and is offered by the chatbots linked to it. Changing fields bumps the form's version, and a ticket keeps the fields exactly as the customer saw them. Every workspace has a built-in "Contact the team" form (name, email, message) that must keep a required email.

**Chatbot forms.** After each answer, the chatbot's own model reads the conversation and the conditions of the chatbot's forms ("the customer wants to return an order") and, when one clearly fits, offers that form in the chat as a `messages.kind = "form"` block. When the customer opens it, the model prefills the fields from what they said; every value is re-checked against its field, and consent checkboxes are never ticked for them. The customer reviews, edits and presses "Confirm and send"; **nothing is submitted without that click** (the API refuses a submission without `confirmed`). Both calls use structured outputs through the `Meter` (operations `form_activation` and `form_prefill`). Without AI (allowance used up, provider down), forms still work: nothing is offered on its own, customers can open forms themselves under "Leave a request", and prefill fills only the name and email the chat already has.

**Handoff and forms together.** "Talk to a person" goes through `escalate()` as before. When the chatbot's ticket fallback is on, the email is no longer required up front: while waiting, the visitor may leave it in an optional box ("Might you leave before someone replies?"). `escalate()` schedules a check on the job queue. If nobody from the team replies within the chatbot's wait (two minutes by default): when the visitor left an email, a ticket opens at once with it, in the category of the form that fits the conversation, so nothing is lost if they've gone; otherwise the chat offers that form, or the contact form, and sending it opens the ticket. Either way the assistant then takes the chat back with the ticket number. The check locks the conversation, so the job and the sweeper never open two tickets. `escalate()` is still the single path: live handoff, a ticket from a form, and an agent's "Create ticket" on a conversation all go through it. Agents reply to a ticket in its chat, and resolving the ticket hands the chat back to the assistant.

**Token tracking.** Every embedding (documents and questions) and every generation goes through `Meter`: reserve an estimate, call the provider, settle the real usage (or release the hold on any error, cancellation or disconnect). Usage is recorded per operation (`embedding`, `chat_response`, `kb_ask`) and model, including the model that actually answered when a server-side fallback ran.

**Job queue.** SAQ on Redis: jobs are keyed so the same source isn't processed twice at once, retried with backoff on transient failures (provider rate limits, outages) and recorded on the row they work on, so customers see a status, not a log line. Cron jobs are de-duplicated across workers, so you can run several.

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
make worker   # terminal 2: ingestion, crawling and scheduled jobs
make web      # terminal 3
```

AI providers are configured with `VERITY_ANTHROPIC_API_KEY` and `VERITY_VOYAGE_API_KEY`. To run without keys, set `VERITY_LLM_PROVIDER=fake` and `VERITY_EMBEDDING_PROVIDER=fake`: answers and embeddings become deterministic and local, but still go through the real quota, queue and citation paths. A free Voyage account allows only a few requests per minute; ingestion waits out rate limits and retries, but production needs a paid Voyage plan.

`make api` uses the in-memory email backend: open the newest messages at http://localhost:8000/api/dev/outbox. Set `VERITY_EMAIL_BACKEND=smtp` to send through Mailpit or a real server.

In development, `POST /api/dev/simulate-usage` runs the real reserve → settle path without calling a model, so the usage pages have data.

## Tests

```bash
make test-api   # 191 API tests: Phase 1, plus parsers, chunker, crawler and SSRF guard, every plan limit (with parallel uploads), metering,
                #   hybrid retrieval and citations, chatbot and widget, handoff, the Redis worker, roles and tenant isolation;
                #   Phase 3: categories, every field type and rule, tickets and their workflow, search, reopen on reply, form
                #   activation, prefill and confirmation, the handoff timeout (with a real worker), every role and tenant isolation
make test-web   # lint, typecheck, production build
make e2e        # Playwright: Phase 1 flows, plus upload → indexed → Ask with citations → chatbot on a customer site → handoff →
                #   agent reply reaches the visitor → widget refused on an unauthorized site; Phase 3: categories → form builder →
                #   the chatbot offers and prefills a form → ticket → reopen on reply → the handoff fallback → chat to ticket
                #   (needs `make worker` and fake providers)
```

## Decisions worth knowing

- **Opaque server-side sessions instead of JWTs** — instant revocation on reset, suspension and sign-out everywhere.
- **Plain CSS from the design system instead of Tailwind** — the design system ships `vr-` component styles; reusing them unchanged keeps the app identical to it.
- **SAQ for the job queue** — async like the API, Redis-backed, with keys, retries, timeouts and cron. (arq is in maintenance mode; Celery and RQ are synchronous.)
- **Exact vector search, no HNSW index** — the installed pgvector (0.6) can't filter inside an HNSW scan, so a global index filtered by workspace could silently return too few passages. Each workspace's chunks are bounded by its plan, so an exact scan over its own rows (btree on `workspace_id`) is fast, complete and strictly isolated. With pgvector ≥ 0.8, an HNSW index with `hnsw.iterative_scan` becomes worth adding for very large workspaces.
- **Claude's native citations** instead of asking the model to write `[1]` markers: the API ties each cited span to a passage, so citations can't be invented.
- **An iframe for the widget** — the customer's CSS and scripts can't touch it, it reuses the design system, and the browser itself enforces the authorized domains.
- **Fake AI providers for tests, CI and e2e** — deterministic, free and offline, while the quota, queue, retrieval and citation code paths stay real.
- **Mock payments** until Phase 5, behind the provider interface.
- **Forms use the chatbot's own model** for activation and prefill, so one setting decides cost and quality for each chatbot. Each AI answer from a chatbot with forms is followed by one short classification call (citations and structured outputs can't share a request).
- **A scheduled job plus a sweeper for the handoff timeout**: the job fires on time, the every-minute sweeper catches checks lost while no worker ran, and each escalation gets at most one offer.
- **Model weights are business settings.** The seeded weights and list prices for the Claude 5.5 models and `voyage-4` are placeholders for admins to set under Admin → Model weights.
