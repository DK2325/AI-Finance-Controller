"""The invariant, the precedence, and the one shape every audit record takes.

THE INVARIANT, AS THREE TESTS AND NOT ONE

    matched + exceptions == every settlement, exactly once each

Written as three separate assertions -- no gaps, no double counting, nothing invented --
because a gap and a double count are different bugs and a single `len(a) + len(b) == n`
can be satisfied by one of each cancelling out. A test that both bugs can pass is a test
neither is checked by.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest

from core.exceptions import (
    AMBIGUITY_BAND,
    EnumerationResult,
    ExceptionRecord,
    SettlementEvidence,
    classify,
    enumerate_exceptions,
)
from ledgerloop.audit import (
    DECISION_EXCEPTION,
    DECISION_MATCHED,
    LAYER_LLM,
    LAYER_MODEL,
    AuditRecord,
    row_hash,
)
from llm.codes import DETERMINISTIC_CODES, ReasonCode, needs_llm


def evidence(**kwargs) -> SettlementEvidence:
    return SettlementEvidence(**{"entity_id": "S1", **kwargs})


# ------------------------------------------------------------------ precedence


def test_a_capped_bucket_outranks_everything() -> None:
    """Nothing after the cap was attempted, so nothing after it can be the reason."""
    code, _ = classify(evidence(capped=True, n_candidates=5, best_score=0.9))
    assert code is ReasonCode.SUBSET_SEARCH_CAPPED


def test_no_candidate_when_blocking_found_nothing() -> None:
    code, detail = classify(evidence(n_candidates=0))
    assert code is ReasonCode.NO_CANDIDATE
    assert "no bank credit" in detail


def test_a_settlement_with_no_evidence_row_still_gets_a_code() -> None:
    """The rows most easily lost: blocking produced nothing, so no loop over candidates
    would ever reach them. Their absence would flatter every coverage number."""
    result = enumerate_exceptions(["S1", "S2"], set(), {})
    assert {r.reason_code for r in result.exceptions} == {ReasonCode.NO_CANDIDATE}


def test_a_claimed_invoice_outranks_a_low_score() -> None:
    """A structural refusal, not a confidence judgement -- an invoice is paid once."""
    code, detail = classify(
        evidence(n_candidates=3, best_score=0.1, best_invoice_id="INV1", invoice_claimed_by="S9")
    )
    assert code is ReasonCode.INVOICE_ALREADY_CLAIMED
    assert "S9" in detail


def test_no_invoice_link_when_a_transaction_fits_but_no_invoice_does() -> None:
    code, _ = classify(evidence(n_candidates=2, best_score=0.99, best_invoice_id=""))
    assert code is ReasonCode.NO_INVOICE_LINK


def test_ambiguity_outranks_the_threshold() -> None:
    """A near-tie whose top score CLEARS the threshold is still not safe to post.

    Reporting it as BELOW_THRESHOLD would be false -- it was not below the threshold.
    """
    code, detail = classify(
        evidence(n_candidates=2, best_score=0.999, second_score=0.998, best_invoice_id="INV1"),
        threshold=0.99,
        calibrated=True,
    )
    assert code is ReasonCode.AMBIGUOUS_CANDIDATES
    assert "0.9990" in detail and "0.9980" in detail


def test_a_clear_winner_below_the_threshold_is_an_abstention() -> None:
    code, detail = classify(
        evidence(n_candidates=2, best_score=0.7, second_score=0.1, best_invoice_id="INV1"),
        threshold=0.99,
        calibrated=True,
    )
    assert code is ReasonCode.BELOW_THRESHOLD
    assert "0.9900" in detail


def test_rule_tiers_and_calibrated_probabilities_get_different_codes() -> None:
    """One code for both would let an uncalibrated run be read as a calibrated one."""
    weak = evidence(n_candidates=2, best_score=0.4, second_score=0.1, best_invoice_id="INV1")
    assert classify(weak, calibrated=True)[0] is ReasonCode.BELOW_THRESHOLD
    assert classify(weak, calibrated=False)[0] is ReasonCode.LOW_CONFIDENCE
    assert "ranks" in classify(weak, calibrated=False)[1]


def test_a_single_candidate_is_never_ambiguous() -> None:
    """Ambiguity needs something to be ambiguous with."""
    lone = evidence(n_candidates=1, best_score=0.5, second_score=0.0, best_invoice_id="INV1")
    assert not lone.is_ambiguous
    assert classify(lone, calibrated=True)[0] is ReasonCode.BELOW_THRESHOLD


@pytest.mark.parametrize(
    "margin,ambiguous", [(0.0, True), (AMBIGUITY_BAND / 2, True), (AMBIGUITY_BAND * 2, False)]
)
def test_the_ambiguity_band_is_where_it_says_it_is(margin: float, ambiguous: bool) -> None:
    row = evidence(n_candidates=2, best_score=0.9, second_score=0.9 - margin,
                   best_invoice_id="INV1")
    assert row.is_ambiguous is ambiguous


# ------------------------------------------------------------------ the invariant


ALL = [f"S{i}" for i in range(10)]
MATCHED = {"S0", "S1", "S2"}
EVIDENCE = {
    "S3": SettlementEvidence("S3", n_candidates=2, best_score=0.5, second_score=0.1,
                             best_invoice_id="INV3"),
    "S4": SettlementEvidence("S4", n_candidates=1, best_score=0.2, best_invoice_id=""),
    "S5": SettlementEvidence("S5", capped=True),
}


def result() -> EnumerationResult:
    return enumerate_exceptions(ALL, MATCHED, EVIDENCE, threshold=0.9, calibrated=True)


def test_no_settlement_is_left_out() -> None:
    """A gap. Every settlement is either matched or carries an exception."""
    covered = result().matched_entity_ids | {r.entity_id for r in result().exceptions}
    missing = set(ALL) - covered
    assert not missing, f"settlements accounted for nowhere: {sorted(missing)}"


def test_no_settlement_is_counted_twice() -> None:
    """A double count, which the arithmetic check above cannot see on its own.

    A gap and a double count can cancel: nine settlements covered with one duplicated
    still sums to ten. This is why the invariant is three tests.
    """
    counts = Counter(r.entity_id for r in result().exceptions)
    duplicated = [k for k, v in counts.items() if v > 1]
    assert not duplicated, f"more than one reason code for: {duplicated}"

    both = result().matched_entity_ids & {r.entity_id for r in result().exceptions}
    assert not both, f"matched AND excepted: {sorted(both)}"


def test_no_exception_exists_without_a_settlement() -> None:
    """The other direction. An exception for a settlement that is not in the batch is an
    exception about nothing, and it would inflate the denominator of every rate."""
    invented = {r.entity_id for r in result().exceptions} - set(ALL)
    assert not invented, f"exceptions for settlements that do not exist: {sorted(invented)}"


def test_the_counts_reconcile() -> None:
    outcome = result()
    assert len(outcome.matched_entity_ids) + len(outcome.exceptions) == len(ALL)
    assert outcome.n_settlements == len(ALL)


def test_a_matched_settlement_never_gets_an_exception() -> None:
    outcome = enumerate_exceptions(ALL, set(ALL), EVIDENCE)
    assert outcome.exceptions == []


def test_every_exception_carries_a_code_from_the_enum() -> None:
    for record in result().exceptions:
        assert isinstance(record.reason_code, ReasonCode)
        assert record.detail, f"{record.entity_id} has a code and no explanation"


# ------------------------------------------------------- the deterministic share


def test_the_deterministic_share_counts_only_codes_that_skip_the_model() -> None:
    """The number the batching design, the rate limiter and the run-time estimate rest on."""
    outcome = EnumerationResult(
        exceptions=[
            ExceptionRecord("S1", ReasonCode.NO_CANDIDATE),
            ExceptionRecord("S2", ReasonCode.NO_INVOICE_LINK),
            ExceptionRecord("S3", ReasonCode.INVOICE_ALREADY_CLAIMED),
            ExceptionRecord("S4", ReasonCode.BELOW_THRESHOLD),
        ]
    )
    assert outcome.deterministic_share() == 0.75
    assert outcome.as_dict()["llm_bound_exceptions"] == 1


def test_every_deterministic_code_really_skips_the_llm() -> None:
    for code in DETERMINISTIC_CODES:
        assert not needs_llm(code), f"{code} is filed deterministic but would be sent to a model"


def test_an_empty_run_has_no_share_rather_than_a_divide_by_zero() -> None:
    assert EnumerationResult().deterministic_share() == 0.0


# ---------------------------------------------------------------- audit records


def deterministic_record() -> AuditRecord:
    return AuditRecord(
        run_id="r1", layer=LAYER_MODEL, decision=DECISION_MATCHED,
        entity_id="S1", invoice_id="INV1", txn_id="T1",
        input_row_hashes={"settlement": row_hash("S1")},
        confidence=0.999, calibrated=True, model_version="v1",
    )


def llm_record() -> AuditRecord:
    return AuditRecord(
        run_id="r1", layer=LAYER_LLM, decision=DECISION_EXCEPTION,
        entity_id="S2", reason_code=ReasonCode.BELOW_THRESHOLD, reason_detail="0.71",
        provider="nvidia", model_name="nemotron", prompt_version="parse.v5+abc",
        input_tokens=1500, output_tokens=800, token_cost_inr=0.0123,
    )


def test_a_deterministic_record_and_an_llm_record_have_the_same_shape() -> None:
    """The requirement: indistinguishable in structure, distinguishable only by content.

    A narrow row for cheap layers and a wide one for expensive layers means every query
    spanning layers grows a branch -- and the first query nobody writes is the one that
    would have found the problem.
    """
    assert set(deterministic_record().as_row()) == set(llm_record().as_row())


def test_a_deterministic_record_reports_zero_cost_rather_than_no_cost() -> None:
    """Zero is a measurement. A missing field is not, and cannot be summed."""
    row = deterministic_record().as_row()
    assert row["input_tokens"] == 0 and row["output_tokens"] == 0
    assert row["prompt_version"] is None
    assert row["cache_hit"] is False


def test_an_exception_without_a_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="no reason code"):
        AuditRecord(run_id="r", layer=LAYER_MODEL, decision=DECISION_EXCEPTION, entity_id="S1")


def test_a_match_carrying_a_reason_code_is_refused() -> None:
    """A match is not an exception and must not look like one in the trail."""
    with pytest.raises(ValueError, match="is not an exception"):
        AuditRecord(
            run_id="r", layer=LAYER_MODEL, decision=DECISION_MATCHED, entity_id="S1",
            reason_code=ReasonCode.BELOW_THRESHOLD,
        )


def test_an_unknown_layer_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown layer"):
        AuditRecord(run_id="r", layer="vibes", decision=DECISION_MATCHED, entity_id="S1")


def test_a_record_cannot_be_edited() -> None:
    """An audit record that can be edited is not an audit record."""
    record = deterministic_record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.entity_id = "S99"


def test_the_confidence_says_whether_it_is_calibrated() -> None:
    """A rule tier and a calibrated probability are both numbers in [0, 1] and mean
    entirely different things."""
    assert deterministic_record().as_row()["calibrated"] is True
    assert llm_record().as_row()["calibrated"] is False


def test_row_hashes_are_stable_and_distinct() -> None:
    assert row_hash("S1") == row_hash("S1")
    assert row_hash("S1") != row_hash("S2")
    # A separator, so ("ab", "c") and ("a", "bc") are not the same row.
    assert row_hash("ab", "c") != row_hash("a", "bc")


def test_every_record_is_timestamped_without_being_told_to() -> None:
    assert deterministic_record().created_at
