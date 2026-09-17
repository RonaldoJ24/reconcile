from __future__ import annotations

import math
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from reconcile.config import InterpretationSettings, interpretation_settings
from reconcile.ml.artifact import ArtifactError
from reconcile.ml.runtime import ACTIVE_RULES_IDENTITY, rank_candidates
from reconcile.persistence.models import CreditNote, ImportBatch, Invoice, Payment, Proposal, Source
from reconcile.persistence.service import ReconcileService, ServiceError, _shadow_group

from .budget import BudgetPolicy, RateCard
from .budget import Usage as BudgetUsage
from .budget import finalize_attempt as finalize_budget_attempt
from .budget import reserve_attempt as reserve_budget_attempt
from .cache import cache_key, load_cached, source_fingerprint, store_cached
from .provider import AttemptContext, AttemptEvent, DeepSeekProvider
from .schemas import (
    InterpretationRequest,
    InterpretationResult,
    SourceSpan,
    validate_result,
)

_PROVIDER_SLOT = threading.BoundedSemaphore(1)


@dataclass(frozen=True)
class WorkflowOutcome:
    status: Literal["selected", "needs_review", "unavailable"]
    source: Literal["live", "cache", "none"]
    mode: Literal["direct", "hybrid"]
    candidate_id: str | None = None
    reason_code: str | None = None
    citations: tuple[dict[str, Any], ...] = ()
    failure_code: str | None = None
    trace: dict[str, Any] | None = None
    proposal_revision: int | None = None
    proposal_status: str | None = None

    def api_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "mode": self.mode,
            "candidate_id": self.candidate_id,
            "reason_code": self.reason_code,
            "citations": list(self.citations),
            "failure_code": self.failure_code,
            "trace": self.trace or {},
        }


class CompiledInterpretationWorkflow:
    stages = (
        "load_observations",
        "enumerate_candidates",
        "rank_if_hybrid",
        "compile_and_validate",
        "read_cache",
        "reserve_and_call",
        "validate_and_cache",
        "record_proposal_revision",
    )

    def __init__(
        self,
        provider_factory: Callable[[InterpretationSettings], DeepSeekProvider] | None = None,
    ):
        self.provider_factory = provider_factory or (
            lambda settings: DeepSeekProvider(
                api_key=settings.api_key,
                model=settings.model,
                enabled=settings.enabled,
                budget_available=settings.execution_microdollars > 0,
            )
        )

    def _request(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        proposal: Proposal,
        mode: Literal["direct", "hybrid"],
        policy: BudgetPolicy,
    ) -> tuple[InterpretationRequest, dict[str, str]]:
        payment = db.scalar(
            select(Payment)
            .join(Source, Payment.source_id == Source.id)
            .join(ImportBatch, Source.batch_id == ImportBatch.id)
            .where(
                Payment.id == proposal.payment_id,
                Payment.workspace_id == workspace_id,
                Source.status != "REJECTED_CONFLICT",
                Source.status != "SUPERSEDED",
                or_(ImportBatch.status == "COMMITTED", Source.status == "COMMITTED"),
            )
        )
        if payment is None:
            raise ServiceError("not_found", "payment not found", 404)
        invoices = list(
            db.scalars(
                select(Invoice)
                .join(Source, Invoice.source_id == Source.id)
                .join(ImportBatch, Source.batch_id == ImportBatch.id)
                .where(
                    Invoice.workspace_id == workspace_id,
                    Invoice.outstanding_amount > 0,
                    Invoice.conflicted.is_(False),
                    Source.status != "REJECTED_CONFLICT",
                    Source.status != "SUPERSEDED",
                    or_(ImportBatch.status == "COMMITTED", Source.status == "COMMITTED"),
                )
                .order_by(Invoice.invoice_id)
            )
        )
        credits = list(
            db.scalars(
                select(CreditNote)
                .join(Source, CreditNote.source_id == Source.id)
                .join(ImportBatch, Source.batch_id == ImportBatch.id)
                .where(
                    CreditNote.workspace_id == workspace_id,
                    CreditNote.available_amount > 0,
                    CreditNote.conflicted.is_(False),
                    Source.status != "REJECTED_CONFLICT",
                    Source.status != "SUPERSEDED",
                    or_(ImportBatch.status == "COMMITTED", Source.status == "COMMITTED"),
                )
                .order_by(CreditNote.credit_note_id)
            )
        )
        messages = list(
            db.scalars(
                select(Source)
                .join(ImportBatch, Source.batch_id == ImportBatch.id)
                .where(
                    Source.workspace_id == workspace_id,
                    Source.kind == "message",
                    or_(ImportBatch.status == "COMMITTED", Source.status == "COMMITTED"),
                    Source.status != "REJECTED_CONFLICT",
                    Source.status != "SUPERSEDED",
                )
                .order_by(Source.created_at, Source.id)
            )
        )
        relevant = [
            item
            for item in messages
            if item.source_metadata.get("payment_source_account_id") == payment.source_account_id
            and item.source_metadata.get("payment_transaction_id") == payment.transaction_id
        ][:5]
        evidence = {
            str(item.id): item.raw_bytes.decode("utf-8") for item in relevant if item.raw_bytes
        }
        payment_source = db.scalar(select(Source).where(Source.id == payment.source_id))
        if payment_source is None:
            raise ServiceError("stale_source", "payment source is unavailable")
        from reconcile.domain.matching import propose
        from reconcile.persistence.service import _credit_fact, _invoice_fact, _payment_fact

        rules_result = propose(
            _payment_fact(payment),
            [_invoice_fact(item) for item in invoices],
            [_credit_fact(item) for item in credits],
            evidence,
        )
        group = _shadow_group(payment, invoices, credits, evidence, rules_result)
        if not group["candidates"]:
            raise ServiceError("no_candidates", "no bounded allocation candidates are available")
        invoice_ids = {str(item["invoice_id"]) for item in group["invoices"]}
        invoice_rows = [item for item in invoices if item.invoice_id in invoice_ids]
        credit_rows = [
            item
            for item in credits
            if group["credit"] and item.credit_note_id == group["credit"]["credit_note_id"]
        ]
        record_source_ids = {
            payment.source_id,
            *(item.source_id for item in invoice_rows),
            *(item.source_id for item in credit_rows),
        }
        record_sources = [db.get(Source, source_id) for source_id in record_source_ids]
        if any(item is None for item in record_sources):
            raise ServiceError("stale_source", "an observed record source is unavailable")
        source_hashes = {
            str(source.id): source.sha256
            for source in [*record_sources, *relevant]
            if source is not None
        }
        ranked: list[dict[str, Any]] = []
        if mode == "hybrid":
            try:
                rank_trace = rank_candidates(group, force=True)
            except ArtifactError as exc:
                raise ServiceError("ranker_unavailable", "verified ranker is unavailable") from exc
            ranked = list(rank_trace.get("ranked_candidates", []))
            if not ranked:
                raise ServiceError("ranker_unavailable", "ranker returned no candidate order")
        request = InterpretationRequest.model_validate(
            {
                "workspace_id": str(workspace_id),
                "payment": {
                    "payment_id": str(payment.id),
                    "amount_centavos": payment.amount,
                    "currency": payment.currency,
                    "booking_date": payment.booking_date,
                    "payer_name": payment.payer_name,
                    "reference": payment.reference,
                    "source_id": str(payment.source_id),
                    "source_hash": source_hashes[str(payment.source_id)],
                    "version": payment.version,
                },
                "invoices": [
                    {
                        "customer_id": item["customer_id"],
                        "customer_name": item["customer_name"],
                        "invoice_id": item["invoice_id"],
                        "issued_date": item["issued_date"],
                        "due_date": item["due_date"],
                        "balance_as_of": item["balance_as_of"],
                        "currency": item["currency"],
                        "outstanding_amount_centavos": item["outstanding_amount"],
                        "source_id": str(row.source_id),
                        "source_hash": source_hashes[str(row.source_id)],
                        "source_version": row.version,
                        "version": row.version,
                    }
                    for item in group["invoices"]
                    for row in invoice_rows
                    if row.invoice_id == item["invoice_id"]
                ],
                "credits": (
                    [
                        {
                            "customer_id": group["credit"]["customer_id"],
                            "credit_note_id": group["credit"]["credit_note_id"],
                            "balance_as_of": group["credit"]["balance_as_of"],
                            "currency": group["credit"]["currency"],
                            "invoice_id": group["credit"]["invoice_id"],
                            "available_amount_centavos": group["credit"]["available_amount"],
                            "source_id": str(credit_rows[0].source_id),
                            "source_hash": source_hashes[str(credit_rows[0].source_id)],
                            "source_version": credit_rows[0].version,
                            "version": credit_rows[0].version,
                        }
                    ]
                    if group["credit"] and credit_rows
                    else []
                ),
                "candidates": group["candidates"],
                "source_spans": [
                    SourceSpan(
                        source_id=str(item.id),
                        start=0,
                        end=len(evidence[str(item.id)]),
                        content=evidence[str(item.id)],
                        source_hash=item.sha256,
                        source_version=1,
                    )
                    for item in relevant
                    if evidence.get(str(item.id))
                ],
                "mode": mode,
                "ranked_candidates": ranked,
                "prompt_version": "reconcile-interpretation-prompt-v1",
                "schema_version": "reconcile-interpretation-schema-v1",
                "budget_policy_version": policy.policy_version,
                "decision_timestamp": proposal.created_at,
            }
        )
        return request, source_hashes

    def run(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        proposal_id: uuid.UUID,
        mode: Literal["direct", "hybrid"],
        settings: InterpretationSettings | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> WorkflowOutcome:
        config = settings or interpretation_settings()
        policy = BudgetPolicy(execution_microdollars=config.execution_microdollars)
        proposal = db.scalar(
            select(Proposal).where(
                Proposal.id == proposal_id,
                Proposal.workspace_id == workspace_id,
            )
        )
        if proposal is None:
            raise ServiceError("not_found", "proposal not found", 404)
        if proposal.status != "NEEDS_REVIEW":
            raise ServiceError(
                "interpretation_not_allowed", "only review-required proposals can be interpreted"
            )
        expected_revision = proposal.current_revision
        try:
            request, expected_hashes = self._request(
                db,
                workspace_id=workspace_id,
                proposal=proposal,
                mode=mode,
                policy=policy,
            )
        except ServiceError as exc:
            return WorkflowOutcome("unavailable", "none", mode, failure_code=exc.code)
        if not request.source_spans:
            return WorkflowOutcome(
                "needs_review",
                "none",
                mode,
                reason_code="insufficient_evidence",
                failure_code="missing_evidence",
            )
        if cancelled and cancelled():
            return WorkflowOutcome("unavailable", "none", mode, failure_code="cancelled")
        key_payload = {
            "request": request.model_dump(mode="json"),
            "rules_identity": ACTIVE_RULES_IDENTITY,
            "model": config.model,
            "base_url": "https://api.deepseek.com",
            "thinking": "disabled",
            "max_output_tokens": policy.max_output_tokens,
        }
        key = cache_key(key_payload)
        cached = load_cached(db, workspace_id=workspace_id, key=key)
        if cached is not None:
            try:
                result = InterpretationResult.model_validate(cached.result)
                validate_result(request, result)
            except ValueError:
                db.rollback()
            else:
                db.rollback()
                return self._record(
                    db,
                    workspace_id=workspace_id,
                    proposal_id=proposal_id,
                    expected_revision=expected_revision,
                    request=request,
                    result=result,
                    source="cache",
                    response_model=cached.response_model,
                    source_hashes=expected_hashes,
                    trace={"cache_hit": True, "cache_key": key},
                )
        else:
            db.rollback()
        if not config.enabled:
            return WorkflowOutcome("unavailable", "none", mode, failure_code="disabled")
        if cancelled and cancelled():
            return WorkflowOutcome("unavailable", "none", mode, failure_code="cancelled")

        def reserve(context: AttemptContext) -> uuid.UUID:
            return reserve_budget_attempt(
                db,
                workspace_id=workspace_id,
                session_id=session_id,
                execution_id=config.execution_id or "",
                mode=mode,
                requested_model=config.model,
                attempt=context.attempt,
                policy=policy,
                rate_card=RateCard(),
            )

        def finalize(event: AttemptEvent) -> None:
            call_id = event.reservation
            if not isinstance(call_id, uuid.UUID):
                return
            usage = event.telemetry.usage
            complete_usage = usage.input_tokens is not None and usage.output_tokens is not None
            known = event.billing_state == "reconciled" and complete_usage
            finalize_budget_attempt(
                db,
                call_id=call_id,
                policy=policy,
                rate_card=RateCard(),
                status=(
                    "SUCCEEDED" if known and event.telemetry.failure_code is None else "FAILED"
                ),
                usage=(
                    BudgetUsage(
                        input_tokens=usage.input_tokens or 0,
                        output_tokens=usage.output_tokens or 0,
                        cached_input_tokens=usage.provider_cache_tokens or 0,
                        reasoning_tokens=usage.reasoning_tokens,
                    )
                    if known
                    else None
                ),
                response_model=event.telemetry.response_model,
                latency_ms=math.ceil(event.telemetry.latency_ms),
                error_code=(
                    event.telemetry.failure_code.value
                    if event.telemetry.failure_code is not None
                    else None
                ),
                billing_unknown=event.billing_state == "unknown",
            )

        provider = self.provider_factory(config)
        try:
            with _PROVIDER_SLOT:
                outcome = provider.interpret(
                    request,
                    reserve_attempt=reserve,
                    finalize_attempt=finalize,
                )
        finally:
            provider.close()
        if not outcome.ok or outcome.result is None:
            return WorkflowOutcome(
                "unavailable",
                "none",
                mode,
                failure_code=(
                    outcome.failure.code.value if outcome.failure else "provider_failure"
                ),
                trace={"attempts": [item.model_dump(mode="json") for item in outcome.attempts]},
            )
        try:
            validate_result(request, outcome.result)
        except ValueError:
            return WorkflowOutcome(
                "unavailable", "none", mode, failure_code="invalid_provider_result"
            )
        response_model = outcome.attempts[-1].response_model
        if not response_model:
            return WorkflowOutcome(
                "unavailable", "none", mode, failure_code="missing_response_model"
            )
        store_cached(
            db,
            workspace_id=workspace_id,
            key=key,
            fingerprint=source_fingerprint(request.model_dump(mode="json")),
            requested_model=config.model,
            response_model=response_model,
            mode=mode,
            result=outcome.result.model_dump(mode="json"),
        )
        return self._record(
            db,
            workspace_id=workspace_id,
            proposal_id=proposal_id,
            expected_revision=expected_revision,
            request=request,
            result=outcome.result,
            source="live",
            response_model=response_model,
            source_hashes=expected_hashes,
            trace={
                "cache_hit": False,
                "cache_key": key,
                "attempts": [item.model_dump(mode="json") for item in outcome.attempts],
            },
        )

    @staticmethod
    def _record(
        db: Session,
        *,
        workspace_id: uuid.UUID,
        proposal_id: uuid.UUID,
        expected_revision: int,
        request: InterpretationRequest,
        result: InterpretationResult,
        source: Literal["live", "cache"],
        response_model: str,
        source_hashes: dict[str, str],
        trace: dict[str, object],
    ) -> WorkflowOutcome:
        candidates = {item.candidate_id: item for item in request.candidates}
        candidate = candidates.get(result.candidate_id) if result.candidate_id else None
        citations = tuple(item.model_dump(mode="json") for item in result.citations)
        try:
            proposal = ReconcileService(db).record_interpretation(
                workspace_id,
                proposal_id,
                expected_revision=expected_revision,
                mode=request.mode,
                source=source,
                candidate=candidate.model_dump(mode="json") if candidate else None,
                citations=list(citations),
                reason_code=result.reason_code.value,
                trace={
                    **trace,
                    "status": "selected" if result.candidate_id else "needs_review",
                    "candidate_id": result.candidate_id,
                    "reason_code": result.reason_code.value,
                    "response_model": response_model,
                    "ranked_candidates": [
                        item.model_dump(mode="json") for item in request.ranked_candidates
                    ],
                },
                expected_source_hashes=source_hashes,
                expected_payment_version=request.payment.version,
                expected_invoice_versions={
                    item.invoice_id: item.version for item in request.invoices
                },
                expected_credit_versions={
                    item.credit_note_id: item.version for item in request.credits
                },
            )
        except ServiceError as exc:
            return WorkflowOutcome("unavailable", "none", request.mode, failure_code=exc.code)
        return WorkflowOutcome(
            "selected" if result.candidate_id else "needs_review",
            source,
            request.mode,
            candidate_id=result.candidate_id,
            reason_code=result.reason_code.value,
            citations=citations,
            trace={
                **trace,
                "status": "selected" if result.candidate_id else "needs_review",
                "candidate_id": result.candidate_id,
                "reason_code": result.reason_code.value,
                "response_model": response_model,
                "ranked_candidates": [
                    item.model_dump(mode="json") for item in request.ranked_candidates
                ],
            },
            proposal_revision=proposal.current_revision,
            proposal_status=proposal.status,
        )


def compile_workflow(
    provider_factory: Callable[[InterpretationSettings], DeepSeekProvider] | None = None,
) -> CompiledInterpretationWorkflow:
    return CompiledInterpretationWorkflow(provider_factory)
