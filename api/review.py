"""The review queue: evidence for one exception, and what a human decided about it.

EVIDENCE MEANS THE ROWS, SIDE BY SIDE

BUILD.md asks for "each exception with evidence side by side". The evidence is the three
source rows the decision was made from -- the settlement, the bank credit the matcher
considered, and the invoice if one could be named -- shown as they actually are, not
summarised. An operator resolving an exception is comparing an amount against an amount
and a name against a narration, and a summary removes exactly the detail they need.

Amounts are returned as integer paise *and* as formatted rupees. The integer is the truth;
the string is for the screen. Nothing computes with the string.

WHAT AN APPROVAL WRITES

An audit record, with an approver and a timestamp, in the same shape every other layer
uses. Postgres is the canonical store and the append-only trigger lives there.

**If Postgres is unreachable the decision is still recorded**, to an append-only file
beside the run, and the response says which store took it. A demo that silently drops a
human decision because a database was down would be a worse failure than refusing it, and
refusing it would make the review screen unusable whenever the database is. Saying which
store holds it is the honest third option.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.pipeline import load_sources
from ledgerloop.audit import (
    DECISION_ESCALATED,
    LAYER_MODEL,
    AuditRecord,
    row_hash,
)
from llm.codes import ReasonCode

APPROVALS_FILE = "approvals.jsonl"
ACTIONS = ("approve", "reject", "edit")

# Loading a batch parses every row; the review screen hits it once per page of exceptions.
_SOURCES_CACHE: dict[str, Any] = {}


def _sources(batch_dir: str):
    if batch_dir not in _SOURCES_CACHE:
        _SOURCES_CACHE[batch_dir] = load_sources(batch_dir)
    return _SOURCES_CACHE[batch_dir]


def rupees(paise: int | None) -> str:
    return "—" if paise is None else f"{paise / 100:,.2f}"


def evidence_for(exception: dict, batch_dir: str) -> dict:
    """The three source rows behind one exception, plus what the matcher made of them."""
    sources = _sources(batch_dir)

    settlement = next(
        (s for s in sources.payments if s.entity_id == exception["entity_id"]), None
    )
    txn = next((t for t in sources.bank if t.txn_id == exception.get("txn_id")), None)
    invoice = sources.invoice_by_id.get(exception.get("invoice_id") or "")

    code = ReasonCode(exception["reason_code"])

    difference = None
    if settlement is not None and txn is not None:
        difference = txn.credit - settlement.net_amount

    return {
        "entity_id": exception["entity_id"],
        "reason_code": str(code),
        "reason_family": str(code.family),
        "reason_description": code.description,
        "detail": exception.get("detail", ""),
        "confidence": exception.get("confidence"),
        "needs_human": True,
        "settlement": None if settlement is None else {
            "entity_id": settlement.entity_id,
            "settlement_id": settlement.settlement_id,
            "utr": settlement.utr,
            "method": settlement.method,
            "settled_date": settlement.settled_date.isoformat()
            if settlement.settled_date else "",
            "gross_paise": settlement.amount,
            "fee_paise": settlement.fee,
            "tax_paise": settlement.tax,
            "net_paise": settlement.net_amount,
            "gross": rupees(settlement.amount),
            "fee": rupees(settlement.fee),
            "tax": rupees(settlement.tax),
            "net": rupees(settlement.net_amount),
        },
        # None where the matcher found no candidate at all -- and the screen should show
        # that absence rather than an empty card, because "nothing resembled this payout"
        # is the finding.
        "bank_txn": None if txn is None else {
            "txn_id": txn.txn_id,
            "bank": txn.bank,
            "value_date": txn.value_date.isoformat() if txn.value_date else "",
            "credit_paise": txn.credit,
            "credit": rupees(txn.credit),
            "narration": txn.narration,
            "bank_ref": txn.bank_ref,
        },
        "invoice": None if invoice is None else {
            "invoice_id": invoice.invoice_id,
            "customer_name": invoice.customer_name,
            "amount_paise": invoice.amount,
            "amount": rupees(invoice.amount),
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else "",
            "tds_section": invoice.tds_section,
        },
        "difference_paise": difference,
        "difference": rupees(difference) if difference is not None else "—",
    }


# ------------------------------------------------------------------ decisions


def _run_dir(run_id: str, root: Path) -> Path:
    if "/" in run_id or "\\" in run_id or run_id in ("", ".", ".."):
        raise ValueError(f"unsafe run id: {run_id!r}")
    return root / run_id


def record_decision(
    run_id: str,
    entity_id: str,
    action: str,
    approver: str,
    batch_dir: str = "",
    run_counts: dict | None = None,
    note: str = "",
    edited_entry: dict | None = None,
    root: Path | str = "runs",
) -> dict:
    """Write a human decision as an audit record. Returns where it was stored.

    The record is built first and stored second, so a storage failure cannot produce a
    half-formed record -- AuditRecord refuses to exist in an invalid state.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; known: {', '.join(ACTIONS)}")
    if not approver.strip():
        raise ValueError("an approval with no approver is not an approval")

    record = AuditRecord(
        run_id=run_id,
        layer=LAYER_MODEL,
        # A human verdict is an escalation resolved, not a match the system made. Filing
        # it as `matched` would let a human decision be counted in the auto-match rate,
        # which is the number the whole thesis rests on.
        decision=DECISION_ESCALATED,
        entity_id=entity_id,
        reason_code=ReasonCode.LOW_CONFIDENCE,
        reason_detail=f"human {action}: {note}"[:400] if note else f"human {action}",
        input_row_hashes={"settlement": row_hash(entity_id)},
        approver=approver.strip()[:128],
    )

    payload = record.as_row()
    payload["batch_dir"] = batch_dir
    payload["run_counts"] = run_counts or {}
    payload["action"] = action
    payload["edited_journal_entry"] = edited_entry
    payload["decided_at"] = datetime.now(UTC).isoformat()

    stored, detail = _persist(payload, run_id, Path(root))
    return {"stored_in": stored, "detail": detail, "record": payload}


def _persist(payload: dict, run_id: str, root: Path) -> tuple[str, str]:
    """Postgres if it will take it, an append-only file otherwise. Never silently.

    `audit_records.run_id` is a foreign key to `runs.id`, so a decision cannot be recorded
    against a run the database has never heard of -- and runs scored by the CLI live on the
    filesystem, not in Postgres. The run row is therefore created first, idempotently.

    That constraint is doing its job rather than getting in the way: an audit record
    pointing at a run that does not exist is a record nobody can trace back.
    """
    counts = payload.get("run_counts") or {}
    try:
        from sqlalchemy import text

        from ledgerloop.db import engine

        with engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO runs (id, batch_dir, status, mock_llm, "
                    "rows_total, rows_auto_matched, rows_exception) "
                    "VALUES (:run_id, :batch_dir, 'scored', true, "
                    ":rows_total, :rows_auto_matched, :rows_exception) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                # The counters are NOT NULL with no *server* default -- SQLAlchemy's
                # `default=0` is client-side and invisible to raw SQL. So the real counts
                # are passed rather than zeros: a run row claiming 0 settlements would be
                # a stub that reads as a fact.
                {
                    "run_id": payload["run_id"],
                    "batch_dir": payload.get("batch_dir", ""),
                    "rows_total": counts.get("settlements", 0),
                    "rows_auto_matched": counts.get("matched", 0),
                    "rows_exception": counts.get("exceptions", 0),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO audit_records "
                    "(id, run_id, layer, decision, approver, created_at) "
                    "VALUES (gen_random_uuid()::text, :run_id, :layer, :decision, "
                    ":approver, now())"
                ),
                {
                    "run_id": payload["run_id"],
                    "layer": payload["layer"],
                    "decision": payload["decision"],
                    "approver": payload["approver"],
                },
            )
        return "postgres", "append-only, enforced by a trigger"
    except Exception as exc:
        # Distinguish "the database is down" from "the database refused this row". The
        # first version said "unreachable" for both, and sent the first investigation to
        # the wrong place -- Postgres was up and the insert was rejected by a foreign key.
        target = _run_dir(run_id, root)
        target.mkdir(parents=True, exist_ok=True)
        with (target / APPROVALS_FILE).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return "file", f"{_classify(exc)}; appended to {APPROVALS_FILE}"


def _classify(exc: Exception) -> str:
    """What actually went wrong, in words that point somewhere useful."""
    name = type(exc).__name__
    if name in ("OperationalError", "InterfaceError", "DBAPIError"):
        return f"postgres unreachable ({name})"
    if name == "IntegrityError":
        return f"postgres refused the row ({name}) -- a constraint, not an outage"
    if name == "ProgrammingError":
        return f"postgres schema mismatch ({name}) -- migrations may not have run"
    return f"postgres write failed ({name})"


def decisions_for(run_id: str, root: Path | str = "runs") -> list[dict]:
    """Decisions recorded to file for this run. Postgres-held ones are queried in SQL."""
    path = _run_dir(run_id, Path(root)) / APPROVALS_FILE
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [json.loads(line) for line in handle if line.strip()]
