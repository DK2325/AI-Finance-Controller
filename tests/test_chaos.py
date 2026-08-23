"""Chaos mode, where a graceful failure is the pass condition.

The success criterion here is inverted from everywhere else in the project:

    coverage collapsing while precision holds   -> PASS
    coverage holding while precision collapses  -> the only failure that matters

Because the second is money posted against the wrong invoice with no warning, and the
first is the system doing exactly what it claims: routing what it does not understand to a
human instead of guessing.
"""

from __future__ import annotations

import pytest

from core.chaos import CORRUPTIONS, ChaosSpec, apply_chaos
from core.pipeline import load_sources
from llm.chaos_spec import DEFAULT_CORRUPTION, by_keyword, interpret, share_from_text

BATCH = "data/demo"


@pytest.fixture(scope="module")
def clean():
    return load_sources(BATCH)


# ------------------------------------------------------- free-text interpretation


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("swap the date format", "date_format_swap"),
        ("a bank that deducts an extra fee", "unmodelled_fee"),
        ("split the UTRs across lines", "wrapped_utr"),
        ("truncate the narrations", "truncated_narration"),
        ("merge two payments into one", "merged_credits"),
        ("transliterate the payer names", "transliterated_counterparty"),
    ],
)
def test_a_panel_can_name_a_corruption_without_a_model(phrase: str, expected: str) -> None:
    """The deterministic path must carry the demo on its own.

    The LLM interpreter is layered on top with automatic fallback, but it sits in a live
    demonstration -- so the keyword mapping is what actually has to work, and it is tested
    without a model anywhere near it.
    """
    spec = interpret(phrase, use_model=False)
    assert expected in spec.corruptions
    assert spec.interpreted_by == "keyword"


def test_an_unrecognised_request_still_runs_something() -> None:
    """A panel naming something we cannot parse should see a corruption, not an error."""
    spec = interpret("something nobody has ever thought of", use_model=False)
    assert spec.corruptions == [DEFAULT_CORRUPTION]
    assert spec.interpreted_by == "default"


def test_the_response_says_which_path_interpreted_it() -> None:
    """A panel should be able to tell whether a model was involved. "keyword" is a fine
    answer and often the honest one."""
    assert interpret("swap the dates", use_model=False).interpreted_by == "keyword"
    assert interpret("", use_model=False).interpreted_by == "default"


@pytest.mark.parametrize(
    "phrase,share",
    [("corrupt 30% of rows", 0.3), ("break all of them", 1.0), ("every row", 1.0),
     ("just some rows", 0.5)],
)
def test_a_proportion_in_the_request_is_honoured(phrase: str, share: float) -> None:
    assert share_from_text(phrase) == share


def test_the_model_is_not_consulted_when_keywords_already_matched() -> None:
    """It cannot improve a correct answer, and every call is a chance to fail live."""
    assert by_keyword("swap the date format")
    spec = interpret("swap the date format", use_model=True)
    assert spec.interpreted_by == "keyword"


# ------------------------------------------------------------- the corruptions


def test_every_corruption_actually_changes_rows(clean) -> None:
    """A corruption that silently did nothing would make the system look robust when it
    was never tested -- the same failure as a test that cannot fail."""
    for name in CORRUPTIONS:
        _, results = apply_chaos(clean, ChaosSpec(corruptions=[name], share=1.0))
        assert results[0].rows_touched > 0, f"{name} touched no rows"


def test_corruption_does_not_mutate_the_original_batch(clean) -> None:
    """BankTxn is frozen and the batch on disk is never written to."""
    before = [t.narration for t in clean.bank]
    apply_chaos(clean, ChaosSpec(corruptions=list(CORRUPTIONS), share=1.0))
    assert [t.narration for t in clean.bank] == before


def test_derived_fields_are_recomputed_from_the_corrupted_text(clean) -> None:
    """Carrying the clean row's extracted UTRs and tokens across would let the matcher
    succeed on evidence the corrupted row no longer contains -- which would make chaos
    look survivable when it was never applied."""
    corrupted, _ = apply_chaos(clean, ChaosSpec(corruptions=["wrapped_utr"], share=1.0))

    changed = [
        (before, after)
        for before, after in zip(clean.bank, corrupted.bank, strict=True)
        if before.narration != after.narration
    ]
    assert changed, "wrapped_utr changed nothing"

    wrapped = [(b, a) for b, a in changed if b.utrs]
    assert wrapped, "no row with a UTR was wrapped"
    for before, after in wrapped:
        assert after.utrs != before.utrs, (
            "the corrupted row still reports the clean row's UTRs; derived fields were "
            "carried over rather than recomputed"
        )


def test_only_the_bank_side_is_corrupted(clean) -> None:
    """A merchant controls their ledger and their PSP's export. The bank statement is the
    one that arrives in whatever shape a third party chose."""
    corrupted, _ = apply_chaos(clean, ChaosSpec(corruptions=list(CORRUPTIONS), share=1.0))
    assert corrupted.invoices == clean.invoices
    assert corrupted.settlements == clean.settlements


def test_the_same_seed_produces_the_same_corruption(clean) -> None:
    spec = ChaosSpec(corruptions=["merged_credits"], share=0.5, seed=11)
    first, _ = apply_chaos(clean, spec)
    second, _ = apply_chaos(clean, spec)
    assert [t.credit for t in first.bank] == [t.credit for t in second.bank]


def test_an_unknown_corruption_name_is_skipped_rather_than_crashing(clean) -> None:
    corrupted, results = apply_chaos(clean, ChaosSpec(corruptions=["not_a_corruption"]))
    assert results == []
    assert len(corrupted.bank) == len(clean.bank)


def test_share_controls_how_much_is_touched(clean) -> None:
    _, few = apply_chaos(clean, ChaosSpec(corruptions=["truncated_narration"], share=0.1))
    _, many = apply_chaos(clean, ChaosSpec(corruptions=["truncated_narration"], share=0.9))
    assert few[0].rows_touched < many[0].rows_touched


def test_every_corruption_says_what_it_breaks() -> None:
    """The screen shows this beside the result. "unseen_narration" alone tells a viewer
    nothing about why coverage moved."""
    for name, (_, description, breaks) in CORRUPTIONS.items():
        assert description and breaks, name
        assert len(breaks) > 20, f"{name} does not explain what it breaks"
