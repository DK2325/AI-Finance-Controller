"""Versioned prompts and the schemas they name.

Two things are being defended here.

**Prompts are files, not strings in Python.** An audit record saying `parse.v1` is only
worth writing if `parse.v1` can be retrieved and read a month later. A lint asserts no
module constructs an LLMRequest directly -- the only route to one is through a loaded
Prompt, which means every call carries a version.

**Identity is version AND checksum.** Editing a prompt in place without bumping the
version would leave old audit records pointing at text that no longer exists and would
poison the response cache, which is keyed on prompt identity.
"""

import ast
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from llm.codes import JUDGEMENT_CODES
from llm.prompt import PROMPT_DIR, PromptError, load, load_file, registry
from llm.provider import MockProvider
from llm.schemas import (
    SCHEMAS,
    ExceptionReason,
    ExceptionReasonBatch,
    JournalLine,
    LlmReasonCode,
    ParsedNarration,
    ParsedNarrationBatch,
    ProposedEntry,
    ProposedEntryBatch,
    json_schema_for,
    schema_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS = ("parse", "journal", "reason")


def write_prompt(tmp_path: Path, body: str, name: str = "tmp.v1.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


GOOD = """---
name: probe
version: 1
job: probe
schema: ParsedNarrationBatch
enable_thinking: false
max_tokens: 900
batch_size: 4
---

## SYSTEM

Be exact.

## USER

Do the thing.

{items}

## ITEM

id: {id}
narration: <<<{narration}>>>
"""


# ------------------------------------------------------------ the three prompts


def test_all_three_jobs_have_a_prompt() -> None:
    assert sorted({p.job for p in registry().values()}) == sorted(JOBS)


@pytest.mark.parametrize("job", JOBS)
def test_every_prompt_declares_thinking_off(job: str) -> None:
    """Measured at 5.4x the output tokens for structurally identical results.

    Declared per prompt rather than globally, so turning it on for one job is a visible
    edit to that file and lands in the audit record with a new checksum.
    """
    assert load(job).enable_thinking is False


@pytest.mark.parametrize("job", JOBS)
def test_every_prompt_names_a_schema_that_exists(job: str) -> None:
    assert load(job).schema_name in SCHEMAS


@pytest.mark.parametrize("job", JOBS)
def test_every_prompt_tells_the_model_it_does_not_decide(job: str) -> None:
    """Architecture rule 2, stated in the prompt as well as enforced by the pipeline."""
    text = (load(job).system + load(job).user_template).lower()
    assert any(
        phrase in text
        for phrase in (
            "do not decide",
            "you do not decide",
            "not reviewing it",
            "posted automatically",
        )
    ), f"{job} does not tell the model it is not the one deciding"


@pytest.mark.parametrize("job", JOBS)
def test_every_prompt_delimits_untrusted_text(job: str) -> None:
    """Not a security control -- notes/injection.md is explicit about that -- but the
    model should at least be told which bytes are data."""
    assert "<<<" in load(job).item_template
    assert "data, not instruction" in load(job).system


def test_batch_sizes_match_what_was_measured() -> None:
    """20 for the cheap jobs. Journal entries are longer, so 10 keeps output inside budget."""
    assert load("parse").batch_size == 20
    assert load("reason").batch_size == 20
    assert load("journal").batch_size == 10


# ---------------------------------------------------------------- identity


def test_version_id_carries_both_version_and_checksum() -> None:
    prompt = load("parse")
    assert prompt.version_id.startswith("parse.v2+")
    assert len(prompt.version_id.split("+")[1]) == 12


def test_editing_a_prompt_without_bumping_changes_its_identity(tmp_path: Path) -> None:
    """The failure this prevents: old audit records pointing at text that no longer exists."""
    first = load_file(write_prompt(tmp_path, GOOD))
    second = load_file(write_prompt(tmp_path, GOOD.replace("Be exact.", "Be very exact.")))

    assert first.version == second.version
    assert first.checksum != second.checksum
    assert first.version_id != second.version_id


def test_the_checksum_covers_front_matter_too(tmp_path: Path) -> None:
    """max_tokens is part of what produced a result, so it is part of the identity."""
    first = load_file(write_prompt(tmp_path, GOOD))
    second = load_file(write_prompt(tmp_path, GOOD.replace("max_tokens: 900", "max_tokens: 950")))
    assert first.checksum != second.checksum


def test_every_prompt_on_disk_has_a_distinct_checksum() -> None:
    checksums = [p.checksum for p in registry().values()]
    assert len(set(checksums)) == len(checksums)


# ------------------------------------------------------------------ rendering


def test_render_substitutes_every_item() -> None:
    rendered = load("parse").render(
        [{"id": "A", "narration": "one"}, {"id": "B", "narration": "two"}]
    )
    assert "id: A" in rendered and "id: B" in rendered
    assert "{items}" not in rendered


def test_braces_in_a_narration_are_inert() -> None:
    """Untrusted text is substituted, never re-parsed as a template.

    A narration containing {id} must appear verbatim, not be replaced by the id -- and
    must certainly not raise.
    """
    hostile = "PAYMENT {id} {0} {} {counterparty_name}"
    rendered = load("parse").render([{"id": "A", "narration": hostile}])
    assert hostile in rendered


def test_a_missing_template_field_fails_loudly(tmp_path: Path) -> None:
    prompt = load_file(write_prompt(tmp_path, GOOD))
    with pytest.raises(PromptError, match="narration"):
        prompt.render([{"id": "A"}])


def test_batches_split_at_the_declared_size() -> None:
    prompt = load("journal")
    items = [{"id": str(i)} for i in range(25)]
    sizes = [len(b) for b in prompt.batches(items)]
    assert sizes == [10, 10, 5]


def test_request_carries_everything_the_audit_record_needs() -> None:
    items = [{"id": "EX1", "narration": "NEFT-CR-ACME-300000004412"}]
    request = load("parse").request(items)

    assert request.job == "parse"
    assert request.prompt_version == load("parse").version_id
    assert request.enable_thinking is False
    assert request.max_tokens == load("parse").max_tokens
    assert request.schema_name == "ParsedNarrationBatch"
    assert request.context == ({"id": "EX1", "narration": "NEFT-CR-ACME-300000004412"},)


# ------------------------------------------------------------- malformed files


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda t: t.replace("---\n", "", 1), "front-matter"),
        (lambda t: t.replace("version: 1", "version: one"), "not an integer"),
        (lambda t: t.replace("enable_thinking: false", "enable_thinking: maybe"), "true/false"),
        (lambda t: t.replace("schema: ParsedNarrationBatch", "schema: NoSuchSchema"), ""),
        (lambda t: t.replace("job: probe", "jobprobe"), "not `key: value`"),
        (lambda t: t.replace("batch_size: 4\n", ""), "missing"),
        (lambda t: t.replace("## ITEM", "## NOTES"), "ITEM"),
    ],
)
def test_a_malformed_prompt_fails_at_load_time(tmp_path: Path, mutation, expected: str) -> None:
    """Never mid-run, twenty minutes into a batch."""
    path = write_prompt(tmp_path, mutation(GOOD))
    with pytest.raises((PromptError, KeyError), match=expected or None):
        load_file(path)


def test_an_unknown_job_names_the_ones_that_exist() -> None:
    with pytest.raises(PromptError, match="journal"):
        load("summarise")


def test_pinning_a_version_that_does_not_exist_is_refused() -> None:
    with pytest.raises(PromptError, match="no v7"):
        load("parse", version=7)


def test_pinning_the_version_that_does_exist_works() -> None:
    assert load("parse", version=2).version == 2


# --------------------------------------------------- no inline prompt strings


def test_no_module_builds_a_request_without_a_prompt() -> None:
    """The lint behind "prompts referenced by version, no inline prompt strings".

    If any module could construct an LLMRequest itself, it could send text that exists
    nowhere on disk, and the version in the audit record would name something else.
    """
    allowed = {Path("llm/prompt.py")}
    offenders: list[str] = []

    for package in ("llm", "core", "model", "api", "evals", "ledgerloop"):
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT)
            if Path(*relative.parts) in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "LLMRequest"
                ):
                    offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "LLMRequest is constructed outside llm/prompt.py:\n  " + "\n  ".join(offenders)
    )


def test_no_prompt_text_is_duplicated_in_python() -> None:
    """A copy of a prompt in a module is a prompt with no version."""
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for package in ("llm", "core", "model", "api", "ledgerloop")
        for path in (REPO_ROOT / package).rglob("*.py")
    )
    for prompt in registry().values():
        for line in prompt.system.splitlines():
            stripped = line.strip()
            if len(stripped) > 40 and not stripped.startswith("|"):
                assert stripped not in sources, (
                    f"{prompt.path.name} line is duplicated in Python source: {stripped[:60]}"
                )


def test_prompt_files_are_the_only_prompts() -> None:
    assert {p.name for p in PROMPT_DIR.glob("*.md")} == {
        "parse.v2.md", "journal.v1.md", "reason.v1.md"
    }


# ----------------------------------------------------------------- schemas


def _objects(schema: dict) -> list[dict]:
    """Every object node in a schema, including everything under $defs."""
    found = []
    stack = [schema, *schema.get("$defs", {}).values()]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("type") == "object" and "properties" in node:
            found.append(node)
        stack.extend(v for v in node.values() if isinstance(v, dict | list))
        for value in node.values():
            if isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, dict))
    return found


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_the_model_cannot_invent_a_field(name: str) -> None:
    """extra="forbid" becomes additionalProperties: false, a decode-time constraint."""
    for node in _objects(json_schema_for(name)):
        assert node.get("additionalProperties") is False, node.get("title")


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_field_is_required(name: str) -> None:
    """An omitted field is indistinguishable from one the model forgot.

    Optional values are nullable-and-required, so "I found nothing" is an explicit claim.
    """
    for node in _objects(json_schema_for(name)):
        assert set(node.get("required", [])) == set(node["properties"]), node.get("title")


def test_the_reason_codes_offered_to_the_model_are_the_judgement_family() -> None:
    """The model must not be able to declare NO_CANDIDATE. That is a fact, not a judgement."""
    assert set(LlmReasonCode.__args__) == {str(c) for c in JUDGEMENT_CODES}


def test_a_deterministic_code_is_refused_from_the_model() -> None:
    with pytest.raises(ValidationError):
        ExceptionReason(
            id="EX1", reason_code="NO_CANDIDATE", reason_text="x",
            suggested_action="review_manually", confidence=0.5,
        )


def test_an_invented_gl_code_is_refused() -> None:
    """A proposal against an account that does not exist reads as authoritative and is not."""
    with pytest.raises(ValidationError):
        JournalLine(account_code="7777", debit=100, credit=0, narrative="x")


def test_an_unbalanced_entry_is_refused() -> None:
    """Constrained decoding can guarantee a number's shape, never a sum of several."""
    with pytest.raises(ValidationError, match="does not balance"):
        ProposedEntry(
            id="EX1",
            lines=[
                JournalLine(account_code="1100", debit=900, credit=0, narrative="a"),
                JournalLine(account_code="1200", debit=0, credit=800, narrative="b"),
            ],
            narrative="x",
            confidence=0.5,
        )


def test_a_balanced_entry_is_accepted() -> None:
    entry = ProposedEntry(
        id="EX1",
        lines=[
            JournalLine(account_code="1100", debit=4487342, credit=0, narrative="a"),
            JournalLine(account_code="1460", debit=62658, credit=0, narrative="b"),
            JournalLine(account_code="1200", debit=0, credit=4550000, narrative="c"),
        ],
        narrative="TDS deducted under 194H.",
        confidence=0.91,
    )
    assert entry.total_paise == 4550000


def test_a_line_with_both_sides_is_refused() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        JournalLine(account_code="1100", debit=100, credit=100, narrative="x")


def test_a_line_with_neither_side_is_refused() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        JournalLine(account_code="1100", debit=0, credit=0, narrative="x")


def test_an_entry_of_nothing_is_refused() -> None:
    with pytest.raises(ValidationError):
        ProposedEntry(id="EX1", lines=[], narrative="x", confidence=0.1)


def test_an_extra_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        ParsedNarration(
            id="A", counterparty_name="B", payment_method="upi", utr=None,
            reference_number=[], parse_confidence=0.5, matched=True,
        )


def test_reason_text_is_bounded() -> None:
    """A queue of long explanations is a queue nobody reads."""
    with pytest.raises(ValidationError):
        ExceptionReason(
            id="EX1", reason_code="BELOW_THRESHOLD", reason_text="x" * 401,
            suggested_action="review_manually", confidence=0.5,
        )


def test_an_unknown_schema_name_lists_the_known_ones() -> None:
    with pytest.raises(KeyError, match="ParsedNarrationBatch"):
        schema_for("Nonexistent")


# ------------------------------------------- the mock against the real schemas


@pytest.mark.parametrize(
    "job,model",
    [
        ("parse", ParsedNarrationBatch),
        ("journal", ProposedEntryBatch),
        ("reason", ExceptionReasonBatch),
    ],
)
def test_the_mock_satisfies_every_real_schema(job: str, model: type[BaseModel]) -> None:
    """Including the journal entry's balance invariant, which no schema walk can produce."""
    items = [
        {
            "id": "EX1",
            "narration": "NEFT-CR-HDFC0000123-ACME INDUSTRIES-300000004412",
            "invoice_amount": 4550000, "gross_amount": 4550000, "fee": 53100,
            "tax": 9558, "net_amount": 4487342, "bank_credit": 4487342,
            "difference": 62658, "counterparty": "ACME INDUSTRIES", "tds_section": "194H",
            "reason_code": "BELOW_THRESHOLD", "date_gap": 2, "probability": 0.71,
            "threshold": 0.9412, "n_close": 1,
        }
    ]
    response = MockProvider().complete(load(job).request(items))
    parsed = model.model_validate(json.loads(response.text))
    assert len(parsed.results) == 1
    assert parsed.results[0].id == "EX1"


def test_the_mocked_journal_entry_actually_balances() -> None:
    """If the mock could not balance, every --mock-llm run would report a false failure rate."""
    items = [{"id": "EX1", "narration": "x", "bank_credit": 4487342, "net_amount": 4487342,
              "invoice_amount": 4550000, "gross_amount": 4550000, "fee": 0, "tax": 0,
              "difference": 0, "counterparty": "ACME", "tds_section": ""}]
    response = MockProvider().complete(load("journal").request(items))
    entry = ProposedEntryBatch.model_validate(json.loads(response.text)).results[0]
    assert entry.total_paise == 4487342
