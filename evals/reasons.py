"""Reason-code accuracy, scored against truth rather than against ourselves.

WHY NOT AGREEMENT

The first attempt at this measured whether the LLM's reason-code tag matched the
pipeline's. It came back 100 out of 100, and it meant nothing: the prompt hands the model
`system_reason_code` and tells it agreeing is normal, so the metric scored transcription.
A metric comparing two things where one was told the other's answer is not measuring
agreement.

WHAT IS ACTUALLY MEASURED: ACTIONABILITY, NOT CHOICE

The question is not "did the system pick the right code given what it knew" -- by that
standard every code is correct, because each is a true statement about our own computation.
`BELOW_THRESHOLD` says the best candidate scored below the threshold, and it always did.

The question is **does this code send the operator to the right place?** An exception queue
is read by someone with limited time, and each code implies a next action:

    NO_CANDIDATE            look upstream; the money may not have arrived
    NO_INVOICE_LINK         find the invoice reference
    INVOICE_ALREADY_CLAIMED check the settlement that took it
    BELOW_THRESHOLD         review the candidate we found
    AMBIGUOUS_CANDIDATES    review the candidates and pick
    LOW_CONFIDENCE          review the candidate we found

A code is scored **unjustified** when following it wastes that time -- when the operator
would review candidates that cannot contain the answer, or chase a duplicate that is not
one. Truth can settle that, and nothing else can.

Two consequences worth stating plainly:

*   **An unjustified code is not always a labelling bug.** Where the true credit was never
    among the candidates, no code the system could have chosen would have been actionable,
    because the system cannot know what it did not retrieve. Those rows measure **blocking
    recall**, and the remedy is better candidate generation rather than a different label.
*   **Some of them are real defects.** `INVOICE_ALREADY_CLAIMED` on a settlement that truth
    says owns the invoice is the matcher having given it away wrongly, and that is a bug in
    resolution with money attached.

Keeping both in one number would hide the second behind the first, so the breakdown is
per code and the examples are carried with it.

It also catches the specific failure that shipped once already: a `NO_CANDIDATE` written
for a settlement blocking had found five candidates for, whose text would send an
investigator to the payment gateway instead of to the matcher.

This is the only module allowed to hold both sides.

ORPHANS ARE SCORED SEPARATELY

A settlement truth says has no real link cannot have its exception be *wrong* in outcome:
declining was correct. Folding those into an accuracy figure would inflate it with rows
that were never at risk, exactly as counting orphans in precision would. They are reported
as their own number.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import ExceptionRecord
from evals.metrics import Batch
from llm.codes import ReasonCode

# Codes whose claim is "we had the evidence and declined it on confidence".
_HAD_THE_EVIDENCE = frozenset(
    {
        ReasonCode.BELOW_THRESHOLD,
        ReasonCode.LOW_CONFIDENCE,
        ReasonCode.AMBIGUOUS_CANDIDATES,
    }
)


@dataclass
class CodeScore:
    total: int = 0
    justified: int = 0
    unjustified: int = 0
    orphan: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float | None:
        decidable = self.justified + self.unjustified
        return self.justified / decidable if decidable else None

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "justified": self.justified,
            "unjustified": self.unjustified,
            "orphan": self.orphan,
            "accuracy": round(self.rate, 4) if self.rate is not None else None,
            "examples": self.examples[:3],
        }


@dataclass
class ReasonScore:
    by_code: dict[str, CodeScore] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(c.total for c in self.by_code.values())

    @property
    def justified(self) -> int:
        return sum(c.justified for c in self.by_code.values())

    @property
    def unjustified(self) -> int:
        return sum(c.unjustified for c in self.by_code.values())

    @property
    def orphan(self) -> int:
        return sum(c.orphan for c in self.by_code.values())

    @property
    def accuracy(self) -> float | None:
        decidable = self.justified + self.unjustified
        return self.justified / decidable if decidable else None

    def as_dict(self) -> dict:
        return {
            "exceptions_scored": self.total,
            "justified": self.justified,
            "unjustified": self.unjustified,
            # Truth says there was nothing to find, so declining was right whatever the
            # code. Excluded from the accuracy denominator for the same reason orphans are
            # excluded from precision's.
            "correctly_refused_orphans": self.orphan,
            "accuracy": round(self.accuracy, 4) if self.accuracy is not None else None,
            "by_code": {k: v.as_dict() for k, v in sorted(self.by_code.items())},
        }


def settlement_index(batch_dir: Path | str) -> dict[str, str]:
    """entity_id -> settlement_id. Truth keys on the payout batch, exceptions on the row."""
    path = Path(batch_dir) / "gateway_settlements.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["entity_id"]: r["settlement_id"] for r in csv.DictReader(handle)}


def score_reasons(
    exceptions: list[ExceptionRecord],
    candidates_by_entity: dict[str, set[tuple[str, str]]],
    batch: Batch,
    entity_to_settlement: dict[str, str],
) -> ReasonScore:
    """Score every exception's reason code against what truth says was available.

    `candidates_by_entity` maps an entity to the (txn_id, invoice_id) pairs blocking
    produced for it -- what we actually had to work with at the moment the code was chosen.
    """
    true_txns: dict[str, set[str]] = defaultdict(set)
    true_invoices: dict[str, set[str]] = defaultdict(set)
    orphan_settlements: set[str] = set()
    real_settlements: set[str] = set()

    for row in batch.truth:
        if row.is_orphan:
            if row.settlement_id:
                orphan_settlements.add(row.settlement_id)
            continue
        real_settlements.add(row.settlement_id)
        true_txns[row.settlement_id].add(row.txn_id)
        true_invoices[row.settlement_id].add(row.invoice_id)

    score = ReasonScore()

    for record in exceptions:
        code = str(record.reason_code)
        bucket = score.by_code.setdefault(code, CodeScore())
        bucket.total += 1

        settlement_id = entity_to_settlement.get(record.entity_id, "")

        # Nothing to find. Declining was correct whatever the code says.
        if settlement_id not in real_settlements:
            bucket.orphan += 1
            continue

        pairs = candidates_by_entity.get(record.entity_id, set())
        had_true_txn = any(txn in true_txns[settlement_id] for txn, _ in pairs)
        had_true_invoice = any(inv in true_invoices[settlement_id] for _, inv in pairs)

        justified, why = _justify(
            record.reason_code, had_true_txn, had_true_invoice, len(pairs)
        )
        if justified:
            bucket.justified += 1
        else:
            bucket.unjustified += 1
            if len(bucket.examples) < 3:
                bucket.examples.append(f"{record.entity_id}: {why}")

    return score


def _justify(
    code: ReasonCode,
    had_true_txn: bool,
    had_true_invoice: bool,
    n_candidates: int,
) -> tuple[bool, str]:
    """Whether this code's claim about availability holds, and why not if it does not."""
    if code is ReasonCode.NO_CANDIDATE:
        if n_candidates:
            return False, (
                f"claims no credit resembled this payout, but blocking produced "
                f"{n_candidates} candidates"
            )
        return True, ""

    if code is ReasonCode.NO_INVOICE_LINK:
        if had_true_invoice:
            return False, "claims no invoice could be identified, but a candidate carried it"
        return True, ""

    if code is ReasonCode.INVOICE_ALREADY_CLAIMED:
        # The claim is that the invoice belongs elsewhere. If a candidate for this
        # settlement carried the true invoice, it belonged here.
        if had_true_invoice:
            return False, "claims the invoice belongs to another settlement; truth says this one"
        return True, ""

    if code in _HAD_THE_EVIDENCE:
        if not had_true_txn:
            return False, (
                "sends the operator to review candidates, none of which is the true "
                "credit -- blocking never retrieved it, so no label could have been "
                "actionable here"
            )
        return True, ""

    # SUBSET_SEARCH_CAPPED and the FAILURE family make claims about the machinery rather
    # than about availability, and truth has nothing to say about them.
    return True, ""
