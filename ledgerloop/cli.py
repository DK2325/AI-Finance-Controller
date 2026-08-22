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
) -> None:
    """Reconcile a batch: deterministic, then fuzzy, then learned, then LLM on the residue."""
    typer.echo(_NOT_YET.format(phase=3))
    typer.echo(f"  would reconcile {in_dir} (mock_llm={mock_llm}, threshold={threshold})")


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
