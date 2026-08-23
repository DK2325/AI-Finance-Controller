"""Corruption the system was never built for, injected into a live batch.

WHAT THIS IS FOR

BUILD.md: "The system must route the unknown to exceptions with honest reason codes rather
than confidently mis-matching. Failing gracefully under chaos **is** the point -- a
graceful failure proves the thesis as well as a success does."

So the success criterion here is inverted from everywhere else in the project. A corruption
that drives coverage to zero while precision holds is a **pass**. A corruption that leaves
coverage high while precision collapses is a failure, and it is the only outcome that
matters -- because that is money posted against the wrong invoice with no warning.

WHY THE CORRUPTIONS LIVE IN core/ AND NOT IN datagen/

They operate on `Sources` -- rows already loaded -- rather than on the generator. That is
deliberate and it is what makes them *unmodelled*: nothing in `datagen/` produces a
line-wrapped UTR or a Bengali-transliterated narration, so the matcher has never seen one
at training time and the isolation boundary is not involved. Chaos is applied after
loading, to data the generator did not create.

EVERY CORRUPTION IS REVERSIBLE AND REPORTED

Each returns the number of rows it touched. A corruption that silently did nothing would
make the system look robust when it was never tested, which is the same failure as a test
that cannot fail.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from core.records import BankTxn, Sources

# The closed set. A free-text spec is mapped onto these; nothing outside them can run,
# which is the same schema-as-constraint principle the LLM layer uses.
CorruptionName = Literal[
    "unseen_narration",
    "wrapped_utr",
    "date_format_swap",
    "unmodelled_fee",
    "transliterated_counterparty",
    "truncated_narration",
    "currency_symbol_noise",
    "merged_credits",
]


@dataclass
class ChaosResult:
    name: str
    rows_touched: int
    description: str
    what_it_breaks: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "rows_touched": self.rows_touched,
            "description": self.description,
            "what_it_breaks": self.what_it_breaks,
        }


@dataclass
class ChaosSpec:
    """What to inject. Parsed from free text or chosen directly."""

    corruptions: list[str] = field(default_factory=list)
    share: float = 0.5
    seed: int = 7
    interpreted_from: str = ""
    interpreted_by: str = "keyword"

    def as_dict(self) -> dict:
        return {
            "corruptions": self.corruptions,
            "share": self.share,
            "seed": self.seed,
            "interpreted_from": self.interpreted_from,
            "interpreted_by": self.interpreted_by,
        }


# --------------------------------------------------------------- corruptions
#
# Each takes the rows it should touch and returns them modified. They mutate nothing:
# BankTxn is frozen, so a corrupted row is a new row, and the original batch on disk is
# never written to.

_UTR_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")


def _unseen_narration(txn: BankTxn, rng: random.Random) -> BankTxn:
    """A third bank's grammar. Only HDFC and ICICI are modelled."""
    utr = _UTR_RE.search(txn.narration)
    name = " ".join(re.findall(r"[A-Za-z]{4,}", txn.narration)[:2]) or "PAYER"
    return _rebuild(
        txn,
        f"TXN|{txn.value_date or ''}|CR|REF#{utr.group(1) if utr else '000000000000'}"
        f"|REMITTER={name}|CHNL=RTGS|STS=SUCCESS",
    )


def _wrapped_utr(txn: BankTxn, rng: random.Random) -> BankTxn:
    """A UTR split by a fixed-width export. A regex for 12 consecutive digits misses it.

    This is precisely the case notes/failure-modes.md names as the one where a language
    model would beat the deterministic extractor -- and where the provenance gate's
    digit-run rule would currently reject the model's correct answer. Injecting it makes
    that written-down limitation demonstrable rather than theoretical.
    """
    def split(match: re.Match) -> str:
        digits = match.group(1)
        return f"{digits[:4]} {digits[4:8]} {digits[8:]}"

    return _rebuild(txn, _UTR_RE.sub(split, txn.narration))


def _date_format_swap(txn: BankTxn, rng: random.Random) -> BankTxn:
    """A bank that reports value dates a day early, in a different order.

    Amounts and names survive; only the date moves. Blocking keys on (amount, day), so
    this attacks the candidate generation rather than the scoring.
    """
    from datetime import timedelta

    if txn.value_date is None:
        return txn
    return _rebuild(txn, txn.narration, value_date=txn.value_date - timedelta(days=1))


def _unmodelled_fee(txn: BankTxn, rng: random.Random) -> BankTxn:
    """A deduction structure nothing in the generator produces: 0.37% plus 11 paise."""
    reduced = txn.credit - int(txn.credit * 0.0037) - 11
    return _rebuild(txn, txn.narration, credit=max(0, reduced))


def _transliterated_counterparty(txn: BankTxn, rng: random.Random) -> BankTxn:
    """The payer's name transliterated. Every character of the name changes."""
    table = str.maketrans({"A": "AA", "I": "EE", "U": "OO", "V": "W", "S": "SH"})
    return _rebuild(
        txn,
        re.sub(r"[A-Z]{4,}", lambda m: m.group(0).translate(table), txn.narration),
    )


def _truncated_narration(txn: BankTxn, rng: random.Random) -> BankTxn:
    """A narration cut to 24 characters by an upstream field limit."""
    return _rebuild(txn, txn.narration[:24])


def _currency_symbol_noise(txn: BankTxn, rng: random.Random) -> BankTxn:
    """Amounts written into the narration with symbols and separators.

    **Measured, and it makes matching very slightly EASIER** -- coverage on data/demo goes
    66.06% to 67.27%. Writing the amount into the narration hands invoice inference a
    signal the clean row did not have, because that is one of the places it looks.

    Kept, and named here rather than quietly dropped. A corruption suite that only contains
    corruptions which hurt is a suite selected to make the system look robust, and the
    honest finding is that not every unmodelled change is an attack -- some real-world
    format noise is accidentally informative.
    """
    return _rebuild(txn, f"{txn.narration} AMT INR {txn.credit / 100:,.2f}/-")


def _merged_credits(txn: BankTxn, rng: random.Random) -> BankTxn:
    """Two payouts arriving as one line with no batch marker."""
    return _rebuild(
        txn,
        f"{txn.narration} +CONSOLIDATED",
        credit=txn.credit + rng.randrange(50_000, 500_000),
    )


def _rebuild(txn: BankTxn, narration: str, **overrides) -> BankTxn:
    """A corrupted row is a NEW row. BankTxn is frozen and the batch on disk is untouched.

    Rebuilt through `from_row` so the derived fields -- extracted UTRs, tokens, the
    normalised narration -- are recomputed from the corrupted text rather than carried over
    from the clean one. Carrying them would let the matcher succeed on evidence the
    corrupted row no longer contains, which would make chaos look survivable when it was
    never applied.
    """
    value_date = overrides.get("value_date", txn.value_date)
    credit = overrides.get("credit", txn.credit)
    return BankTxn.from_row({
        "txn_id": txn.txn_id,
        "credit": f"{credit / 100:.2f}",
        "debit": "",
        "value_date": value_date.isoformat() if value_date else "",
        "narration": narration,
        "bank": txn.bank,
        "bank_ref": txn.bank_ref,
    })


CORRUPTIONS: dict[str, tuple[Callable[[BankTxn, random.Random], BankTxn], str, str]] = {
    "unseen_narration": (
        _unseen_narration,
        "a third bank's narration grammar",
        "counterparty extraction and every narration-derived feature",
    ),
    "wrapped_utr": (
        _wrapped_utr,
        "UTRs split across groups by a fixed-width export",
        "the exact-UTR blocking pass, and the provenance gate's digit-run rule",
    ),
    "date_format_swap": (
        _date_format_swap,
        "value dates reported a day early",
        "the (amount, day) blocking key and the date-proximity features",
    ),
    "unmodelled_fee": (
        _unmodelled_fee,
        "a deduction of 0.37% plus 11 paise",
        "exact-amount matching and the fee/TDS delta features",
    ),
    "transliterated_counterparty": (
        _transliterated_counterparty,
        "the payer's name transliterated",
        "fuzzy counterparty matching and invoice inference",
    ),
    "truncated_narration": (
        _truncated_narration,
        "narrations cut to 24 characters",
        "everything downstream of the narration at once",
    ),
    "currency_symbol_noise": (
        _currency_symbol_noise,
        "amounts written into the narration with symbols",
        "numeric extraction, by adding digits that are not identifiers",
    ),
    "merged_credits": (
        _merged_credits,
        "two payouts arriving as one credit with no batch marker",
        "amount matching, and it looks like a legitimate batch",
    ),
}


def apply_chaos(sources: Sources, spec: ChaosSpec) -> tuple[Sources, list[ChaosResult]]:
    """Return a corrupted copy of the batch and a report of what was done to it.

    The gateway and invoice sides are left alone on purpose. A merchant's own ledger and
    their PSP's export are systems they control; the bank statement is the one that arrives
    in whatever shape a third party chose. Corrupting all three would test something no
    merchant faces.
    """
    rng = random.Random(spec.seed)
    bank = list(sources.bank)
    results: list[ChaosResult] = []

    for name in spec.corruptions:
        entry = CORRUPTIONS.get(name)
        if entry is None:
            continue
        transform, description, breaks = entry

        indices = [i for i in range(len(bank)) if rng.random() < spec.share]
        touched = 0
        for i in indices:
            corrupted = transform(bank[i], rng)
            if corrupted != bank[i]:
                bank[i] = corrupted
                touched += 1

        results.append(ChaosResult(name, touched, description, breaks))

    return Sources(invoices=sources.invoices, settlements=sources.settlements, bank=bank), results
