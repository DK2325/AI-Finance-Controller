"""Turning "swap the date format" into a corruption the system can actually run.

BUILD.md wants a panel to name a corruption on the spot. That is a natural-language problem
and the obvious place for a model -- but it sits in the live demo, so it is built in the
order that makes the demo safe:

1.  **Keyword mapping, deterministic, no network.** This is the default and it always
    works. A panelist saying "swap the date format" or "make the narrations unreadable"
    gets the right corruption with no model involved.
2.  **The model, layered on top, with automatic fallback.** It interprets phrasings the
    keywords miss, and it can only ever *select* from corruptions that already exist --
    the response schema is a closed `Literal`, so the decoder cannot invent behaviour.
    Any failure falls back to the keyword result rather than to nothing.

That ordering is the same principle as the rest of the LLM layer: the model is asked for
the part that resists pattern matching, and nothing depends on it succeeding.

**Whichever path answers, the response says which one did.** A panel watching a corruption
run should be able to tell whether a model was involved, and "interpreted_by: keyword" is
a fine answer -- often the honest one.
"""

# No `from __future__ import annotations`: Literal must stay resolvable for the schema.

import re
from typing import Literal

from pydantic import Field

from core.chaos import CORRUPTIONS, ChaosSpec
from llm.schemas import Strict

# Deliberately generous. A missed keyword falls through to the model or to the default;
# a wrong one runs the wrong corruption in front of an audience.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "date_format_swap": ("date", "dates", "day", "timing", "value date", "calendar",
                         "early", "late", "shift"),
    "wrapped_utr": ("utr", "reference number", "wrap", "wrapped", "split", "line break",
                    "fixed width", "spaces in"),
    "unseen_narration": ("narration", "format", "different bank", "new bank", "another bank",
                         "unknown bank", "grammar", "layout", "unreadable", "garbled"),
    "unmodelled_fee": ("fee", "fees", "charge", "commission", "deduction", "deducted",
                       "short", "shortfall", "percentage"),
    "transliterated_counterparty": ("name", "names", "counterparty", "payer", "spelling",
                                    "transliterat", "translated", "regional", "language"),
    "truncated_narration": ("truncat", "cut", "cut off", "shorten", "clipped", "limit"),
    "currency_symbol_noise": ("currency", "symbol", "rupee", "inr", "amount in the",
                              "noise", "junk"),
    "merged_credits": ("merge", "merged", "combine", "combined", "lump", "consolidat",
                       "batch", "together", "one payment"),
}

DEFAULT_CORRUPTION = "unseen_narration"


class InterpretedChaos(Strict):
    """What the model may say. It selects; it does not invent."""

    corruptions: list[
        Literal[
            "unseen_narration",
            "wrapped_utr",
            "date_format_swap",
            "unmodelled_fee",
            "transliterated_counterparty",
            "truncated_narration",
            "currency_symbol_noise",
            "merged_credits",
        ]
    ] = Field(min_length=1, max_length=3, description="Which corruptions the request means.")
    share: float = Field(ge=0.05, le=1.0, description="Fraction of bank rows to corrupt.")
    reading: str = Field(max_length=200, description="What you understood the request to ask for.")


def by_keyword(text: str) -> list[str]:
    """Every corruption whose keywords appear. Deterministic, no network, always available."""
    lowered = (text or "").lower()
    hits = [
        name
        for name, words in KEYWORDS.items()
        if any(word in lowered for word in words)
    ]
    # Longest-keyword-first ordering would be arbitrary; declaration order is at least
    # stable and inspectable.
    return hits[:3]


def share_from_text(text: str, default: float = 0.5) -> float:
    """A percentage in the request, if one is there. "corrupt 30% of rows" should mean 30%."""
    match = re.search(r"(\d{1,3})\s*%", text or "")
    if match:
        return max(0.05, min(1.0, int(match.group(1)) / 100))
    if re.search(r"\ball\b|\bevery\b|\bwhole\b", (text or "").lower()):
        return 1.0
    return default


def interpret(text: str, use_model: bool = True) -> ChaosSpec:
    """Free text to a runnable spec. Keywords first; the model only where they found nothing.

    The model is not consulted when the keywords already matched. It cannot improve a
    correct answer, and every call is a chance to fail in front of an audience.
    """
    text = (text or "").strip()
    share = share_from_text(text)

    keyword_hits = by_keyword(text)
    if keyword_hits or not text:
        return ChaosSpec(
            corruptions=keyword_hits or [DEFAULT_CORRUPTION],
            share=share,
            interpreted_from=text,
            interpreted_by="keyword" if keyword_hits else "default",
        )

    if not use_model:
        return ChaosSpec(
            corruptions=[DEFAULT_CORRUPTION], share=share,
            interpreted_from=text, interpreted_by="default",
        )

    try:
        from llm.handler import run_job

        result = run_job("chaos", [{"id": "spec", "request": text,
                                    "options": ", ".join(sorted(CORRUPTIONS))}])
        outcome = result.outcomes[0]
        if outcome.ok and outcome.fields and outcome.fields.get("corruptions"):
            return ChaosSpec(
                corruptions=list(outcome.fields["corruptions"]),
                share=float(outcome.fields.get("share") or share),
                interpreted_from=text,
                interpreted_by="model",
            )
    except Exception:
        # Any failure -- no key, a rate limit, a malformed response -- lands on the
        # deterministic answer rather than on nothing. The demo does not depend on it.
        pass

    return ChaosSpec(
        corruptions=[DEFAULT_CORRUPTION], share=share,
        interpreted_from=text, interpreted_by="fallback",
    )
