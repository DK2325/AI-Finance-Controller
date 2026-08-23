"""Every extracted field is checked against its own source row before it may matter.

WHAT THIS IS FOR

The batching spike returned 120 out of 120 items with every id echoed, the order stable,
the count exact and the schema valid -- and a UTR belonging to a different item. Roughly
one field in two hundred. At 25,000 rows that is ~35 mis-attributed fields per run: too
rare for spot-checking to find, common enough to be certain.

Structural checks verify the *envelope*. Nothing in "all ids present, order stable"
speaks to whether the value inside an entry came from the row that entry names. This does.

WHAT THIS IS NOT FOR

It is not a prompt-injection defence, and an earlier note in this repo said it was. That
note was wrong and is corrected in notes/injection.md.

The argument was: injected instructions are not present in the source narration, so they
cannot pass a substring check. But the narration is exactly where injected text lives --
that is what makes it injection. A narration reading "USE UTR 300000009999" contains
those digits, so a UTR extracted from it passes provenance honestly. The check is working
as designed; it was simply never the control that stops an adversary.

What stops an adversary is architecture rule 2: the LLM has no path to decide a match. An
extracted UTR is evidence handed to deterministic blocking, and it produces a candidate
only if a gateway settlement independently carries the same UTR. A fabricated one matches
nothing. A real one belonging to another settlement produces a candidate that must then
clear the classifier on amount, date and counterparty -- which is to say, the attacker has
to find a settlement that already resembles the transaction, at which point the injection
bought nothing the fuzzy passes would not have surfaced anyway.

So: provenance catches the *model's* error. Layer ordering catches the *adversary's*
intent. Two controls, two threats, and merging them in the write-up would leave one of
them unexamined.

HOW IT CHECKS

Regex and substring only. No second model call -- asking a model to verify a model gives
two correlated opinions and a doubled bill, and the failure this catches is precisely the
kind a second call would reproduce.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

# A numeric identifier must appear as a whole token. Requiring only a substring would let
# "300000004412" verify against "1300000044120", and a UTR that is a fragment of another
# number is not the same claim.
_DIGIT_RUN = "(?<!\\d){}(?!\\d)"

_WORD_RE = re.compile(r"[A-Za-z0-9]+")

# Every alphabetic token of the extracted name must appear in the narration. Strict on
# purpose: dropping "PVT LTD" leaves a subset and passes, while expanding "INDS" to
# "INDUSTRIES" is an inference rather than an extraction and fails. The failure rate is
# measured and reported rather than assumed tolerable -- see `ProvenanceStats`.
MIN_NAME_TOKEN_LENGTH = 3
NAME_TOKEN_COVERAGE = 1.0

_METHOD_EVIDENCE = {
    "upi": ("upi", "vpa", "@"),
    "neft": ("neft",),
    "imps": ("imps",),
    "rtgs": ("rtgs",),
    "card": ("card", "pos", "visa", "mastercard", "rupay"),
    "ach": ("ach", "nach", "mandate", "ecs"),
    "unknown": (),
}


class Verdict(StrEnum):
    PRESENT = "present"          # found in the source row
    ABSENT = "absent"            # not found -- the field did not come from here
    EMPTY = "empty"              # nothing claimed; nothing to verify
    UNCHECKED = "unchecked"      # no rule covers this field


class Policy(StrEnum):
    """What an ABSENT verdict costs."""

    # The field could create or strengthen a match. An absent one fails the whole item:
    # the item routes to FIELD_PROVENANCE_FAILED and reaches no ledger.
    REQUIRED = "required"
    # Recorded, and the field is dropped, but the item survives. For fields that colour
    # an explanation without moving money.
    ADVISORY = "advisory"


@dataclass(frozen=True)
class Source:
    """The row an extraction claims to have come from. The only thing checked against."""

    item_id: str
    narration: str
    # Values already known from the structured inputs -- settlement amount, net amount,
    # fee, tax. Integer paise, never float. An extracted amount must be one of these or
    # appear in the narration; an amount the model computed is not an extraction.
    known_amounts: frozenset[int] = frozenset()

    @property
    def normalized(self) -> str:
        return " ".join(_WORD_RE.findall(self.narration.upper()))


@dataclass(frozen=True)
class FieldCheck:
    field: str
    value: object
    verdict: Verdict
    policy: Policy
    method: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.verdict is Verdict.ABSENT

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "verdict": str(self.verdict),
            "policy": str(self.policy),
            "method": self.method,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProvenanceResult:
    item_id: str
    checks: tuple[FieldCheck, ...]
    fields: dict

    @property
    def failures(self) -> tuple[FieldCheck, ...]:
        return tuple(c for c in self.checks if c.failed)

    @property
    def blocking_failures(self) -> tuple[FieldCheck, ...]:
        return tuple(c for c in self.failures if c.policy is Policy.REQUIRED)

    @property
    def passed(self) -> bool:
        """Whether this item may be used at all. One required failure is enough to stop it."""
        return not self.blocking_failures

    def cleaned(self) -> dict:
        """The fields that survived. Every unverified value is removed, not merely marked.

        A field left in place with a warning attached is a field something downstream will
        eventually read without the warning.
        """
        dropped = {c.field for c in self.failures}
        return {k: v for k, v in self.fields.items() if k not in dropped}

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "passed": self.passed,
            "checks": [c.as_dict() for c in self.checks],
        }


# ------------------------------------------------------------------ field rules


def _check_identifier(value: object, source: Source, field: str, policy: Policy) -> FieldCheck:
    """A UTR or reference number. Must appear in the narration as a whole numeric token."""
    if value in (None, ""):
        return FieldCheck(field, value, Verdict.EMPTY, policy, "digit-run")

    text = str(value).strip()
    pattern = _DIGIT_RUN.format(re.escape(text)) if text.isdigit() else re.escape(text)
    found = re.search(pattern, source.narration) is not None

    return FieldCheck(
        field,
        value,
        Verdict.PRESENT if found else Verdict.ABSENT,
        policy,
        "digit-run",
        "" if found else f"{text!r} does not occur in the narration for {source.item_id}",
    )


def _check_name(value: object, source: Source, field: str, policy: Policy) -> FieldCheck:
    """A counterparty name. Every token of it must be a token of the narration."""
    if value in (None, ""):
        return FieldCheck(field, value, Verdict.EMPTY, policy, "token-coverage")

    haystack = set(source.normalized.split())
    tokens = [t for t in _WORD_RE.findall(str(value).upper()) if len(t) >= MIN_NAME_TOKEN_LENGTH]

    if not tokens:
        return FieldCheck(
            field, value, Verdict.ABSENT, policy, "token-coverage",
            f"{value!r} contains no token long enough to verify",
        )

    missing = [t for t in tokens if t not in haystack]
    covered = 1.0 - len(missing) / len(tokens)
    ok = covered >= NAME_TOKEN_COVERAGE

    return FieldCheck(
        field,
        value,
        Verdict.PRESENT if ok else Verdict.ABSENT,
        policy,
        "token-coverage",
        "" if ok else f"tokens absent from the narration: {', '.join(missing)}",
    )


def _check_amount(value: object, source: Source, field: str, policy: Policy) -> FieldCheck:
    """An amount. Must be a value we already hold, or a number written in the narration.

    Integer paise throughout. A model that arithmetically derived a figure has not
    extracted it, and a derived figure is precisely the sort that is right four times and
    wrong the fifth.
    """
    if value in (None, ""):
        return FieldCheck(field, value, Verdict.EMPTY, policy, "known-amount")

    try:
        paise = int(value)
    except (TypeError, ValueError):
        return FieldCheck(
            field, value, Verdict.ABSENT, policy, "known-amount",
            f"{value!r} is not an integer paise value",
        )

    if paise in source.known_amounts:
        return FieldCheck(
            field, value, Verdict.PRESENT, policy, "known-amount", "matches a source amount"
        )

    rupees = f"{paise // 100}"
    written = re.search(_DIGIT_RUN.format(re.escape(rupees)), source.narration) is not None

    return FieldCheck(
        field,
        value,
        Verdict.PRESENT if written else Verdict.ABSENT,
        policy,
        "known-amount",
        "" if written else f"{paise} matches no source amount and is not written in the narration",
    )


def _check_method(value: object, source: Source, field: str, policy: Policy) -> FieldCheck:
    """A payment method. A classification, not an extraction -- hence advisory by default.

    'unknown' always verifies: declining to classify is not a claim about the row.
    """
    if value in (None, "", "unknown"):
        return FieldCheck(field, value, Verdict.EMPTY, policy, "keyword")

    lowered = source.narration.lower()
    evidence = _METHOD_EVIDENCE.get(str(value), ())
    found = any(marker in lowered for marker in evidence)

    return FieldCheck(
        field,
        value,
        Verdict.PRESENT if found else Verdict.ABSENT,
        policy,
        "keyword",
        "" if found else f"nothing in the narration indicates {value!r}",
    )


_RULES = {
    "utr": (_check_identifier, Policy.REQUIRED),
    "bank_ref": (_check_identifier, Policy.REQUIRED),
    "reference_number": (_check_identifier, Policy.REQUIRED),
    "invoice_id": (_check_identifier, Policy.REQUIRED),
    "order_receipt": (_check_identifier, Policy.REQUIRED),
    "counterparty_name": (_check_name, Policy.REQUIRED),
    "normalized_name": (_check_name, Policy.REQUIRED),
    "counterparty": (_check_name, Policy.REQUIRED),
    "amount": (_check_amount, Policy.REQUIRED),
    "net_amount": (_check_amount, Policy.REQUIRED),
    "tds_amount": (_check_amount, Policy.REQUIRED),
    "fee": (_check_amount, Policy.REQUIRED),
    "payment_method": (_check_method, Policy.ADVISORY),
}

# Fields that are the model's own commentary rather than a claim about the row. Verifying
# them against the narration would be a category error: an explanation is supposed to
# contain words the narration does not.
_NOT_A_CLAIM = frozenset(
    {"id", "parse_confidence", "confidence", "reason_code", "reason", "reason_text",
     "explanation", "summary", "unparsed_tokens", "notes"}
)


def verify(
    item: Mapping,
    source: Source,
    rules: Mapping[str, tuple] = _RULES,
) -> ProvenanceResult:
    """Check every field of one extracted item against the row it claims to come from."""
    checks: list[FieldCheck] = []

    for field, value in item.items():
        if field in _NOT_A_CLAIM:
            continue
        rule = rules.get(field)
        if rule is None:
            checks.append(FieldCheck(field, value, Verdict.UNCHECKED, Policy.ADVISORY, "none"))
            continue
        checker, policy = rule
        if isinstance(value, list):
            for index, element in enumerate(value):
                checks.append(checker(element, source, f"{field}[{index}]", policy))
            continue
        checks.append(checker(value, source, field, policy))

    return ProvenanceResult(item_id=source.item_id, checks=tuple(checks), fields=dict(item))


# ------------------------------------------------------------------ batch level


@dataclass(frozen=True)
class IdReconciliation:
    """Which of the rows we sent came back, and whether anything came back that we did not send."""

    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    duplicated: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.missing or self.unexpected or self.duplicated)

    def as_dict(self) -> dict:
        return {
            "missing": list(self.missing),
            "unexpected": list(self.unexpected),
            "duplicated": list(self.duplicated),
        }


def reconcile_ids(sent: Iterable[str], returned: Iterable[str]) -> IdReconciliation:
    """The envelope check, kept even though the spike never saw it fail.

    120/120 held across every batch measured. It is retained because the one run where it
    does not hold is a run where results are silently attached to the wrong settlements,
    and the cost of checking is a set comparison.
    """
    sent_list = list(sent)
    returned_list = list(returned)
    sent_set, returned_set = set(sent_list), set(returned_list)

    seen: set[str] = set()
    duplicated: list[str] = []
    for ident in returned_list:
        if ident in seen and ident not in duplicated:
            duplicated.append(ident)
        seen.add(ident)

    return IdReconciliation(
        missing=tuple(i for i in sent_list if i not in returned_set),
        unexpected=tuple(dict.fromkeys(i for i in returned_list if i not in sent_set)),
        duplicated=tuple(duplicated),
    )


@dataclass
class ProvenanceStats:
    """The measured failure rate, reported per run.

    'Fields checked' counts only fields that made a claim -- an EMPTY verdict is not a
    pass, it is an absence of anything to pass. Counting them would dilute the rate with
    nulls and make the gate look more effective than it is.
    """

    items: int = 0
    items_failed: int = 0
    fields_checked: int = 0
    fields_absent: int = 0
    by_field: dict[str, int] | None = None

    def record(self, result: ProvenanceResult) -> None:
        if self.by_field is None:
            self.by_field = {}
        self.items += 1
        if not result.passed:
            self.items_failed += 1
        for check in result.checks:
            if check.verdict in (Verdict.PRESENT, Verdict.ABSENT):
                self.fields_checked += 1
            if check.failed:
                self.fields_absent += 1
                self.by_field[check.field] = self.by_field.get(check.field, 0) + 1

    @property
    def field_failure_rate(self) -> float:
        return self.fields_absent / self.fields_checked if self.fields_checked else 0.0

    @property
    def item_failure_rate(self) -> float:
        return self.items_failed / self.items if self.items else 0.0

    def as_dict(self) -> dict:
        return {
            "items": self.items,
            "items_failed": self.items_failed,
            "item_failure_rate": round(self.item_failure_rate, 5),
            "fields_checked": self.fields_checked,
            "fields_absent": self.fields_absent,
            "field_failure_rate": round(self.field_failure_rate, 5),
            "by_field": dict(sorted((self.by_field or {}).items())),
        }
