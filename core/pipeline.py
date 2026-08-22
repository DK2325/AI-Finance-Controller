"""Orchestration: the only module in core/ that touches I/O.

A plain state machine, readable top to bottom, per architecture rule 7. No agent
framework, no dynamic dispatch, nothing a reviewer has to trace through indirection.

    load -> block -> score rules -> subset-sum for the residue -> resolve -> features

Timing and candidate counts are collected as it runs rather than measured afterwards, so
the throughput number in the report is the run that produced the metrics, not a separate
benchmark.
"""

from __future__ import annotations

import csv
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from core.blocking import Candidate, generate_candidates
from core.features import counterparty_frequencies, extract
from core.invoices import InvoiceLink, resolve_invoices
from core.records import BankTxn, Invoice, Settlement, Sources
from core.rules import RuleHit, apply_rules, date_penalty, subset_sum_hit
from core.subsetsum import REASON_CAPPED, SubsetSumStats, search_bucket

GATEWAY_FILE = "gateway_settlements.csv"
BANK_FILE = "bank_statement.csv"
INVOICE_FILE = "invoice_ledger.csv"


@dataclass
class Match:
    invoice_id: str
    settlement_id: str
    txn_id: str
    score: float
    rule: str
    layer: str
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class Exception_:
    settlement_id: str
    invoice_id: str
    reason_code: str
    detail: str = ""


@dataclass
class Timing:
    """Wall time per stage. Reported per layer, never as a single aggregate."""

    stages: dict[str, float] = field(default_factory=dict)

    def record(self, stage: str, seconds: float) -> None:
        self.stages[stage] = self.stages.get(stage, 0.0) + seconds

    @property
    def total(self) -> float:
        return sum(self.stages.values())

    def as_dict(self, rows: int) -> dict:
        total = self.total
        return {
            "rows": rows,
            "seconds_total": round(total, 4),
            "rows_per_second": round(rows / total, 1) if total else 0.0,
            "seconds_by_stage": {k: round(v, 4) for k, v in sorted(self.stages.items())},
        }


@dataclass
class ReconResult:
    matches: list[Match]
    exceptions: list[Exception_]
    timing: Timing
    blocking: dict
    subset_sum: dict
    n_rows: int

    def meta(self) -> dict:
        return {
            # Architecture rule 3: scores here are rule tiers, not probabilities. Phase 6
            # must refuse to render an uncalibrated run as if it were the real curve.
            "calibrated": False,
            "calibration_note": (
                "Phase 3 rule scores are ranked tiers, not calibrated probabilities. "
                "The risk-coverage curve from this run is not the thesis curve."
            ),
            "timing": self.timing.as_dict(self.n_rows),
            "blocking": self.blocking,
            "subset_sum": self.subset_sum,
            "n_matches": len(self.matches),
            "n_exceptions": len(self.exceptions),
        }


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_sources(batch_dir: Path | str) -> Sources:
    batch_dir = Path(batch_dir)
    return Sources(
        invoices=[Invoice.from_row(r) for r in _read(batch_dir / INVOICE_FILE)],
        settlements=[Settlement.from_row(r) for r in _read(batch_dir / GATEWAY_FILE)],
        bank=[BankTxn.from_row(r) for r in _read(batch_dir / BANK_FILE)],
    )


def _score_candidates(
    candidates: list[Candidate], sources: Sources
) -> list[tuple[Candidate, RuleHit, str]]:
    """Apply the rule tiers to every candidate. Pure scoring, no assignment yet."""
    invoice_by_id = sources.invoice_by_id
    scored = []
    for candidate in candidates:
        invoice = invoice_by_id.get(candidate.settlement.invoice_id)
        counterparty = invoice.counterparty if invoice else ""
        similarity = (
            fuzz.token_set_ratio(counterparty, candidate.txn.normalized_narration) / 100.0
            if counterparty
            else 0.0
        )
        hit = apply_rules(
            candidate.settlement,
            candidate.txn,
            counterparty=counterparty,
            narration_similarity=similarity,
        )
        if hit is not None:
            scored.append((candidate, hit, counterparty))
    return scored


def _resolve(
    scored: list[tuple[Candidate, RuleHit, str]],
) -> tuple[list[tuple[Candidate, RuleHit, str]], set[str]]:
    """Greedy assignment, best score first.

    A settlement is paid out once, so it may be claimed by at most one transaction.
    A transaction may cover several settlements -- that is what a batch is -- so
    transactions are not consumed.

    Ties break on date distance: when two rules score identically, the credit closer in
    time is the better explanation.
    """
    ordered = sorted(
        scored,
        key=lambda item: (-item[1].score, date_penalty(item[0].settlement, item[0].txn)),
    )

    claimed: set[str] = set()
    accepted = []
    for candidate, hit, counterparty in ordered:
        key = candidate.settlement.entity_id
        if key in claimed:
            continue
        claimed.add(key)
        accepted.append((candidate, hit, counterparty))
    return accepted, claimed


def _subset_sum_pass(
    sources: Sources,
    claimed: set[str],
    stats: SubsetSumStats,
) -> list[tuple[Settlement, BankTxn]]:
    """Look for batched settlements among what the rules could not settle.

    Buckets are (settlement_id, day) -- a real batch shares a settlement id, and this is
    where a bounded search is affordable.
    """
    unclaimed = [s for s in sources.payments if s.entity_id not in claimed]
    if not unclaimed:
        return []

    by_settlement: dict[str, list[Settlement]] = defaultdict(list)
    for settlement in unclaimed:
        by_settlement[settlement.settlement_id].append(settlement)

    credits_by_day: dict[int, list[BankTxn]] = defaultdict(list)
    for txn in sources.bank:
        if txn.credit > 0 and txn.value_date is not None:
            credits_by_day[txn.value_date.toordinal()].append(txn)

    found: list[tuple[Settlement, BankTxn]] = []

    for group in by_settlement.values():
        if len(group) < 2:
            continue
        anchor = group[0].settled_date
        if anchor is None:
            continue

        targets: list[BankTxn] = []
        for offset in range(-3, 4):
            targets.extend(credits_by_day.get(anchor.toordinal() + offset, ()))

        amounts = [s.net_amount for s in group]
        ids = [s.entity_id for s in group]

        for txn in targets:
            subset = search_bucket(amounts, txn.credit, settlement_ids=ids, stats=stats)
            if subset is None:
                continue
            for index in subset:
                found.append((group[index], txn))
            break

    return found


def reconcile(batch_dir: Path | str, with_features: bool = True) -> ReconResult:
    """Run the deterministic pipeline over one batch. No model, no LLM."""
    timing = Timing()

    start = time.perf_counter()
    sources = load_sources(batch_dir)
    timing.record("load", time.perf_counter() - start)

    start = time.perf_counter()
    candidates, blocking_stats = generate_candidates(sources)
    timing.record("blocking", time.perf_counter() - start)

    start = time.perf_counter()
    scored = _score_candidates(candidates, sources)
    timing.record("rules", time.perf_counter() - start)

    start = time.perf_counter()
    accepted, claimed = _resolve(scored)
    timing.record("resolve", time.perf_counter() - start)

    start = time.perf_counter()
    subset_stats = SubsetSumStats()
    batched = _subset_sum_pass(sources, claimed, subset_stats)
    timing.record("subset_sum", time.perf_counter() - start)

    # Invoice inference. Only ~38% of gateway rows carry order_receipt, so for the rest
    # the invoice link is reconstructed through the bank narration. A settlement that
    # matched no transaction cannot have its invoice inferred either -- correctly, since
    # there is no evidence left to do it with.
    start = time.perf_counter()
    pairs = [(c.settlement, c.txn, h.score) for c, h, _ in accepted]
    pairs += [(s_, t, 0.8) for s_, t in batched]
    invoice_links = resolve_invoices(pairs, sources)
    timing.record("invoice_link", time.perf_counter() - start)

    frequencies = counterparty_frequencies(sources)
    invoice_by_id = sources.invoice_by_id

    def _linked(settlement) -> InvoiceLink | None:
        return invoice_links.get(settlement.entity_id)

    start = time.perf_counter()
    matches: list[Match] = []

    for candidate, hit, counterparty in accepted:
        features = (
            extract(
                candidate.settlement,
                candidate.txn,
                counterparty=counterparty,
                passes=candidate.passes,
                frequencies=frequencies,
            )
            if with_features
            else {}
        )
        link = _linked(candidate.settlement)
        if link is None:
            # Matched a transaction but no invoice can be identified. That is an
            # exception, not a match -- emitting a triple with an empty invoice would be
            # a false auto-match with money attached.
            continue
        matches.append(
            Match(
                invoice_id=link.invoice_id,
                settlement_id=candidate.settlement.settlement_id,
                txn_id=candidate.txn.txn_id,
                # Both links must be right for the triple to be right, so the two
                # uncertainties compound rather than the stronger one masking the weaker.
                score=round(hit.score * link.score, 4),
                rule=f"{hit.rule}+{link.rule}",
                layer=hit.layer,
                features=features,
            )
        )

    batch_hit = subset_sum_hit()
    for settlement, txn in batched:
        provisional = _linked(settlement)
        invoice = invoice_by_id.get(provisional.invoice_id) if provisional else None
        counterparty = invoice.counterparty if invoice else ""
        features = (
            extract(
                settlement,
                txn,
                counterparty=counterparty,
                passes=frozenset({"subset_sum"}),
                frequencies=frequencies,
                in_subset_sum=True,
            )
            if with_features
            else {}
        )
        link = _linked(settlement)
        if link is None:
            continue
        matches.append(
            Match(
                invoice_id=link.invoice_id,
                settlement_id=settlement.settlement_id,
                txn_id=txn.txn_id,
                score=round(batch_hit.score * link.score, 4),
                rule=f"{batch_hit.rule}+{link.rule}",
                layer=batch_hit.layer,
                features=features,
            )
        )
    timing.record("features", time.perf_counter() - start)

    # A bucket too large to search is an exception with a reason code, never a silent
    # drop. Coverage must not be flattered by work that was quietly skipped.
    exceptions = [
        Exception_(
            settlement_id=entity_id,
            invoice_id="",
            reason_code=REASON_CAPPED,
            detail="subset-sum bucket exceeded the search cap",
        )
        for entity_id in dict.fromkeys(subset_stats.skipped_settlement_ids)
    ]

    return ReconResult(
        matches=matches,
        exceptions=exceptions,
        timing=timing,
        blocking=blocking_stats.as_dict(),
        subset_sum=subset_stats.as_dict(),
        n_rows=len(sources.payments),
    )
