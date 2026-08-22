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
    rows: int = typer.Option(1000, "--rows", help="Number of invoice rows to synthesise."),
    seed: int = typer.Option(42, "--seed", help="Deterministic seed. Same seed, same bytes."),
    difficulty: Difficulty = typer.Option(Difficulty.hard, "--difficulty"),
    out: Path = typer.Option(..., "--out", help="Output directory for the batch."),
) -> None:
    """Synthesise a batch plus its ground-truth answer key."""
    typer.echo(_NOT_YET.format(phase=1))
    typer.echo(f"  would write {rows} rows (seed={seed}, difficulty={difficulty.value}) to {out}")


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
) -> None:
    """Score a run against ground truth and regenerate the README metrics table."""
    typer.echo(_NOT_YET.format(phase=2))
    typer.echo(f"  would score run {run}")


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
