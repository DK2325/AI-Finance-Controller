"""Pydantic schemas for every LLM response. Nothing is ever parsed from free text.

Three jobs, three schemas, all batched. Design rules that apply to all of them:

**Every field is required, and optional ones are nullable rather than defaulted.** A field
with a default can be omitted, and an omitted field is indistinguishable from one the
model forgot. Forcing an explicit `null` makes "I found no UTR" a claim the model has to
make rather than an absence we have to interpret.

**`extra="forbid"`**, which makes Pydantic emit `additionalProperties: false`. Under
`json_schema` strict decoding that is a decode-time constraint, not a post-hoc check: the
model cannot emit a field we did not ask for.

**Every constrained vocabulary is a `Literal`.** GL account codes, payment methods, reason
codes, suggested actions. This is the single most valuable thing the schema does, because
it turns a business rule into something the decoder physically cannot violate. A model
free-texting a GL code produces a journal entry that looks right and posts to an account
that does not exist.

**Money is integer paise.** Never float, never a formatted string. `NUMERIC(14,2)` in the
database, `int` here, and no arithmetic anywhere between the two.
"""

# No `from __future__ import annotations`: it turns annotations into strings that Pydantic
# cannot resolve for Literal, and generating JSON Schema from these models is the whole
# point of the file. Python 3.12 handles `str | None` natively regardless.

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from llm.codes import JUDGEMENT_CODES

# --------------------------------------------------------------- shared vocabularies

PaymentMethod = Literal["upi", "neft", "imps", "rtgs", "card", "ach", "unknown"]

# The chart of accounts. Deliberately small and closed.
#
# A proposed journal entry against an invented GL code is worse than no proposal at all:
# it reads as authoritative, and the error surfaces at the point someone posts it. Making
# this a Literal means the constrained decoder cannot produce a code outside the list --
# the model is not trusted to stay inside the chart, it is prevented from leaving it.
GLCode = Literal[
    "1100",  # Bank - Current Account
    "1200",  # Trade Receivables
    "1310",  # Payment Gateway Receivable (settlement suspense)
    "1450",  # Input GST Credit - on gateway fees
    "1460",  # TDS Receivable - deducted by customer u/s 194H / 194J
    "2100",  # Trade Payables
    "2310",  # GST Payable
    "4000",  # Revenue
    "5300",  # Payment Gateway Fees
    "9999",  # Suspense - Unreconciled
]

GL_ACCOUNT_NAMES: dict[str, str] = {
    "1100": "Bank - Current Account",
    "1200": "Trade Receivables",
    "1310": "Payment Gateway Receivable",
    "1450": "Input GST Credit",
    "1460": "TDS Receivable",
    "2100": "Trade Payables",
    "2310": "GST Payable",
    "4000": "Revenue",
    "5300": "Payment Gateway Fees",
    "9999": "Suspense - Unreconciled",
}

# The model may only tag an exception with a code from the JUDGEMENT family. It must not
# be able to declare NO_CANDIDATE -- that is a fact about the data the pipeline already
# established -- nor any FAILURE code, which describes the machinery rather than the row.
# Built from the enum so the two cannot drift; tests/test_prompts.py asserts they agree.
LlmReasonCode = Literal[
    "BELOW_THRESHOLD",
    "LOW_CONFIDENCE",
    "AMBIGUOUS_CANDIDATES",
]

SuggestedAction = Literal[
    "review_manually",
    "request_remittance_advice",
    "check_tds_certificate",
    "check_for_batched_payout",
    "confirm_bank_charges",
    "write_off_rounding",
    "no_action_possible",
]


class Strict(BaseModel):
    """Base for every response model. Forbids anything we did not ask for."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------ job 1: parse a narration


class ParsedNarration(Strict):
    """Fields pulled out of one pathological bank narration.

    NO IDENTIFIERS HERE, AND THAT IS THE POINT.

    `utr` and `reference_number` used to be on this model. They were removed because of a
    property of our own design:

        The provenance gate verifies an identifier by finding it as a whole digit-run in
        the narration. So the gate only ever *accepts* values a regex could have found.
        Any field the gate can verify by regex is a field regex could have extracted.

    The model therefore adds nothing on an identifier by construction -- and measurably
    subtracted. Over 100 real narrations, 71 of which carry a 12-digit UTR: regex found
    71, the model found 48. Across all 4,528 narrations in data/train, zero contain a UTR
    that needs anything more than a plain regex. `core.normalize.extract_utrs` does this
    work at 100% for no tokens and no latency.

    What is left is the complement, and that is not a coincidence: these are exactly the
    fields the gate *cannot* verify by regex. A counterparty name has to be located inside
    mangled text; a payment method has to be inferred from context; legibility is a
    judgement. The model does the work that resists pattern matching, and the boundary
    that decides what it keeps is the same boundary seen from the other side.

    Every field here is still re-verified against the source narration. The model's
    confidence in a field is not evidence for it.
    """

    id: str = Field(description="Echo back the id given for this narration, exactly.")
    counterparty_name: str = Field(
        description="The paying party as written in the narration. Do not expand "
        "abbreviations and do not correct spelling."
    )
    payment_method: PaymentMethod
    parse_confidence: float = Field(
        ge=0.0, le=1.0, description="How legible this narration was. Not how likely a match is."
    )


class ParsedNarrationBatch(Strict):
    results: list[ParsedNarration]


# ------------------------------------------------- job 2: propose a journal entry


class JournalLine(Strict):
    """One Dr/Cr line. Exactly one of debit/credit is non-zero."""

    account_code: GLCode
    debit: int = Field(ge=0, description="Integer paise. Zero if this is a credit line.")
    credit: int = Field(ge=0, description="Integer paise. Zero if this is a debit line.")
    narrative: str = Field(description="Why this line exists, in one short clause.")

    @model_validator(mode="after")
    def one_side_only(self) -> "JournalLine":
        if bool(self.debit) == bool(self.credit):
            raise ValueError(
                "a journal line must be exactly one of debit or credit, and non-zero"
            )
        return self


class ProposedEntry(Strict):
    """A journal entry proposed for a human to approve. Never posted automatically.

    The balance check is in the schema rather than downstream on purpose. Constrained
    decoding can guarantee the shape of a number but not the sum of several, so this is
    the earliest point the arithmetic can be enforced -- and an unbalanced entry that
    reached the approval screen would be asking a human to catch a bug for us.
    """

    id: str = Field(description="Echo back the exception id, exactly.")
    lines: list[JournalLine] = Field(
        min_length=2, description="At least one debit and one credit."
    )
    narrative: str = Field(description="One sentence a finance operator can read.")
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def balances(self) -> "ProposedEntry":
        debits = sum(line.debit for line in self.lines)
        credits = sum(line.credit for line in self.lines)
        if debits != credits:
            raise ValueError(
                f"entry does not balance: debits {debits} paise, credits {credits} paise"
            )
        if debits == 0:
            raise ValueError("entry has no value")
        return self

    @property
    def total_paise(self) -> int:
        return sum(line.debit for line in self.lines)


class ProposedEntryBatch(Strict):
    results: list[ProposedEntry]


# --------------------------------------------- job 3: explain an open exception


class ExceptionReason(Strict):
    """Prose for a human, plus the model's own tag.

    The tag is recorded and compared with the pipeline's, never allowed to override it.
    Agreement is a measurement worth having; disagreement is a signal that either the
    prompt or the threshold is describing something other than what the operator sees.
    """

    id: str = Field(description="Echo back the exception id, exactly.")
    reason_code: LlmReasonCode
    reason_text: str = Field(
        max_length=400,
        description="Why this could not be matched, for a finance operator. Plain "
        "English, no jargon, no apology, and no speculation beyond the evidence given.",
    )
    suggested_action: SuggestedAction
    confidence: float = Field(ge=0.0, le=1.0)


class ExceptionReasonBatch(Strict):
    results: list[ExceptionReason]


# ------------------------------------------------------------------- the registry

# Prompts name their schema by string in front-matter. This is the only place that string
# is resolved, so a prompt naming a schema that does not exist fails at load time rather
# than on the first live call.
SCHEMAS: dict[str, type[BaseModel]] = {
    "ParsedNarrationBatch": ParsedNarrationBatch,
    "ProposedEntryBatch": ProposedEntryBatch,
    "ExceptionReasonBatch": ExceptionReasonBatch,
}


def schema_for(name: str) -> type[BaseModel]:
    try:
        return SCHEMAS[name]
    except KeyError:
        raise KeyError(
            f"unknown response schema {name!r}. Known: {', '.join(sorted(SCHEMAS))}"
        ) from None


def json_schema_for(name: str) -> dict:
    return schema_for(name).model_json_schema()


# Guard against LlmReasonCode drifting from the enum it mirrors. Kept as a module-level
# assertion as well as a test, because a mismatch here means the model can tag an
# exception with a code the pipeline does not recognise.
_JUDGEMENT = {str(c) for c in JUDGEMENT_CODES}
if set(LlmReasonCode.__args__) != _JUDGEMENT:  # pragma: no cover - fails at import
    raise RuntimeError(
        "LlmReasonCode has drifted from the JUDGEMENT family of ReasonCode: "
        f"{sorted(set(LlmReasonCode.__args__) ^ _JUDGEMENT)}"
    )
