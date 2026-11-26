# Local development without Docker. Needs Postgres 16 on :5432 and Redis on :6379.
API = apps/api
WEB = apps/web

.PHONY: setup migrate seed api worker web test test-api test-web e2e lint tokens

setup:            ## install API and web dependencies
	cd $(API) && uv venv -q .venv && uv pip install -q --python .venv/bin/python -e . pytest pytest-asyncio httpx fakeredis ruff
	cd $(WEB) && npm ci

migrate:          ## apply database migrations
	cd $(API) && .venv/bin/alembic upgrade head

seed:             ## roles, default plans, model weights, first admin (VERITY_ADMIN_EMAIL / _PASSWORD)
	cd $(API) && .venv/bin/python -m app.seed

api:              ## API on :8000 (emails go to the in-memory outbox at /api/dev/outbox)
	cd $(API) && VERITY_EMAIL_BACKEND=$${VERITY_EMAIL_BACKEND:-memory} .venv/bin/uvicorn app.main:app --reload --port 8000

worker:           ## renewals, stale token holds, audit retention
	cd $(API) && .venv/bin/python -m app.worker

web:              ## web app on :3000
	cd $(WEB) && npm run dev

lint:
	cd $(API) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests
	cd $(WEB) && npm run lint && npm run typecheck

test-api:         ## needs a database named verity_test
	cd $(API) && .venv/bin/pytest -q

test-web:
	cd $(WEB) && npm run lint && npm run typecheck && npm run build

e2e:              ## needs `make api` and the built web app running; clears rate limits first
	redis-cli FLUSHDB >/dev/null
	cd $(WEB) && npm run test:e2e

test: lint test-api test-web

tokens:           ## regenerate tokens.css after editing src/design/tokens.json
	cd $(WEB) && npm run tokens
