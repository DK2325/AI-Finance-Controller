"""Reason-code actionability on the sealed test set, and the deferred invoice defect.

Two things `notes/failure-modes.md` left open, both answerable from the same deterministic
run that produced `sealed_test.json`:

1.  **Reason-code actionability scored against truth.** failure-modes.md offers two ways to
    replace the agreement metric that turned out to measure transcription, and records that
    neither had been taken. Option 1 -- score against the answer key rather than against
    the model's own tag -- is implemented in `evals/reasons.py` and was reported on
    `data/train`. It had not been run on held-out data.

2.  **The two invoice-inference errors, "left for Phase 7, sized at two rows in 4,945".**
    `INVOICE_ALREADY_CLAIMED` raised against a settlement that truth says owns the invoice
    is not a labelling problem, it is the matcher having given the invoice away. Counting
    them out of sample is what turns a logged defect into a measured one.

**This does not re-score the operating point and cannot.** No threshold, feature or
calibrator is touched; the pipeline is deterministic, and the run is asserted below to
reproduce the reported match count exactly before any new number is computed. A metric
added after the fact is not a second run selected from -- but that claim is only worth
making if the reproduction is checked rather than assumed.

    python notes/measurements/sealed_test_reasons.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.candidates import export_candidates  # noqa: E402
from core.exceptions import ExceptionRecord  # noqa: E402
from evals.metrics import load_batch, score_at, wilson  # noqa: E402
from evals.models import Prediction, Triple  # noqa: E402
from evals.reasons import score_reasons, settlement_index  # noqa: E402
from llm.codes import ReasonCode  # noqa: E402
from model.artifact import Artifact  # noqa: E402
from model.predict import reconcile_batch  # noqa: E402

BATCH = Path("data/test")
MODEL = Path("runs/_models/v1")
THRESHOLD = 0.9564

# What the reported run said. Reproducing these is the precondition for adding anything.
REPORTED = {"matched": 3114, "true_positives": 3111, "false_positives": 3}


def main() -> None:
    artifact = Artifact.load(MODEL)
    outcome = reconcile_batch(BATCH, artifact, threshold=THRESHOLD)
    batch = load_batch(BATCH)

    predictions = [
        Prediction(Triple(*c.triple), c.probability, "model", c.row.entity_id)
        for c in outcome.all_resolved
    ]
    score = score_at(predictions, batch, THRESHOLD)

    reproduced = {
        "matched": len(outcome.matches),
        "true_positives": score.n_true_positives,
        "false_positives": score.n_false_positives,
    }
    assert reproduced == REPORTED, (
        f"the pipeline did not reproduce the reported run: {reproduced} != {REPORTED}. "
        "Nothing further is computed, because a metric added to a run that does not "
        "reproduce is a second run."
    )
    print("determinism check: reproduces the reported run exactly")
    for key, value in sorted(REPORTED.items()):
        print(f"    {key:16} {value:,}")

    # ------------------------------------------------ 1. reason-code actionability

    pairs: dict[str, set] = defaultdict(set)
    for row in export_candidates(BATCH):
        pairs[row.entity_id].add((row.txn_id, row.invoice_id))

    records = [
        ExceptionRecord(
            entity_id=e.entity_id,
            reason_code=e.reason_code,
            detail=e.detail,
            txn_id=e.txn_id,
            invoice_id=e.invoice_id,
        )
        for e in outcome.enumeration.exceptions
    ]
    reasons = score_reasons(records, pairs, batch, settlement_index(BATCH))
    scored = reasons.as_dict()

    print("\nREASON-CODE ACTIONABILITY (scored against truth, out of sample)")
    print(f"  exceptions scored     {scored['exceptions_scored']:,}")
    print(f"  justified             {scored['justified']:,}")
    print(f"  unjustified           {scored['unjustified']:,}")
    print(f"  correctly refused     {scored['correctly_refused_orphans']:,}  (orphans, excluded)")
    acc = scored["accuracy"]
    lo, hi = wilson(reasons.justified, reasons.justified + reasons.unjustified)
    print(f"  accuracy              {acc:.2%}   95% CI [{lo:.2%}, {hi:.2%}]")
    print(f"\n  {'code':26} {'total':>7} {'just':>7} {'unjust':>7} {'orphan':>7} {'accuracy':>9}")
    for code, row in scored["by_code"].items():
        rate = f"{row['accuracy']:.2%}" if row["accuracy"] is not None else "n/a"
        print(f"  {code:26} {row['total']:7,} {row['justified']:7,} "
              f"{row['unjustified']:7,} {row['orphan']:7,} {rate:>9}")

    # -------------------------------------- 2. the deferred invoice-inference defect

    # INVOICE_ALREADY_CLAIMED where truth says this settlement really does own the invoice
    # the exception says belongs to somebody else. The code is a true statement about our
    # own computation and still sends the operator to a duplicate that does not exist.
    owner_of_invoice = {r.invoice_id: r.settlement_id for r in batch.decidable if r.invoice_id}
    entity_to_settlement = settlement_index(BATCH)

    wrongly_given_away = []
    for exception in outcome.enumeration.exceptions:
        if exception.reason_code is not ReasonCode.INVOICE_ALREADY_CLAIMED:
            continue
        settlement = entity_to_settlement.get(exception.entity_id, "")
        if exception.invoice_id and owner_of_invoice.get(exception.invoice_id) == settlement:
            wrongly_given_away.append(
                {
                    "entity_id": exception.entity_id,
                    "settlement_id": settlement,
                    "invoice_id": exception.invoice_id,
                    "detail": exception.detail,
                }
            )

    n_claimed = sum(
        1 for e in outcome.enumeration.exceptions
        if e.reason_code is ReasonCode.INVOICE_ALREADY_CLAIMED
    )
    n_settlements = outcome.enumeration.n_settlements
    lo_d, hi_d = wilson(len(wrongly_given_away), n_settlements)

    print("\nINVOICE GIVEN AWAY WRONGLY  (the defect deferred from Phase 5)")
    print(f"  INVOICE_ALREADY_CLAIMED total   {n_claimed:,}")
    print(f"  of those, truth says THIS settlement owns the invoice: "
          f"{len(wrongly_given_away):,}")
    print(f"  as a share of all settlements   {len(wrongly_given_away) / n_settlements:.4%} "
          f"95% CI [{lo_d:.4%}, {hi_d:.4%}]")
    for row in wrongly_given_away[:5]:
        print(f"    {row['entity_id']}  invoice {row['invoice_id']}")

    report = {
        "batch": str(BATCH).replace("\\", "/"),
        "threshold": THRESHOLD,
        "determinism_check": {"reported": REPORTED, "reproduced": reproduced, "identical": True},
        "note": (
            "Computed after the reported run from the same deterministic pipeline. No "
            "threshold, feature or calibrator was touched; the match count is asserted "
            "against the reported one before any metric here is computed."
        ),
        "reason_codes": scored,
        "reason_code_accuracy_ci": {"low": round(lo, 6), "high": round(hi, 6)},
        "invoice_given_away_wrongly": {
            "n": len(wrongly_given_away),
            "of_invoice_already_claimed": n_claimed,
            "of_settlements": n_settlements,
            "rate": round(len(wrongly_given_away) / n_settlements, 6),
            "ci_low": round(lo_d, 6),
            "ci_high": round(hi_d, 6),
            "rows": wrongly_given_away,
        },
    }
    out = Path(__file__).with_name("sealed_test_reasons.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
