"""Reading a stored run and turning it into what the three screens need.

THE SLIDER READS THIS, AND IT DOES NOT RE-RUN ANYTHING

BUILD.md: "Operating-point slider updates metrics without a full re-run." That is satisfied
structurally rather than by caching: `runs/<id>/curve.json` already contains every
achievable operating point, computed once when the run was scored. Moving the slider is a
lookup.

WHAT THE SLIDER MUST BE HONEST ABOUT

**It snaps to real points.** There are ~16 achievable operating points on this curve, not a
continuum. A slider that glides through intermediate values implies a resolution the model
does not have -- isotonic calibration maps to a step function, and 99.7% of candidates
share an exact probability with another. Dragging therefore produces jumps of visibly
different sizes, and the largest step is *felt*. That is a true thing about the model, and
hiding it behind a smooth gradient would be the interaction-design version of reporting
99.5031% from four events.

**Precision comes with its interval.** Every point carries a Wilson interval and the raw
counts behind it. At low coverage the interval widens because the count is small, and that
widening is the signal.

**Counts, not just percentages.** "2 wrong matches, Rs 27,372 mis-posted of
Rs 395,349,148 at stake" is the sentence a finance operator reasons about.

COST AT AN ARBITRARY OPERATING POINT, WITHOUT ASSUMING ANYTHING

The first attempt assumed the deterministic reason codes were threshold-independent and
subtracted a fixed count. That is *almost* true and produced a cost of Rs 0.00 at the most
permissive operating point, which is how it was caught: `INVOICE_ALREADY_CLAIMED` is
assigned from a settlement's **best** candidate, and a settlement whose best candidate lost
its invoice can still be accepted through its second choice. Five settlements on
`data/train` are coded deterministically at the operating point and are matchable at a
lower one.

The structural fact needs no assumption:

*   a settlement with **no accepted candidate at all** cannot be matched at any threshold,
    so it is an exception everywhere, and its code is always a deterministic one;
*   a settlement **with** an accepted candidate is an exception exactly when that
    candidate's probability is below the threshold, and its code is then a judgement one.

So `llm_bound(t) = accepted_settlements - matched(t)`, counted rather than derived.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evals.curve import CURVE_FILE, RiskCoverageCurve
from evals.runs import FilesystemRunStore
from llm.codes import ReasonCode, needs_llm
from llm.cost import band

RUNS_ROOT = Path("runs")
REPORT_FILE = "report.json"

# Measured over 100 real exceptions through the reason job. Used to price an arbitrary
# operating point; the run's own totals are used where a run actually called the model.
TOKENS_IN_PER_EXCEPTION = 220
TOKENS_OUT_PER_EXCEPTION = 168


def rupees(paise: int) -> str:
    return f"{paise / 100:,.2f}"


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    batch_dir: str
    meta: dict
    report: dict
    curve: RiskCoverageCurve
    exceptions: list[dict]
    # Best calibrated probability per settlement, from the run's own predictions. The
    # operating-point explorer counts SETTLEMENTS matched at a threshold, and the curve's
    # n_predicted counts triples -- several rows of one payout batch produce distinct
    # triples sharing a settlement_id, so the two differ by a handful of rows. Deriving
    # exception counts from the triple count showed up as a cost of Rs 0.00 at the most
    # permissive operating point, which is how the discrepancy was noticed.
    settlement_confidence: dict[str, float]

    @property
    def n_settlements(self) -> int:
        return int(self.meta.get("n_settlements") or 0)

    @property
    def deterministic_exceptions(self) -> int:
        """Threshold-independent. See the module docstring for why."""
        return sum(
            1
            for e in self.exceptions
            if not needs_llm(ReasonCode(e["reason_code"]))
        )

    def reason_breakdown(self) -> list[dict]:
        counts: dict[str, int] = {}
        for e in self.exceptions:
            counts[e["reason_code"]] = counts.get(e["reason_code"], 0) + 1
        out = []
        for code, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            enum = ReasonCode(code)
            out.append({
                "code": code,
                "count": count,
                "family": str(enum.family),
                "needs_llm": needs_llm(enum),
                "description": enum.description,
            })
        return out


def load_run(run_id: str, root: Path | str = RUNS_ROOT) -> RunSummary:
    root = Path(root)
    run = FilesystemRunStore(root).load(run_id)

    report_path = root / run_id / REPORT_FILE
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}

    curve_path = root / run_id / CURVE_FILE
    curve = RiskCoverageCurve.load(curve_path) if curve_path.is_file() else RiskCoverageCurve()

    by_settlement: dict[str, float] = {}
    for prediction in run.predictions:
        key = prediction.entity_id or "|".join(
            (prediction.triple.invoice_id, prediction.triple.settlement_id,
             prediction.triple.txn_id)
        )
        by_settlement[key] = max(by_settlement.get(key, 0.0), prediction.confidence)

    return RunSummary(
        run_id=run_id,
        batch_dir=run.batch_dir,
        meta=run.meta,
        report=report,
        curve=curve,
        exceptions=run.exceptions,
        settlement_confidence=by_settlement,
    )


def operating_points(summary: RunSummary) -> list[dict]:
    """Every achievable operating point, with all three consequences attached.

    Deduplicated by the matched count: two thresholds that admit the same set of matches
    are the same operating point, and offering both as distinct choices would be the
    continuum illusion in another form.
    """
    settlements = summary.n_settlements
    # Settlements that resolution accepted a candidate for, at any probability. Everything
    # else is an exception at every threshold -- see the module docstring.
    accepted = len(summary.settlement_confidence)
    never_matchable = max(0, settlements - accepted)

    confidences = sorted(summary.settlement_confidence.values(), reverse=True)

    def settlements_at(threshold: float) -> int:
        """How many SETTLEMENTS clear this threshold. Counted, never derived."""
        return sum(1 for c in confidences if c >= threshold)

    points: list[dict] = []
    seen: set[int] = set()

    for point in sorted(summary.curve.points, key=lambda p: p.threshold):
        matched = settlements_at(point.threshold)
        if matched in seen:
            # Two thresholds admitting the same settlements are one operating point.
            # Offering both as distinct choices would be the continuum illusion again.
            continue
        seen.add(matched)

        exceptions = max(0, settlements - matched) if settlements else 0
        # Counted from what resolution accepted, not subtracted from a fixed total.
        llm_bound = max(0, accepted - matched)

        cost = band(
            llm_bound * TOKENS_IN_PER_EXCEPTION,
            llm_bound * TOKENS_OUT_PER_EXCEPTION,
        )
        low, high = point.precision_interval

        points.append({
            "threshold": round(point.threshold, 6),
            # --- what it decides ---------------------------------------------
            "matched": matched,
            "to_review": exceptions,
            "coverage": round(point.coverage, 6),
            # --- what it gets wrong ------------------------------------------
            "false_matches": point.n_false_positives,
            "precision": round(point.precision, 6),
            "precision_ci_low": round(low, 6),
            "precision_ci_high": round(high, 6),
            "wrong_money_paise": point.wrong_money,
            "wrong_money": rupees(point.wrong_money),
            "total_money": rupees(point.total_money),
            # --- what it costs -----------------------------------------------
            "llm_bound_exceptions": llm_bound,
            "deterministic_exceptions": never_matchable,
            "cost_low_inr": round(cost.low_inr, 2),
            "cost_high_inr": round(cost.high_inr, 2),
        })

    # Ascending coverage: dragging right buys more automation and more risk, which is the
    # direction the trade actually runs.
    points.sort(key=lambda p: p["matched"])
    return points


def step_sizes(points: list[dict]) -> list[int]:
    """How many settlements each step admits over the one before it.

    Surfaced so the UI can size the jumps honestly rather than spacing points evenly. On
    this curve one step admits ~196 candidates and several admit one; drawing them the same
    width would be a lie about the shape of the model.
    """
    sizes = []
    previous = 0
    for point in points:
        sizes.append(point["matched"] - previous)
        previous = point["matched"]
    return sizes


def dashboard(summary: RunSummary) -> dict:
    """Everything the dashboard screen needs, in one response."""
    points = operating_points(summary)
    sizes = step_sizes(points)
    selected = summary.meta.get("threshold")

    chosen = 0
    if selected is not None and points:
        chosen = min(
            range(len(points)),
            key=lambda i: abs(points[i]["threshold"] - float(selected)),
        )

    score = summary.report.get("score", {})
    return {
        "run_id": summary.run_id,
        "batch_dir": summary.batch_dir,
        "settlements": summary.n_settlements,
        "model_version": summary.meta.get("model_version"),
        "calibrated": bool(summary.meta.get("calibrated")),
        "calibration_method": summary.meta.get("calibration_method"),
        "mock_llm": bool(summary.meta.get("mock_llm")),
        "selected_index": chosen,
        "operating_points": points,
        "step_sizes": sizes,
        "reason_breakdown": summary.reason_breakdown(),
        "reason_codes": summary.report.get("reason_codes"),
        "accounting": summary.report.get("accounting"),
        "money_at_stake": rupees(int(score.get("total_money", 0))),
        "orphan_refusal_rate": score.get("orphan_refusal_rate"),
        # Stated on every response so the UI can never render an uncalibrated run as if it
        # were the real curve -- architecture rule 3.
        "calibration_note": summary.meta.get("calibration_note", ""),
    }


def list_runs(root: Path | str = RUNS_ROOT) -> list[dict]:
    store = FilesystemRunStore(Path(root))
    out = []
    for run_id in store.list_runs():
        try:
            summary = load_run(run_id, root)
        except Exception:
            continue
        out.append({
            "run_id": run_id,
            "batch_dir": summary.batch_dir,
            "settlements": summary.n_settlements,
            "exceptions": len(summary.exceptions),
            "model_version": summary.meta.get("model_version"),
        })
    return out
