from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    value = normalize_database_url(
        os.getenv("DATABASE_URL", "postgresql+psycopg://localhost/reconcile")
    )
    if value.startswith("sqlite"):
        raise RuntimeError("PostgreSQL is required; SQLite is not supported")
    return value


def make_engine(url: str | None = None) -> Engine:
    value = normalize_database_url(url or database_url())
    if value.startswith("sqlite"):
        raise RuntimeError("PostgreSQL is required; SQLite is not supported")
    return create_engine(value, pool_pre_ping=True, pool_size=2, max_overflow=0)


def normalize_database_url(value: str) -> str:
    """Use psycopg3 for provider URLs emitted as postgres/postgresql schemes."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def readiness() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
