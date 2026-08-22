"""Candidate generation.

Comparing every settlement against every bank transaction is O(n*m): at 25,000 rows that
is hundreds of millions of pairs and the run never finishes. Blocking indexes both sides
by keys that a *true* pair is likely to share, then compares only within a bucket.

A single key cannot work here. An exact-amount key misses fee_deducted, because the
gateway fee plus GST shifts the credit by about 2.4%, and misses tds_deducted by up to
10%. A counterparty key alone misses nothing but buckets far too coarsely. So four
independent passes run and their candidate sets are unioned:

    A  utr            UTR printed in the narration matches the settlement's UTR
    B  exact_amount   credit equals net_amount exactly            -> clean, date_skew
    C  rate_amount    credit equals net_amount after a KNOWN       -> fee_deducted,
                      deduction structure, within a few paise         tds_deducted,
                                                                      rounding_drift
    D  counterparty   normalised counterparty prefix plus a        -> partial_payment,
                      +/-3 day window                                 batched_settlement

Pass C encodes domain knowledge directly: rather than widening a band and hoping, it asks
whether the difference is explainable by a fee, GST on that fee, or a TDS section. That
makes the pass both tighter and more interpretable than a tolerance band, and it produces
a feature Phase 4 can use.

Every function here is pure.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from core.normalize import counterparty_key, day_bucket
from core.records import BankTxn, Settlement, Sources

# Fee and GST as charged by the gateway, and the TDS sections a B2B receipt may carry.
FEE_RATE = 0.02
GST_ON_FEE_RATE = 0.18
TDS_RATES = (0.001, 0.02, 0.05, 0.10)

# rounding_drift injects 1-5 paise, so exact keys are widened by exactly that much --
# enough to catch a deliberate drift, too tight to collide with anything else.
PAISE_TOLERANCE = 5

DATE_WINDOW_DAYS = 3
COUNTERPARTY_PREFIX = 10

PASS_UTR = "utr"
PASS_EXACT_AMOUNT = "exact_amount"
PASS_RATE_AMOUNT = "rate_amount"
PASS_COUNTERPARTY = "counterparty"
PASSES = (PASS_UTR, PASS_EXACT_AMOUNT, PASS_RATE_AMOUNT, PASS_COUNTERPARTY)


@dataclass(frozen=True)
class Candidate:
    settlement: Settlement
    txn: BankTxn
    passes: frozenset[str]


@dataclass
class BlockingStats:
    """Per-pass candidate counts, so a pass that explodes is visible rather than hidden.

    Reported in the run meta alongside timings: an aggregate hides the case where one
    pass produces most of the work.
    """

    per_pass: dict[str, int]
    unique_candidates: int
    n_settlements: int
    n_bank: int

    @property
    def pairs_per_settlement(self) -> float:
        return self.unique_candidates / self.n_settlements if self.n_settlements else 0.0

    def as_dict(self) -> dict:
        return {
            "per_pass": dict(sorted(self.per_pass.items())),
            "unique_candidates": self.unique_candidates,
            "n_settlements": self.n_settlements,
            "n_bank": self.n_bank,
            "pairs_per_settlement": round(self.pairs_per_settlement, 3),
        }


def net_after_fee(amount: int) -> int:
    """Gateway fee plus GST on that fee, half-up, matching how processors round."""
    fee = _round_half_up(amount * FEE_RATE)
    tax = _round_half_up(fee * GST_ON_FEE_RATE)
    return amount - fee - tax


def _round_half_up(value: float) -> int:
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def plausible_credits(settlement: Settlement) -> set[int]:
    """Every credit this settlement could plausibly produce under a known structure.

    Deliberately small and explicit. Widening this set trades precision for recall, and
    the entries are all defensible: the payout as reported, the payout after gateway fee
    and GST, and the payout after each TDS section.
    """
    out = {settlement.net_amount, settlement.amount}
    out.add(net_after_fee(settlement.amount))
    for rate in TDS_RATES:
        deducted = settlement.amount - _round_half_up(settlement.amount * rate)
        out.add(deducted)
        out.add(net_after_fee(deducted))
    return {value for value in out if value > 0}


NAME_PREFIX_LEN = 6

# Pass D keys on (counterparty, day, amount band), not (counterparty, day).
#
# Once counterparty selection became realistically skewed -- a merchant bills a few
# customers constantly -- a counterparty+day bucket held dozens of rows and candidate
# growth went straight back to quadratic (exponent 1.97 measured at 5k vs 25k).
#
# Bands are multiplicative because the differences that matter here are proportional: a
# gateway fee shifts a payout ~2.4% and TDS up to 10%. A settlement is indexed into its
# own band and the two below, which covers any known deduction while still splitting a
# dense bucket by order of magnitude.
AMOUNT_BAND_RATIO = 1.15
AMOUNT_BAND_SPREAD = 2


def amount_band(paise: int) -> int:
    """Multiplicative band index. Monotone, so band(x) <= band(y) whenever x <= y."""
    if paise <= 0:
        return 0
    return int(math.log(paise) / math.log(AMOUNT_BAND_RATIO))


def _narration_keys(txn: BankTxn) -> set[str]:
    """Counterparty keys a narration could be carrying.

    Every adjacent pair of alphabetic tokens is keyed the same way a ledger name is, so
    "NEFT HDFC0000123 AMRAVATI AGRO EXPORTS 3000..." yields AMRAAGR among its keys and
    meets the settlement in a bucket of the right size.
    """
    tokens = [t for t in txn.normalized_narration.split() if t.isalpha() and len(t) >= 3]
    out = set()
    for first, second in zip(tokens, tokens[1:], strict=False):
        out.add(first[:4] + second[:3])
    for token in tokens:
        if len(token) >= 7:
            out.add(token[:7])
    return out


def _index_bank_by_amount(bank: list[BankTxn]) -> dict[tuple[int, int], list[BankTxn]]:
    """Bank rows keyed by (credit, day) -- amount ALONE is not a blocking key.

    Once invoice values cluster on round numbers, an exact-amount key matches every
    transaction of that value anywhere in the batch. The bucket therefore grows linearly
    with the batch and the pass becomes quadratic: measured at 4.88 candidates per
    settlement over 5,000 rows and 19.93 over 25,000, purely from the batch being longer.

    Pairing the amount with the day bounds the bucket to what a real pair could occupy,
    since a settlement and its credit are at most three days apart.
    """
    index: dict[tuple[int, int], list[BankTxn]] = defaultdict(list)
    for txn in bank:
        if txn.credit > 0 and txn.value_date is not None:
            index[(txn.credit, txn.value_date.toordinal())].append(txn)
    return index


def _lookup_amount(
    index: dict[tuple[int, int], list[BankTxn]], amount: int, days: list[int]
) -> list[BankTxn]:
    out: list[BankTxn] = []
    for ordinal in days:
        out.extend(index.get((amount, ordinal), ()))
    return out


def generate_candidates(sources: Sources) -> tuple[list[Candidate], BlockingStats]:
    """Union of the four passes, deduplicated, with per-pass counts retained."""
    payments = sources.payments
    bank = sources.bank

    by_amount = _index_bank_by_amount(bank)

    by_utr: dict[str, list[BankTxn]] = defaultdict(list)
    for txn in bank:
        for utr in txn.utrs:
            by_utr[utr].append(txn)

    # Pass D is indexed by (counterparty prefix, day), NOT by day alone.
    #
    # Indexing by day only and then scanning that day's transactions is quadratic: the
    # number of transactions per day grows with the batch, so doubling the rows doubles
    # both the settlements and the scan length. Measured at 5,000 vs 25,000 rows that was
    # a growth exponent of 1.93 -- effectively O(n^2), which BUILD.md forbids outright.
    #
    # Keying on a six-character counterparty prefix taken from the narration's own tokens
    # makes the lookup O(1). Six characters survives the truncation, case loss and
    # separator loss that bank statements inflict, because all of those keep the start of
    # the name intact.
    by_name_day: dict[tuple[str, int, int], list[BankTxn]] = defaultdict(list)
    for txn in bank:
        if txn.value_date is None or txn.credit <= 0:
            continue
        ordinal = txn.value_date.toordinal()
        band = amount_band(txn.credit)
        for key in _narration_keys(txn):
            by_name_day[(key, ordinal, band)].append(txn)

    invoice_by_id = sources.invoice_by_id
    hits: dict[tuple[str, str], set[str]] = defaultdict(set)
    per_pass: dict[str, int] = dict.fromkeys(PASSES, 0)
    pair_objects: dict[tuple[str, str], tuple[Settlement, BankTxn]] = {}

    def record(settlement: Settlement, txn: BankTxn, pass_name: str) -> None:
        key = (settlement.settlement_id + "|" + settlement.entity_id, txn.txn_id)
        hits[key].add(pass_name)
        pair_objects[key] = (settlement, txn)
        per_pass[pass_name] += 1

    for settlement in payments:
        # Pass A -- the UTR is printed in the narration. Unique enough to need no date.
        for txn in by_utr.get(settlement.utr, ()):
            record(settlement, txn, PASS_UTR)

        days = day_bucket(settlement.settled_date, DATE_WINDOW_DAYS)

        # Pass B -- the credit is exactly the reported payout, within the date window.
        for txn in _lookup_amount(by_amount, settlement.net_amount, days):
            record(settlement, txn, PASS_EXACT_AMOUNT)

        # Pass C -- the difference is explainable by a known deduction structure.
        for target in plausible_credits(settlement):
            for offset in range(-PAISE_TOLERANCE, PAISE_TOLERANCE + 1):
                for txn in _lookup_amount(by_amount, target + offset, days):
                    record(settlement, txn, PASS_RATE_AMOUNT)

        # Pass D -- same counterparty, near enough in time. The only pass that can reach
        # a batched settlement, where no single amount matches anything.
        invoice = invoice_by_id.get(settlement.invoice_id)
        if invoice is None or not invoice.counterparty:
            continue
        key = counterparty_key(invoice.customer_name)
        if not key:
            continue
        base_band = amount_band(settlement.net_amount)
        bands = range(base_band - AMOUNT_BAND_SPREAD, base_band + 1)
        for ordinal in day_bucket(settlement.settled_date, DATE_WINDOW_DAYS):
            for band in bands:
                for txn in by_name_day.get((key, ordinal, band), ()):
                    record(settlement, txn, PASS_COUNTERPARTY)

    candidates = [
        Candidate(
            settlement=pair_objects[key][0],
            txn=pair_objects[key][1],
            passes=frozenset(names),
        )
        for key, names in hits.items()
    ]

    stats = BlockingStats(
        per_pass=per_pass,
        unique_candidates=len(candidates),
        n_settlements=len(payments),
        n_bank=len(bank),
    )
    return candidates, stats
