from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reconcile.persistence.models import InterpretationCache


def cache_key(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_fingerprint(payload: Mapping[str, Any]) -> str:
    source_context = {
        "workspace_id": payload.get("workspace_id"),
        "payment": payload.get("payment"),
        "invoices": payload.get("invoices"),
        "credits": payload.get("credits"),
        "source_spans": payload.get("source_spans"),
    }
    return cache_key(source_context)


def load_cached(
    session: Session, *, workspace_id: uuid.UUID, key: str
) -> InterpretationCache | None:
    return session.scalar(
        select(InterpretationCache).where(
            InterpretationCache.workspace_id == workspace_id,
            InterpretationCache.cache_key == key,
        )
    )


def store_cached(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    key: str,
    fingerprint: str,
    requested_model: str,
    response_model: str,
    mode: str,
    result: Mapping[str, Any],
) -> InterpretationCache:
    statement = (
        insert(InterpretationCache)
        .values(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            cache_key=key,
            source_fingerprint=fingerprint,
            requested_model=requested_model,
            response_model=response_model,
            mode=mode,
            result=dict(result),
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "cache_key"])
    )
    session.execute(statement)
    session.commit()
    cached = load_cached(session, workspace_id=workspace_id, key=key)
    if cached is None:
        raise RuntimeError("validated interpretation cache write failed")
    return cached
