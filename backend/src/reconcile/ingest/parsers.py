from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from reconcile.domain.money import MoneyError, parse_money

SourceKind = Literal["bank", "invoice", "credit", "message"]
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9._:/-]{1,100}\Z")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}$")
LIMITS = {
    "local": {
        "file": 2 * 1024 * 1024,
        "batch": 10 * 1024 * 1024,
        "rows": 2_000,
        "entities": 6_000,
        "workspace": 10 * 1024 * 1024,
    },
    "preview": {
        "file": 1 * 1024 * 1024,
        "batch": 4 * 1024 * 1024,
        "rows": 500,
        "entities": 1_500,
        "workspace": 5 * 1024 * 1024,
    },
}

HEADERS: dict[str, tuple[str, ...]] = {
    "bank": (
        "source_account_id",
        "transaction_id",
        "booking_date",
        "payer_name",
        "reference",
        "amount",
        "currency",
    ),
    "invoice": (
        "customer_id",
        "customer_name",
        "invoice_id",
        "issued_date",
        "due_date",
        "balance_as_of",
        "outstanding_amount",
        "currency",
    ),
    "credit": ("customer_id", "credit_note_id", "balance_as_of", "available_amount", "currency"),
}


@dataclass(frozen=True, slots=True)
class RowIssue:
    row: int
    field: str | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedSource:
    kind: SourceKind
    raw: bytes
    sha256: str
    rows: tuple[dict[str, Any], ...] = ()
    issues: tuple[RowIssue, ...] = ()
    text: str | None = None
    row_locators: tuple[dict[str, int], ...] = ()

    @property
    def accepted_count(self) -> int:
        return len(self.rows)

    @property
    def rejected_count(self) -> int:
        return len({i.row for i in self.issues})


@dataclass(frozen=True, slots=True)
class MessageContext:
    message_time: datetime
    payment_source_account_id: str
    payment_transaction_id: str


@dataclass(frozen=True, slots=True)
class ParsedBatch:
    bank: ParsedSource
    invoices: ParsedSource
    credits: ParsedSource | None = None
    message: ParsedSource | None = None
    message_context: MessageContext | None = None

    @property
    def total_bytes(self) -> int:
        return sum(len(s.raw) for s in (self.bank, self.invoices, self.credits, self.message) if s)


def source_hash(kind: SourceKind, raw: bytes) -> str:
    return hashlib.sha256(kind.encode() + b"\0" + raw).hexdigest()


def _csv_record_spans(raw: bytes) -> tuple[dict[str, int], ...]:
    spans: list[dict[str, int]] = []
    start = 0
    quoted = False
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte == 34:
            if quoted and index + 1 < len(raw) and raw[index + 1] == 34:
                index += 2
                continue
            quoted = not quoted
        elif not quoted and byte in (10, 13):
            end = index + 1
            if byte == 13 and end < len(raw) and raw[end] == 10:
                end += 1
            spans.append({"record": len(spans) + 1, "start_byte": start, "end_byte": end})
            start = end
            index = end
            continue
        index += 1
    if start < len(raw):
        spans.append({"record": len(spans) + 1, "start_byte": start, "end_byte": len(raw)})
    return tuple(spans)


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 byte-order mark is not accepted")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("source must be valid UTF-8") from exc


def _identifier(value: str, field: str, row: int, issues: list[RowIssue]) -> str:
    value = value.strip()
    if not IDENTIFIER_RE.fullmatch(value):
        issues.append(
            RowIssue(row, field, "invalid_identifier", "identifier has an unsupported format")
        )
    return value


def _date(value: str, field: str, row: int, issues: list[RowIssue]) -> date | None:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        issues.append(RowIssue(row, field, "invalid_date", "date must be YYYY-MM-DD"))
        return None


def _money(value: str, field: str, row: int, issues: list[RowIssue]) -> int | None:
    try:
        return parse_money(value)
    except MoneyError as exc:
        issues.append(RowIssue(row, field, "invalid_money", str(exc)))
        return None


def parse_csv_source(
    kind: Literal["bank", "invoice", "credit"], raw: bytes, *, profile: str = "local"
) -> ParsedSource:
    limits = LIMITS[profile]
    if len(raw) > limits["file"]:
        raise ValueError("source exceeds file-size limit")
    text = _decode(raw)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV must contain a header") from exc
    expected = HEADERS[kind]
    if len(header) != len(set(header)) or tuple(header) != expected:
        raise ValueError("CSV header does not exactly match the required schema")
    rows: list[dict[str, Any]] = []
    issues: list[RowIssue] = []
    for row_number, values in enumerate(reader, 2):
        if not values or all(not value.strip() for value in values):
            continue
        if row_number - 1 > limits["rows"]:
            issues.append(RowIssue(row_number, None, "row_limit", "CSV exceeds record limit"))
            continue
        if len(values) != len(expected):
            issues.append(
                RowIssue(row_number, None, "column_count", "row has incorrect number of columns")
            )
            continue
        row_issues: list[RowIssue] = []
        values = [v.strip() for v in values]
        if any(v == "" for v in values):
            for field, value in zip(expected, values, strict=True):
                if value == "":
                    row_issues.append(RowIssue(row_number, field, "required", "value is required"))

        def text_field(field: str, max_length: int) -> str:
            value = values[expected.index(field)]
            if len(value) > max_length:
                row_issues.append(
                    RowIssue(
                        row_number, field, "too_long", f"value exceeds {max_length} characters"
                    )
                )
            return value

        parsed: dict[str, Any] = {}
        if kind == "bank":
            parsed = {
                "source_account_id": _identifier(
                    values[0], "source_account_id", row_number, row_issues
                ),
                "transaction_id": _identifier(values[1], "transaction_id", row_number, row_issues),
                "booking_date": _date(values[2], "booking_date", row_number, row_issues),
                "payer_name": text_field("payer_name", 200),
                "reference": text_field("reference", 500),
                "amount": _money(values[5], "amount", row_number, row_issues),
                "currency": values[6],
            }
        elif kind == "invoice":
            parsed = {
                "customer_id": _identifier(values[0], "customer_id", row_number, row_issues),
                "customer_name": text_field("customer_name", 200),
                "invoice_id": _identifier(values[2], "invoice_id", row_number, row_issues),
                "issued_date": _date(values[3], "issued_date", row_number, row_issues),
                "due_date": _date(values[4], "due_date", row_number, row_issues),
                "balance_as_of": _date(values[5], "balance_as_of", row_number, row_issues),
                "outstanding_amount": _money(
                    values[6], "outstanding_amount", row_number, row_issues
                ),
                "currency": values[7],
            }
        else:
            parsed = {
                "customer_id": _identifier(values[0], "customer_id", row_number, row_issues),
                "credit_note_id": _identifier(values[1], "credit_note_id", row_number, row_issues),
                "balance_as_of": _date(values[2], "balance_as_of", row_number, row_issues),
                "available_amount": _money(values[3], "available_amount", row_number, row_issues),
                "currency": values[4],
            }
        if parsed.get("currency") != "MXN":
            row_issues.append(
                RowIssue(row_number, "currency", "unsupported_currency", "currency must be MXN")
            )
        if row_issues:
            issues.extend(row_issues)
        else:
            rows.append(parsed)
    return ParsedSource(
        kind,
        raw,
        source_hash(kind, raw),
        tuple(rows),
        tuple(issues),
        row_locators=_csv_record_spans(raw),
    )


def parse_credit_source(raw: bytes, *, profile: str = "local") -> ParsedSource:
    """Parse the optional credit schema, accepting its optional invoice_id column."""
    text = _decode(raw)
    limits = LIMITS[profile]
    if len(raw) > limits["file"]:
        raise ValueError("source exceeds file-size limit")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV must contain a header") from exc
    required = HEADERS["credit"]
    if len(header) != len(set(header)) or tuple(header) not in (
        required,
        required + ("invoice_id",),
    ):
        raise ValueError("CSV header does not exactly match the required schema")
    rows: list[dict[str, Any]] = []
    issues: list[RowIssue] = []
    for row_number, values in enumerate(reader, 2):
        if not values or all(not value.strip() for value in values):
            continue
        if row_number - 1 > limits["rows"]:
            issues.append(RowIssue(row_number, None, "row_limit", "CSV exceeds record limit"))
            continue
        if len(values) != len(header):
            issues.append(
                RowIssue(row_number, None, "column_count", "row has incorrect number of columns")
            )
            continue
        values = [v.strip() for v in values]
        row_issues: list[RowIssue] = []
        if any(v == "" for v in values[:5]):
            for field, value in zip(header[:5], values[:5], strict=True):
                if value == "":
                    row_issues.append(RowIssue(row_number, field, "required", "value is required"))
        parsed = {
            "customer_id": _identifier(values[0], "customer_id", row_number, row_issues),
            "credit_note_id": _identifier(values[1], "credit_note_id", row_number, row_issues),
            "balance_as_of": _date(values[2], "balance_as_of", row_number, row_issues),
            "available_amount": _money(values[3], "available_amount", row_number, row_issues),
            "currency": values[4],
            "invoice_id": _identifier(values[5], "invoice_id", row_number, row_issues)
            if len(values) == 6 and values[5]
            else None,
        }
        if parsed["currency"] != "MXN":
            row_issues.append(
                RowIssue(row_number, "currency", "unsupported_currency", "currency must be MXN")
            )
        if row_issues:
            issues.extend(row_issues)
        else:
            rows.append(parsed)
    return ParsedSource(
        "credit",
        raw,
        source_hash("credit", raw),
        tuple(rows),
        tuple(issues),
        row_locators=_csv_record_spans(raw),
    )


def parse_message_source(raw: bytes, *, profile: str = "local") -> ParsedSource:
    limits = LIMITS[profile]
    if len(raw) > limits["file"]:
        raise ValueError("source exceeds file-size limit")
    text = _decode(raw)
    if len(text) > 100_000:
        raise ValueError("message exceeds character limit")
    return ParsedSource("message", raw, source_hash("message", raw), text=text)


def parse_message_context(
    message_time: str, payment_source_account_id: str, payment_transaction_id: str
) -> MessageContext:
    if not RFC3339_RE.fullmatch(message_time):
        raise ValueError("message_time must be RFC 3339 with a numeric UTC offset")
    try:
        parsed = datetime.fromisoformat(message_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("message_time must be RFC 3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("message_time must include a numeric UTC offset")
    issues: list[RowIssue] = []
    account = _identifier(payment_source_account_id, "payment_source_account_id", 0, issues)
    transaction = _identifier(payment_transaction_id, "payment_transaction_id", 0, issues)
    if issues:
        raise ValueError("message payment association is invalid")
    return MessageContext(parsed, account, transaction)


def parse_batch(
    bank: bytes,
    invoices: bytes,
    credits: bytes | None = None,
    message: bytes | None = None,
    *,
    message_context: MessageContext | None = None,
    profile: str = "local",
) -> ParsedBatch:
    limits = LIMITS[profile]
    if len(bank) + len(invoices) + len(credits or b"") + len(message or b"") > limits["batch"]:
        raise ValueError("batch exceeds byte limit")
    parsed_message = parse_message_source(message, profile=profile) if message is not None else None
    if parsed_message is not None and message_context is None:
        raise ValueError("message context is required")
    return ParsedBatch(
        parse_csv_source("bank", bank, profile=profile),
        parse_csv_source("invoice", invoices, profile=profile),
        parse_credit_source(credits, profile=profile) if credits is not None else None,
        parsed_message,
        message_context,
    )
