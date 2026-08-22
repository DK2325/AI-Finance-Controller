"""Scorer tests against hand-built fixtures with answers computable on paper.

The scorer is never validated against its own output. Every expected value below is
arithmetic a reader can check, which is the only way to know the harness is measuring
what notes/metrics.md says it measures.
"""

from __future__ import annotations

import pytest

from evals.metrics import Batch, confusion_by_case_type, score_at, to_paise
from evals.models import Prediction, Triple, TruthRow


def truth(n: int, case: str = "clean") -> TruthRow:
    return TruthRow(f"INV{n}", f"SETL{n}", f"TXN{n}", case)


def orphan(n: int) -> TruthRow:
    return TruthRow("", "", f"TXN{n}", "orphan")


def predict(n: int, confidence: float = 1.0) -> Prediction:
    return Prediction(Triple(f"INV{n}", f"SETL{n}", f"TXN{n}"), confidence)


def wrong(n: int, m: int, confidence: float = 1.0) -> Prediction:
    """Predict invoice n against transaction m -- a plausible, wrong link."""
    return Prediction(Triple(f"INV{n}", f"SETL{n}", f"TXN{m}"), confidence)


def make_batch(rows: list[TruthRow], amounts: dict[str, int] | None = None) -> Batch:
    """Every invoice Rs 1,000 and every credit Rs 1,000 unless overridden."""
    amounts = amounts or {}
    return Batch(
        truth=rows,
        amount_by_invoice={
            r.invoice_id: amounts.get(r.invoice_id, 100_000) for r in rows if r.invoice_id
        },
        amount_by_txn={r.txn_id: amounts.get(r.txn_id, 100_000) for r in rows},
    )


# ------------------------------------------------------------------ sanity anchors


def test_a_perfect_prediction_set_scores_one() -> None:
    rows = [truth(1), truth(2), truth(3)]
    score = score_at([predict(1), predict(2), predict(3)], make_batch(rows))

    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.coverage == 1.0
    assert score.money_weighted_precision == 1.0
    assert score.money_error_ratio == 0.0


def test_an_entirely_wrong_prediction_set_scores_zero() -> None:
    rows = [truth(1), truth(2)]
    score = score_at([wrong(1, 2), wrong(2, 1)], make_batch(rows))

    assert score.precision == 0.0
    assert score.recall == 0.0
    assert score.n_false_positives == 2


def test_predicting_nothing_gives_zero_coverage_not_zero_precision() -> None:
    """Precision over an empty set is 1.0 by convention: it got nothing wrong."""
    score = score_at([], make_batch([truth(1), truth(2)]))
    assert score.coverage == 0.0
    assert score.recall == 0.0
    assert score.precision == 1.0


def test_recall_equals_precision_times_coverage() -> None:
    rows = [truth(n) for n in range(1, 11)]
    predictions = [predict(n) for n in range(1, 7)] + [wrong(7, 8)]
    score = score_at(predictions, make_batch(rows))

    assert score.recall == pytest.approx(score.precision * score.coverage)


def test_duplicate_predictions_do_not_inflate_coverage() -> None:
    rows = [truth(1), truth(2)]
    score = score_at([predict(1), predict(1), predict(1)], make_batch(rows))
    assert score.n_predicted == 1
    assert score.coverage == 0.5


# -------------------------------------------------------------------- thresholds


def test_threshold_selects_predictions() -> None:
    rows = [truth(1), truth(2), truth(3), truth(4)]
    predictions = [predict(1, 0.9), predict(2, 0.8), predict(3, 0.5), wrong(4, 1, 0.4)]
    batch = make_batch(rows)

    assert score_at(predictions, batch, 0.85).n_predicted == 1
    assert score_at(predictions, batch, 0.45).n_predicted == 3
    assert score_at(predictions, batch, 0.0).n_predicted == 4

    # Dropping the only wrong prediction should take precision to 1.0.
    assert score_at(predictions, batch, 0.45).precision == 1.0
    assert score_at(predictions, batch, 0.0).precision == 0.75


# ----------------------------------------------------------------------- orphans


def test_a_refused_orphan_is_excluded_from_both_denominators() -> None:
    """Two real links and one orphan. Refusing the orphan must not change the ratios."""
    rows = [truth(1), truth(2), orphan(9)]
    score = score_at([predict(1), predict(2)], make_batch(rows))

    assert score.n_decidable == 2, "the orphan must not be decidable"
    assert score.coverage == 1.0
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.orphan_refusal_rate == 1.0


def test_auto_matching_an_orphan_is_punished_twice() -> None:
    """Once as a false positive, once in the orphan refusal rate.

    This is the failure the separate metric exists to prevent: without it, a system that
    matches orphans is rewarded by a denominator that never grows.
    """
    rows = [truth(1), truth(2), orphan(9)]
    bogus = Prediction(Triple("INV1", "SETL1", "TXN9"), 1.0)
    score = score_at([predict(1), predict(2), bogus], make_batch(rows))

    assert score.n_false_positives == 1
    assert score.precision == pytest.approx(2 / 3)
    assert score.orphan_refusal_rate == 0.0
    assert score.wrong_money > 0, "the orphan's credit is money that would be misposted"


def test_orphan_money_is_taken_from_the_bank_credit() -> None:
    """An orphan has no invoice, so the credit is the money at risk."""
    rows = [truth(1), orphan(9)]
    batch = make_batch(rows, amounts={"TXN9": 5_000_000})
    bogus = Prediction(Triple("", "", "TXN9"), 1.0)

    score = score_at([predict(1), bogus], batch)
    assert score.wrong_money == 5_000_000


# ---------------------------------------------------------------- money weighting


def test_money_weighted_precision_diverges_from_row_weighted() -> None:
    """The scenario the metric exists for: healthy row precision, poor money precision.

    Ninety-nine correct matches of Rs 1,000 each, and one wrong match of Rs 50,00,000.
    Row-weighted precision is 99% and looks fine. Money-weighted precision is not fine,
    because the single wrong row carries more value than all ninety-nine right ones.
    """
    rows = [truth(n) for n in range(1, 100)] + [truth(100)]
    amounts = {f"INV{n}": 100_000 for n in range(1, 100)}
    amounts["INV100"] = 500_000_000
    amounts["TXN100"] = 500_000_000
    batch = make_batch(rows, amounts)

    predictions = [predict(n) for n in range(1, 100)] + [wrong(100, 1)]
    score = score_at(predictions, batch)

    # Row-weighted looks healthy.
    assert score.precision == pytest.approx(0.99)

    # Money-weighted does not. Rs 50,00,000 wrong out of Rs 50,99,00,00 matched.
    assert score.wrong_money == 500_000_000
    assert score.money_weighted_precision < 0.85
    assert score.money_error_ratio > 0.15

    # The whole point: the two metrics disagree, and the money one is the honest one.
    assert score.precision - score.money_weighted_precision > 0.10


def test_money_error_ratio_uses_money_not_row_counts() -> None:
    rows = [truth(1), truth(2)]
    batch = make_batch(rows, amounts={"INV1": 100_000, "INV2": 900_000})

    # One of two rows wrong = 50% by row count, but INV2 is 90% of the money.
    score = score_at([predict(1), wrong(2, 1)], batch)
    assert score.money_error_ratio == pytest.approx(0.9)
    assert score.total_money == 1_000_000


def test_all_money_arithmetic_is_integer_paise() -> None:
    rows = [truth(1)]
    score = score_at([predict(1)], make_batch(rows))
    for value in (score.total_money, score.matched_money, score.wrong_money):
        assert isinstance(value, int)


def test_to_paise_is_exact_on_awkward_decimals() -> None:
    assert to_paise("0.07") == 7
    assert to_paise("1234.56") == 123456
    assert to_paise("") == 0
    assert to_paise("-12.30") == -1230


# --------------------------------------------------------------------- confusion


def test_confusion_separates_matched_missed_and_refused() -> None:
    rows = [truth(1, "clean"), truth(2, "clean"), truth(3, "fee_deducted"), orphan(9)]
    confusion = confusion_by_case_type([predict(1), predict(3)], make_batch(rows))

    assert confusion["clean"] == {"total": 2, "matched": 1, "missed": 1, "refused": 0}
    assert confusion["fee_deducted"] == {"total": 1, "matched": 1, "missed": 0, "refused": 0}
    assert confusion["orphan"] == {"total": 1, "matched": 0, "missed": 0, "refused": 1}


def test_confusion_counts_a_matched_orphan_as_matched_not_refused() -> None:
    rows = [orphan(9)]
    bogus = Prediction(Triple("INVX", "SETLX", "TXN9"), 1.0)
    confusion = confusion_by_case_type([bogus], make_batch(rows))
    assert confusion["orphan"]["matched"] == 1
    assert confusion["orphan"]["refused"] == 0


def test_confidence_outside_zero_to_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="confidence"):
        Prediction(Triple("a", "b", "c"), 1.5)
