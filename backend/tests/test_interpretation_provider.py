from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from reconcile.interpretation import (
    AllocationLine,
    CandidateAllocation,
    Citation,
    DeepSeekProvider,
    FailureCode,
    InterpretationRequest,
    InterpretationResult,
    InvoiceObservation,
    PaymentObservation,
    PromptTooLarge,
    SourceSpan,
    compile_prompt,
    validate_result,
)
from reconcile.interpretation.provider import AttemptContext, AttemptEvent


def make_request(*, mode: str = "direct", source_text: str = "INV-1") -> InterpretationRequest:
    return InterpretationRequest(
        workspace_id="workspace-1",
        payment=PaymentObservation(
            payment_id="payment-1",
            amount_centavos=100,
            currency="MXN",
            booking_date="2026-09-14",
            reference="INV-1",
        ),
        invoices=[
            InvoiceObservation(
                invoice_id="INV-1",
                outstanding_amount=100,
                currency="MXN",
                balance_as_of="2026-09-13",
            )
        ],
        candidates=[
            CandidateAllocation(
                candidate_id="candidate-1",
                invoice_ids=["INV-1"],
                cash=[AllocationLine(invoice_id="INV-1", amount=100)],
            )
        ],
        source_spans=[
            SourceSpan(
                source_id="source-1",
                start=0,
                end=len(source_text),
                text=source_text,
                source_hash="hash-1",
            )
        ],
        mode=mode,
        ranked_candidates=(
            [{"candidate_id": "candidate-1", "raw_score": 1.5}]
            if mode == "hybrid"
            else []
        ),
        decision_timestamp="2026-09-14T12:00:00Z",
    )


def response(result: dict[str, object], *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={
            "model": "deepseek-flash-2026",
            "choices": [{"message": {"content": json.dumps(result)}}],
            "usage": {
                "prompt_tokens": 101,
                "completion_tokens": 42,
                "prompt_cache_hit_tokens": 5,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        },
    )


def test_strict_models_and_exact_citations() -> None:
    with pytest.raises(ValidationError):
        Citation(source_id="source-1", start=0, end=1, quote="I", extra="x")
    request = make_request()
    result = InterpretationResult(
        decision="select",
        candidate_id="candidate-1",
        reason_code="evidence_supported",
        citations=[Citation(source_id="source-1", start=0, end=5, quote="INV-1")],
    )
    validate_result(request, result)
    with pytest.raises(ValueError, match="exact source slice"):
        validate_result(request, result.model_copy(update={"citations": [Citation(
            source_id="source-1", start=0, end=5, quote="wrong"
        )]}))


def test_prompt_modes_are_bounded_and_delimited() -> None:
    direct = compile_prompt(make_request())
    hybrid = compile_prompt(make_request(mode="hybrid"))
    assert direct.mode == "direct"
    assert hybrid.mode == "hybrid"
    assert direct.request_bytes <= 6000
    assert "<untrusted_source>INV-1</untrusted_source>" in direct.user
    assert "rank_context" in hybrid.user
    with pytest.raises(PromptTooLarge):
        compile_prompt(make_request(source_text="x" * 100_000))


def test_provider_posts_required_deepseek_options_without_network() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response(
            {
                "decision": "select",
                "candidate_id": "candidate-1",
                "reason_code": "evidence_supported",
                "citations": [
                    {"source_id": "source-1", "start": 0, "end": 5, "quote": "INV-1"}
                ],
            }
        )

    client = httpx.Client(
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(handler),
    )
    provider = DeepSeekProvider(api_key="provided-explicitly", enabled=True, client=client)
    outcome = provider.interpret(make_request())
    assert outcome.ok
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.deepseek.com/chat/completions"
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    assert body["max_tokens"] == 2048
    assert outcome.attempts[0].requested_model == "deepseek-flash"
    assert outcome.attempts[0].response_model == "deepseek-flash-2026"
    assert outcome.attempts[0].usage.provider_cache_tokens == 5
    client.close()


def test_only_transient_failures_retry_and_hooks_run_per_attempt() -> None:
    statuses = iter((503, 200))
    reservations: list[AttemptContext] = []
    finalized: list[AttemptEvent] = []

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        status = next(statuses)
        if status == 503:
            return httpx.Response(status)
        return response(
            {
                "decision": "needs_review",
                "candidate_id": None,
                "reason_code": "insufficient_evidence",
                "citations": [],
            }
        )

    client = httpx.Client(
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(handler),
    )
    provider = DeepSeekProvider(
        api_key="provided-explicitly",
        enabled=True,
        client=client,
        reserve_attempt=lambda context: reservations.append(context) or context.attempt,
        finalize_attempt=finalized.append,
    )
    outcome = provider.interpret(make_request())
    assert outcome.ok
    assert [item.retry_number for item in reservations] == [0, 1]
    assert [item.billing_state for item in finalized] == ["unknown", "reconciled"]
    assert [item.telemetry.retry_number for item in finalized] == [0, 1]
    client.close()

    client = httpx.Client(
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(400)),
    )
    outcome = DeepSeekProvider(
        api_key="provided-explicitly", enabled=True, client=client
    ).interpret(make_request())
    assert not outcome.ok
    assert outcome.failure is not None
    assert outcome.failure.code is FailureCode.BAD_REQUEST
    assert len(outcome.attempts) == 1
    client.close()
