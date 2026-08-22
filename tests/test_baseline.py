"""The baseline is a matcher living inside evals/, the one package allowed to read truth.

That is exactly where the isolation boundary could rot, so it is enforced structurally
here rather than by convention -- the same standard as tests/test_import_lint.py and
tests/test_seal.py.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from astutil import code_strings
from evals import baseline
from evals.baseline import run_baseline
from evals.metrics import load_batch, score_at

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_SOURCE = Path(baseline.__file__)


def test_the_signature_cannot_accept_truth() -> None:
    """No truth parameter, and no **kwargs through which one could be smuggled."""
    signature = inspect.signature(run_baseline)

    forbidden = [name for name in signature.parameters if "truth" in name.lower()]
    assert not forbidden, f"run_baseline accepts {forbidden}"

    kinds = {p.kind for p in signature.parameters.values()}
    assert inspect.Parameter.VAR_KEYWORD not in kinds, (
        "run_baseline accepts **kwargs, through which truth could be passed"
    )
    assert list(signature.parameters) == ["batch_dir"]


def test_the_module_never_names_the_answer_key() -> None:
    tree = ast.parse(BASELINE_SOURCE.read_text(encoding="utf-8"))
    offenders = [
        f"line {node.lineno}: {node.value!r}"
        for node in code_strings(tree)
        if "truth.csv" in node.value or "truth.json" in node.value
    ]
    assert not offenders, "the baseline names the answer key:\n  " + "\n  ".join(offenders)


def test_the_module_does_not_import_the_scorer_or_the_generator() -> None:
    """It may share models with evals/, but it must not reach truth-reading helpers."""
    tree = ast.parse(BASELINE_SOURCE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    forbidden = {"evals.metrics", "evals.harness", "datagen", "datagen.generator"}
    breaches = forbidden & set(imported)
    assert not breaches, f"baseline imports {sorted(breaches)}"


def test_the_boundary_check_would_catch_a_breach(tmp_path: Path) -> None:
    """A passing guard is only reassuring if it can fail."""
    offender = tmp_path / "offender.py"
    offender.write_text('PATH = "data/train/truth.csv"\n', encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    found = any("truth.csv" in n.value for n in code_strings(tree))
    assert found, "the guard failed to flag a deliberate breach"


# ----------------------------------------------------------------- behaviour


def test_baseline_produces_predictions_on_the_demo_batch() -> None:
    predictions = run_baseline("data/demo")
    assert predictions, "baseline found nothing at all"
    assert all(p.layer == "baseline_exact_utr" for p in predictions)


def test_baseline_scores_plausibly_low() -> None:
    """BUILD.md: a scorer reporting near-perfect results on a weak baseline is broken.

    This baseline can only find UTRs printed in the narration, and cannot tell two
    settlements apart when a UTR is reused. If either bound stops holding, something is
    wrong with the scorer, not right with the baseline.
    """
    predictions = run_baseline("data/train")
    batch = load_batch("data/train")
    score = score_at(predictions, batch)

    # Bounds rechecked after the Phase 3 data rework. Measured: coverage 49.87%,
    # precision 37.96%.
    assert 0.30 < score.coverage < 0.75, (
        f"coverage {score.coverage:.2%} is not plausible for exact-UTR-only"
    )

    # Precision is now low for a specific, checkable reason: the baseline reads the
    # invoice off order_receipt and never infers it, so it is wrong on roughly the ~62%
    # of gateway rows where that field is empty. A lower bound is asserted as well as an
    # upper one -- a collapse to near-zero would mean the baseline broke, not that the
    # data got harder, and the two must not look the same.
    assert 0.25 < score.precision < 0.90, (
        f"precision {score.precision:.2%} is outside the band explainable by "
        "order_receipt being sparsely populated"
    )
    assert score.n_false_positives > 0, "a UTR-only matcher must fail on reused UTRs"


def test_the_real_matcher_beats_the_baseline_decisively() -> None:
    """The floor exists to be cleared. If core/ ever stops clearing it, something broke."""
    from core.pipeline import reconcile
    from evals.models import Prediction, Triple

    batch = load_batch("data/train")
    baseline_score = score_at(run_baseline("data/train"), batch)

    result = reconcile("data/train")
    real = score_at(
        [
            Prediction(Triple(m.invoice_id, m.settlement_id, m.txn_id), m.score, m.layer)
            for m in result.matches
        ],
        batch,
    )

    assert real.precision > baseline_score.precision + 0.30
    assert real.recall > baseline_score.recall + 0.30


def test_baseline_refuses_every_orphan() -> None:
    """Orphans have no settlement, so a UTR-driven matcher should never touch them."""
    predictions = run_baseline("data/demo")
    batch = load_batch("data/demo")
    score = score_at(predictions, batch)
    assert score.orphan_refusal_rate == 1.0
