from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "postgresql+psycopg://localhost/reconcile")
    if value.startswith("sqlite"):
        raise RuntimeError("PostgreSQL is required; SQLite is not supported")
    return value


def make_engine(url: str | None = None) -> Engine:
    value = url or database_url()
    if value.startswith("sqlite"):
        raise RuntimeError("PostgreSQL is required; SQLite is not supported")
    return create_engine(value, pool_pre_ping=True, pool_size=2, max_overflow=0)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_schema() -> None:
    Base.metadata.create_all(engine)


def readiness() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
