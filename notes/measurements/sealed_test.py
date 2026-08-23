"""Scoring a batch at the pre-committed operating point.

Written to be pointed at `data/test` once. It is parameterised by batch directory so that
the identical code path can be rehearsed against `data/scale` first -- a crash or a
mis-wired metric discovered *after* the seal breaks cannot be fixed by re-running, because
the first scored run is the reported run. Rehearsing on an unsealed batch is how that
guarantee survives contact with a script that had never been executed.

    python notes/measurements/sealed_test.py data/scale --out scale_scored.json
    python notes/measurements/sealed_test.py data/test  --out sealed_test.json

The threshold is fixed at the pre-committed value and is not a command-line argument. See
notes/phase-7-precommitment.md: scoring at two thresholds and reporting the better one is
the exact failure this design exists to prevent.

What it reports, in the order the pre-commitment asks for it:

    1. coverage, precision with a Wilson interval and raw counts, false matches, wrong money
    2. the two held-out case types separately, never folded into an aggregate
    3. reliability on this batch beside the recorded train-side eval split, with the ECE
       decomposed by whether a candidate belongs to a case type the model never saw
    4. the per-case-type confusion matrix, held-out types called out
"""

import argparse
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.candidates import export_candidates  # noqa: E402
from evals.metrics import confusion_by_case_type, load_batch, score_at, wilson  # noqa: E402
from evals.models import Prediction, Triple  # noqa: E402
from evals.report import HELD_OUT_CASES  # noqa: E402
from evals.training import label_candidates  # noqa: E402
from llm.codes import ReasonCode, needs_llm  # noqa: E402
from model.artifact import Artifact  # noqa: E402
from model.calibration import (  # noqa: E402
    brier_score,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_bins,
)
from model.chart import render_reliability  # noqa: E402
from model.predict import reconcile_batch, score_candidates  # noqa: E402

MODEL = Path("runs/_models/v1")

# The pre-committed operating point, from notes/phase-7-precommitment.md. Fixed here as a
# constant rather than exposed as a flag, deliberately.
THRESHOLD = 0.9564


def rupees(paise: int) -> str:
    return f"{paise / 100:,.2f}"


def pct(x: float) -> str:
    return f"{x:.2%}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--out", required=True, help="filename, written beside this script")
    parser.add_argument("--chart", default="", help="reliability diagram filename, optional")
    args = parser.parse_args()

    batch_dir = args.batch
    artifact = Artifact.load(MODEL)

    machine = {
        "cpu": platform.processor() or "unknown",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    print(f"batch      {batch_dir}")
    print(f"model      {artifact.model_version}")
    print(f"threshold  {THRESHOLD}   (pre-committed)")
    print(f"machine    {machine['platform']}, python {machine['python']}\n")

    started = time.perf_counter()
    outcome = reconcile_batch(batch_dir, artifact, threshold=THRESHOLD)
    elapsed = time.perf_counter() - started

    # Every accepted resolution, with its calibrated probability. score_at applies the
    # operating point itself; handing it the pre-threshold list is what lets coverage and
    # the curve share one definition.
    predictions = [
        Prediction(Triple(*c.triple), c.probability, "model", c.row.entity_id)
        for c in outcome.all_resolved
    ]

    batch = load_batch(batch_dir)
    score = score_at(predictions, batch, THRESHOLD)
    low, high = score.precision_interval

    # ---------------------------------------------------------------- 1. headline

    enumeration = outcome.enumeration
    n_settlements = enumeration.n_settlements
    settlement_coverage = len(outcome.matches) / n_settlements if n_settlements else 0.0

    print("HEADLINE")
    print(f"  settlements            {n_settlements:,}")
    print(f"  decidable truth links  {score.n_decidable:,}")
    print(f"  auto-matched (triples) {score.n_predicted:,}")
    print(f"  coverage  |P|/|D|      {pct(score.coverage)}     <- notes/metrics.md")
    print(f"  coverage  matched/settlements {pct(settlement_coverage)}  <- scale.py's figure")
    print(f"  precision              {score.precision:.4%}  "
          f"(95% CI {low:.4%}-{high:.4%})")
    print(f"    true positives       {score.n_true_positives:,}")
    print(f"    false matches        {score.n_false_positives:,}")
    print(f"  recall                 {pct(score.recall)}")
    print(f"  Rs at stake            {rupees(score.total_money)}")
    print(f"  Rs auto-matched        {rupees(score.matched_money)}")
    print(f"  Rs incorrectly matched {rupees(score.wrong_money)}")
    print(f"  money-weighted prec.   {score.money_weighted_precision:.6%}")
    print(f"  money error ratio      {score.money_error_ratio:.6%}")
    print(f"  orphans                {score.n_orphans:,}, "
          f"refused {score.n_orphans_refused:,} ({pct(score.orphan_refusal_rate)})")
    print(f"  wall time              {elapsed:.1f}s")

    # ------------------------------------------------- 2. per-case-type confusion

    confusion = confusion_by_case_type(predictions, batch, THRESHOLD)

    # A false match names an invoice. Attribute it to the case type of the truth row that
    # invoice really belongs to, so "which case types does it get WRONG" has an answer --
    # confusion_by_case_type keys off truth rows and therefore cannot see false positives
    # at all.
    case_of_invoice = {r.invoice_id: r.case_type for r in batch.decidable if r.invoice_id}
    truth_triples = {r.triple for r in batch.decidable}
    selected = [p for p in predictions if p.confidence >= THRESHOLD]
    false_by_case: Counter = Counter()
    for prediction in selected:
        if prediction.triple not in truth_triples:
            case = case_of_invoice.get(prediction.triple.invoice_id, "(no truth invoice)")
            false_by_case[case] += 1

    print("\nPER-CASE-TYPE")
    print(f"  {'case_type':24} {'total':>7} {'matched':>8} {'missed':>7} {'refused':>8} "
          f"{'false':>6}   matched rate (95% CI)")
    rows_out = {}
    for case in sorted(confusion):
        counts = confusion[case]
        held = case in HELD_OUT_CASES
        denominator = counts["total"]
        # For orphans the correct outcome is refusal, so the rate worth an interval is the
        # refusal rate; for everything else it is the matched rate.
        successes = counts["refused"] if case == "orphan" else counts["matched"]
        lo, hi = wilson(successes, denominator)
        rate = successes / denominator if denominator else 0.0
        marker = " (HELD OUT)" if held else ""
        print(f"  {case:24} {counts['total']:7,} {counts['matched']:8,} "
              f"{counts['missed']:7,} {counts['refused']:8,} {false_by_case.get(case, 0):6,}   "
              f"{rate:7.2%}  [{lo:.2%}, {hi:.2%}]  width {hi - lo:.2%}{marker}")
        rows_out[case] = {
            **counts,
            "false_matches_attributed": false_by_case.get(case, 0),
            "rate": round(rate, 6),
            "rate_is": "refusal" if case == "orphan" else "matched",
            "ci_low": round(lo, 6),
            "ci_high": round(hi, 6),
            "ci_width": round(hi - lo, 6),
            "held_out": held,
        }

    # ------------------------------------------------------------- 3. reliability

    rows = export_candidates(batch_dir)
    labelled = label_candidates(rows, batch_dir)
    y = np.array([r["label"] for r in labelled], dtype=int)
    p = np.array([c.probability for c in score_candidates(rows, artifact)], dtype=float)

    # Which candidates belong to a case type the model never saw. Attributed by invoice,
    # the only id a candidate and a truth row reliably share.
    held_out_invoices = {
        r.invoice_id for r in batch.decidable if r.case_type in HELD_OUT_CASES and r.invoice_id
    }
    is_held = np.array([r["invoice_id"] in held_out_invoices for r in labelled], dtype=bool)

    def calibration_of(mask) -> dict:
        yy, pp = y[mask], p[mask]
        if len(yy) == 0:
            return {"n": 0}
        return {
            "n": int(len(yy)),
            "ece": round(expected_calibration_error(yy, pp), 6),
            "mce": round(maximum_calibration_error(yy, pp), 6),
            "brier": round(brier_score(yy, pp), 6),
            "base_rate": round(float(yy.mean()), 6),
        }

    everything = calibration_of(np.ones(len(y), dtype=bool))
    seen_only = calibration_of(~is_held)
    held_only = calibration_of(is_held)
    train_side = artifact.metrics["evaluation_out_of_sample"]

    print("\nRELIABILITY  (calibrated probability vs observed frequency)")
    print(f"  {'population':38} {'n':>7} {'ECE':>9} {'MCE':>9} {'Brier':>9} {'base rate':>10}")
    print(f"  {'train eval split (recorded, 8 types)':38} {train_side['n']:7,} "
          f"{train_side['ece']:9.6f} {train_side['mce']:9.6f} {train_side['brier']:9.6f} "
          f"{artifact.class_prior['base_rate_evaluation']:10.4f}")
    for label, block in (
        ("this batch, all candidates", everything),
        ("this batch, seen case types only", seen_only),
        ("this batch, held-out types only", held_only),
    ):
        if block["n"]:
            print(f"  {label:38} {block['n']:7,} {block['ece']:9.6f} {block['mce']:9.6f} "
                  f"{block['brier']:9.6f} {block['base_rate']:10.4f}")

    bins = reliability_bins(y, p)
    print(f"\n  bins on this batch ({len(bins)} non-empty):")
    print(f"    {'range':>14} {'n':>7} {'mean pred':>10} {'observed':>10} {'gap':>9}"
          f" {'held-out n':>11} {'held-out share':>15}")
    bin_rows = []
    for b in bins:
        mask = (p >= b.lower) & (p <= b.upper) if b.upper >= 1.0 else (p >= b.lower) & (p < b.upper)
        n_held = int((mask & is_held).sum())
        share = n_held / b.n if b.n else 0.0
        print(f"    [{b.lower:.1f}, {b.upper:.1f}) {b.n:7,} {b.mean_predicted:10.5f} "
              f"{b.observed_rate:10.5f} {b.gap:9.5f} {n_held:11,} {share:15.2%}")
        bin_rows.append({**b.as_dict(), "n_held_out": n_held, "held_out_share": round(share, 6)})

    # Where the unseen case types sit in the score distribution -- the pre-commitment asks
    # for prior shift to be diagnosed with a measurement rather than asserted.
    held_p = p[is_held]
    held_position = {}
    if len(held_p):
        held_position = {
            "n_candidates": int(len(held_p)),
            "share_of_all_candidates": round(float(is_held.mean()), 6),
            "mean_probability": round(float(held_p.mean()), 6),
            "median_probability": round(float(np.median(held_p)), 6),
            "share_at_or_above_threshold": round(float((held_p >= THRESHOLD).mean()), 6),
            "n_at_or_above_threshold": int((held_p >= THRESHOLD).sum()),
            "positives_among_held_out": int(y[is_held].sum()),
        }
        print(f"\n  where the unseen types sit: {held_position['n_candidates']:,} candidates "
              f"({held_position['share_of_all_candidates']:.2%} of all), mean p "
              f"{held_position['mean_probability']:.4f}, "
              f"{held_position['share_at_or_above_threshold']:.2%} at or above the threshold")

    if args.chart:
        chart_path = Path(__file__).with_name(args.chart)
        render_reliability(
            bins, chart_path, ece=everything["ece"],
            title=f"Reliability - {batch_dir}",
        )
        print(f"  diagram written to {chart_path}")

    # ------------------------------------------------------------ 4. accounting

    judgement = [r for r in enumeration.exceptions if needs_llm(r.reason_code)]
    accounted = len(outcome.matches) + len(enumeration.exceptions)
    print("\nACCOUNTING")
    print(f"  matched {len(outcome.matches):,} + exceptions {len(enumeration.exceptions):,} "
          f"= {accounted:,}  (settlements {n_settlements:,}) "
          f"{'OK' if accounted == n_settlements else 'MISMATCH'}")
    for code, count in enumeration.by_reason().items():
        marker = "-> LLM" if needs_llm(ReasonCode(code)) else "free  "
        print(f"      {marker}  {code:26} {count:6,}")
    print(f"  deterministic share  {enumeration.deterministic_share():.2%}")
    print(f"  LLM-bound            {len(judgement):,}")

    truth_types = Counter(r.case_type for r in batch.truth)

    report = {
        "batch": str(batch_dir).replace("\\", "/"),
        "model_version": artifact.model_version,
        "threshold": THRESHOLD,
        "threshold_source": "notes/phase-7-precommitment.md, committed at a733ad4",
        "machine": machine,
        "seconds": round(elapsed, 3),
        "score": score.as_dict(),
        "coverage_definitions": {
            "predictions_over_decidable": round(score.coverage, 6),
            "matched_over_settlements": round(settlement_coverage, 6),
            "note": (
                "notes/metrics.md defines coverage as |P(t)|/|D| over non-orphan truth "
                "triples. notes/measurements/scale.py reported matched/settlements. Both "
                "are given so the pre-committed prediction is judged like for like."
            ),
        },
        "settlements": n_settlements,
        "matched_settlements": len(outcome.matches),
        "truth_rows_by_case_type": dict(sorted(truth_types.items())),
        "confusion_by_case_type": {
            "seen": {k: v for k, v in sorted(rows_out.items()) if k not in HELD_OUT_CASES},
            "held_out": {k: v for k, v in sorted(rows_out.items()) if k in HELD_OUT_CASES},
        },
        "calibration": {
            "train_eval_split_recorded": train_side,
            "this_batch_all": everything,
            "this_batch_seen_types": seen_only,
            "this_batch_held_out_types": held_only,
            "bins": bin_rows,
            "held_out_position_in_score_distribution": held_position,
        },
        "accounting": {
            "matched": len(outcome.matches),
            "exceptions": len(enumeration.exceptions),
            "accounted_for": accounted,
            "complete": accounted == n_settlements,
            "by_reason": enumeration.by_reason(),
            "deterministic_share": round(enumeration.deterministic_share(), 4),
            "llm_bound": len(judgement),
        },
    }

    out = Path(__file__).with_name(args.out)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
