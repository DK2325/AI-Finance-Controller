"""core/ tests. Every matching rule has a named test describing its case.

The sub-quadratic growth test is the one that matters most structurally: BUILD.md forbids
O(n^2) candidate generation outright, and the first implementation here violated it
silently while still passing the 60-second budget. A budget test alone would not have
caught it.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from core.blocking import (
    PASS_COUNTERPARTY,
    PASS_EXACT_AMOUNT,
    PASS_RATE_AMOUNT,
    PASS_UTR,
    generate_candidates,
    net_after_fee,
    plausible_credits,
)
from core.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, extract, schema, to_vector
from core.normalize import (
    counterparty_key,
    date_delta,
    extract_utrs,
    normalize_counterparty,
    normalize_narration,
    parse_date,
    to_paise,
)
from core.pipeline import load_sources, reconcile
from core.records import BankTxn, Invoice, Settlement, Sources
from core.rules import apply_rules
from core.subsetsum import MAX_BUCKET_SIZE, SubsetSumStats, find_subset, search_bucket

# --------------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("26/06/26", date(2026, 6, 26)),  # HDFC dialect
        ("26-06-2026", date(2026, 6, 26)),  # ICICI dialect
        ("2026-06-26", date(2026, 6, 26)),
        ("not a date", None),
        ("", None),
    ],
)
def test_dates_parse_without_being_told_the_dialect(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


def test_dates_are_day_first_never_month_first() -> None:
    """Guessing month-first silently shifts a transaction by months."""
    assert parse_date("03/04/26") == date(2026, 4, 3)


def test_money_parses_to_exact_paise() -> None:
    assert to_paise("1234.56") == 123456
    assert to_paise("0.07") == 7
    assert to_paise("") == 0
    assert to_paise("1,00,000.00") == 10000000


def test_counterparty_normalisation_survives_casing_and_suffix() -> None:
    variants = [
        "HALDIA GARMENTS PRIVATE LIMITED",
        "Haldia Garments Pvt Ltd",
        "haldia garments limited",
    ]
    assert len({normalize_counterparty(v) for v in variants}) == 1


def test_counterparty_key_uses_two_words_not_one_prefix() -> None:
    """A one-word prefix collapsed 2,000 names into 52 buckets and kept blocking quadratic."""
    a = counterparty_key("AMRAVATI AGRO EXPORTS PRIVATE LIMITED")
    b = counterparty_key("AMRAVATI TEXTILES PVT LTD")
    assert a != b, "two counterparties sharing a city must not share a key"
    assert a == "AMRAAGR"


def test_counterparty_key_survives_bank_truncation() -> None:
    """Statements truncate to a field width; the key must survive it."""
    assert counterparty_key("AMRAVATI AGRO EXPORTS") == counterparty_key("AMRAVATI AGR")


def test_counterparty_key_ignores_corporate_suffix_words() -> None:
    assert counterparty_key("ORION PRIVATE LIMITED") == counterparty_key("ORION LTD")


def test_utr_extraction_is_bounded_to_twelve_digits() -> None:
    assert extract_utrs("NEFT-300000000123-ACME") == ["300000000123"]
    assert extract_utrs("REF 3000000001234567") == [], "a longer number is not a UTR"
    assert extract_utrs("") == []


def test_narration_normalisation_erases_the_dialect_difference() -> None:
    hdfc = normalize_narration("NEFT-HDFC0000123-ACME RETAIL PVT-300000000001")
    icici = normalize_narration("NEFT/HDFC0000123/ACME RETAIL PVT/300000000001")
    assert hdfc == icici


def test_date_delta_is_none_when_a_side_failed_to_parse() -> None:
    assert date_delta(date(2026, 6, 3), None) is None


# ---------------------------------------------------------------------- amounts


def test_net_after_fee_matches_two_percent_plus_gst() -> None:
    """Rs 1,00,000 -> fee Rs 2,000 -> GST Rs 360 -> net Rs 97,640."""
    assert net_after_fee(10_000_000) == 9_764_000


def test_plausible_credits_covers_fee_and_every_tds_section() -> None:
    settlement = _settlement(amount=10_000_000, net=10_000_000)
    credits = plausible_credits(settlement)
    assert net_after_fee(10_000_000) in credits
    assert 10_000_000 - 1_000_000 in credits, "10% TDS must be reachable"
    assert all(value > 0 for value in credits)


# -------------------------------------------------------------------- fixtures


def _settlement(**kw) -> Settlement:
    defaults = dict(
        entity_id="pay_1",
        settlement_id="setl_1",
        utr="300000000001",
        txn_type="payment",
        invoice_id="INV1",
        amount=10_000_000,
        fee=0,
        tax=0,
        net=10_000_000,
        settled=date(2026, 6, 10),
        method="upi",
    )
    defaults.update(kw)
    return Settlement(
        entity_id=defaults["entity_id"],
        settlement_id=defaults["settlement_id"],
        utr=defaults["utr"],
        txn_type=defaults["txn_type"],
        invoice_id=defaults["invoice_id"],
        amount=defaults["amount"],
        fee=defaults["fee"],
        tax=defaults["tax"],
        net_amount=defaults["net"],
        settled_date=defaults["settled"],
        method=defaults["method"],
    )


def _txn(credit: int, narration: str, value_date: date = date(2026, 6, 10)) -> BankTxn:
    return BankTxn.from_row(
        {
            "txn_id": "TXN1",
            "credit": f"{credit / 100:.2f}",
            "debit": "",
            "value_date": value_date.isoformat(),
            "narration": narration,
            "bank": "HDFC",
            "bank_ref": "1234567890",
        }
    )


# ------------------------------------------------------------------- the rules


def test_rule_utr_amount_date_settles_a_clean_pair() -> None:
    hit = apply_rules(
        _settlement(),
        _txn(10_000_000, "NEFT-HDFC0000123-ACME-300000000001"),
        counterparty="ACME",
        narration_similarity=1.0,
    )
    assert hit is not None and hit.rule == "utr_amount_date"
    assert hit.layer == "exact"


def test_rule_utr_amount_is_what_separates_a_duplicate_utr_pair() -> None:
    """Two settlements share a UTR; only the amount tells them apart.

    The credit here matches the OTHER settlement, so the amount must stop this pair from
    scoring as an exact match.
    """
    hit = apply_rules(
        _settlement(amount=10_000_000, net=10_000_000),
        _txn(7_777_777, "NEFT-HDFC0000123-ACME-300000000001"),
        counterparty="ACME",
        narration_similarity=1.0,
    )
    assert hit is not None
    assert hit.rule == "utr_only", "a UTR match with the wrong amount must score low"


def test_rule_fee_adjusted_settles_a_fee_deducted_case() -> None:
    """Credit is the payout less gateway fee and GST on that fee."""
    settlement = _settlement(amount=10_000_000, net=10_000_000, utr="999999999999")
    hit = apply_rules(
        settlement,
        _txn(net_after_fee(10_000_000), "NEFT-HDFC0000123-SOMEONE-111111111111"),
        counterparty="ZZZZ",
        narration_similarity=0.0,
    )
    assert hit is not None and hit.rule == "fee_adjusted"


def test_rule_exact_amount_counterparty_settles_when_the_utr_is_absent() -> None:
    hit = apply_rules(
        _settlement(utr="999999999999"),
        _txn(10_000_000, "NEFT-HDFC0000123-HALDIAGARMENTS-111111111111"),
        counterparty="HALDIAGARMENTS",
        narration_similarity=0.9,
    )
    assert hit is not None and hit.rule == "exact_amount_counterparty"


def test_rule_fuzzy_never_outranks_a_deterministic_rule() -> None:
    """Architecture rule 1: nothing reaches a fuzzy rule that an exact one settles."""
    exact = apply_rules(
        _settlement(),
        _txn(10_000_000, "NEFT-ACME-300000000001"),
        counterparty="ACME",
        narration_similarity=1.0,
    )
    fuzzy = apply_rules(
        _settlement(utr="999999999999"),
        _txn(9_999_999, "NEFT-HALDIAGARMENTS-111111111111"),
        counterparty="HALDIAGARMENTS",
        narration_similarity=1.0,
    )
    assert exact is not None and fuzzy is not None
    assert exact.score > fuzzy.score


def test_rule_returns_none_when_nothing_matches() -> None:
    assert (
        apply_rules(
            _settlement(utr="999999999999"),
            _txn(1, "NEFT-UNRELATED-111111111111", date(2020, 1, 1)),
            counterparty="ZZZZZZ",
            narration_similarity=0.0,
        )
        is None
    )


def test_a_date_outside_the_window_blocks_the_dated_rules() -> None:
    hit = apply_rules(
        _settlement(utr="999999999999"),
        _txn(10_000_000, "NEFT-ACME-111111111111", date(2026, 7, 30)),
        counterparty="ACME",
        narration_similarity=1.0,
    )
    assert hit is None or hit.rule not in {"exact_amount_counterparty", "exact_amount_date"}


# ---------------------------------------------------------------- subset-sum


def test_subset_sum_finds_a_two_invoice_batch() -> None:
    assert find_subset([100, 250, 375], 350) == (0, 1)


def test_subset_sum_prefers_the_smaller_explanation() -> None:
    """A two-invoice batch is likelier than a three-invoice one when both fit."""
    assert len(find_subset([100, 200, 300, 100, 200], 300)) == 2


def test_subset_sum_tolerates_a_rounding_drift() -> None:
    assert find_subset([100, 250], 353) == (0, 1)


def test_subset_sum_respects_the_size_cap() -> None:
    """Six amounts summing to the target must not be found when the cap is five.

    Amounts are far larger than the paise tolerance, so no smaller subset can satisfy
    the target by falling inside it -- otherwise the tolerance, not the cap, decides.
    """
    assert find_subset([100_000] * 6, 600_000, max_size=5) is None
    assert find_subset([100_000] * 6, 600_000, max_size=6) is not None


def test_an_oversized_bucket_is_skipped_and_recorded_not_silently_dropped() -> None:
    """A cap that quietly swallows work would make the coverage number a lie."""
    stats = SubsetSumStats()
    amounts = list(range(1, MAX_BUCKET_SIZE + 2))
    ids = [f"pay_{i}" for i in amounts]

    result = search_bucket(amounts, sum(amounts[:3]), settlement_ids=ids, stats=stats)

    assert result is None
    assert stats.buckets_skipped == 1
    assert stats.buckets_searched == 0
    assert stats.skipped_settlement_ids == ids, "capped settlements must be reportable"


def test_a_searched_bucket_and_a_capped_bucket_are_distinguishable() -> None:
    """Both return None; only stats tell them apart, and the pipeline needs that."""
    searched = SubsetSumStats()
    search_bucket([1, 2, 3], 999_999, stats=searched)
    assert searched.buckets_searched == 1 and searched.buckets_skipped == 0


# ------------------------------------------------------------------- blocking


def _sources_for_blocking() -> Sources:
    invoice = Invoice.from_row(
        {
            "invoice_id": "INV1",
            "customer_name": "HALDIA GARMENTS PRIVATE LIMITED",
            "amount": "100000.00",
            "invoice_date": "2026-06-10",
            "tds_section": "",
            "status": "paid",
        }
    )
    settlement = _settlement(net=10_000_000)
    return Sources(invoices=[invoice], settlements=[settlement], bank=[])


def test_pass_utr_fires_when_the_narration_carries_the_utr() -> None:
    sources = _sources_for_blocking()
    sources = Sources(
        sources.invoices,
        sources.settlements,
        [_txn(1, "NEFT-HDFC0000123-SOMEONE-300000000001")],
    )
    candidates, stats = generate_candidates(sources)
    assert stats.per_pass[PASS_UTR] == 1
    assert PASS_UTR in candidates[0].passes


def test_pass_exact_amount_fires_on_an_identical_credit() -> None:
    sources = _sources_for_blocking()
    sources = Sources(sources.invoices, sources.settlements, [_txn(10_000_000, "NEFT-X-1")])
    _, stats = generate_candidates(sources)
    assert stats.per_pass[PASS_EXACT_AMOUNT] == 1


def test_pass_rate_amount_catches_a_fee_shifted_credit() -> None:
    """This is the pass that exists because an exact-amount key misses fee_deducted."""
    sources = _sources_for_blocking()
    sources = Sources(
        sources.invoices, sources.settlements, [_txn(net_after_fee(10_000_000), "NEFT-X-1")]
    )
    _, stats = generate_candidates(sources)
    assert stats.per_pass[PASS_RATE_AMOUNT] >= 1


def test_pass_counterparty_catches_a_credit_matching_no_amount() -> None:
    """The only pass that can reach a batched settlement."""
    sources = _sources_for_blocking()
    sources = Sources(
        sources.invoices,
        sources.settlements,
        [_txn(4_242_424, "NEFT HDFC0000123 HALDIA GARMENTS PVT 111111111111")],
    )
    _, stats = generate_candidates(sources)
    assert stats.per_pass[PASS_COUNTERPARTY] >= 1


def test_candidate_generation_is_sub_quadratic() -> None:
    """BUILD.md forbids O(n^2) candidate generation. This asserts it rather than hoping.

    The first implementation grew at exponent 1.93 -- effectively quadratic -- while
    still passing the 60-second budget, so a timing test alone would have missed it.
    """
    from datagen.generator import generate

    counts = []
    for rows in (400, 2000):
        emission, _ = generate(rows=rows, seed=5)
        sources = Sources(
            invoices=[Invoice.from_row(r) for r in emission.invoices],
            settlements=[Settlement.from_row(r) for r in emission.gateway],
            bank=[BankTxn.from_row(r) for r in emission.bank],
        )
        _, stats = generate_candidates(sources)
        counts.append((stats.n_settlements, stats.unique_candidates))

    (n0, c0), (n1, c1) = counts
    exponent = math.log(c1 / c0) / math.log(n1 / n0)
    assert exponent < 1.6, (
        f"candidate growth exponent {exponent:.2f} is too close to quadratic "
        f"({n0} rows -> {c0} candidates, {n1} rows -> {c1} candidates)"
    )


# -------------------------------------------------------------------- features


def test_feature_vector_matches_the_declared_schema() -> None:
    features = extract(
        _settlement(),
        _txn(10_000_000, "NEFT-ACME-300000000001"),
        counterparty="ACME",
        passes=frozenset({PASS_UTR}),
        frequencies={"ACME": 3},
    )
    assert set(features) == set(FEATURE_NAMES)
    assert len(to_vector(features)) == len(FEATURE_NAMES)


def test_no_feature_is_ever_none() -> None:
    """A None reaching a gradient-boosted model as NaN behaves differently from a
    sentinel, and the difference is invisible in the metrics."""
    features = extract(
        _settlement(settled=None),
        _txn(1, "x"),
        counterparty="",
        passes=frozenset(),
        frequencies={},
    )
    assert all(value is not None for value in features.values())


def test_feature_schema_is_versioned_for_the_model_artifact() -> None:
    """Phase 4 stamps this into the artifact; Phase 7 refuses a mismatch."""
    contract = schema()
    assert contract["version"] == FEATURE_SCHEMA_VERSION
    assert contract["n_features"] == len(FEATURE_NAMES)
    assert contract["names"] == list(FEATURE_NAMES)


def test_feature_order_is_stable() -> None:
    """Reordering silently feeds the model different columns than it trained on."""
    assert FEATURE_NAMES[0] == "amount_delta_abs"
    assert FEATURE_NAMES[-1] == "credit_is_larger"


def test_fee_and_tds_features_fire_on_the_right_cases() -> None:
    settlement = _settlement(amount=10_000_000, net=10_000_000)
    fee_case = extract(
        settlement,
        _txn(net_after_fee(10_000_000), "x"),
        counterparty="",
        passes=frozenset(),
        frequencies={},
    )
    assert fee_case["delta_matches_fee"] == 1.0

    tds_case = extract(
        settlement,
        _txn(9_000_000, "x"),
        counterparty="",
        passes=frozenset(),
        frequencies={},
    )
    assert tds_case["delta_matches_tds"] == 1.0, "10% TDS must be recognised"


# -------------------------------------------------------------------- pipeline


def test_pipeline_runs_end_to_end_and_reports_its_own_timing() -> None:
    result = reconcile("data/demo")
    assert result.matches
    meta = result.meta()
    assert meta["timing"]["rows"] > 0
    assert meta["timing"]["rows_per_second"] > 0
    assert set(meta["timing"]["seconds_by_stage"]) >= {"blocking", "rules", "load"}


def test_pipeline_reports_per_pass_candidate_counts() -> None:
    """An aggregate hides the case where one pass produces most of the work."""
    result = reconcile("data/demo")
    per_pass = result.blocking["per_pass"]
    assert set(per_pass) == {
        PASS_UTR,
        PASS_EXACT_AMOUNT,
        PASS_RATE_AMOUNT,
        PASS_COUNTERPARTY,
    }


def test_every_run_is_stamped_uncalibrated() -> None:
    """Rule scores are ranked tiers. A Phase 3 curve must never pass as a Phase 4 one."""
    meta = reconcile("data/demo").meta()
    assert meta["calibrated"] is False
    assert "calibrated probabilities" in meta["calibration_note"]


def test_a_settlement_is_never_matched_to_two_transactions() -> None:
    """A payout happens once. Double-assigning it would post the money twice."""
    result = reconcile("data/demo")
    seen = [m.settlement_id + "|" + m.invoice_id for m in result.matches]
    assert len(seen) == len(set(seen))


def test_load_sources_never_reads_the_answer_key(tmp_path) -> None:
    """load_sources reads exactly three files. A missing truth.csv must not matter."""
    import shutil

    for name in ("gateway_settlements.csv", "bank_statement.csv", "invoice_ledger.csv"):
        shutil.copy(f"data/demo/{name}", tmp_path / name)

    sources = load_sources(tmp_path)
    assert sources.settlements and sources.bank and sources.invoices
