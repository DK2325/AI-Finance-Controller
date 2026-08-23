"""The canonical interface. Windows-safe; there is no Makefile in this project.

Every command is a registered no-op in Phase 0. Each phase fills one in:
Phase 1 generate, Phase 2 eval, Phase 3-5 recon, Phase 6 chaos.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import typer

app = typer.Typer(
    name="ledgerloop",
    help="Reconciliation that knows when it is wrong.",
    no_args_is_help=True,
    add_completion=False,
)


class Difficulty(StrEnum):
    easy = "easy"
    hard = "hard"


_NOT_YET = "[phase {phase}] not implemented yet - Phase 0 registers this command only."


@app.command()
def generate(
    rows: int = typer.Option(1000, "--rows", help="Number of truth rows to synthesise."),
    seed: int = typer.Option(42, "--seed", help="Deterministic seed. Same seed, same bytes."),
    difficulty: Difficulty = typer.Option(Difficulty.hard, "--difficulty"),
    out: Path = typer.Option(..., "--out", help="Output directory for the batch."),
    exclude_cases: str = typer.Option(
        "",
        "--exclude-cases",
        help="Comma-separated case types to omit, e.g. tds_deducted,refund_netted. "
        "Used to build the training batch without the held-out types.",
    ),
) -> None:
    """Synthesise a batch plus its ground-truth answer key."""
    from datagen.generator import generate_to

    exclude = tuple(c.strip() for c in exclude_cases.split(",") if c.strip())

    try:
        manifest = generate_to(out_dir=out, rows=rows, seed=seed, exclude=exclude)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    totals = manifest["totals"]
    typer.echo(f"wrote {out}  (seed={seed}, difficulty={difficulty.value})")
    typer.echo(
        f"  {totals['invoices']} invoices, {totals['gateway_rows']} gateway rows, "
        f"{totals['bank_rows']} bank rows, {totals['truth_rows']} truth rows"
    )
    if exclude:
        typer.echo(f"  excluded: {', '.join(exclude)}")


@app.command()
def recon(
    in_dir: Path = typer.Option(..., "--in", help="Batch directory to reconcile."),
    mock_llm: bool = typer.Option(
        False, "--mock-llm", help="Run the full pipeline with no API key."
    ),
    threshold: float | None = typer.Option(
        None, "--threshold", help="Operating point. Defaults to the selected point."
    ),
    run: str = typer.Option("", "--run", help="Run id to write. Defaults to recon-<batch>."),
    model: Path | None = typer.Option(
        None, "--model", help="Artifact directory. Without it, rule tiers only."
    ),
) -> None:
    """Reconcile a batch: deterministic, then fuzzy, then learned, then LLM on the residue."""
    from core.pipeline import reconcile

    if model is not None:
        from evals.models import Prediction, Run, Triple
        from evals.runs import FilesystemRunStore
        from model.artifact import Artifact, FeatureSchemaMismatch
        from model.predict import reconcile_batch

        try:
            artifact = Artifact.load(model)
        except FeatureSchemaMismatch as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=3) from exc

        point = threshold if threshold is not None else artifact.operating_point["threshold"]
        outcome = reconcile_batch(in_dir, artifact, threshold=point)

        # Predictions carry EVERY resolved candidate with its calibrated probability, not
        # only those above the operating point. The risk-coverage curve is a sweep across
        # thresholds, so a run storing only its accepted matches cannot produce one -- the
        # curve collapsed from 23 points to 3 the first time this was got wrong.
        #
        # The exceptions, by contrast, are the ones at the operating point, because an
        # exception is a decision and a decision needs a threshold to have been taken.
        # `n_matched_at_threshold` in meta is what the accounting invariant checks against.
        selected = outcome.all_resolved
        run_id = run or f"model-{Path(in_dir).name}"
        FilesystemRunStore().save(
            Run(
                run_id=run_id,
                batch_dir=str(in_dir).replace("\\", "/"),
                predictions=[
                    Prediction(Triple(*s.triple), s.probability, "model") for s in selected
                ],
                meta={
                    "calibrated": True,
                    "model_version": artifact.model_version,
                    "feature_schema_version": artifact.feature_schema_version,
                    "calibration_method": artifact.calibration["method"],
                    "operating_point": artifact.operating_point,
                    "trained_on": artifact.trained_on,
                    "excluded_cases": artifact.excluded_cases,
                    "mock_llm": mock_llm,
                    "n_settlements": outcome.enumeration.n_settlements,
                    "n_matched_at_threshold": len(outcome.matches),
                    "threshold": point,
                    "deterministic_share": round(
                        outcome.enumeration.deterministic_share(), 4
                    ),
                },
                exceptions=[r.as_dict() for r in outcome.enumeration.exceptions],
            )
        )
        n = outcome.enumeration.n_settlements
        typer.echo(f"scored {in_dir} with {artifact.model_version} -> run '{run_id}'")
        typer.echo(f"  {len(outcome.matches)} matches, "
                   f"{len(outcome.enumeration.exceptions)} exceptions"
                   f"  ({len(outcome.matches) + len(outcome.enumeration.exceptions)}"
                   f" of {n} settlements)")
        typer.echo(f"  {len(selected)} resolved candidates stored for the curve sweep")
        typer.echo(f"  calibrated probabilities ({artifact.calibration['method']})")
        typer.echo(
            f"  {outcome.enumeration.deterministic_share():.1%} of exceptions carry a "
            "deterministic reason code and never reach a model"
        )
        for code, count in outcome.enumeration.by_reason().items():
            typer.echo(f"      {code:26} {count:6,}")
        return
    from evals.models import Prediction, Run, Triple
    from evals.runs import FilesystemRunStore

    result = reconcile(in_dir)

    predictions = [
        Prediction(
            triple=Triple(m.invoice_id, m.settlement_id, m.txn_id),
            confidence=m.score,
            layer=m.layer,
        )
        for m in result.matches
        if threshold is None or m.score >= threshold
    ]

    run_id = run or f"recon-{Path(in_dir).name}"
    meta = result.meta()
    meta["mock_llm"] = mock_llm
    FilesystemRunStore().save(
        Run(run_id=run_id, batch_dir=str(in_dir).replace("\\", "/"),
            predictions=predictions, meta=meta)
    )

    t = meta["timing"]
    typer.echo(f"reconciled {in_dir} -> run '{run_id}'")
    typer.echo(f"  {len(result.matches)} matches, {len(result.exceptions)} exceptions")
    typer.echo(f"  {t['rows']} rows in {t['seconds_total']}s ({t['rows_per_second']:,.0f} rows/s)")
    for stage, seconds in t["seconds_by_stage"].items():
        typer.echo(f"      {stage:12} {seconds:7.3f}s")
    typer.echo("  candidates per blocking pass:")
    for name, count in result.blocking["per_pass"].items():
        typer.echo(f"      {name:16} {count:8,}")
    typer.echo(f"      {'unique':16} {result.blocking['unique_candidates']:8,}")
    ss = result.subset_sum
    typer.echo(
        f"  subset-sum: {ss['buckets_searched']} searched, {ss['buckets_skipped']} capped"
        f" ({ss['cap_hit_rate']:.2%}), {ss['subsets_found']} found"
    )
    typer.echo("")
    typer.echo("  NOTE: rule scores are ranked tiers, not calibrated probabilities.")


@app.command()
def train(
    batch: Path = typer.Option(Path("data/train"), "--batch", help="Batch to train on."),
    out: Path = typer.Option(Path("runs/_models/v1"), "--out", help="Artifact directory."),
    chart: Path = typer.Option(
        Path("notes/reliability.png"), "--chart", help="Reliability diagram output."
    ),
) -> None:
    """Train and calibrate the classifier, and select an operating point.

    A fifth command beyond BUILD.md's four. Training is a distinct operation with its own
    inputs and artifact, and hiding it inside recon would make the canonical interface
    lie about what it does.
    """
    from evals.training import build_dataset
    from model.calibration import ReliabilityBin
    from model.chart import render_reliability
    from model.train import train as run_train

    dataset_path = Path("runs/_datasets") / f"{batch.name}.csv"
    summary = build_dataset(batch, dataset_path)
    typer.echo(
        f"dataset {dataset_path}: {summary['n_candidates']} candidates, "
        f"{summary['n_positive']} positive (base rate {summary['base_rate']:.1%}), "
        f"{summary['share_unscored_by_rules']:.1%} scored by no rule"
    )

    artifact = run_train(dataset_path, out, trained_on=str(batch).replace("\\", "/"))
    m = artifact.manifest()

    metrics = m["metrics"]["evaluation_out_of_sample"]
    bins = [ReliabilityBin(**{k: v for k, v in b.items() if k != "gap"})
            for b in m["metrics"]["reliability_bins"]]
    render_reliability(bins, chart, ece=metrics["ece"], title="Calibration - v1")

    op = m["operating_point"]
    typer.echo("")
    typer.echo(f"model {m['model_version']}  ({m['algorithm']})")
    typer.echo(f"  calibration      {m['calibration']['method']} "
               f"(beat {'platt' if m['calibration']['method'] == 'isotonic' else 'isotonic'} "
               f"by {m['calibration']['margin_over_runner_up']:.5f} ECE)")
    typer.echo(f"  out-of-sample    ECE {metrics['ece']:.5f}  MCE {metrics['mce']:.5f}  "
               f"Brier {metrics['brier']:.5f}  n={metrics['n']}")
    typer.echo(f"  operating point  threshold {op['threshold']:.4f} -> "
               f"coverage {op['coverage']:.2%} at precision {op['precision']:.4%}")
    if not op.get("floor_met", True):
        typer.echo(f"  WARNING: precision floor {op['precision_floor']:.3%} NOT met "
                   f"(shortfall {op['shortfall']:.4%})")
    typer.echo("")
    typer.echo(f"  artifact            -> {out}")
    typer.echo(f"  reliability diagram -> {chart}")


@app.command(name="eval")
def eval_(
    run: str = typer.Option(..., "--run", help="RUN_ID to score."),
    threshold: float = typer.Option(0.0, "--threshold", help="Operating point to report at."),
    baseline_from: Path | None = typer.Option(
        None,
        "--baseline-from",
        help="Create the exact-UTR-only baseline run over this batch first, then score it.",
    ),
    no_readme: bool = typer.Option(False, "--no-readme", help="Skip the README table rewrite."),
) -> None:
    """Score a run against ground truth and regenerate the README metrics table."""
    from evals.harness import evaluate, make_baseline_run

    if baseline_from is not None:
        made = make_baseline_run(baseline_from, run_id=run)
        typer.echo(
            f"baseline run '{run}' over {baseline_from}: {len(made.predictions)} predictions"
        )

    try:
        report = evaluate(
            run_id=run,
            threshold=threshold,
            readme=None if no_readme else Path("README.md"),
        )
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    s = report["score"]
    typer.echo(f"\nrun {run}  batch {report['batch_dir']}  threshold {threshold}")
    typer.echo(f"  coverage                 {s['coverage']:.2%}")
    typer.echo(f"  precision                {s['precision']:.2%}")
    typer.echo(f"  recall                   {s['recall']:.2%}")
    typer.echo(f"  money-weighted precision {s['money_weighted_precision']:.4%}")
    typer.echo(f"  money error ratio        {s['money_error_ratio']:.4%}")
    typer.echo(f"  orphan refusal rate      {s['orphan_refusal_rate']:.2%}")
    typer.echo(f"  false auto-matches       {s['n_false_positives']}")
    degenerate = " (degenerate)" if report["curve"]["is_degenerate"] else ""
    typer.echo(f"  curve points             {report['curve']['n_points']}{degenerate}")

    if "reason_codes" in report:
        rc = report["reason_codes"]
        acc = report.get("accounting", {})
        typer.echo("")
        typer.echo(f"  reason-code actionability {rc['accuracy']:.2%} "
                   f"({rc['justified']}/{rc['justified'] + rc['unjustified']})")
        for code, detail in rc["by_code"].items():
            rate = f"{detail['accuracy']:.1%}" if detail["accuracy"] is not None else "n/a"
            typer.echo(f"      {code:26} {rate:>7}  ({detail['justified']}/{detail['total']})")
        if acc:
            mark = "OK" if acc.get("complete") else "INCOMPLETE"
            typer.echo(
                f"  every settlement accounted for: {mark} "
                f"({acc['matched']} matched + {acc['exceptions']} exceptions "
                f"= {acc['accounted_for']} of {acc['settlements']})"
            )
    typer.echo(f"\n  written to runs/{run}/")


@app.command()
def chaos(
    run: str = typer.Option(..., "--run", help="RUN_ID to corrupt."),
    corruption: str = typer.Option(..., "--corruption", help="Corruption type or free-text spec."),
) -> None:
    """Inject novel, unmodelled corruption and watch the system route it to exceptions."""
    typer.echo(_NOT_YET.format(phase=6))
    typer.echo(f"  would inject '{corruption}' into run {run}")


if __name__ == "__main__":
    app()
