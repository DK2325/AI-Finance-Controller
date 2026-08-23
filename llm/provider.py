"""The provider seam: one protocol, a mock, and NVIDIA NIM behind it.

WHY A SEAM AT ALL

Not for provider-shopping. For two concrete reasons:

1.  `--mock-llm` has to run the entire pipeline with no key present, and it has to run
    the *same* code path -- same prompts, same schema validation, same provenance gate,
    same reason codes. A mock that shortcuts past validation proves nothing, because the
    thing most likely to break is the validation.

2.  If NIM is unavailable on 3 September, swapping providers must be a config change and
    not a rewrite two days before the deadline.

WHY THE MOCK IS ALSO THE FAULT INJECTOR

Every failure this system claims to handle needs a test, and most of them cannot be
provoked on demand against a live endpoint: you cannot ask a provider for a 429 at a
chosen moment, or for the one-in-two-hundred response where a field belongs to a
different item. So MockProvider takes a `Fault` and produces each failure deterministically.
The reason-code enum's FAILURE family and the tests that exercise it are the same set.

The strongest of these is `Fault.OBEYS_INJECTION`, which simulates a *fully compromised*
model -- one that reads instruction text in a bank narration and does what it says. The
test then asserts the pipeline's output is unchanged. That is the real claim: not that
the model resists injection, but that nothing it returns is trusted enough for resisting
to be necessary.

WHY THE MOCK IS SCHEMA-DRIVEN

MockProvider builds its response by walking the JSON Schema it was handed, rather than
from a hardcoded shape per job. A hardcoded mock drifts the moment a prompt gains a
field, and it drifts *silently* -- passing tests while no longer resembling the real
response. Walking the schema means the mock cannot fall behind it.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ledgerloop.config import (
    llm_provider_name,
    nvidia_api_key,
    nvidia_base_url,
    nvidia_model,
)
from llm.ratelimit import DEFAULT_RPM, TokenBucket

UTR_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")

# Retry only what retrying can fix. A 429 and a 503 are worth another attempt; a 400 means
# the request is wrong and sending it again wastes a slot to learn the same thing.
BACKOFF_SECONDS = (2.0, 5.0, 12.0)


# ------------------------------------------------------------------- exceptions


class LLMError(Exception):
    """Base for anything that stopped a call completing. Maps to the FAILURE family."""


class RateLimited(LLMError):
    """Throughput exhausted after backoff. Re-runnable; the data is fine."""


class TransportFailed(LLMError):
    """Network, timeout, or provider 5xx. Also re-runnable, and deliberately not the same
    code as RateLimited -- a provider outage is not our throughput problem."""


class ProviderUnavailable(LLMError):
    """Asked for a real provider with no credential. Raised at construction, not mid-run."""


# ---------------------------------------------------------------------- the wire


@dataclass(frozen=True)
class LLMRequest:
    """One call. Rendered prompt plus the structured source it was rendered from."""

    job: str
    system: str
    user: str
    schema: dict
    schema_name: str
    prompt_version: str
    max_tokens: int = 4000
    # Off by default: measured at 5.4x the output tokens for structurally identical
    # results under json_schema decoding. Declared per prompt, never changed mid-run,
    # and recorded in the audit record beside the prompt version.
    enable_thinking: bool = False

    # The items the prompt was built from. NEVER sent over the wire -- NvidiaProvider
    # serialises `system` and `user` only, and a test asserts the request body excludes
    # this. It is here because two things downstream need the source rows: the mock, so
    # it can answer from the real input instead of parsing its own prompt back out, and
    # the provenance gate, which verifies every extracted field against the exact
    # narration it was supposed to come from.
    context: tuple[dict, ...] = ()


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    cache_hit: bool = False
    # The model ran out of room mid-object. Worth its own flag: truncated output is
    # malformed output, but the fix is a token budget rather than a prompt change, and
    # one "malformed" bucket covering both sends you looking in the wrong place.
    truncated: bool = False

    def as_audit_fields(self) -> dict:
        return {
            "provider": self.provider,
            "model_name": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit": self.cache_hit,
        }


@runtime_checkable
class LLMProvider(Protocol):
    """Text in, text out. Everything that makes the text *mean* something -- schema
    validation, provenance, reason codes -- lives above this line, so it is exercised
    identically whichever provider is underneath."""

    name: str
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# --------------------------------------------------------------------- the mock


class Fault(StrEnum):
    """Failure modes the mock can produce on demand. One per thing we claim to survive."""

    NONE = "none"
    MALFORMED = "malformed"
    SCHEMA_INVALID = "schema_invalid"
    # A field from one item attached to another, with the envelope perfect: ids all
    # echoed, order stable, count correct. Measured at ~1 in 200 in the batching spike.
    # This is the fault the provenance gate exists for.
    CROSS_CONTAMINATION = "cross_contamination"
    # Fewer results than items sent. The spike never saw it; a batch of 20 near the token
    # ceiling is where it would appear.
    SHORT_BATCH = "short_batch"
    RATE_LIMITED = "rate_limited"
    TRANSPORT = "transport"
    TRUNCATED = "truncated"
    # A model that reads instruction text in a narration and complies with it.
    OBEYS_INJECTION = "obeys_injection"


# Instruction-shaped text. Used ONLY by the mock, to decide when to play compromised --
# nothing in the real path scans for these, because a blocklist is not a defence and
# pretending otherwise would be worse than not having one.
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard",
    "system:",
    "you must",
    "instead output",
    "mark this as",
)

_METHOD_KEYWORDS = {
    "upi": ("upi", "@ok", "@ybl", "@paytm", "vpa"),
    "neft": ("neft",),
    "imps": ("imps",),
    "rtgs": ("rtgs",),
    "card": ("card", "pos", "visa", "mastercard"),
    "ach": ("ach", "nach", "mandate"),
}


def _counterparty_guess(narration: str) -> str:
    """The longest run of letters, which is what a name usually is in a mangled narration.

    Crude on purpose. The mock is not meant to be a good parser -- it is meant to be a
    *plausible* one, so that the layers above it do real work on real-shaped input.
    """
    words = re.findall(r"[A-Za-z]{3,}", narration)
    skip = {"NEFT", "IMPS", "RTGS", "UPI", "CR", "DR", "REF", "TRF", "PMT", "BY", "TO", "FROM"}
    kept = [w for w in words if w.upper() not in skip]
    return " ".join(kept[:2]).upper() if kept else ""


def _method_guess(narration: str, allowed: list[str]) -> str:
    lowered = narration.lower()
    for method, keywords in _METHOD_KEYWORDS.items():
        if method in allowed and any(k in lowered for k in keywords):
            return method
    return "unknown" if "unknown" in allowed else allowed[0]


class MockProvider:
    """Deterministic, offline, and capable of every failure the real one can produce.

    Same input, same bytes out -- no clock, no randomness -- so `--mock-llm` runs are
    reproducible and a diff in a mock run means a diff in the code above it.
    """

    name = "mock"

    def __init__(self, fault: Fault = Fault.NONE, model: str = "mock-1") -> None:
        self.fault = fault
        self.model = model
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1

        if self.fault is Fault.RATE_LIMITED:
            raise RateLimited("mock: 429 after backoff")
        if self.fault is Fault.TRANSPORT:
            raise TransportFailed("mock: connection reset")

        payload = self._build(request)

        if self.fault is Fault.MALFORMED:
            text = "Here is the JSON you asked for:\n{" + json.dumps(payload)[1:-1]
        elif self.fault is Fault.TRUNCATED:
            text = json.dumps(payload)[: max(1, len(json.dumps(payload)) // 2)]
        else:
            text = json.dumps(payload)

        return LLMResponse(
            text=text,
            model=self.model,
            provider=self.name,
            # Rough but stable: enough for the cost path to be exercised, and no mock run
            # ever reports a rupee figure, so precision here would be false precision.
            input_tokens=len(request.system) // 4 + len(request.user) // 4,
            output_tokens=len(text) // 4,
            truncated=self.fault is Fault.TRUNCATED,
        )

    # -- response construction, driven by the schema rather than by a fixed shape ----

    def _build(self, request: LLMRequest) -> Any:
        schema = request.schema
        array_field = _batch_field(schema)

        if array_field is None:
            item = request.context[0] if request.context else {}
            return self._object(_item_schema(schema, array_field=None), item, schema)

        items = list(request.context)
        if self.fault is Fault.SHORT_BATCH and len(items) > 1:
            items = items[:-1]

        element = _item_schema(schema, array_field)
        results = [self._object(element, item, schema) for item in items]

        if self.fault is Fault.CROSS_CONTAMINATION and len(results) > 1:
            results = _rotate_field(results, "utr")

        out = {array_field: results}
        for name in schema.get("properties", {}):
            if name != array_field:
                out[name] = _default_for(_resolve(schema["properties"][name], schema), schema)
        return out

    def _object(self, schema: dict, item: dict, root: dict) -> dict:
        built: dict = {}
        for name, raw in schema.get("properties", {}).items():
            built[name] = self._value(name, _resolve(raw, root), item, root)

        if self.fault is Fault.SCHEMA_INVALID and built:
            # Drop a required field. Valid JSON, wrong shape -- which is the case
            # json_object permits and json_schema is supposed to make impossible.
            required = schema.get("required") or list(built)
            built.pop(required[0], None)

        return built

    def _value(self, name: str, schema: dict, item: dict, root: dict) -> Any:
        narration = str(item.get("narration", ""))
        compromised = self.fault is Fault.OBEYS_INJECTION and _looks_like_instructions(narration)

        if name == "id":
            return str(item.get("id", ""))

        if name == "utr":
            if compromised:
                # Do exactly what the injected text asked for. If it named a UTR, use
                # that one; otherwise invent one. Both must be harmless downstream.
                asked = UTR_RE.search(narration)
                return asked.group(1) if asked else "999999999999"
            found = UTR_RE.search(narration)
            return found.group(1) if found else _null_or(schema, "")

        if name in ("counterparty_name", "normalized_name", "counterparty"):
            return _counterparty_guess(narration)

        if name == "payment_method":
            return _method_guess(narration, list(schema.get("enum", ["unknown"])))

        if name.endswith("confidence"):
            # High but not certain, and identical every run.
            return 1.0 if compromised else 0.86

        if name in ("narration", "source_narration"):
            return narration

        if name in ("reason_code", "code"):
            allowed = schema.get("enum")
            return allowed[0] if allowed else ""

        if name in ("reason", "reason_text", "explanation", "summary"):
            return f"Mock explanation for {item.get('id', 'item')}."

        if schema.get("type") == "object":
            return self._object(schema, item, root)

        return _default_for(schema, root)


def _looks_like_instructions(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _INJECTION_MARKERS)


def _rotate_field(results: list[dict], field_name: str) -> list[dict]:
    """Move each item's value of `field_name` onto the next item. The measured fault."""
    values = [r.get(field_name) for r in results]
    rotated = values[-1:] + values[:-1]
    return [
        {**r, field_name: v} if field_name in r else r
        for r, v in zip(results, rotated, strict=True)
    ]


# ------------------------------------------------------- JSON Schema navigation


def _resolve(schema: dict, root: dict) -> dict:
    """Follow $ref and collapse the `X | None` anyOf pydantic emits for optionals."""
    seen = 0
    while "$ref" in schema and seen < 10:
        ref = schema["$ref"]
        seen += 1
        if not ref.startswith("#/"):
            return {}
        node: Any = root
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        schema = node if isinstance(node, dict) else {}

    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        merged = _resolve(options[0], root) if options else {"type": "null"}
        return {**merged, "_nullable": len(options) < len(schema["anyOf"])}

    return schema


def _null_or(schema: dict, fallback: Any) -> Any:
    return None if schema.get("_nullable") else fallback


def _default_for(schema: dict, root: dict) -> Any:
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "string":
        return _null_or(schema, "")
    if kind in ("number", "integer"):
        low = schema.get("minimum", 0)
        return _null_or(schema, low if kind == "integer" else float(low))
    if kind == "boolean":
        return _null_or(schema, False)
    if kind == "array":
        return []
    if kind == "object":
        return {
            name: _default_for(_resolve(sub, root), root)
            for name, sub in schema.get("properties", {}).items()
        }
    return None


def _batch_field(schema: dict) -> str | None:
    """The array property that carries one entry per input item, if this is a batch schema."""
    for name, raw in schema.get("properties", {}).items():
        if raw.get("type") == "array":
            return name
    return None


def _item_schema(schema: dict, array_field: str | None) -> dict:
    if array_field is None:
        return schema
    items = schema["properties"][array_field].get("items", {})
    return _resolve(items, schema)


# ------------------------------------------------------------------ NVIDIA NIM


class NvidiaProvider:
    """NVIDIA NIM over the OpenAI-compatible SDK.

    Structured output is locked to `response_format={"type": "json_schema", strict}`.
    Measured over 50 real narrations: 0.0% schema failures against 4.0% unconstrained,
    and the best p95 of the three configurations that work. `nvext.guided_json`, which
    NVIDIA's own docs recommend, returns 400 on the hosted endpoint -- those docs
    describe self-hosted NIM. See notes/spikes/nim_structured_output.json.
    """

    name = "nvidia"

    def __init__(
        self,
        rpm: int = DEFAULT_RPM,
        timeout: float = 180.0,
        bucket: TokenBucket | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = nvidia_api_key()
        if key is None:
            raise ProviderUnavailable(
                "NVIDIA_API_KEY is not set. Run with --mock-llm, or set LLM_PROVIDER=mock."
            )
        from openai import OpenAI

        self.model = nvidia_model()
        self.bucket = bucket or TokenBucket(rpm)
        self._sleep = sleep
        self._client = OpenAI(base_url=nvidia_base_url(), api_key=key, timeout=timeout)

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": 0,
            "max_tokens": request.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.schema,
                },
            },
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": request.enable_thinking}
            },
        }

        last = ""
        for pause in (0.0, *BACKOFF_SECONDS):
            if pause:
                self._sleep(pause)
            self.bucket.take()

            started = time.perf_counter()
            try:
                response = self._client.chat.completions.create(**body)
            except Exception as exc:
                last = f"{type(exc).__name__}: {str(exc)[:200]}"
                if _is_retryable(last):
                    continue
                raise TransportFailed(last) from exc

            choice = response.choices[0]
            usage = response.usage
            return LLMResponse(
                text=(choice.message.content or "").strip(),
                model=self.model,
                provider=self.name,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                seconds=round(time.perf_counter() - started, 3),
                truncated=getattr(choice, "finish_reason", "") == "length",
            )

        raise RateLimited(f"retries exhausted: {last}")


def _is_retryable(message: str) -> bool:
    lowered = message.lower()
    return (
        "429" in message
        or "rate" in lowered
        or "timeout" in lowered
        or "timed out" in lowered
        or "connection" in lowered
        or any(code in message for code in ("500", "502", "503", "504"))
    )


# ------------------------------------------------------------------- selection


@dataclass
class ProviderChoice:
    """What was selected and why, so a run can say which it used without guessing."""

    provider: LLMProvider
    reason: str
    forced_by_flag: bool = False
    faults: Fault = field(default=Fault.NONE)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider.name,
            "model": self.provider.model,
            "reason": self.reason,
            "mock_llm": self.provider.name == "mock",
        }


def get_provider(mock: bool = False, fault: Fault = Fault.NONE) -> ProviderChoice:
    """Resolve the provider. `--mock-llm` wins over everything.

    Precedence is explicit rather than clever: the flag, then LLM_PROVIDER, then whether
    a key exists at all. A run that was asked for the mock must never reach the network,
    including by accident on a machine that happens to have a key configured.
    """
    if mock:
        return ProviderChoice(
            MockProvider(fault), reason="--mock-llm was passed", forced_by_flag=True, faults=fault
        )

    name = llm_provider_name()
    if name == "mock":
        # Distinguish "asked for the mock" from "fell back to the mock". A run that meant
        # to use the real provider and quietly used the mock instead would report
        # measurements that came from nowhere.
        reason = "LLM_PROVIDER=mock" if nvidia_api_key() else "no NVIDIA_API_KEY is set"
        return ProviderChoice(MockProvider(fault), reason=reason, faults=fault)

    if name == "nvidia":
        return ProviderChoice(NvidiaProvider(), reason="LLM_PROVIDER=nvidia")

    raise ProviderUnavailable(
        f"unknown LLM_PROVIDER {name!r}. Known providers: mock, nvidia."
    )
