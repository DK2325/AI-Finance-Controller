"""Spike: is batching exceptions into one LLM call safe?

Batching takes a 25,000-row run from 175 minutes to under 10 -- an arithmetic bound that
assumed the rate ceiling was saturated. Measured later at 27.2 rpm with a pool of 8, the
real figure is ~11 minutes; see notes/decisions.md. But mis-ordering is a
*silent correctness bug in a financial control* -- a reason attached to the wrong
exception is worse than no reason, because it looks right.

Four questions, none answered by the single-item spike:

1.  **Ordering.** Does result N describe input N? Checked objectively rather than by
    trusting the model: each narration contains a UTR extractable by regex, so if the
    result carrying id X reports the UTR that is really in narration X, the mapping held.
2.  **Partial malformation.** One contentless item in a batch -- does the whole response
    fail, or come back short? That decides per-batch versus per-item fallback.
3.  **Batch size.** 10 against 20. If 10 is materially more reliable it is worth 17
    minutes instead of 9.
4.  **Quality drift.** Same narrations batched and single. If batching degrades
    extraction, that is a cost paid invisibly.

    python notes/spikes/nim_batching.py
"""

import csv
import json
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from ledgerloop.config import nvidia_api_key, nvidia_base_url, nvidia_model  # noqa: E402

RATE_LIMIT_RPM = 36
CONCURRENCY = 3
N_NARRATIONS = 120
SINGLE_CALL_SAMPLE = 30
UTR_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")


class _Bucket:
    def __init__(self, rpm: int) -> None:
        self.interval = 60.0 / rpm
        self._lock = threading.Lock()
        self._next = 0.0

    def take(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            time.sleep(start - now)


BUCKET = _Bucket(RATE_LIMIT_RPM)


# --------------------------------------------------------------------- schemas


class ParsedItem(BaseModel):
    id: str = Field(description="Echo back the id given for this narration")
    counterparty_name: str
    payment_method: Literal["upi", "neft", "imps", "rtgs", "card", "ach", "unknown"]
    utr: str | None = Field(default=None, description="The 12-digit UTR, or null")
    parse_confidence: float = Field(ge=0.0, le=1.0)


class BatchResult(BaseModel):
    results: list[ParsedItem]


SINGLE_SCHEMA = ParsedItem.model_json_schema()
BATCH_SCHEMA = BatchResult.model_json_schema()

SYSTEM = (
    "You extract structured fields from Indian bank statement narrations. "
    "Respond with a single JSON object and nothing else."
)

BATCH_PROMPT = """Extract fields from EACH narration below.

Return one result per narration, echoing the given id exactly.
If a narration is unreadable, still return an entry for it with parse_confidence 0.

{items}
"""

SINGLE_PROMPT = """Extract fields from this narration.

id: {id}
narration: {narration}
"""


def pick_narrations(n: int) -> list[str]:
    path = Path(__file__).resolve().parents[2] / "data" / "train" / "bank_statement.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r["narration"] for r in csv.DictReader(handle) if r.get("narration")]

    def ugliness(text: str) -> tuple:
        return ("  " in text, not any(c.isspace() for c in text), len(text))

    ranked = sorted(set(rows), key=ugliness, reverse=True)
    # Only narrations carrying a UTR, so the mapping check has ground truth.
    return [t for t in ranked if UTR_RE.search(t)][:n]


def call(client, messages, schema, max_tokens):
    BUCKET.take()
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=nvidia_model(),
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "Result", "strict": True, "schema": schema},
        },
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return (
        (response.choices[0].message.content or "").strip(),
        time.perf_counter() - start,
        response.usage.completion_tokens,
    )


def run_batch(client, batch: list[tuple[str, str]], max_tokens: int) -> dict:
    """One batched call. `batch` is [(id, narration), ...]."""
    items = "\n".join(f"id: {i}\nnarration: {t}\n" for i, t in batch)
    try:
        text, seconds, tokens = call(
            client,
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": BATCH_PROMPT.format(items=items)},
            ],
            BATCH_SCHEMA,
            max_tokens,
        )
    except Exception as exc:
        return {"ok": False, "failure": "transport", "detail": str(exc)[:160]}

    try:
        parsed = BatchResult.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        return {"ok": False, "failure": "schema", "detail": str(exc)[:160],
                "seconds": seconds, "tokens": tokens}

    sent = {i: t for i, t in batch}
    returned = {item.id: item for item in parsed.results}

    # Objective mapping check: does the result for id X carry the UTR really in X?
    mapped_right = 0
    mapped_wrong = 0
    for ident, narration in batch:
        item = returned.get(ident)
        if item is None:
            continue
        truth = UTR_RE.search(narration)
        if truth and item.utr == truth.group(1):
            mapped_right += 1
        elif truth:
            mapped_wrong += 1

    order_stable = [item.id for item in parsed.results] == [i for i, _ in batch]

    return {
        "ok": True,
        "seconds": seconds,
        "tokens": tokens,
        "sent": len(batch),
        "returned": len(parsed.results),
        "ids_all_present": set(returned) == set(sent),
        "order_stable": order_stable,
        "mapped_right": mapped_right,
        "mapped_wrong": mapped_wrong,
        "items": {i: item.model_dump() for i, item in returned.items()},
    }


def run_single(client, ident: str, narration: str) -> dict:
    try:
        text, seconds, tokens = call(
            client,
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": SINGLE_PROMPT.format(id=ident, narration=narration)},
            ],
            SINGLE_SCHEMA,
            700,
        )
        item = ParsedItem.model_validate(json.loads(text))
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:120]}
    truth = UTR_RE.search(narration)
    return {
        "ok": True,
        "seconds": seconds,
        "tokens": tokens,
        "item": item.model_dump(),
        "utr_right": bool(truth) and item.utr == truth.group(1),
    }


def summarise(label: str, results: list[dict]) -> dict:
    good = [r for r in results if r.get("ok")]
    lat = sorted(r["seconds"] for r in good)
    sent = sum(r["sent"] for r in good)
    return {
        "batch": label,
        "calls": len(results),
        "call_failures": len(results) - len(good),
        "items_sent": sent,
        "items_returned": sum(r["returned"] for r in good),
        "calls_with_all_ids": sum(1 for r in good if r["ids_all_present"]),
        "calls_order_stable": sum(1 for r in good if r["order_stable"]),
        "mapped_right": sum(r["mapped_right"] for r in good),
        "mapped_wrong": sum(r["mapped_wrong"] for r in good),
        "p50_seconds": round(statistics.median(lat), 2) if lat else 0,
        "p95_seconds": round(lat[int(len(lat) * 0.95) - 1], 2) if lat else 0,
        "mean_tokens_per_item": round(
            statistics.mean([r["tokens"] / r["sent"] for r in good]) if good else 0
        ),
    }


def main() -> None:
    if not nvidia_api_key():
        raise SystemExit("NVIDIA_API_KEY not set")
    from openai import OpenAI

    client = OpenAI(base_url=nvidia_base_url(), api_key=nvidia_api_key(), timeout=300.0)
    narrations = pick_narrations(N_NARRATIONS)
    tagged = [(f"EX{i:04d}", t) for i, t in enumerate(narrations)]
    print(f"model {nvidia_model()}   {len(tagged)} narrations, all carrying a UTR\n")

    report: dict = {"model": nvidia_model()}

    # --- tests 1 and 3: ordering and batch size -----------------------------
    for size, budget in ((10, 4000), (20, 8000)):
        batches = [tagged[i : i + size] for i in range(0, len(tagged), size)]
        print(f"batch size {size}: {len(batches)} calls...", flush=True)
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(
                pool.map(lambda b, _budget=budget: run_batch(client, b, _budget), batches)
            )
        summary = summarise(str(size), results)
        report[f"batch_{size}"] = summary
        print(f"  {json.dumps(summary)}\n", flush=True)
        for r in results:
            if not r.get("ok"):
                print(f"    FAILURE [{r['failure']}] {r['detail'][:140]}")

    # --- test 2: one contentless item inside a batch ------------------------
    print("partial malformation: one contentless item in a batch of 10...", flush=True)
    poisoned = list(tagged[:6]) + [("EX9999", "-----")] + list(tagged[6:9])
    outcome = run_batch(client, poisoned, 4000)
    report["partial_malformation"] = {
        k: v for k, v in outcome.items() if k != "items"
    }
    if outcome.get("ok"):
        got = outcome["items"].get("EX9999")
        report["partial_malformation"]["contentless_item_returned"] = got is not None
        report["partial_malformation"]["contentless_item"] = got
        print(f"  sent {outcome['sent']}, returned {outcome['returned']}, "
              f"contentless entry present: {got is not None}")
        if got:
            print(f"  its entry: {json.dumps(got)}")
    else:
        print(f"  WHOLE RESPONSE FAILED: {outcome['failure']} {outcome['detail'][:120]}")
    print()

    # --- test 4: quality drift, batched vs single ---------------------------
    print(f"quality drift: {SINGLE_CALL_SAMPLE} narrations single-call...", flush=True)
    sample = tagged[:SINGLE_CALL_SAMPLE]
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        singles = list(pool.map(lambda p: run_single(client, p[0], p[1]), sample))

    batched_items: dict[str, dict] = {}
    for i in range(0, len(sample), 20):
        out = run_batch(client, sample[i : i + 20], 8000)
        if out.get("ok"):
            batched_items.update(out["items"])

    agree = disagree = 0
    single_utr_right = batch_utr_right = 0
    for (ident, _), single in zip(sample, singles, strict=True):
        if not single.get("ok") or ident not in batched_items:
            continue
        s, b = single["item"], batched_items[ident]
        single_utr_right += int(single["utr_right"])
        batch_utr_right += int(s.get("utr") == b.get("utr"))
        for field_name in ("payment_method", "utr"):
            if s.get(field_name) == b.get(field_name):
                agree += 1
            else:
                disagree += 1

    report["quality_drift"] = {
        "compared": len(batched_items),
        "field_agreements": agree,
        "field_disagreements": disagree,
        "agreement_rate": round(agree / (agree + disagree), 4) if agree + disagree else None,
        "single_call_utr_correct": single_utr_right,
        "mean_single_seconds": round(
            statistics.mean([s["seconds"] for s in singles if s.get("ok")]), 2
        ),
    }
    print(f"  {json.dumps(report['quality_drift'])}")

    out_path = Path(__file__).with_name("nim_batching.json")
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(f"\nwritten to {out_path}")


if __name__ == "__main__":
    main()
