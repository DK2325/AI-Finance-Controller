"""Exporting every candidate with its features, for training and for scoring.

Phase 3's pipeline emits only *resolved* matches. A classifier needs the whole candidate
distribution it will face at inference, which includes:

*   candidates a rule accepted,
*   candidates a rule scored but resolution rejected,
*   and candidates **no rule scored at all** -- blocking produced them and every tier
    declined. Those near-misses are exactly where the residual lives, and a model trained
    without them would never have seen the population it has to be careful about.

Truth-blind by construction. This module produces features and identifiers; labelling
happens in evals/training.py, which is the only package allowed to hold both sides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from core.blocking import generate_candidates
from core.features import counterparty_frequencies as _frequencies
from core.features import extract
from core.invoices import (
    RULE_GIVEN,
    SCORE_GIVEN,
    candidates_for,
    index_invoices,
    score_invoice,
)
from core.records import Sources
from core.rules import apply_rules


@dataclass
class CandidateRow:
    """One (settlement, transaction, inferred invoice) triple, with evidence."""

    entity_id: str
    settlement_id: str
    txn_id: str
    invoice_id: str

    rule: str
    rule_score: float
    invoice_rule: str
    invoice_score: float
    blocking_passes: str
    # Not a feature. Carried so the model can take a time-based validation split, which
    # is what production actually faces: train on the past, predict the future.
    settled_date: str

    features: dict[str, float] = field(default_factory=dict)

    @property
    def triple(self) -> tuple[str, str, str]:
        return (self.invoice_id, self.settlement_id, self.txn_id)


def export_candidates(sources: Sources | Path | str) -> list[CandidateRow]:
    """Every blocking candidate, scored and featurised, with no labels anywhere.

    The invoice link is chosen per candidate rather than globally resolved: resolution is
    a decision, and at training time the model needs to see the evidence *before* any
    decision was taken.
    """
    if not isinstance(sources, Sources):
        from core.pipeline import load_sources

        sources = load_sources(sources)

    candidates, _stats = generate_candidates(sources)
    frequencies = _frequencies(sources)
    invoice_index = index_invoices(sources)
    invoice_by_id = sources.invoice_by_id

    rows: list[CandidateRow] = []

    for candidate in candidates:
        settlement, txn = candidate.settlement, candidate.txn

        # The invoice, either asserted by the merchant or inferred through the narration.
        if settlement.invoice_id:
            invoice = invoice_by_id.get(settlement.invoice_id)
            invoice_id = settlement.invoice_id
            invoice_rule, invoice_score = RULE_GIVEN, SCORE_GIVEN
        else:
            best = None
            for option in candidates_for(settlement, txn, invoice_index):
                link = score_invoice(settlement, option)
                if link is not None and (best is None or link.score > best[0].score):
                    best = (link, option)
            if best is None:
                invoice, invoice_id = None, ""
                invoice_rule, invoice_score = "none", 0.0
            else:
                link, invoice = best
                invoice_id = link.invoice_id
                invoice_rule, invoice_score = link.rule, link.score

        counterparty = invoice.counterparty if invoice else ""
        similarity = (
            fuzz.token_set_ratio(counterparty, txn.normalized_narration) / 100.0
            if counterparty
            else 0.0
        )

        # A rule hit is evidence, not a gate. Candidates no rule scored are exported too.
        hit = apply_rules(
            settlement, txn, counterparty=counterparty, narration_similarity=similarity
        )

        invoice_date_delta = None
        if invoice is not None and invoice.invoice_date and settlement.settled_date:
            invoice_date_delta = (settlement.settled_date - invoice.invoice_date).days

        rows.append(
            CandidateRow(
                entity_id=settlement.entity_id,
                settlement_id=settlement.settlement_id,
                txn_id=txn.txn_id,
                invoice_id=invoice_id,
                rule=hit.rule if hit else "none",
                rule_score=hit.score if hit else 0.0,
                invoice_rule=invoice_rule,
                invoice_score=invoice_score,
                blocking_passes="|".join(sorted(candidate.passes)),
                settled_date=(
                    settlement.settled_date.isoformat() if settlement.settled_date else ""
                ),
                features=extract(
                    settlement,
                    txn,
                    counterparty=counterparty,
                    passes=candidate.passes,
                    frequencies=frequencies,
                    invoice_link_score=invoice_score,
                    invoice_receipt_given=bool(settlement.invoice_id),
                    invoice_amount=invoice.amount if invoice else 0,
                    invoice_date_delta=invoice_date_delta,
                ),
            )
        )

    return rows
