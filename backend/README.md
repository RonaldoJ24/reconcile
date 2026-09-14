# Reconcile backend

The backend is a synchronous FastAPI service backed by PostgreSQL. Domain
matching and financial validation live under `src/reconcile/domain` and do not
depend on the transport or persistence layers.

Set `DATABASE_URL` to a PostgreSQL URL, run `uv sync`, then apply migrations:

```sh
uv run alembic -c backend/alembic.ini upgrade head
uv run uvicorn reconcile.api.app:app --app-dir backend/src
```

`reconcile-worker` claims one leased job and exits. Pass `--loop` only for a
local process that should continue while work is available. PostgreSQL is
required in every profile; SQLite is intentionally rejected.
