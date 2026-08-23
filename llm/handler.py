"""The exception layer: batch, call, validate, verify, and route whatever failed.

This is the only place an LLM response becomes something the rest of the system will act
on, so it is also the only place that decides a response is not good enough. Every exit
from here is either a verified result or a reason code -- there is no third outcome where
something unvalidated leaks onward.

THE PIPELINE FOR ONE BATCH

    render -> cache lookup -> call -> JSON -> schema -> envelope -> provenance -> floor

Each stage has its own reason code, and they are kept apart because the remedies differ:

    JSON failed          LLM_MALFORMED_RESPONSE    the constraint is not being applied
    schema failed        LLM_SCHEMA_INVALID        the prompt or the schema is wrong
    envelope failed      LLM_BATCH_MISMATCH        the batch is too big
    provenance failed    FIELD_PROVENANCE_FAILED   the model mis-attributed a field
    confidence floor     LOW_PARSE_CONFIDENCE      the input was illegible
    call failed          LLM_RATE_LIMITED / LLM_TRANSPORT_FAILED

ONE RETRY, THEN THE QUEUE

BUILD.md: "One retry on validation failure, then route to the exception queue." Exactly
one, and it bypasses the cache -- retrying against a cached bad response would return the
same bytes and burn the retry for nothing. A failed response is never cached, so a
transient problem cannot become permanent.

The retry is not a second opinion. If the second response also fails, the exception is
the correct outcome: a finance operator reading "we could not parse this" is better served
than one reading a guess.

WHAT THE BATCH COSTS PER ITEM

Token counts arrive per call, not per item, so per-item figures here are the call's tokens
divided by the number of items in it. That is an apportionment and is labelled as one --
`Usage` keeps the true per-call totals, and any rupee figure is computed from those.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from llm.cache import ResponseCache
from llm.codes import ReasonCode
from llm.prompt import Prompt, load
from llm.provenance import ProvenanceResult, ProvenanceStats, Source, reconcile_ids, verify
from llm.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    RateLimited,
    TransportFailed,
    get_provider,
)
from llm.schemas import schema_for

# Below this, the model is telling us it could not read the input. Kept as a declared
# constant rather than a magic number: its effect on coverage is measured in Phase 7, and
# a threshold nobody can find is a threshold nobody re-examines.
MIN_PARSE_CONFIDENCE = 0.35

# Which keys in an input row hold money. Used to build the set of amounts an extraction is
# allowed to claim -- see llm/provenance.py. Deliberately a keyword test rather than "any
# integer in the row", because a row also carries counts and day-gaps, and admitting those
# as known amounts would let a two-paise figure verify against `n_close: 2`.
AMOUNT_KEY_MARKERS = ("amount", "credit", "debit", "fee", "tax", "paise", "difference")

# Batches in flight. Under the ceiling on purpose, not at it.
#
# 36 rpm with ~20s calls is saturated by about twelve concurrent requests. Running at
# exactly that leaves nothing for the retries -- which draw on the same bucket -- or for
# a latency spike, and the failure mode of guessing high is 429s, which cost a round
# trip AND a backoff. Eight is deliberately short of the arithmetic; the rate actually
# achieved is reported by JobResult.achieved_rpm rather than assumed from this number.
DEFAULT_CONCURRENCY = 8


@dataclass
class Usage:
    """Real token spend, kept separate from what the cache served.

    Conflating them would make a second run of the same batch look free *and* make the
    first run's cost unrecoverable, which is precisely the number Phase 7 needs.
    """

    calls: int = 0
    cached_calls: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cached_output_tokens: int = 0
    seconds: float = 0.0
    # One entry per real call. Latency and output size vary enormously between batches,
    # and an average hides which batch was the expensive one -- which is the batch that
    # explains a truncation.
    call_log: list[dict] = field(default_factory=list)

    def record(self, response: LLMResponse, retry: bool = False) -> None:
        if response.cache_hit:
            self.cached_calls += 1
            self.cached_input_tokens += response.input_tokens
            self.cached_output_tokens += response.output_tokens
            return
        self.calls += 1
        self.call_log.append({
            "seconds": round(response.seconds, 2),
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "truncated": response.truncated,
            # Recorded per call, not just per failure: a stall that a retry rescues leaves
            # no exception behind, so without this the only trace of it is a token bill.
            "stalled": response.stalled,
            "raw_chars": response.raw_chars,
            "retry": retry,
        })
        self.retries += int(retry)
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.seconds += response.seconds

    @property
    def billed_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "retries": self.retries,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "billed_tokens": self.billed_tokens,
            "tokens_served_from_cache": self.cached_input_tokens + self.cached_output_tokens,
            "seconds": round(self.seconds, 2),
        }


@dataclass
class ItemOutcome:
    """What happened to one row. Either `fields` is set, or `reason_code` is."""

    item_id: str
    fields: dict | None = None
    reason_code: ReasonCode | None = None
    detail: str = ""
    provenance: ProvenanceResult | None = None
    prompt_version: str = ""
    model: str = ""
    provider: str = ""
    cache_hit: bool = False
    # The call's tokens divided by its item count. An apportionment, not a measurement.
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.reason_code is None

    def as_audit_fields(self) -> dict:
        return {
            "prompt_version": self.prompt_version,
            "model_name": self.model,
            "provider": self.provider,
            "cache_hit": self.cache_hit,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class JobResult:
    job: str
    outcomes: list[ItemOutcome] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    provenance: ProvenanceStats = field(default_factory=ProvenanceStats)
    prompt_version: str = ""
    batches: int = 0
    unexpected_ids: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0

    @property
    def achieved_rpm(self) -> float:
        """Requests per minute actually reached, retries included.

        The measured number, not the theoretical one. Retries compete for the same
        token bucket as first attempts, so a run with a high stall rate achieves less
        than the pool size suggests -- and that gap is exactly what a capacity estimate
        needs to account for."""
        total = self.usage.calls + self.usage.cached_calls
        return round(total / (self.wall_seconds / 60), 1) if self.wall_seconds else 0.0

    @property
    def succeeded(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if not o.ok]

    def by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.failed:
            key = str(outcome.reason_code)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def failure_rate(self) -> float:
        return len(self.failed) / len(self.outcomes) if self.outcomes else 0.0

    @property
    def schema_failure_rate(self) -> float:
        """Conformance only. A 429 is not a schema failure, and counting it as one
        reported an 8% failure rate in the first spike for a config whose every completed
        call was valid."""
        conformance = sum(
            1
            for o in self.failed
            if o.reason_code
            in (ReasonCode.LLM_MALFORMED_RESPONSE, ReasonCode.LLM_SCHEMA_INVALID)
        )
        completed = sum(
            1
            for o in self.outcomes
            if o.reason_code
            not in (ReasonCode.LLM_RATE_LIMITED, ReasonCode.LLM_TRANSPORT_FAILED)
        )
        return conformance / completed if completed else 0.0

    def as_dict(self) -> dict:
        return {
            "job": self.job,
            "prompt_version": self.prompt_version,
            "batches": self.batches,
            "items": len(self.outcomes),
            "succeeded": len(self.succeeded),
            "failed": len(self.failed),
            "failure_rate": round(self.failure_rate, 5),
            "schema_failure_rate": round(self.schema_failure_rate, 5),
            "by_reason": self.by_reason(),
            "unexpected_ids": self.unexpected_ids,
            "wall_seconds": self.wall_seconds,
            "achieved_rpm": self.achieved_rpm,
            "usage": self.usage.as_dict(),
            "provenance": self.provenance.as_dict(),
        }


def _source_for(item: Mapping) -> Source:
    amounts = {
        value
        for key, value in item.items()
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value
        and any(marker in key.lower() for marker in AMOUNT_KEY_MARKERS)
    }
    return Source(
        item_id=str(item.get("id", "")),
        narration=str(item.get("narration", "")),
        known_amounts=frozenset(amounts),
    )


def _fail_batch(
    items: Sequence[Mapping],
    code: ReasonCode,
    detail: str,
    request: LLMRequest,
    response: LLMResponse | None = None,
) -> list[ItemOutcome]:
    return [
        ItemOutcome(
            item_id=str(item.get("id", "")),
            reason_code=code,
            detail=detail,
            prompt_version=request.prompt_version,
            model=response.model if response else "",
            provider=response.provider if response else "",
            cache_hit=bool(response and response.cache_hit),
        )
        for item in items
    ]


def _call(
    provider: LLMProvider,
    request: LLMRequest,
    cache: ResponseCache | None,
    usage: Usage,
    retry: bool = False,
) -> LLMResponse:
    """One call, through the cache unless this is the retry."""
    if cache is not None and not retry:
        hit = cache.get(request, provider.model)
        if hit is not None:
            usage.record(hit)
            return hit

    started = time.perf_counter()
    response = provider.complete(request)
    if not response.seconds:
        response = LLMResponse(
            **{**response.__dict__, "seconds": round(time.perf_counter() - started, 3)}
        )
    usage.record(response, retry=retry)
    return response


def _decode(response: LLMResponse, model_cls: type) -> tuple[Any | None, ReasonCode | None, str]:
    """JSON, then schema. Returns (parsed, failure_code, detail)."""
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        if response.stalled:
            # Constrained decoding got stuck emitting legal JSON whitespace. Named
            # precisely, because the remedy is a schema change and not a bigger budget --
            # a stall consumes whatever budget it is given.
            detail = (
                f"decoder stalled: {response.raw_chars - len(response.text)} chars of "
                f"whitespace after {len(response.text)} chars of content"
            )
        elif response.truncated:
            detail = f"truncated at {len(response.text)} chars of content"
        else:
            detail = str(exc)[:160]
        return None, ReasonCode.LLM_MALFORMED_RESPONSE, detail

    try:
        return model_cls.model_validate(payload), None, ""
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"])
        return None, ReasonCode.LLM_SCHEMA_INVALID, f"{location}: {first['msg']}"[:160]


@dataclass
class _BatchResult:
    """Everything one batch produced, returned rather than written into shared state.

    Under concurrency this is what keeps the run deterministic. Workers touch nothing
    outside their own batch; the caller merges these in *input order*, so token totals,
    the call log and the provenance statistics come out identical regardless of which
    batch happened to finish first.
    """

    outcomes: list[ItemOutcome] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    provenance: list[ProvenanceResult] = field(default_factory=list)
    unexpected_ids: list[str] = field(default_factory=list)


def _run_batch(
    prompt: Prompt,
    items: Sequence[Mapping],
    provider: LLMProvider,
    cache: ResponseCache | None,
) -> _BatchResult:
    result = _BatchResult()
    request = prompt.request(items)
    model_cls = schema_for(prompt.schema_name)

    try:
        response = _call(provider, request, cache, result.usage)
    except RateLimited as exc:
        result.outcomes = _fail_batch(
            items, ReasonCode.LLM_RATE_LIMITED, str(exc)[:160], request
        )
        return result
    except TransportFailed as exc:
        result.outcomes = _fail_batch(
            items, ReasonCode.LLM_TRANSPORT_FAILED, str(exc)[:160], request
        )
        return result

    parsed, code, detail = _decode(response, model_cls)

    if parsed is None:
        # The single retry. Bypasses the cache: re-reading the same bad bytes would spend
        # the retry to learn nothing.
        try:
            response = _call(provider, request, cache, result.usage, retry=True)
        except RateLimited as exc:
            result.outcomes = _fail_batch(
                items, ReasonCode.LLM_RATE_LIMITED, str(exc)[:160], request, response
            )
            return result
        except TransportFailed as exc:
            result.outcomes = _fail_batch(
                items, ReasonCode.LLM_TRANSPORT_FAILED, str(exc)[:160], request, response
            )
            return result
        parsed, code, detail = _decode(response, model_cls)

    if parsed is None:
        result.outcomes = _fail_batch(items, code, detail, request, response)
        return result

    if cache is not None:
        cache.put(request, response)

    returned = list(parsed.results)
    sent_ids = [str(item.get("id", "")) for item in items]
    envelope = reconcile_ids(sent_ids, [r.id for r in returned])
    result.unexpected_ids.extend(envelope.unexpected)

    by_id: dict[str, Any] = {}
    for entry in returned:
        # First entry wins the slot; a duplicate is refused below rather than overwriting,
        # because two answers for one row means one of them describes something else.
        by_id.setdefault(entry.id, entry)

    per_item_in = response.input_tokens // max(1, len(items))
    per_item_out = response.output_tokens // max(1, len(items))

    outcomes: list[ItemOutcome] = []
    for item in items:
        item_id = str(item.get("id", ""))
        outcome = ItemOutcome(
            item_id=item_id,
            prompt_version=request.prompt_version,
            model=response.model,
            provider=response.provider,
            cache_hit=response.cache_hit,
            input_tokens=per_item_in,
            output_tokens=per_item_out,
        )

        if item_id in envelope.missing or item_id in envelope.duplicated:
            outcome.reason_code = ReasonCode.LLM_BATCH_MISMATCH
            outcome.detail = (
                "no entry returned for this row"
                if item_id in envelope.missing
                else "two entries returned for this row"
            )
            outcomes.append(outcome)
            continue

        entry = by_id[item_id]
        fields = entry.model_dump()

        checked = verify(fields, _source_for(item))
        result.provenance.append(checked)
        outcome.provenance = checked

        if not checked.passed:
            outcome.reason_code = ReasonCode.FIELD_PROVENANCE_FAILED
            outcome.detail = "; ".join(c.detail for c in checked.blocking_failures)[:200]
            outcomes.append(outcome)
            continue

        confidence = fields.get("parse_confidence")
        if isinstance(confidence, int | float) and confidence < MIN_PARSE_CONFIDENCE:
            outcome.reason_code = ReasonCode.LOW_PARSE_CONFIDENCE
            outcome.detail = f"parse_confidence {confidence} below floor {MIN_PARSE_CONFIDENCE}"
            outcomes.append(outcome)
            continue

        outcome.fields = checked.cleaned()
        outcomes.append(outcome)

    result.outcomes = outcomes
    return result


def _merge(result: JobResult, batch: _BatchResult) -> None:
    """Fold one batch into the run, in the caller's thread and in input order."""
    result.outcomes.extend(batch.outcomes)
    result.unexpected_ids.extend(batch.unexpected_ids)
    for checked in batch.provenance:
        result.provenance.record(checked)

    usage = result.usage
    usage.calls += batch.usage.calls
    usage.cached_calls += batch.usage.cached_calls
    usage.retries += batch.usage.retries
    usage.input_tokens += batch.usage.input_tokens
    usage.output_tokens += batch.usage.output_tokens
    usage.cached_input_tokens += batch.usage.cached_input_tokens
    usage.cached_output_tokens += batch.usage.cached_output_tokens
    usage.seconds += batch.usage.seconds
    usage.call_log.extend(batch.usage.call_log)


def run_job(
    job: str,
    items: Sequence[Mapping],
    provider: LLMProvider | None = None,
    cache: ResponseCache | None = None,
    mock: bool = False,
    version: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> JobResult:
    """Run one LLM job over every row, batched at the prompt's declared size.

    Returns an outcome for every row handed in, always. A row that produced no entry, or
    an unverifiable one, comes back with a reason code rather than being absent -- the
    caller must be able to account for every settlement, and silently dropping rows is how
    a coverage number starts flattering itself.

    CONCURRENCY WITHOUT LOSING DETERMINISM

    Batches run in a bounded pool because sequential calls cannot use the rate limit we
    are allowed: at ~20s a call, one at a time is 3 rpm against a 36 rpm ceiling, and a
    25,000-row run takes 101 minutes instead of nine.

    Two properties are preserved, and both are asserted in tests:

    *   **Order.** `executor.map` yields in submission order, so outcomes reassemble in
        input order however completion interleaved.
    *   **Reproducibility.** Workers mutate nothing shared -- each returns a `_BatchResult`
        and the caller folds it in. Token totals, the call log and the provenance
        statistics are therefore accumulated in batch order, not in finish order, so two
        runs of the same input produce byte-identical reports.

    The pool is deliberately smaller than the arithmetic allows. Saturating 36 rpm at 20s
    latency needs ~12 in flight; the default is 8, which leaves room for retries -- which
    compete for the same bucket -- and for a latency spike, without either turning into
    429s. What the run actually achieves is measured rather than assumed: see
    `JobResult.achieved_rpm`.
    """
    prompt = load(job, version=version)
    if provider is None:
        provider = get_provider(mock=mock).provider

    result = JobResult(job=job, prompt_version=prompt.version_id)
    batches = prompt.batches(list(items))
    result.batches = len(batches)
    if not batches:
        return result

    started = time.perf_counter()

    if concurrency <= 1 or len(batches) == 1:
        for batch in batches:
            _merge(result, _run_batch(prompt, batch, provider, cache))
    else:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as pool:
            # map preserves submission order regardless of completion order.
            for outcome in pool.map(
                lambda b: _run_batch(prompt, b, provider, cache), batches
            ):
                _merge(result, outcome)

    result.wall_seconds = round(time.perf_counter() - started, 3)
    return result
