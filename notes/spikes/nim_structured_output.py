"""Spike: does NVIDIA NIM guarantee schema conformance on the hosted endpoint?

This decides a design question in llm/, not a tuning parameter.

If `guided_json` constrains decoding, conformance is guaranteed at decode time and the
retry-then-exception path is a rarely-exercised safety net. If it does not, that path is
load-bearing and the reason-code enum has to tolerate malformed responses. Those are
different implementations, and building one to discover we needed the other is the
expensive outcome.

NVIDIA recommends `nvext.guided_json` over `response_format={"type":"json_object"}`
precisely because json_object permits *any* valid JSON, including `{}`. But those docs
describe self-hosted NIM and support varies by model, so this confirms the hosted
endpoint rather than assuming.

Method: the same 50 real narrations from data/train through three configurations, against
a schema representative of the actual narration-parsing job -- nested object, enum,
optionals, list, bounded float. A two-field object succeeds under any configuration;
failures live in the shapes above.

enable_thinking is false throughout, per the earlier measurement.

    python notes/spikes/nim_structured_output.py
"""

# No `from __future__ import annotations` here: it turns annotations into strings that
# Pydantic cannot resolve for Literal, and this script's whole purpose is generating a
# JSON schema from these models. Python 3.12 handles `str | None` natively anyway.

import csv
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from ledgerloop.config import nvidia_api_key, nvidia_base_url, nvidia_model  # noqa: E402

N_CALLS = 50
CONCURRENCY = 3
MAX_TOKENS = 700
BACKOFF_SECONDS = (2, 5, 12)

# The free tier is capped at 40 requests per minute. Pacing to just under it means calls
# are spaced rather than fired and rejected: a 429 costs a round trip AND a backoff sleep,
# so retry-on-rejection is strictly worse than not being rejected. Backoff stays as the
# safety net for the times pacing is not enough.
RATE_LIMIT_RPM = 36


class TokenBucket:
    """Paces calls to a requests-per-minute ceiling. Thread-safe."""

    def __init__(self, rpm: int) -> None:
        self.interval = 60.0 / rpm
        self._lock = threading.Lock()
        self._next = 0.0

    def take(self) -> float:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        waited = start - now
        if waited > 0:
            time.sleep(waited)
        return waited


BUCKET = TokenBucket(RATE_LIMIT_RPM)


# --------------------------------------------------------------------- schema


class CounterpartyDetail(BaseModel):
    """Nested object -- the shape a flat two-field probe never exercises."""

    normalized_name: str = Field(description="Counterparty with corporate suffix removed")
    vpa: str | None = Field(default=None, description="UPI VPA if present, else null")
    bank_code: str | None = Field(default=None, description="IFSC or 4-letter bank code")


class ParsedNarration(BaseModel):
    """Representative of the real Phase 5 narration parser."""

    counterparty_name: str
    payment_method: Literal["upi", "neft", "imps", "rtgs", "card", "ach", "unknown"]
    utr: str | None = Field(default=None, description="12-digit UTR if present, else null")
    reference_numbers: list[str] = Field(default_factory=list)
    counterparty: CounterpartyDetail
    parse_confidence: float = Field(ge=0.0, le=1.0)
    unparsed_tokens: list[str] = Field(default_factory=list)


SCHEMA = ParsedNarration.model_json_schema()

SYSTEM = (
    "You extract structured fields from Indian bank statement narrations. "
    "Respond with a single JSON object and nothing else."
)

PROMPT = """Extract the fields from this bank narration.

Narration: {narration}

Return JSON with exactly these keys:
  counterparty_name  string
  payment_method     one of: upi, neft, imps, rtgs, card, ach, unknown
  utr                the 12-digit UTR, or null
  reference_numbers  array of any other reference numbers found
  counterparty       object: normalized_name, vpa, bank_code (last two may be null)
  parse_confidence   number between 0 and 1
  unparsed_tokens    array of tokens you could not classify
"""


# ------------------------------------------------------------------ narrations


def pick_narrations(n: int = N_CALLS) -> list[str]:
    """The ugliest real narrations, not clean synthetic ones.

    Ranked by a crude mangling score -- missing separators, doubled spaces, truncation,
    mixed case -- so the sample is weighted toward what actually breaks a parser. Taken
    from data/train, deterministically, so re-running compares like with like.
    """
    path = Path(__file__).resolve().parents[2] / "data" / "train" / "bank_statement.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r["narration"] for r in csv.DictReader(handle) if r.get("narration")]

    def ugliness(text: str) -> tuple:
        return (
            "  " in text,
            not any(ch.isspace() for ch in text),
            sum(ch.isdigit() for ch in text),
            len(text),
        )

    ranked = sorted(set(rows), key=ugliness, reverse=True)
    # Two thirds worst-case, one third spread across the rest, so the sample is hard
    # without being unrepresentative.
    hard = ranked[: (n * 2) // 3]
    step = max(1, len(ranked) // (n - len(hard)))
    rest = ranked[len(hard) :: step][: n - len(hard)]
    return (hard + rest)[:n]


# --------------------------------------------------------------- configurations

CONFIGS = {
    # No constraint at all -- the model is merely asked for JSON.
    "plain": {},
    # Valid JSON guaranteed, correct SHAPE not: `{}` is valid JSON.
    "json_object": {"response_format": {"type": "json_object"}},
    # NVIDIA's documented guided decoding. Measured 400 on the hosted endpoint: nvext
    # accepts greed_sampling, use_raw_prompt, max_thinking_tokens and others, but not
    # guided_json. The docs describe self-hosted NIM.
    "guided_json": {"extra_body": {"nvext": {"guided_json": SCHEMA}}},
    # OpenAI-style structured output. IS supported here, and is the decode-time
    # constraint guided_json was supposed to provide.
    "json_schema": {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "ParsedNarration", "strict": True, "schema": SCHEMA},
        }
    },
}


@dataclass
class CallResult:
    ok: bool
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure: str = ""
    detail: str = ""


@dataclass
class ConfigResult:
    name: str
    calls: list[CallResult] = field(default_factory=list)
    unsupported: str = ""

    def summary(self) -> dict:
        if self.unsupported:
            return {"config": self.name, "unsupported": self.unsupported}
        ok = [c for c in self.calls if c.ok]
        latencies = sorted(c.seconds for c in self.calls if c.seconds)
        failures: dict[str, int] = {}
        for c in self.calls:
            if not c.ok:
                failures[c.failure] = failures.get(c.failure, 0) + 1

        # A 429 is not a schema failure. Conflating them reported an 8% failure rate for
        # a config whose every completed call was valid, which would have made the retry
        # path look load-bearing when it was the rate limiter talking.
        transport = failures.get("rate_limited", 0) + failures.get("transport", 0)
        completed = len(self.calls) - transport
        conformance = failures.get("schema", 0) + failures.get("not_json", 0)
        return {
            "config": self.name,
            "n": len(self.calls),
            "completed": completed,
            "valid": len(ok),
            "schema_failure_rate": round(conformance / completed, 4) if completed else None,
            "transport_failures": transport,
            "p50_seconds": round(statistics.median(latencies), 2) if latencies else 0,
            "p95_seconds": round(latencies[int(len(latencies) * 0.95) - 1], 2) if latencies else 0,
            "mean_prompt_tokens": round(
                statistics.mean([c.prompt_tokens for c in self.calls]) if self.calls else 0
            ),
            "mean_completion_tokens": round(
                statistics.mean([c.completion_tokens for c in self.calls]) if self.calls else 0
            ),
            "failures": failures,
        }


def call_once(client, narration: str, extra: dict) -> CallResult:
    body = {
        "model": nvidia_model(),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(narration=narration)},
        ],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        **{k: v for k, v in extra.items() if k != "extra_body"},
    }
    extra_body = dict(extra.get("extra_body", {}))
    extra_body["chat_template_kwargs"] = {"enable_thinking": False}
    body["extra_body"] = extra_body

    last_error = ""
    for pause in (0, *BACKOFF_SECONDS):
        if pause:
            time.sleep(pause)
        BUCKET.take()
        start = time.perf_counter()
        try:
            response = client.chat.completions.create(**body)
        except Exception as exc:  # rate limits and transport errors
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            if "429" in last_error or "rate" in last_error.lower():
                continue
            return CallResult(False, time.perf_counter() - start, failure="transport",
                              detail=last_error)
        elapsed = time.perf_counter() - start
        usage = response.usage
        text = (response.choices[0].message.content or "").strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return CallResult(False, elapsed, usage.prompt_tokens, usage.completion_tokens,
                              "not_json", str(exc)[:120])
        try:
            ParsedNarration.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            return CallResult(False, elapsed, usage.prompt_tokens, usage.completion_tokens,
                              "schema", f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
        return CallResult(True, elapsed, usage.prompt_tokens, usage.completion_tokens)

    return CallResult(False, 0.0, failure="rate_limited", detail=last_error)


def run_config(client, name: str, extra: dict, narrations: list[str]) -> ConfigResult:
    result = ConfigResult(name=name)

    probe = call_once(client, narrations[0], extra)
    if probe.failure == "transport" and (
        "guided" in probe.detail.lower()
        or "nvext" in probe.detail.lower()
        or "400" in probe.detail
        or "response_format" in probe.detail.lower()
    ):
        result.unsupported = probe.detail
        return result

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        result.calls = list(pool.map(lambda n: call_once(client, n, extra), narrations))
    return result


def main() -> None:
    if not nvidia_api_key():
        print("NVIDIA_API_KEY not set")
        raise SystemExit(1)

    from openai import OpenAI

    client = OpenAI(base_url=nvidia_base_url(), api_key=nvidia_api_key(), timeout=180.0)
    narrations = pick_narrations()

    print(f"model      {nvidia_model()}")
    print(f"narrations {len(narrations)} real, from data/train, ugliest-weighted")
    print(f"schema     {len(SCHEMA.get('properties', {}))} top-level fields, 1 nested object")
    print("thinking   disabled\n")
    print("sample narrations:")
    for text in narrations[:3]:
        print(f"  {text[:96]}")
    print()

    summaries = []
    for name, extra in CONFIGS.items():
        print(f"running {name} ({len(narrations)} calls)...", flush=True)
        started = time.perf_counter()
        outcome = run_config(client, name, extra, narrations)
        summary = outcome.summary()
        summary["wall_seconds"] = round(time.perf_counter() - started, 1)
        summaries.append(summary)
        print(f"  {json.dumps(summary)}\n", flush=True)

        if not outcome.unsupported:
            for call in outcome.calls:
                if not call.ok:
                    print(f"    failure [{call.failure}] {call.detail[:130]}")

    out = Path(__file__).with_name("nim_structured_output.json")
    with out.open("w", newline="", encoding="utf-8") as handle:
        handle.write(json.dumps({"model": nvidia_model(), "n_calls": N_CALLS,
                                 "results": summaries}, indent=2) + "\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
