"""Render the risk-coverage curve.

A consumer of RiskCoverageCurve, never a producer. No test asserts on the pixels here --
matplotlib output shifts between versions and would churn the diff on every upgrade. The
curve data in curve.json is what gets tested.
"""

from __future__ import annotations

from pathlib import Path

from evals.curve import RiskCoverageCurve


def render(curve: RiskCoverageCurve, path: Path | str, title: str = "Risk-coverage") -> Path:
    """Write a PNG of precision against coverage. Returns the path written."""
    import matplotlib

    matplotlib.use("Agg")  # headless: this runs in CI and in a container
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    coverage = [p.coverage for p in curve.points]
    precision = [p.precision for p in curve.points]
    money = [p.money_weighted_precision for p in curve.points]

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=140)

    if curve.is_degenerate:
        # One operating point is not a curve. Say so on the chart rather than drawing a
        # line through a single value and implying a trade-off that does not exist.
        ax.scatter(coverage, precision, s=90, zorder=3, label="precision")
        ax.scatter(coverage, money, s=90, marker="s", zorder=3, label="money-weighted")
        ax.set_title(f"{title} - degenerate: no coverage/precision trade-off available")
    else:
        ax.plot(coverage, precision, marker="o", label="precision")
        ax.plot(coverage, money, marker="s", linestyle="--", label="money-weighted precision")
        ax.set_title(title)

    ax.set_xlabel("coverage (share of decidable links auto-matched)")
    ax.set_ylabel("precision")
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 1.02)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left")

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
