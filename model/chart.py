"""Reliability diagram.

Lives in model/ rather than evals/ so model/ stays self-contained: evals/ is the package
that may read truth, and nothing in model/ should import from it.

The diagram plots predicted confidence against observed frequency. On the diagonal means
calibrated: of the pairs scored 0.9, about 90% really are correct. Above the diagonal is
under-confident, below it is over-confident -- and over-confident is the dangerous
direction here, because it means the system auto-matches on a promise it does not keep.
"""

from __future__ import annotations

from pathlib import Path

from model.calibration import ReliabilityBin


def render_reliability(
    bins: list[ReliabilityBin],
    path: Path | str,
    *,
    ece: float,
    title: str = "Reliability",
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax, ax_hist) = plt.subplots(
        2, 1, figsize=(7, 6), dpi=140, height_ratios=[3, 1], sharex=True
    )

    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1, label="perfectly calibrated")

    x = [b.mean_predicted for b in bins]
    y = [b.observed_rate for b in bins]
    # Marker area tracks bin population: the eye should not weight a bin of 2 the same as
    # a bin of 900, which is exactly how a reliability diagram misleads.
    sizes = [max(20.0, 220.0 * (b.n / max(c.n for c in bins))) for b in bins]

    ax.scatter(x, y, s=sizes, zorder=3, label="observed (area = bin size)")
    ax.plot(x, y, linewidth=1, alpha=0.6)

    ax.set_ylabel("observed frequency")
    ax.set_title(f"{title}  -  ECE {ece:.4f}, out of sample")
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 1.02)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)

    ax_hist.bar(x, [b.n for b in bins], width=0.06)
    ax_hist.set_yscale("log")
    ax_hist.set_xlabel("predicted probability")
    ax_hist.set_ylabel("count (log)")
    ax_hist.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
