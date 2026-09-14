from __future__ import annotations

import os

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from reconcile.persistence.db import normalize_database_url

MIGRATION_LOCK_ID = 7_354_131_969_130_238_719


def migrate() -> None:
    raw_url = os.getenv("MIGRATION_DATABASE_URL")
    if not raw_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required for hosted startup")
    engine = create_engine(normalize_database_url(raw_url), poolclass=NullPool, pool_pre_ping=True)
    config = Config("backend/alembic.ini")
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_ID})
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_ID})
    engine.dispose()


def main() -> None:
    migrate()
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "reconcile.api.app:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
