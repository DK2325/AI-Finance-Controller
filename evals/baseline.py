"""A deliberately weak exact-UTR-only matcher, as the regression floor.

Any real matcher that fails to beat this is broken, and if the scorer ever reports
near-perfect results here the scorer is broken -- BUILD.md makes investigating that a
Phase 2 exit criterion.

**This module never sees the answer key.** `run_baseline` takes the three input files and
has no `truth` parameter; `tests/test_baseline.py` asserts both that the signature cannot
accept one and that this module never names truth.csv. A baseline living inside evals/ --
the one package allowed to read truth -- is exactly where that boundary could rot, so it
is enforced structurally rather than by convention.

What it does: pull the UTR out of the bank narration by exact substring, link every
settlement sharing that UTR to that transaction. Nothing else. It cannot help where the
narration omits the UTR, and it cannot tell two settlements apart when a UTR is reused.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from evals.models import Prediction, Triple

LAYER = "baseline_exact_utr"

# Razorpay UTRs in this data are 12-digit numerics. Bounded by non-digits so a longer
# reference number cannot masquerade as one.
_UTR_PATTERN = re.compile(r"(?<!\d)(\d{12})(?!\d)")

# Confidence is crude on purpose: this layer has no calibrated notion of uncertainty.
CONFIDENCE_AMOUNT_AGREES = 1.0
CONFIDENCE_AMOUNT_DIFFERS = 0.7


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _to_paise(text: str) -> int:
    if not text:
        return 0
    whole, _, frac = text.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def run_baseline(batch_dir: Path | str) -> list[Prediction]:
    """Predict links using only exact UTR presence in the bank narration.

    Takes a batch directory and reads three files from it: gateway_settlements.csv,
    bank_statement.csv, invoice_ledger.csv. It does not read, and cannot be given,
    truth.csv.
    """
    batch_dir = Path(batch_dir)
    gateway = _read(batch_dir / "gateway_settlements.csv")
    bank = _read(batch_dir / "bank_statement.csv")

    # Settlements grouped by UTR. A reused UTR maps to several settlements, and this
    # baseline has no way to choose between them -- which is where it loses precision.
    by_utr: dict[str, list[dict]] = {}
    for row in gateway:
        if row["type"] != "payment":
            continue
        by_utr.setdefault(row["settlement_utr"], []).append(row)

    predictions: list[Prediction] = []

    for txn in bank:
        candidates = _UTR_PATTERN.findall(txn["narration"] or "")
        if not candidates:
            continue

        credited = _to_paise(txn["credit"])

        for utr in dict.fromkeys(candidates):
            settlements = by_utr.get(utr)
            if not settlements:
                continue

            expected = sum(int(r["net_amount"]) for r in settlements)
            confidence = (
                CONFIDENCE_AMOUNT_AGREES
                if expected == credited
                else CONFIDENCE_AMOUNT_DIFFERS
            )

            for row in settlements:
                predictions.append(
                    Prediction(
                        triple=Triple(
                            invoice_id=row["order_receipt"],
                            settlement_id=row["settlement_id"],
                            txn_id=txn["txn_id"],
                        ),
                        confidence=confidence,
                        layer=LAYER,
                    )
                )

    return predictions
