.PHONY: install lock lint typecheck test test-postgres migrate run worker

install:
	uv sync

lock:
	uv lock

lint:
	uv run ruff check backend

typecheck:
	uv run mypy backend/src

test:
	uv run pytest -m 'not postgres'

test-postgres:
	TEST_DATABASE_URL=$${TEST_DATABASE_URL:?set TEST_DATABASE_URL} uv run pytest -m postgres

migrate:
	uv run alembic -c backend/alembic.ini upgrade head

run:
	uv run uvicorn reconcile.api.app:app --app-dir backend/src

worker:
	uv run reconcile-worker
