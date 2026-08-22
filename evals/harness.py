"""Wiring: produce a baseline run, and score any run end to end."""

from __future__ import annotations

from pathlib import Path

from evals.baseline import LAYER, run_baseline
from evals.chart import render
from evals.curve import CURVE_FILE, build_curve
from evals.metrics import load_batch, score_at
from evals.models import Run
from evals.report import build_report, markdown_table, save_report, write_readme_table
from evals.runs import FilesystemRunStore, RunStore

REPORT_FILE = "report.json"
CHART_FILE = "risk-coverage.png"


def make_baseline_run(batch_dir: Path | str, run_id: str, store: RunStore | None = None) -> Run:
    """Generate the regression-floor run. Reads only the three input files."""
    store = store or FilesystemRunStore()
    predictions = run_baseline(batch_dir)
    run = Run(
        run_id=run_id,
        batch_dir=str(batch_dir).replace("\\", "/"),
        predictions=predictions,
        meta={"layer": LAYER, "kind": "baseline"},
    )
    store.save(run)
    return run


def evaluate(
    run_id: str,
    store: RunStore | None = None,
    threshold: float = 0.0,
    readme: Path | None = None,
    chart: bool = True,
) -> dict:
    """Score a stored run, write curve/report/chart, and refresh the README table."""
    store = store or FilesystemRunStore()
    run = store.load(run_id)

    batch = load_batch(run.batch_dir)
    score = score_at(run.predictions, batch, threshold)
    curve = build_curve(run.predictions, batch)

    report = build_report(run_id, run.batch_dir, run.predictions, batch, score, curve)

    out_dir = Path("runs") / run_id
    curve.save(out_dir / CURVE_FILE)
    save_report(out_dir / REPORT_FILE, report)
    if chart:
        render(curve, out_dir / CHART_FILE, title=f"Risk-coverage - {run_id}")

    if readme is not None:
        write_readme_table(readme, markdown_table(report))

    return report
