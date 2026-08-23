"""Experiment A: is the decoder stall caused by the array field?

The stall was observed stopping at `"reference_number":` -- the one array field in the
parse schema. Arrays are a known stall point under constrained decoding, but one observed
position is not a cause, and the previous hypothesis in this investigation was confirmed
twice by experiment before one look at the response body killed it.

So this measures rather than assumes, and it measures three things:

1.  **A control arm.** The previous experiment had an arm that was accidentally identical
    to its control, which made its numbers meaningless. Here the control is deliberate:
    the schema exactly as shipped.
2.  **The removal arm**: the same schema with `reference_number` deleted.
3.  **Where the stall lands, on every repeat.** If it is always at the same field the
    array hypothesis is strong. If it wanders, the hypothesis is dead regardless of what
    the removal arm shows -- a removal arm can come out clean by luck on a
    non-deterministic failure.

It also records UTR extraction against a regex ground truth, because the first measurement
reported 37 nulls where only 29 narrations actually lack a 12-digit UTR.

    python notes/measurements/array_stall.py [repeats]
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ledgerloop.config import nvidia_api_key, nvidia_base_url, nvidia_model  # noqa: E402
from llm.prompt import load  # noqa: E402
from notes.measurements.llm_rates import pick_narrations  # noqa: E402

REPEATS = 5
UTR_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")
LAST_KEY_RE = re.compile(r'"([A-Za-z_]+)"\s*:\s*[^,}\]]*$')


def without_array(schema: dict) -> dict:
    """The parse schema with its single array property removed."""
    trimmed = json.loads(json.dumps(schema))
    for node in [trimmed, *trimmed.get("$defs", {}).values()]:
        properties = node.get("properties")
        if not properties:
            continue
        for name, spec in list(properties.items()):
            if spec.get("type") == "array" and name != "results":
                del properties[name]
                if name in node.get("required", []):
                    node["required"].remove(name)
    return trimmed


def stall_position(content: str) -> str:
    """The field the decoder stopped on, from the last complete key in the content."""
    stripped = content.rstrip()
    match = LAST_KEY_RE.search(stripped[-400:])
    if match:
        return match.group(1)
    keys = re.findall(r'"([A-Za-z_]+)"\s*:', stripped)
    return keys[-1] if keys else "<none>"


def call(client, request, schema: dict, max_tokens: int = 8000) -> dict:
    response = client.chat.completions.create(
        model=nvidia_model(),
        messages=[
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        temperature=0,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "ParsedNarrationBatch", "strict": True, "schema": schema},
        },
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    choice = response.choices[0]
    raw = choice.message.content or ""
    stripped = raw.strip()

    return {
        "finish_reason": choice.finish_reason,
        "completion_tokens": response.usage.completion_tokens,
        "raw_chars": len(raw),
        "content_chars": len(stripped),
        "whitespace_chars": len(raw) - len(stripped),
        "stalled": choice.finish_reason == "length" and len(raw) > len(stripped) * 2 + 1000,
        "stall_at": stall_position(stripped) if choice.finish_reason == "length" else "",
        "text": stripped,
    }


def utr_accuracy(text: str, rows: list[dict]) -> dict:
    """Compare extracted UTRs against a regex over each row's own narration."""
    try:
        results = json.loads(text)["results"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}

    by_id = {r["id"]: r["narration"] for r in rows}
    hits = misses = correct_nulls = wrong = 0
    missed_ids = []
    for entry in results:
        narration = by_id.get(entry.get("id"), "")
        truth = UTR_RE.search(narration)
        got = entry.get("utr")
        if truth and got == truth.group(1):
            hits += 1
        elif truth and not got:
            misses += 1
            missed_ids.append(entry.get("id"))
        elif truth:
            wrong += 1
        elif not got:
            correct_nulls += 1
        else:
            wrong += 1
    return {
        "extracted_correctly": hits,
        "missed_a_real_utr": misses,
        "correct_nulls": correct_nulls,
        "wrong_value": wrong,
        "missed_ids": missed_ids,
    }


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else REPEATS
    if not nvidia_api_key():
        raise SystemExit("NVIDIA_API_KEY not set")
    from openai import OpenAI

    client = OpenAI(base_url=nvidia_base_url(), api_key=nvidia_api_key(), timeout=300.0)

    rows = pick_narrations(100)[20:40]
    prompt = load("parse")
    request = prompt.request(rows)
    with_truth = sum(1 for r in rows if UTR_RE.search(r["narration"]))

    arms = {
        "control (schema as shipped)": prompt.json_schema,
        "no array (reference_number removed)": without_array(prompt.json_schema),
    }

    print(f"model    {nvidia_model()}")
    print(f"rows     {len(rows)}, {with_truth} of which contain a 12-digit UTR")
    print(f"repeats  {repeats} per arm\n", flush=True)

    report: dict = {"model": nvidia_model(), "repeats": repeats, "rows": len(rows),
                    "rows_with_a_real_utr": with_truth, "arms": {}}

    for name, schema in arms.items():
        outcomes = []
        for attempt in range(repeats):
            outcome = call(client, request, schema)
            accuracy = utr_accuracy(outcome["text"], rows)
            outcomes.append({**{k: v for k, v in outcome.items() if k != "text"}, **accuracy})
            print(
                f"  {name[:24]:24} run {attempt + 1}: "
                f"{outcome['finish_reason']:6} tokens={outcome['completion_tokens']:5} "
                f"ws={outcome['whitespace_chars']:6} "
                f"stall_at={outcome['stall_at'] or '-'}",
                flush=True,
            )

        stalls = [o for o in outcomes if o["stalled"]]
        report["arms"][name] = {
            "runs": len(outcomes),
            "stalls": len(stalls),
            "stall_rate": round(len(stalls) / len(outcomes), 3),
            "stall_positions": dict(Counter(o["stall_at"] for o in stalls)),
            "median_tokens_when_clean": (
                sorted(o["completion_tokens"] for o in outcomes if not o["stalled"])[
                    max(0, (len(outcomes) - len(stalls)) // 2)
                ]
                if len(stalls) < len(outcomes)
                else None
            ),
            "utr": {
                "extracted_correctly": sum(o.get("extracted_correctly", 0) for o in outcomes),
                "missed_a_real_utr": sum(o.get("missed_a_real_utr", 0) for o in outcomes),
                "correct_nulls": sum(o.get("correct_nulls", 0) for o in outcomes),
                "wrong_value": sum(o.get("wrong_value", 0) for o in outcomes),
                "missed_ids": sorted({i for o in outcomes for i in o.get("missed_ids", [])}),
            },
        }
        print(f"  -> {json.dumps(report['arms'][name])}\n", flush=True)

    out = Path(__file__).with_name("array_stall.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
