from __future__ import annotations

import io
from zipfile import ZIP_DEFLATED, ZipFile

SAMPLE_FILES: dict[str, bytes] = {
    "bank": b"""source_account_id,transaction_id,booking_date,payer_name,reference,amount,currency
acct-1,pay-54k,2026-01-15,Acme SA,Invoices 101 102 credit 103,54000,MXN
""",
    "invoices": (
        b"customer_id,customer_name,invoice_id,issued_date,due_date,balance_as_of,"
        b"outstanding_amount,currency\n"
        b"cust-1,Acme SA,100,2025-12-01,2026-01-01,2026-01-15,54000,MXN\n"
        b"cust-1,Acme SA,101,2025-12-01,2026-01-01,2026-01-15,30000,MXN\n"
        b"cust-1,Acme SA,102,2025-12-01,2026-01-01,2026-01-15,25000,MXN\n"
    ),
    "credits": b"""customer_id,credit_note_id,balance_as_of,available_amount,currency,invoice_id
cust-1,103,2026-01-15,1000,MXN,102
""",
    "message": (
        b"Please apply payment pay-54k to invoices 101 and 102, "
        b"using credit note 103 on invoice 102.\n"
    ),
}

SAMPLE_CONTEXT = {
    "message_time": "2026-01-15T12:00:00+00:00",
    "payment_source_account_id": "acct-1",
    "payment_transaction_id": "pay-54k",
}


def is_exact_sample_packet(files: dict[str, bytes]) -> bool:
    return files == SAMPLE_FILES


def sample_zip() -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("bank.csv", SAMPLE_FILES["bank"])
        archive.writestr("invoices.csv", SAMPLE_FILES["invoices"])
        archive.writestr("credits.csv", SAMPLE_FILES["credits"])
        archive.writestr("message.txt", SAMPLE_FILES["message"])
        archive.writestr(
            "README.txt",
            "Use message_time=2026-01-15T12:00:00+00:00 and associate "
            "acct-1/pay-54k when importing message.txt.\n",
        )
    return output.getvalue()
