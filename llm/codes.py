"""The fixed reason-code enum. Every way this system can decline to auto-match.

BUILD.md Phase 5 asks for a *fixed* enum, and fixed is the operative word. These codes
are written into append-only audit records, so a code is not a label that can be tidied
up later -- renaming one silently rewrites the meaning of every historical record that
carries it, and deleting one makes those records unreadable. `tests/test_reason_codes.py`
holds a frozen snapshot and fails on a rename or a removal. Additions are allowed.

Three families, and they are kept permanently distinct because they demand different
responses from whoever is on the other end:

    DETERMINISTIC   The pipeline already knows why. No LLM call is made, and none is
                    needed -- "no candidate existed" is not a judgement, it is a fact
                    about the data. Architecture rule 1: the cheap deterministic layer
                    answers what it can before the expensive one is asked anything.

    JUDGEMENT       A candidate exists and the system declined it on confidence grounds.
                    This is the residue the LLM is asked to *explain* -- never to decide.
                    Architecture rule 2.

    FAILURE         The exception path itself broke. A 429 means retry later; a malformed
                    response means the prompt or the model changed under us; a provenance
                    failure means the model attributed a field to the wrong row. Collapsing
                    these into one "llm_error" code would hide the only one of the three
                    that is a correctness problem rather than an operations problem.

WHERE THIS LIVES, AND WHY IT IMPORTS NOTHING

Phase 5's scope is `llm/`, so the enum belongs here. But `core/` and `model/` raise
exceptions too, and they must name them from the same vocabulary -- two vocabularies held
in sync by hand is how audit trails start disagreeing with themselves.

That makes `core` import from `llm`, which inverts the layer order, so the inversion is
made harmless rather than merely tolerated: this module imports **stdlib only**, and
`tests/test_reason_codes.py` fails the build if that ever stops being true. A reviewer
following `core/pipeline.py` into this file finds an enum and no machinery. Nothing here
can reach a network, and `llm/__init__.py` is deliberately kept empty so importing this
module cannot drag a provider SDK in behind it.
"""

from __future__ import annotations

from enum import StrEnum

# The `reason_code` column in ledgerloop/models.py is String(64). A code that does not
# fit is a runtime insert failure on the one record you most needed to keep.
MAX_CODE_LENGTH = 64


class Family(StrEnum):
    """What kind of thing went wrong, which decides who has to do something about it."""

    DETERMINISTIC = "deterministic"
    JUDGEMENT = "judgement"
    FAILURE = "failure"


class ReasonCode(StrEnum):
    """Every reason a settlement can fail to be auto-matched.

    A StrEnum, so the member is its own wire format: it can be written to a CSV, a JSONB
    column or a String(64) with no conversion step, and no conversion step means no place
    for the code and its stored form to drift apart.
    """

    # Bare annotations. EnumMeta ignores these -- they type the attributes __new__ sets.
    family: Family
    description: str

    def __new__(cls, code: str, family: Family, description: str) -> ReasonCode:
        member = str.__new__(cls, code)
        member._value_ = code
        member.family = family
        member.description = description
        return member

    # ----------------------------------------------------------- deterministic
    # The pipeline knows the answer. No model is consulted for any of these.

    NO_CANDIDATE = (
        "NO_CANDIDATE",
        Family.DETERMINISTIC,
        "Blocking produced no candidate transaction for this settlement. No credit in "
        "the bank statement resembles this payout on any of the four passes, so there "
        "is nothing to judge -- the money has not arrived, or it arrived looking like "
        "nothing we generate keys for.",
    )

    NO_INVOICE_LINK = (
        "NO_INVOICE_LINK",
        Family.DETERMINISTIC,
        "A bank transaction was matched, but no invoice could be identified for it. "
        "Only ~38% of gateway rows carry order_receipt and the narration did not name "
        "one either. Emitting the pair without the invoice would be a match with the "
        "money attached to nothing.",
    )

    SUBSET_SEARCH_CAPPED = (
        "SUBSET_SEARCH_CAPPED",
        Family.DETERMINISTIC,
        "This settlement sits in a batch bucket larger than the subset-sum search cap, "
        "so the search was not attempted. The settlement may well be reconcilable; we "
        "declined to spend the time finding out, and say so rather than counting it as "
        "unmatchable.",
    )

    INVOICE_ALREADY_CLAIMED = (
        "INVOICE_ALREADY_CLAIMED",
        Family.DETERMINISTIC,
        "The best candidate's invoice was already consumed by a higher-scoring "
        "settlement. An invoice is paid once, so the second claim is refused rather "
        "than posted -- allowing both would pay the same invoice twice.",
    )

    # --------------------------------------------------------------- judgement
    # A candidate exists. The system declined it, and the LLM is asked to explain why in
    # language a finance operator can act on. It is never asked whether to accept it.

    BELOW_THRESHOLD = (
        "BELOW_THRESHOLD",
        Family.JUDGEMENT,
        "The best candidate scored a calibrated probability below the operating point. "
        "This is the selective-prediction abstention: the model has an opinion and the "
        "opinion is not confident enough to post money on.",
    )

    LOW_CONFIDENCE = (
        "LOW_CONFIDENCE",
        Family.JUDGEMENT,
        "The best candidate scored weakly under rule tiers with no calibrated model in "
        "play. Kept distinct from BELOW_THRESHOLD because a rule tier is a rank, not a "
        "probability -- reporting the two as one code would let an uncalibrated run be "
        "read as a calibrated one.",
    )

    AMBIGUOUS_CANDIDATES = (
        "AMBIGUOUS_CANDIDATES",
        Family.JUDGEMENT,
        "Two or more candidates scored too close together to separate. The top score "
        "may be above threshold; that is not sufficient, because being confident in the "
        "wrong one of two near-identical options is exactly how a false auto-match "
        "happens.",
    )

    # ----------------------------------------------------------------- failure
    # The exception path itself broke. Each of these needs a different response.

    LLM_MALFORMED_RESPONSE = (
        "LLM_MALFORMED_RESPONSE",
        Family.FAILURE,
        "The provider returned something that is not JSON. Under json_schema strict "
        "decoding this should be unreachable; if it fires, the constraint is not being "
        "applied and every extraction in the run is suspect.",
    )

    LLM_SCHEMA_INVALID = (
        "LLM_SCHEMA_INVALID",
        Family.FAILURE,
        "Valid JSON that failed schema validation, and failed it again on the single "
        "retry. Never parsed as free text -- the exception is the correct outcome.",
    )

    LLM_BATCH_MISMATCH = (
        "LLM_BATCH_MISMATCH",
        Family.FAILURE,
        "The batch envelope did not reconcile: an item we sent came back with no entry, "
        "or came back twice, or an entry arrived for an id we never sent. Kept apart "
        "from LLM_SCHEMA_INVALID because the remedy is different -- a smaller batch, "
        "rather than a changed prompt or schema.",
    )

    LLM_RATE_LIMITED = (
        "LLM_RATE_LIMITED",
        Family.FAILURE,
        "Rate limited after backoff. An operations condition, not a data condition: the "
        "exception is re-runnable and the underlying settlement may be perfectly "
        "matchable once capacity is there.",
    )

    LLM_TRANSPORT_FAILED = (
        "LLM_TRANSPORT_FAILED",
        Family.FAILURE,
        "The call did not complete -- network, timeout, or a provider 5xx. Also "
        "re-runnable, and kept apart from rate limiting so an outage is not read as "
        "our own throughput problem.",
    )

    FIELD_PROVENANCE_FAILED = (
        "FIELD_PROVENANCE_FAILED",
        Family.FAILURE,
        "An extracted field could not be found in its own source narration. Measured at "
        "roughly 1 in 200 when batching, with the response structurally perfect: every "
        "id echoed, order stable, and a field belonging to a different item. The field "
        "is discarded and never reaches the ledger.",
    )

    LOW_PARSE_CONFIDENCE = (
        "LOW_PARSE_CONFIDENCE",
        Family.FAILURE,
        "The parse returned below the confidence floor. The model saying it is unsure "
        "is information worth keeping, and worth keeping separate from a parse that was "
        "confidently wrong.",
    )


ALL_CODES: tuple[ReasonCode, ...] = tuple(ReasonCode)

DETERMINISTIC_CODES: tuple[ReasonCode, ...] = tuple(
    c for c in ReasonCode if c.family is Family.DETERMINISTIC
)
JUDGEMENT_CODES: tuple[ReasonCode, ...] = tuple(
    c for c in ReasonCode if c.family is Family.JUDGEMENT
)
FAILURE_CODES: tuple[ReasonCode, ...] = tuple(
    c for c in ReasonCode if c.family is Family.FAILURE
)


def needs_llm(code: ReasonCode) -> bool:
    """Whether this exception is one the LLM is asked to write a reason for.

    The lever the whole Phase 5 cost model rests on. Every code this returns False for is
    an exception explained without spending a token, which is architecture rule 1 applied
    to inference cost rather than to accuracy.
    """
    return code.family is Family.JUDGEMENT


def is_failure(code: ReasonCode) -> bool:
    """Whether this exception represents the system breaking rather than the system judging.

    Reported separately in every summary. A run with 300 abstentions is working; a run
    with 300 transport failures is not, and one number covering both says neither.
    """
    return code.family is Family.FAILURE


def describe(code: ReasonCode) -> str:
    """The documented meaning, for the operator-facing queue and for `ledgerloop eval`."""
    return code.description
