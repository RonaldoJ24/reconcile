"""Pre-registered v2 evaluation run: prepare, interpret once, score.

Each authored case is imported into its own workspace inside an isolated schema
of the test database. The run records what the production rules, the shadow
ranker and Direct interpretation actually did, then scores those observations
with the frozen v2 evaluator. ``reports/eval-v2/PREREGISTRATION.md`` states the
rules of the run; this module implements them and nothing else.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import subprocess
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from reconcile.config import InterpretationSettings
from reconcile.ingest.parsers import ParsedBatch, parse_batch, parse_message_context
from reconcile.interpretation.budget import BudgetPolicy, RateCard
from reconcile.interpretation.prompt import TEMPERATURE
from reconcile.interpretation.schemas import PROMPT_VERSION, SCHEMA_VERSION
from reconcile.interpretation.workflow import CompiledInterpretationWorkflow
from reconcile.ml.eval_v2_recordings import (
    RECORDING_SCHEMA,
    observation_from_recording,
    request_digest,
    seal_recording,
)
from reconcile.ml.evaluate_v2 import (
    FINAL_SPLIT,
    _materialize_threshold_results,
    allocation_matches,
    evaluate_observations,
    evaluate_ranker_thresholds,
    fingerprint_value,
    freeze_manifest,
    read_split,
    stable_split,
)
from reconcile.ml.runtime import ACTIVE_RULES_IDENTITY
from reconcile.persistence.db import normalize_database_url
from reconcile.persistence.models import Base, Payment, Proposal, ProposalRevision
from reconcile.persistence.service import ReconcileService, ServiceError

DEFAULT_EVAL_SCHEMA = "reconcile_eval_v2"
MODEL = "deepseek-flash"
VALIDATOR = "validate_allocation+validate_result"
SPLITS = ("development", "validation", FINAL_SPLIT)
MAX_REFERENCE_CHARS = 40
JsonRow = dict[str, Any]


def _read_jsonl(path: Path) -> list[JsonRow]:
    rows: list[JsonRow] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number} is not a JSON object")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _centavos(value: Any, context: str) -> int:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{context} is not a decimal MXN amount") from exc
    if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"{context} must be positive with at most two decimals")
    return int(amount * 100)


def _mxn(value: Any) -> str:
    return f"{Decimal(str(value)):.2f}"


def _csv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def validate_authored_case(case: Mapping[str, Any]) -> None:
    """Reject authoring mistakes before import; the reason never includes a label."""

    for field in (
        "case_id",
        "customer_id",
        "customer_name",
        "payer_name",
        "booking_date",
        "amount",
        "bank_reference",
    ):
        if not isinstance(case.get(field), str):
            raise ValueError(f"{field} must be a string")
    if len(str(case["bank_reference"])) > MAX_REFERENCE_CHARS:
        raise ValueError("bank_reference exceeds the 40-character SPEI concept")
    _centavos(case["amount"], "amount")
    invoices = case.get("invoices")
    if not isinstance(invoices, list) or not 1 <= len(invoices) <= 8:
        raise ValueError("invoices must be a list of one to eight rows")
    folios = [str(item.get("invoice_id")) for item in invoices if isinstance(item, Mapping)]
    if len(folios) != len(invoices) or len(set(folios)) != len(folios):
        raise ValueError("invoice_id values must be present and unique")
    for item in invoices:
        _centavos(item.get("amount"), "invoice amount")
    credits = case.get("credit_notes") or []
    if not isinstance(credits, list) or len(credits) > 2:
        raise ValueError("credit_notes must be a list of at most two rows")
    for credit in credits:
        _centavos(credit.get("amount"), "credit amount")
        if credit.get("invoice_id") not in folios:
            raise ValueError("a credit note must link to one of the case invoices")
    note = case.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError("note must be a string or null")


def case_batch(case: Mapping[str, Any]) -> ParsedBatch:
    """Serialize one authored case into the same CSV/TXT inputs a user imports."""

    case_id = str(case["case_id"])
    account, transaction = f"{case_id}-account", f"{case_id}-payment"
    booked = str(case["booking_date"])
    bank = _csv(
        (
            "source_account_id",
            "transaction_id",
            "booking_date",
            "payer_name",
            "reference",
            "amount",
            "currency",
        ),
        [
            (
                account,
                transaction,
                booked,
                str(case["payer_name"]),
                str(case["bank_reference"]).strip() or "—",
                _mxn(case["amount"]),
                "MXN",
            )
        ],
    )
    invoices = _csv(
        (
            "customer_id",
            "customer_name",
            "invoice_id",
            "issued_date",
            "due_date",
            "balance_as_of",
            "outstanding_amount",
            "currency",
        ),
        [
            (
                str(case["customer_id"]),
                str(case["customer_name"]),
                str(item["invoice_id"]),
                str(item["issued_date"]),
                str(item["due_date"]),
                booked,
                _mxn(item["amount"]),
                "MXN",
            )
            for item in case["invoices"]
        ],
    )
    credit_rows = case.get("credit_notes") or []
    credits = (
        _csv(
            (
                "customer_id",
                "credit_note_id",
                "balance_as_of",
                "available_amount",
                "currency",
                "invoice_id",
            ),
            [
                (
                    str(case["customer_id"]),
                    str(item["credit_note_id"]),
                    booked,
                    _mxn(item["amount"]),
                    "MXN",
                    str(item["invoice_id"]),
                )
                for item in credit_rows
            ],
        )
        if credit_rows
        else None
    )
    note = case.get("note")
    message = str(note).encode("utf-8") if note else None
    context = (
        parse_message_context(f"{booked}T12:00:00+00:00", account, transaction)
        if message
        else None
    )
    return parse_batch(bank, invoices, credits, message, message_context=context, profile="local")


def expected_allocation(label: Mapping[str, Any]) -> JsonRow:
    return {
        "cash": [
            {"invoice_id": str(line["invoice_id"]), "amount": _centavos(line["amount"], "cash")}
            for line in label.get("cash") or []
        ],
        "credits": [
            {
                "credit_note_id": str(line["credit_note_id"]),
                "invoice_id": str(line["invoice_id"]),
                "amount": _centavos(line["amount"], "credit"),
            }
            for line in label.get("credits") or []
        ],
    }


def _eval_schema() -> str:
    schema = os.getenv("RECONCILE_EVAL_SCHEMA", DEFAULT_EVAL_SCHEMA)
    if not schema.isidentifier() or not schema.startswith("reconcile_eval_"):
        raise SystemExit("RECONCILE_EVAL_SCHEMA must be a reconcile_eval_* identifier")
    return schema


def _engine() -> Engine:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is required; the run never uses DATABASE_URL")
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        raise SystemExit("set ALLOW_DESTRUCTIVE_TEST_DB=1 for the isolated evaluation schema")
    return create_engine(normalize_database_url(url), pool_pre_ping=True).execution_options(
        schema_translate_map={None: _eval_schema()}
    )


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _require_frozen_tree(commit: str) -> None:
    if _git_head() != commit:
        raise SystemExit(f"HEAD is not the pre-registered commit {commit}")
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "backend/src"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if changed:
        raise SystemExit("backend/src has uncommitted changes; the system is not frozen")


def frozen_identity(commit: str) -> JsonRow:
    return {
        "frozen_commit": commit,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "rules_identity": ACTIVE_RULES_IDENTITY,
        "model": MODEL,
        "temperature": TEMPERATURE,
    }


def _stage_duration(trace: Mapping[str, Any], stage_id: str) -> float | None:
    for stage in trace.get("stages") or []:
        if isinstance(stage, Mapping) and stage.get("id") == stage_id:
            value = stage.get("duration_ms")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def _fingerprints(input_row: Mapping[str, Any]) -> JsonRow:
    return {
        "input_fingerprint": fingerprint_value(dict(input_row)),
        "candidate_fingerprint": fingerprint_value(input_row.get("candidates", [])),
        "evidence_fingerprint": fingerprint_value(input_row.get("evidence", [])),
        "validator_identity": VALIDATOR,
    }


def prepare(
    cases_path: Path, labels_path: Path, out: Path, commit: str, *, check_tree: bool = True
) -> JsonRow:
    """Import every authored case, record rules and ranker behavior, freeze manifests."""

    if check_tree:
        _require_frozen_tree(commit)
    os.environ["RECONCILE_RANKER_MODE"] = "shadow"
    out.mkdir(parents=True, exist_ok=True)
    cases = _read_jsonl(cases_path)
    labels = {str(row["case_id"]): row for row in _read_jsonl(labels_path)}
    engine = _engine()
    schema = _eval_schema()
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    inputs: dict[str, list[JsonRow]] = {split: [] for split in SPLITS}
    evaluator_labels: dict[str, list[JsonRow]] = {split: [] for split in SPLITS}
    unreachable_inputs: list[JsonRow] = []
    rules_rows: list[JsonRow] = []
    ranker_rows: list[JsonRow] = []
    meta: list[JsonRow] = []
    invalid: list[JsonRow] = []

    for case in cases:
        case_id = str(case.get("case_id", ""))
        label = labels.get(case_id)
        try:
            validate_authored_case(case)
            if label is None:
                raise ValueError("case has no label")
            parsed = case_batch(case)
            parts = [parsed.bank, parsed.invoices, parsed.credits]
            if any(part is not None and part.issues for part in parts) or (
                case.get("note") and (parsed.message is None or parsed.message.issues)
            ):
                raise ValueError("the parser rejected part of the case")
            answerable = label.get("answerable")
            if not isinstance(answerable, bool):
                raise ValueError("label.answerable must be boolean")
            expected = expected_allocation(label) if answerable else None
            if answerable and not (expected and (expected["cash"] or expected["credits"])):
                raise ValueError("an answerable label needs allocation lines")
        except (ValueError, KeyError, TypeError) as exc:
            invalid.append({"case_id": case_id, "reason": str(exc)})
            continue

        with factory() as db:
            service = ReconcileService(db)
            workspace = service.create_workspace()
            batch = service.validate_import(workspace.id, parsed, "local")
            db.commit()
            service.commit_import(workspace.id, batch.id)
            payment = db.scalar(select(Payment).where(Payment.workspace_id == workspace.id))
            if payment is None:
                invalid.append({"case_id": case_id, "reason": "payment was not imported"})
                continue
            proposal = service.process_match(workspace.id, payment.id)
            revision = db.scalar(
                select(ProposalRevision).where(
                    ProposalRevision.proposal_id == proposal.id,
                    ProposalRevision.revision == proposal.current_revision,
                )
            )
            if revision is None:
                invalid.append({"case_id": case_id, "reason": "no proposal revision"})
                continue
            trace = dict(revision.model_trace or {})
            rules_status = proposal.status
            rules_lines = {"cash": revision.cash_lines, "credits": revision.credit_lines}
            workspace_id, proposal_id, amount = workspace.id, proposal.id, payment.amount

        snapshot = trace.get("snapshot") or {}
        candidates = [
            {
                "candidate_id": str(item["candidate_id"]),
                "invoice_ids": list(item["invoice_ids"]),
                "cash": [dict(line) for line in item.get("cash", [])],
                "credits": [dict(line) for line in item.get("credits", [])],
            }
            for item in (snapshot.get("candidate_context") or {}).get("candidates", [])
        ]
        evidence = [
            {"source_id": source_id, "start": 0, "end": len(str(body))}
            for source_id, body in sorted((snapshot.get("evidence") or {}).items())
        ]
        group_id = f"eval-v2-{case_id}"
        split = stable_split(group_id)
        input_row: JsonRow = {
            "case_id": case_id,
            "group_id": group_id,
            "split": split,
            "public_exposed": False,
            "lineage": {
                "group_id": group_id,
                "entity_ids": [f"{case_id}:{case['customer_id']}"],
                "variant_ids": [case_id],
            },
            "payment": {"amount": amount, "currency": "MXN"},
            "evidence": evidence,
            "candidates": candidates,
        }
        acceptable = (
            [item["candidate_id"] for item in candidates if allocation_matches(expected, item)]
            if expected
            else []
        )
        reachable = not answerable or bool(acceptable)
        base = {"case_id": case_id, "group_id": group_id, "split": split}
        if reachable:
            inputs[split].append(input_row)
            evaluator_labels[split].append(
                {
                    **base,
                    "answerable": answerable,
                    "acceptable_candidate_ids": acceptable,
                    "expected_allocation": expected,
                    "semantic_support": "supported" if answerable else "unsupported",
                    "citation_location_valid": True if answerable else None,
                }
            )
        else:
            unreachable_inputs.append({**input_row, "expected_allocation": expected})

        rules_row: JsonRow = {
            **base,
            "method": "rules",
            **_fingerprints(input_row),
            "duration_ms": _stage_duration(trace, "rules-decision"),
            "timing_scope": "rules",
            "status": "NEEDS_REVIEW",
            "citation_location_valid": None,
        }
        if rules_status == "PROPOSED":
            chosen = next(
                (item for item in candidates if allocation_matches(rules_lines, item)), None
            )
            if chosen is None:
                rules_row["status"] = "ERROR"
            else:
                rules_row.update(
                    {
                        "status": "PROPOSED",
                        "candidate_id": chosen["candidate_id"],
                        "allocation": chosen,
                        "citation_location_valid": True,
                    }
                )
        rules_rows.append(rules_row)

        ranker = trace.get("ranker") or {}
        ranker_row: JsonRow = {
            **base,
            "method": "ranker",
            **_fingerprints(input_row),
            "duration_ms": _stage_duration(trace, "shadow-ranker") or 0.0,
            "timing_scope": "features+predict",
        }
        if ranker.get("status") in {"observed", "no_candidates"}:
            ranker_row.update(
                {"status": "OBSERVED", "ranked_candidates": ranker.get("ranked_candidates") or []}
            )
        else:
            ranker_row["status"] = "UNAVAILABLE"
        ranker_rows.append(ranker_row)
        meta.append(
            {
                **base,
                "category": str(case.get("category", "")),
                "answerable": answerable,
                "reachable": reachable,
                "rules_status": rules_status,
                "workspace_id": str(workspace_id),
                "proposal_id": str(proposal_id),
                "payment_amount": amount,
            }
        )

    manifests = {}
    for split in SPLITS:
        _write_jsonl(out / f"inputs-{split}.jsonl", inputs[split])
        _write_jsonl(out / f"labels-{split}.jsonl", evaluator_labels[split])
        manifests[split] = freeze_manifest(inputs[split], evaluator_labels[split], split=split)
    _write_jsonl(out / "inputs-unreachable.jsonl", unreachable_inputs)
    _write_jsonl(out / "rules.jsonl", rules_rows)
    _write_jsonl(out / "ranker.jsonl", ranker_rows)
    _write_jsonl(out / "meta.jsonl", meta)
    run = {
        "identity": frozen_identity(commit),
        "manifests": manifests,
        "invalid_cases": invalid,
        "prepared_cases": len(meta),
    }
    (out / "run.json").write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    return {
        "prepared": len(meta),
        "invalid": len(invalid),
        "by_split": dict(Counter(row["split"] for row in meta)),
        "rules_deferred": sum(1 for row in meta if row["rules_status"] != "PROPOSED"),
        "answerable": sum(1 for row in meta if row["answerable"]),
        "unreachable": sum(1 for row in meta if not row["reachable"]),
    }


def interpret(
    out: Path,
    budget_usd: str,
    run_id: str,
    *,
    check_tree: bool = True,
    provider_factory: Any = None,
) -> JsonRow:
    """Run Direct interpretation once for every rules-deferred case and record it."""

    run = json.loads((out / "run.json").read_text())
    identity = run["identity"]
    if check_tree:
        _require_frozen_tree(str(identity["frozen_commit"]))
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is required for the live Direct pass")
    budget = int(Decimal(budget_usd) * 1_000_000)
    if not 0 < budget <= 2_000_000:
        raise SystemExit("the approved evaluation cap is at most USD 2.00")
    settings = InterpretationSettings(
        True, key, MODEL, f"eval-v2-{run_id}", budget, day_microdollars=budget,
        month_microdollars=budget,
    )
    policy = BudgetPolicy(
        day_microdollars=budget,
        month_microdollars=budget,
        execution_microdollars=budget,
        session_attempts=2,
        global_day_attempts=1_000,
        execution_attempts=1_000,
    )
    inputs_by_case = {
        row["case_id"]: row
        for split in SPLITS
        for row in _read_jsonl(out / f"inputs-{split}.jsonl")
    }
    inputs_by_case.update(
        {
            row["case_id"]: {k: v for k, v in row.items() if k != "expected_allocation"}
            for row in _read_jsonl(out / "inputs-unreachable.jsonl")
        }
    )
    plan = [row for row in _read_jsonl(out / "meta.jsonl") if row["rules_status"] != "PROPOSED"]
    direct_path = out / "direct.jsonl"
    done = {row["case_id"] for row in _read_jsonl(direct_path)} if direct_path.exists() else set()
    workflow = CompiledInterpretationWorkflow(provider_factory)
    factory = sessionmaker(bind=_engine(), expire_on_commit=False)
    written = 0
    for item in sorted(plan, key=lambda row: row["case_id"]):
        case_id = item["case_id"]
        if case_id in done:
            continue  # An interrupted run resumes; recorded cases are never called again.
        input_row = inputs_by_case[case_id]
        workspace_id = uuid.UUID(item["workspace_id"])
        proposal_id = uuid.UUID(item["proposal_id"])
        request_payload: JsonRow | None = None
        provider_called = True
        with factory() as db:
            proposal = db.get(Proposal, proposal_id)
            if proposal is None:
                raise SystemExit(f"prepared proposal for {case_id} is missing")
            try:
                request, _ = workflow._request(
                    db, workspace_id=workspace_id, proposal=proposal, mode="direct", policy=policy
                )
                request_payload = request.model_dump(mode="json")
                provider_called = bool(request.source_spans)
            except ServiceError as exc:
                if exc.code != "no_candidates":
                    raise
                provider_called = False
            db.rollback()
            started = time.perf_counter()
            if provider_called:
                outcome = workflow.run(
                    db,
                    workspace_id=workspace_id,
                    session_id=uuid.uuid4(),
                    proposal_id=proposal_id,
                    mode="direct",
                    settings=settings,
                    policy=policy,
                )
            wall_ms = (time.perf_counter() - started) * 1000
            revision = db.scalar(
                select(ProposalRevision)
                .where(ProposalRevision.proposal_id == proposal_id)
                .order_by(ProposalRevision.revision.desc())
                .limit(1)
            )
            trace = dict(revision.model_trace or {}) if revision is not None else {}
        interpretation = trace.get("interpretation") or {}
        if provider_called:
            outcome_row = {
                "status": outcome.status,
                "candidate_id": outcome.candidate_id,
                "reason_code": outcome.reason_code,
                "citations": [dict(citation) for citation in outcome.citations],
                "failure_code": outcome.failure_code,
                "source": outcome.source,
                "provider_called": True,
            }
        else:
            outcome_row = {"status": "needs_review", "provider_called": False}
        recording = seal_recording(
            {
                "schema": RECORDING_SCHEMA,
                "case_id": case_id,
                **identity,
                "request": request_payload,
                "request_sha256": request_digest(request_payload) if request_payload else None,
                "workflow_input_fingerprint": interpretation.get("input_fingerprint")
                or trace.get("input_fingerprint"),
                "outcome": outcome_row,
                "telemetry": {
                    "attempts": interpretation.get("attempts") or [],
                    "wall_ms": round(wall_ms, 3),
                },
            }
        )
        derived = observation_from_recording(recording, input_row=input_row, identity=identity)
        latencies = [
            float(attempt["latency_ms"])
            for attempt in recording["telemetry"]["attempts"]
            if isinstance(attempt, Mapping) and isinstance(attempt.get("latency_ms"), (int, float))
        ]
        row = {
            "case_id": case_id,
            "group_id": item["group_id"],
            "split": item["split"],
            "method": "direct",
            **_fingerprints(input_row),
            **derived,
            "duration_ms": sum(latencies) if latencies else None,
            "timing_scope": "provider",
            "recording": recording,
        }
        if row.get("allocation") is None:
            row.pop("allocation", None)
        if row.get("candidate_id") is None:
            row.pop("candidate_id", None)
        with direct_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        written += 1
    return {"planned": len(plan), "recorded_now": written, "previously_recorded": len(done)}


def _select_ranker_threshold(metrics: Mapping[str, Mapping[str, Any]]) -> float | None:
    """Protocol rule: maximum coverage with zero unsupported proposals, ties to higher."""

    eligible = [
        (float(threshold), row["proposal"]["all_case_coverage"] or 0.0)
        for threshold, row in metrics.items()
        if row["support"]["unsupported"] == 0
    ]
    if not eligible:
        return None
    best = max(coverage for _, coverage in eligible)
    return max(threshold for threshold, coverage in eligible if coverage == best)


def _outcome_counts(rows: Iterable[Mapping[str, Any]], truth: Mapping[str, Any]) -> JsonRow:
    """End-to-end counts over every case, including answerable-but-unreachable ones."""

    counts: Counter[str] = Counter()
    wrong_value = 0
    for row in rows:
        case = truth[row["case_id"]]
        status = str(row["status"]).upper()
        counts["cases"] += 1
        counts["answerable"] += int(case["answerable"])
        if status == "PROPOSED":
            counts["proposals"] += 1
            expected = case.get("expected")
            correct = bool(
                case["answerable"]
                and expected is not None
                and allocation_matches(expected, row.get("allocation") or {})
            )
            counts["correct"] += int(correct)
            if not correct:
                counts["unsupported"] += 1
                wrong_value += int(case["payment_amount"])
        elif status in {"NEEDS_REVIEW", "DEFERRED"}:
            counts["deferred"] += 1
            if case["answerable"]:
                counts["unnecessary_deferrals"] += 1
            else:
                counts["correct_deferrals"] += 1
        else:
            counts["unavailable_or_error"] += 1
    result: JsonRow = dict(counts)
    result["misallocated_centavos"] = wrong_value
    proposals, answerable = counts["proposals"], counts["answerable"]
    result["precision"] = counts["correct"] / proposals if proposals else None
    result["answerable_resolved"] = counts["correct"] / answerable if answerable else None
    return result


def score(out: Path, *, include_final: bool) -> JsonRow:
    """Score dev and validation, select the ranker point, then open final once."""

    run = json.loads((out / "run.json").read_text())
    identity = run["identity"]
    manifests = run["manifests"]
    meta = {row["case_id"]: row for row in _read_jsonl(out / "meta.jsonl")}
    rules = {row["case_id"]: row for row in _read_jsonl(out / "rules.jsonl")}
    ranker = {row["case_id"]: row for row in _read_jsonl(out / "ranker.jsonl")}
    direct_path = out / "direct.jsonl"
    direct = (
        {row["case_id"]: row for row in _read_jsonl(direct_path)} if direct_path.exists() else {}
    )
    unreachable = {row["case_id"]: row for row in _read_jsonl(out / "inputs-unreachable.jsonl")}

    report: JsonRow = {"identity": identity, "evaluator": {}, "end_to_end": {}}
    split_rows: dict[str, tuple[list[JsonRow], list[JsonRow]]] = {}
    for split in SPLITS:
        if split == FINAL_SPLIT and not include_final:
            continue
        inputs_path, labels_path = out / f"inputs-{split}.jsonl", out / f"labels-{split}.jsonl"
        if split == FINAL_SPLIT:
            manifest = manifests[split]
            split_rows[split] = read_split(
                inputs_path,
                labels_path,
                split=split,
                manifest=manifest,
                authorize_final=True,
                manifest_hash=manifest["manifest_sha256"],
                ledger_path=out / "final-access.ledger",
            )
        else:
            split_rows[split] = read_split(
                inputs_path, labels_path, split=split, manifest=manifests[split]
            )

    val_inputs, val_labels = split_rows["validation"]
    thresholds = (
        evaluate_ranker_thresholds(
            val_inputs, val_labels, [ranker[row["case_id"]] for row in val_inputs]
        )
        if val_inputs
        else {}
    )
    threshold = _select_ranker_threshold(thresholds)
    report["ranker_selection"] = {
        "rule": "max coverage with zero unsupported proposals on validation; ties choose higher",
        "selected_threshold": threshold,
        "validation_metrics": {key: value["proposal"] for key, value in thresholds.items()},
    }

    def ranker_rows(case_rows: Iterable[Mapping[str, Any]]) -> list[JsonRow]:
        by_case = {row["case_id"]: row for row in case_rows}
        raw = [ranker[case_id] for case_id in by_case]
        if threshold is None:
            return [
                {k: v for k, v in row.items() if k not in {"ranked_candidates"}}
                | {"status": "DEFERRED"}
                for row in raw
            ]
        return _materialize_threshold_results(by_case, raw, threshold)

    for split, (split_inputs, split_labels) in split_rows.items():
        if not split_inputs:
            continue
        ids = [row["case_id"] for row in split_inputs]
        evaluated = {
            "rules": evaluate_observations(
                split_inputs, split_labels, [rules[i] for i in ids], split=split, method="rules"
            ),
            "ranker": evaluate_observations(
                split_inputs,
                split_labels,
                ranker_rows(split_inputs),
                split=split,
                method="ranker",
                ranker_timing=True,
            ),
        }
        deferred_ids = [i for i in ids if rules[i]["status"] != "PROPOSED"]
        if deferred_ids and all(i in direct for i in deferred_ids):
            subset = [row for row in split_inputs if row["case_id"] in set(deferred_ids)]
            subset_labels = [row for row in split_labels if row["case_id"] in set(deferred_ids)]
            evaluated["direct_on_rules_deferred"] = evaluate_observations(
                subset,
                subset_labels,
                [direct[i] for i in deferred_ids],
                split=split,
                method="direct",
                provider_identity=identity,
            )
        report["evaluator"][split] = evaluated

    def truth(case_id: str) -> JsonRow:
        row = meta[case_id]
        expected = None
        if case_id in unreachable:
            expected = unreachable[case_id]["expected_allocation"]
        else:
            labels_rows = split_rows.get(row["split"], ([], []))[1]
            label = next((item for item in labels_rows if item["case_id"] == case_id), None)
            expected = label.get("expected_allocation") if label else None
        return {**row, "expected": expected}

    all_ranker_inputs = {
        row["case_id"]: row
        for split, (split_inputs, _) in split_rows.items()
        for row in split_inputs
    }
    all_ranker_inputs.update(
        {
            case_id: {k: v for k, v in row.items() if k != "expected_allocation"}
            for case_id, row in unreachable.items()
        }
    )
    for scope, splits in (
        ("reserved-final", (FINAL_SPLIT,)),
        ("all-cases", SPLITS),
    ):
        if scope == "reserved-final" and not include_final:
            continue
        case_ids = [
            case_id
            for case_id, row in meta.items()
            if row["split"] in splits and (row["split"] in split_rows)
        ]
        truths = {case_id: truth(case_id) for case_id in case_ids}
        pipeline = []
        for case_id in case_ids:
            if rules[case_id]["status"] == "PROPOSED":
                pipeline.append(rules[case_id])
            elif case_id in direct:
                pipeline.append(direct[case_id])
            else:
                pipeline.append({"case_id": case_id, "status": "UNAVAILABLE"})
        ranker_ids = [
            case_id for case_id in case_ids if meta[case_id]["split"] != "validation"
        ]
        materialized = (
            _materialize_threshold_results(
                {case_id: all_ranker_inputs[case_id] for case_id in ranker_ids},
                [ranker[case_id] for case_id in ranker_ids],
                threshold,
            )
            if threshold is not None
            else [{"case_id": case_id, "status": "DEFERRED"} for case_id in ranker_ids]
        )
        report["end_to_end"][scope] = {
            "rules": _outcome_counts([rules[i] for i in case_ids], truths),
            "ranker_excluding_validation": _outcome_counts(materialized, truths),
            "rules_then_direct": _outcome_counts(pipeline, truths),
            "answerable_unreachable": sum(1 for i in case_ids if i in unreachable),
        }

    attempts = [
        attempt
        for row in direct.values()
        for attempt in row["recording"]["telemetry"]["attempts"]
        if isinstance(attempt, Mapping)
    ]
    card = RateCard()
    cost = 0
    input_tokens = output_tokens = 0
    for attempt in attempts:
        usage = attempt.get("usage") or {}
        if isinstance(usage.get("input_tokens"), int) and isinstance(
            usage.get("output_tokens"), int
        ):
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            cost += card.estimated_microdollars(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_input_tokens=min(
                    usage.get("provider_cache_tokens") or 0, usage["input_tokens"]
                ),
            )
    latencies = sorted(
        float(attempt["latency_ms"])
        for attempt in attempts
        if isinstance(attempt.get("latency_ms"), (int, float))
    )
    report["provider"] = {
        "cases_recorded": len(direct),
        "attempts": len(attempts),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd_at_peak_list_price": round(cost / 1_000_000, 6),
        "latency_ms_p50": latencies[len(latencies) // 2] if latencies else None,
        "latency_ms_p95": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
        if latencies
        else None,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--cases", type=Path, required=True)
    prep.add_argument("--labels", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--commit", required=True)
    live = commands.add_parser("interpret")
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--budget-usd", required=True)
    live.add_argument("--run-id", required=True)
    grade = commands.add_parser("score")
    grade.add_argument("--out", type=Path, required=True)
    grade.add_argument("--final", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        summary = prepare(args.cases, args.labels, args.out, args.commit)
    elif args.command == "interpret":
        summary = interpret(args.out, args.budget_usd, args.run_id)
    else:
        report = score(args.out, include_final=args.final)
        summary = {"end_to_end": report["end_to_end"], "provider": report["provider"]}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
