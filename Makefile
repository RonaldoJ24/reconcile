.PHONY: setup install lock dev check lint typecheck test test-postgres \
	test-integration test-e2e migrate run worker cost-preflight data-dev train \
	evaluate-dev evaluate-release smoke-live perf

setup:
	uv sync --frozen
	@if test -d frontend; then pnpm --dir frontend install --frozen-lockfile; fi

install:
	uv sync

lock:
	uv lock

dev:
	@test -d frontend || { echo "frontend/ is not present on this branch" >&2; exit 2; }
	@trap 'kill 0' EXIT INT TERM; \
		uv run uvicorn reconcile.api.app:app --app-dir backend/src --reload & \
		pnpm --dir frontend dev

check: lint typecheck test
	@if test -d frontend; then \
		pnpm --dir frontend typecheck && \
		pnpm --dir frontend test && \
		pnpm --dir frontend build; \
	fi
	@git diff --check

lint:
	uv run ruff check backend

typecheck:
	uv run mypy backend/src

test:
	uv run pytest -m 'not postgres'

test-postgres:
	@TEST_DATABASE_URL=$${TEST_DATABASE_URL:?set TEST_DATABASE_URL}; \
	ALLOW_DESTRUCTIVE_TEST_DB=$${ALLOW_DESTRUCTIVE_TEST_DB:?set ALLOW_DESTRUCTIVE_TEST_DB=1}; \
	test "$$ALLOW_DESTRUCTIVE_TEST_DB" = 1 || { echo "ALLOW_DESTRUCTIVE_TEST_DB must equal 1" >&2; exit 2; }; \
	uv run pytest -m postgres

test-integration: test-postgres

test-e2e:
	@test -d frontend || { echo "frontend/ is required" >&2; exit 2; }
	@E2E_BASE_URL=$${E2E_BASE_URL:?set E2E_BASE_URL to the running same-origin UI}; \
	pnpm --dir frontend e2e

migrate:
	uv run alembic -c backend/alembic.ini upgrade head

run:
	uv run uvicorn reconcile.api.app:app --app-dir backend/src

worker:
	uv run reconcile-worker

cost-preflight:
	@echo "Read-only cost/ownership preflight; no provider generation or resource mutation."
	@gh --version | sed -n '1p'
	@gh api user --jq '"GitHub owner: " + .login'
	@render --version
	@render whoami | sed -n '/^Name:/p;/^ID:/p'
	@neon --version
	@neon me --output json | jq '{account: .login, plan}'
	@neon projects list --org-id org-broad-boat-80792120 --output json | \
		jq '{project_count: length, project_names: map(.name)}'

data-dev train evaluate-dev evaluate-release smoke-live perf:
	@echo "$@ belongs to a later authorized phase and is intentionally unavailable in Phases 0-2." >&2
	@exit 2
