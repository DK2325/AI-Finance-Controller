"""The canonical in-memory shapes the matcher works with.

Parsed once at the edge of the pipeline so no layer below re-parses a date or re-derives
paise from a string. Everything downstream is integers and dates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.normalize import (
    counterparty_prefix,
    extract_utrs,
    from_timestamp,
    narration_tokens,
    normalize_counterparty,
    normalize_narration,
    parse_date,
    to_paise,
)

TYPE_PAYMENT = "payment"
TYPE_REFUND = "refund"


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    customer_name: str
    amount: int
    invoice_date: date | None
    tds_section: str
    status: str
    counterparty: str = ""

    @classmethod
    def from_row(cls, row: dict) -> Invoice:
        return cls(
            invoice_id=row["invoice_id"],
            customer_name=row["customer_name"],
            amount=to_paise(row["amount"]),
            invoice_date=parse_date(row["invoice_date"]),
            tds_section=row.get("tds_section", ""),
            status=row.get("status", ""),
            counterparty=normalize_counterparty(row["customer_name"]),
        )


@dataclass(frozen=True)
class Settlement:
    """One recon row. Gateway money is already integer paise in the source."""

    entity_id: str
    settlement_id: str
    utr: str
    txn_type: str
    invoice_id: str
    amount: int
    fee: int
    tax: int
    net_amount: int
    settled_date: date | None
    method: str

    @classmethod
    def from_row(cls, row: dict) -> Settlement:
        return cls(
            entity_id=row["entity_id"],
            settlement_id=row["settlement_id"],
            utr=row["settlement_utr"],
            txn_type=row["type"],
            # order_receipt is the merchant's own reference on the gateway row.
            invoice_id=row["order_receipt"],
            amount=int(row["amount"]),
            fee=int(row["fee"]),
            tax=int(row["tax"]),
            net_amount=int(row["net_amount"]),
            settled_date=from_timestamp(row["settled_at"]),
            method=row.get("method", ""),
        )

    @property
    def is_payment(self) -> bool:
        return self.txn_type == TYPE_PAYMENT


@dataclass(frozen=True)
class BankTxn:
    txn_id: str
    credit: int
    debit: int
    value_date: date | None
    narration: str
    bank: str
    bank_ref: str
    utrs: tuple[str, ...] = ()
    tokens: frozenset[str] = field(default_factory=frozenset)
    normalized_narration: str = ""

    @classmethod
    def from_row(cls, row: dict) -> BankTxn:
        narration = row.get("narration", "")
        return cls(
            txn_id=row["txn_id"],
            credit=to_paise(row.get("credit", "")),
            debit=to_paise(row.get("debit", "")),
            value_date=parse_date(row.get("value_date", "")),
            narration=narration,
            bank=row.get("bank", ""),
            bank_ref=row.get("bank_ref", ""),
            utrs=tuple(extract_utrs(narration)),
            tokens=frozenset(narration_tokens(narration)),
            normalized_narration=normalize_narration(narration),
        )


@dataclass(frozen=True)
class Sources:
    """Everything the matcher is allowed to see. No truth, ever."""

    invoices: list[Invoice]
    settlements: list[Settlement]
    bank: list[BankTxn]

    @property
    def payments(self) -> list[Settlement]:
        return [s for s in self.settlements if s.is_payment]

    @property
    def invoice_by_id(self) -> dict[str, Invoice]:
        return {i.invoice_id: i for i in self.invoices}

    def counterparty_for(self, settlement: Settlement) -> str:
        invoice = self.invoice_by_id.get(settlement.invoice_id)
        return invoice.counterparty if invoice else ""

    def counterparty_prefix_for(self, settlement: Settlement, length: int = 10) -> str:
        invoice = self.invoice_by_id.get(settlement.invoice_id)
        return counterparty_prefix(invoice.customer_name, length) if invoice else ""
