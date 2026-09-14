from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(StrictModel):
    invite_token: str | None = Field(default=None, min_length=16, max_length=200)


class SessionResponse(StrictModel):
    mode: str
    expires_at: datetime
    csrf_token: str
    provider_access: bool


class AllocationLine(StrictModel):
    invoice_id: str = Field(min_length=1, max_length=100)
    amount: int = Field(gt=0, le=999_999_999_999)


class CreditAllocationLine(StrictModel):
    credit_note_id: str = Field(min_length=1, max_length=100)
    invoice_id: str = Field(min_length=1, max_length=100)
    amount: int = Field(gt=0, le=999_999_999_999)


class CorrectionRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    cash: list[AllocationLine] = Field(max_length=3)
    credits: list[CreditAllocationLine] = Field(max_length=1)
    reviewer: str = Field(min_length=1, max_length=200)


class ApplyRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    version_token: str = Field(min_length=64, max_length=64)
    reviewer: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReverseRequest(StrictModel):
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class InterpretationRequestBody(StrictModel):
    mode: Literal["direct", "hybrid"]


class ErrorResponse(StrictModel):
    error: dict[str, Any]
