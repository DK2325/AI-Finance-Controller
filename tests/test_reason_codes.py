"""The reason-code enum is a fixed vocabulary. This is what fixes it.

Reason codes are written into append-only audit records. That makes them different from
ordinary constants in one specific way: the records outlive the code that wrote them.

    Rename a code   -> every historical record carrying the old name now describes
                       something the system no longer admits exists.
    Remove a code   -> those records become unreadable. Nothing in the codebase can say
                       what happened, and the audit trail has a hole in exactly the
                       place someone is looking.

So the snapshot below is a ledger, not a copy. Adding a code means adding a line here
deliberately; renaming or removing one fails the build.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from llm.codes import (
    ALL_CODES,
    DETERMINISTIC_CODES,
    FAILURE_CODES,
    JUDGEMENT_CODES,
    MAX_CODE_LENGTH,
    Family,
    ReasonCode,
    is_failure,
    needs_llm,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------- the ledger
#
# Frozen 23 Aug 2026, Phase 5. Family is frozen alongside the code: moving a code between
# families changes what a historical record means just as surely as renaming it does,
# because the family is what decides whether a human or an operator has to act.

FROZEN: tuple[tuple[str, str], ...] = (
    ("NO_CANDIDATE", "deterministic"),
    ("NO_INVOICE_LINK", "deterministic"),
    ("SUBSET_SEARCH_CAPPED", "deterministic"),
    ("INVOICE_ALREADY_CLAIMED", "deterministic"),
    ("BELOW_THRESHOLD", "judgement"),
    ("LOW_CONFIDENCE", "judgement"),
    ("AMBIGUOUS_CANDIDATES", "judgement"),
    ("LLM_MALFORMED_RESPONSE", "failure"),
    ("LLM_SCHEMA_INVALID", "failure"),
    # Added 23 Aug 2026, Phase 5 step 5, when the handler gained batch reconciliation.
    ("LLM_BATCH_MISMATCH", "failure"),
    ("LLM_RATE_LIMITED", "failure"),
    ("LLM_TRANSPORT_FAILED", "failure"),
    ("FIELD_PROVENANCE_FAILED", "failure"),
    ("LOW_PARSE_CONFIDENCE", "failure"),
)

FROZEN_CODES = {code for code, _ in FROZEN}


# ------------------------------------------------------------------ the guarantee


def test_no_frozen_code_was_removed_or_renamed() -> None:
    """The one that matters. A missing code orphans every record that carries it."""
    live = {str(c) for c in ReasonCode}
    missing = sorted(FROZEN_CODES - live)

    assert not missing, (
        "these reason codes have been removed or renamed:\n  "
        + "\n  ".join(missing)
        + "\n\nAudit records are append-only and already carry these strings. If the "
        "name is genuinely wrong, add the new code and keep the old one -- do not "
        "rename it out from under the records that reference it."
    )


def test_no_frozen_code_changed_family() -> None:
    """A code that moves family changes who is expected to act on it."""
    live = {str(c): str(c.family) for c in ReasonCode}
    moved = [
        f"{code}: frozen as {family}, now {live[code]}"
        for code, family in FROZEN
        if code in live and live[code] != family
    ]

    assert not moved, (
        "these reason codes changed family:\n  "
        + "\n  ".join(moved)
        + "\n\nFamily decides whether an exception is a fact, a judgement, or a "
        "breakage. Historical records were filed under the old meaning."
    )


def test_new_codes_are_recorded_in_the_ledger() -> None:
    """Additions are allowed. Silent additions are not.

    Failing here is not an error, it is a prompt: add the code to FROZEN. BUILD.md asks
    for a *fixed* enum, and a snapshot that only ever lags the code is not fixing
    anything -- it is a subset that happens to still pass.
    """
    live = {str(c) for c in ReasonCode}
    unrecorded = sorted(live - FROZEN_CODES)

    assert not unrecorded, (
        "these reason codes exist but are not in the frozen ledger:\n  "
        + "\n  ".join(unrecorded)
        + "\n\nAdd them to FROZEN with their family. This is the deliberate step that "
        "makes the vocabulary fixed rather than merely current."
    )


# ------------------------------------------------------------------ well-formedness


def test_every_code_is_documented() -> None:
    """'Reason-code enum fixed and documented' is a Phase 5 exit criterion.

    Enforced structurally: ReasonCode.__new__ takes a description, so a member cannot be
    declared without one. This checks the descriptions are real rather than placeholders.
    """
    for code in ReasonCode:
        assert code.description, f"{code} has no description"
        assert len(code.description) > 60, (
            f"{code} has a {len(code.description)}-character description. A reason code "
            "explains itself to a finance operator reading an exception queue; a few "
            "words restating the name does not."
        )
        assert code.description.strip()[0].isupper(), f"{code} description is not a sentence"


def test_descriptions_are_distinct() -> None:
    """Two codes with the same description are one code with two names."""
    seen: dict[str, str] = {}
    for code in ReasonCode:
        assert code.description not in seen, (
            f"{code} and {seen[code.description]} share a description"
        )
        seen[code.description] = str(code)


def test_codes_fit_the_database_column() -> None:
    """reason_code is String(64). A code that does not fit fails at insert time."""
    for code in ReasonCode:
        assert len(code) <= MAX_CODE_LENGTH, (
            f"{code} is {len(code)} characters, over the {MAX_CODE_LENGTH} the column holds"
        )


def test_codes_are_screaming_snake_case() -> None:
    """One convention, so a code is recognisable as a code wherever it surfaces."""
    for code in ReasonCode:
        assert code.replace("_", "").isalnum() and code.isupper(), f"{code} breaks the convention"


def test_a_code_is_its_own_wire_format() -> None:
    """StrEnum, so no conversion step exists to drift from the stored value.

    A plain Enum stringifies to 'ReasonCode.NO_CANDIDATE', which is what would land in
    the database if anyone forgot a `.value`. This makes forgetting harmless.
    """
    assert ReasonCode.NO_CANDIDATE == "NO_CANDIDATE"
    assert str(ReasonCode.NO_CANDIDATE) == "NO_CANDIDATE"
    assert f"{ReasonCode.NO_CANDIDATE}" == "NO_CANDIDATE"
    assert ReasonCode("NO_CANDIDATE") is ReasonCode.NO_CANDIDATE


# ----------------------------------------------------------------------- families


def test_families_partition_the_enum() -> None:
    """Every code in exactly one family, no code in none."""
    grouped = DETERMINISTIC_CODES + JUDGEMENT_CODES + FAILURE_CODES
    assert len(grouped) == len(ALL_CODES) == len(set(grouped))
    assert set(grouped) == set(ALL_CODES)


def test_no_family_is_empty() -> None:
    """A guard against the partition test passing because a family quietly emptied out."""
    for family, codes in (
        (Family.DETERMINISTIC, DETERMINISTIC_CODES),
        (Family.JUDGEMENT, JUDGEMENT_CODES),
        (Family.FAILURE, FAILURE_CODES),
    ):
        assert codes, f"{family} has no codes"


def test_needs_llm_is_true_only_for_judgement() -> None:
    """The cost model rests on this. Every False is an exception explained for free."""
    for code in ReasonCode:
        assert needs_llm(code) is (code.family is Family.JUDGEMENT), code
    assert not needs_llm(ReasonCode.NO_CANDIDATE)
    assert needs_llm(ReasonCode.BELOW_THRESHOLD)


def test_a_failure_is_never_a_judgement() -> None:
    """A 429 is not the system abstaining. Reporting them as one number says neither."""
    for code in ReasonCode:
        assert not (needs_llm(code) and is_failure(code)), code


# -------------------------------------------------------------- the import guard


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import is inside llm/, which is the risk
                roots.add(f".{node.module or ''}")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_codes_module_imports_only_stdlib() -> None:
    """core/ imports this module, which inverts the layer order. This is what makes that safe.

    If llm/codes.py were ever to import a provider SDK, pydantic, or another llm/ module,
    then core/ -- the deterministic layer, the one that must be reasonable about in
    isolation -- would transitively depend on the network. It imports `enum` and nothing
    else, and the moment that changes this fails.
    """
    roots = _imported_roots(REPO_ROOT / "llm" / "codes.py")
    non_stdlib = sorted(r for r in roots if r.lstrip(".") not in sys.stdlib_module_names)

    assert not non_stdlib, (
        "llm/codes.py imports non-stdlib modules: "
        + ", ".join(non_stdlib)
        + "\n\ncore/ imports this module for the shared reason-code vocabulary. Anything "
        "imported here is imported by the deterministic layer too."
    )


def test_llm_package_init_stays_empty() -> None:
    """The guard above holds only because importing llm.codes runs this file first."""
    roots = _imported_roots(REPO_ROOT / "llm" / "__init__.py")
    assert not roots, (
        f"llm/__init__.py imports {sorted(roots)}. Keep it a docstring: `import llm.codes` "
        "runs the package __init__ first, so anything imported there is imported by core/."
    )


def test_core_uses_the_enum_rather_than_a_parallel_string() -> None:
    """One vocabulary. Two constants kept in sync by hand is how audit trails disagree."""
    from core.subsetsum import REASON_CAPPED

    assert REASON_CAPPED is ReasonCode.SUBSET_SEARCH_CAPPED
