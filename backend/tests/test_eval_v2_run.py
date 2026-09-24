from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from reconcile.interpretation.provider import (
    AttemptContext,
    AttemptEvent,
    FinalizeAttempt,
    ProviderOutcome,
    ReserveAttempt,
)
from reconcile.interpretation.schemas import (
    AttemptTelemetry,
    Citation,
    Decision,
    InterpretationRequest,
    InterpretationResult,
    ReasonCode,
    Usage,
)
from reconcile.ml import run_v2
from reconcile.ml.eval_v2_recordings import seal_recording
from reconcile.ml.evaluate_v2 import (
    EvaluationValidationError,
    evaluate_observations,
    stable_split,
)


def _case(case_id: str, **overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "case_id": case_id,
        "category": "test",
        "customer_id": f"{case_id}-customer",
        "customer_name": "Comercial Norte, S.A. de C.V.",
        "payer_name": "COMERCIAL NORTE SA DE CV",
        "booking_date": "2026-09-15",
        "amount": "55000.00",
        "bank_reference": "PAGO FACT 1432 Y 33",
        "note": "Pago de la 1432 y la 33.",
        "invoices": [
            {"invoice_id": "F-1432", "issued_date": "2026-08-01", "due_date": "2026-09-01",
             "amount": "30000.00"},
            {"invoice_id": "F-1433", "issued_date": "2026-08-02", "due_date": "2026-09-02",
             "amount": "25000.00"},
            {"invoice_id": "F-1436", "issued_date": "2026-08-05", "due_date": "2026-09-05",
             "amount": "55000.00"},
        ],
        "credit_notes": [],
    }
    case.update(overrides)
    return case


def test_authored_cases_are_validated_before_import() -> None:
    run_v2.validate_authored_case(_case("ok"))
    with pytest.raises(ValueError, match="40-character"):
        run_v2.validate_authored_case(_case("long", bank_reference="X" * 41))
    with pytest.raises(ValueError, match="link"):
        run_v2.validate_authored_case(
            _case(
                "credit",
                credit_notes=[{"credit_note_id": "NC-1", "amount": "10.00", "invoice_id": "F-9"}],
            )
        )
    with pytest.raises(ValueError, match="two decimals"):
        run_v2.validate_authored_case(_case("amount", amount="10.001"))


def test_authored_case_serializes_to_the_ordinary_import_format() -> None:
    parsed = run_v2.case_batch(
        _case(
            "quoted",
            credit_notes=[{"credit_note_id": "NC-7", "amount": "500.00", "invoice_id": "F-1433"}],
        )
    )
    assert parsed.bank.accepted_count == 1 and not parsed.bank.issues
    assert parsed.invoices.accepted_count == 3 and not parsed.invoices.issues
    assert parsed.invoices.rows[0]["customer_name"] == "Comercial Norte, S.A. de C.V."
    assert parsed.credits is not None and parsed.credits.accepted_count == 1
    assert parsed.message is not None and not parsed.message.issues


def _case_id(prefix: str, split: str) -> str:
    for index in range(10_000):
        case_id = f"{prefix}-{index}"
        if stable_split(f"eval-v2-{case_id}") == split:
            return case_id
    raise AssertionError(split)


class _FakeProvider:
    def __init__(self, decide: Callable[[InterpretationRequest], InterpretationResult]):
        self.decide = decide

    def interpret(
        self,
        request: InterpretationRequest,
        *,
        reserve_attempt: ReserveAttempt | None = None,
        finalize_attempt: FinalizeAttempt | None = None,
    ) -> ProviderOutcome:
        context = AttemptContext(1, 0, "gpt-6-luna")
        reservation = reserve_attempt(context) if reserve_attempt else None
        telemetry = AttemptTelemetry(
            attempt=1,
            retry_number=0,
            requested_model="gpt-6-luna",
            response_model="gpt-6-luna",
            usage=Usage(input_tokens=900, output_tokens=60, provider_cache_tokens=0),
            latency_ms=12,
            http_status=200,
            reservation_state="reconciled",
        )
        if finalize_attempt:
            finalize_attempt(AttemptEvent(context, telemetry, reservation, "reconciled"))
        return ProviderOutcome(self.decide(request), None, (telemetry,))

    def close(self) -> None:
        pass


def _decide(request: InterpretationRequest) -> InterpretationResult:
    source = request.source_spans[0]
    split = next(
        (item for item in request.candidates if item.invoice_ids == ("F-1432", "F-1433")), None
    )
    if split is None:
        return InterpretationResult(
            decision=Decision.NEEDS_REVIEW,
            candidate_id=None,
            reason_code=ReasonCode.AMBIGUOUS,
            citations=[],
        )
    return InterpretationResult(
        decision=Decision.SELECT,
        candidate_id=split.candidate_id,
        reason_code=ReasonCode.EVIDENCE_SUPPORTED,
        citations=[
            Citation(
                source_id=source.source_id,
                start=source.start,
                end=source.end,
                quote=source.content[source.start : source.end],
            )
        ],
    )


@pytest.mark.postgres
def test_run_records_verified_direct_observations_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is not set")
    monkeypatch.setenv("RECONCILE_EVAL_SCHEMA", "reconcile_eval_v2_test")
    monkeypatch.setenv("RECONCILE_RANKER_MODE", "shadow")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")

    shorthand = _case_id("shorthand", "development")
    clean = _case_id("clean", "validation")
    ambiguous = _case_id("ambiguous", "development")
    four = _case_id("four", "development")
    silent = _case_id("silent", "development")
    cases = [
        _case(shorthand),
        _case(clean, bank_reference="FACTURA F-4410", note="Factura F-4410", amount="8450.00",
              invoices=[{"invoice_id": "F-4410", "issued_date": "2026-08-01",
                         "due_date": "2026-09-01", "amount": "8450.00"},
                        {"invoice_id": "F-4415", "issued_date": "2026-08-01",
                         "due_date": "2026-09-01", "amount": "8450.00"}]),
        _case(ambiguous, bank_reference="PAGO PROVEEDOR", note="Pago de agosto.",
              amount="12800.00",
              invoices=[{"invoice_id": "F-3101", "issued_date": "2026-08-04",
                         "due_date": "2026-09-03", "amount": "12800.00"},
                        {"invoice_id": "F-3102", "issued_date": "2026-08-04",
                         "due_date": "2026-09-03", "amount": "12800.00"}]),
        _case(four, bank_reference="PAGO 4 FACTURAS", note="Pago de F-1, F-2, F-3 y F-4.",
              amount="40000.00",
              invoices=[{"invoice_id": f"F-{n}", "issued_date": "2026-08-01",
                         "due_date": "2026-09-01", "amount": "10000.00"} for n in range(1, 5)]),
        _case(silent, bank_reference="PAGO", note=None),
        _case("too-long", bank_reference="Y" * 41),
    ]
    labels = [
        {"case_id": shorthand, "answerable": True,
         "cash": [{"invoice_id": "F-1432", "amount": "30000.00"},
                  {"invoice_id": "F-1433", "amount": "25000.00"}]},
        {"case_id": clean, "answerable": True,
         "cash": [{"invoice_id": "F-4410", "amount": "8450.00"}]},
        {"case_id": ambiguous, "answerable": False},
        {"case_id": four, "answerable": True,
         "cash": [{"invoice_id": f"F-{n}", "amount": "10000.00"} for n in range(1, 5)]},
        {"case_id": silent, "answerable": False},
        {"case_id": "too-long", "answerable": False},
    ]
    cases_path, labels_path = tmp_path / "cases.jsonl", tmp_path / "labels.jsonl"
    cases_path.write_text("".join(json.dumps(row) + "\n" for row in cases))
    labels_path.write_text("".join(json.dumps(row) + "\n" for row in labels))
    out = tmp_path / "run"

    summary = run_v2.prepare(cases_path, labels_path, out, "test-commit", check_tree=False)
    assert summary["prepared"] == 5 and summary["invalid"] == 1
    assert summary["unreachable"] == 1  # four invoices exceed the three-invoice candidates

    recorded = run_v2.interpret(
        out,
        "0.05",
        "test",
        check_tree=False,
        provider_factory=lambda _: _FakeProvider(_decide),
    )
    assert recorded["recorded_now"] == recorded["planned"] == 4

    direct = {
        row["case_id"]: row for row in run_v2._read_jsonl(out / "direct.jsonl")
    }
    assert direct[shorthand]["status"] == "PROPOSED"
    assert direct[ambiguous]["status"] == "NEEDS_REVIEW"
    assert direct[silent]["status"] == "NEEDS_REVIEW"
    assert direct[silent]["recording"]["outcome"]["provider_called"] is False

    report = run_v2.score(out, include_final=False)
    development = report["evaluator"]["development"]["direct_on_rules_deferred"]
    assert development["proposal"]["correct"] == 1
    assert development["support"]["unsupported"] == 0
    everything = report["end_to_end"]["all-cases"]
    assert everything["answerable_unreachable"] == 1
    assert everything["rules_then_direct"]["correct"] == 2
    assert everything["rules_then_direct"]["unnecessary_deferrals"] == 1

    run = json.loads((out / "run.json").read_text())
    inputs = run_v2._read_jsonl(out / "inputs-development.jsonl")
    labels_rows = run_v2._read_jsonl(out / "labels-development.jsonl")
    deferred = [row for row in inputs if row["case_id"] in direct]
    subset_labels = [row for row in labels_rows if row["case_id"] in direct]
    observations = [direct[row["case_id"]] for row in deferred]

    # A selected candidate that differs from the sealed recording is rejected.
    forged = copy.deepcopy(observations)
    target = next(row for row in forged if row["case_id"] == ambiguous)
    target["status"] = "PROPOSED"
    target["candidate_id"] = next(iter(inputs))["candidates"][0]["candidate_id"]
    with pytest.raises(EvaluationValidationError):
        evaluate_observations(deferred, subset_labels, forged, split="development",
                              method="direct", provider_identity=run["identity"])

    # Editing a recording without resealing breaks its digest.
    edited = copy.deepcopy(observations)
    edited[0]["recording"]["outcome"]["reason_code"] = "ambiguous"
    with pytest.raises(EvaluationValidationError, match="not authentic"):
        evaluate_observations(deferred, subset_labels, edited, split="development",
                              method="direct", provider_identity=run["identity"])

    # A resealed recording from a different prompt version is still rejected.
    other = copy.deepcopy(observations)
    other[0]["recording"] = seal_recording(
        {**other[0]["recording"], "prompt_version": "some-other-prompt"}
    )
    with pytest.raises(EvaluationValidationError, match="prompt_version"):
        evaluate_observations(deferred, subset_labels, other, split="development",
                              method="direct", provider_identity=run["identity"])

    # Without the frozen identity the evaluator refuses provider results entirely.
    with pytest.raises(EvaluationValidationError, match="authentic loader"):
        evaluate_observations(deferred, subset_labels, observations, split="development",
                              method="direct")
