"""Does a batch of near-identical narrations make the model loop?

Observed twice, on the same batch, in two consecutive runs of llm_rates.py: output hit the
8,000-token ceiling and truncated, where every other batch of the same size finished in
~1,800 tokens. Roughly 90 tokens per item is normal; that batch produced over 400.

The batch in question is twenty rows of the same shape:

    NEFT-HDFC0002341-NASHIK CASTINGS P  LTD-UTR300000001781
    NEFT-HDFC0009812-HALDIA METALS  LIMITED-UTR300000004377
    ...

Same bank dialect, same field order, same length band, differing only in the name and the
digits. Repetition in the input is a well-known trigger for repetition in the output, and
this measures whether that is what is happening rather than assuming it.

Three arms, same twenty rows' worth of work in each:

    homogeneous    the twenty near-identical rows, as sent today
    diversified    ten of those plus ten of a different dialect, interleaved
    halved         the same twenty as two batches of ten

If homogeneous truncates and diversified does not, the remedy is how batches are composed,
not the token ceiling. Repeated three times per arm because a single observation of a
non-deterministic failure is an anecdote.

    python notes/measurements/batch_homogeneity.py
"""

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm.cache import ResponseCache  # noqa: E402
from llm.handler import run_job  # noqa: E402
from llm.provider import get_provider  # noqa: E402
from notes.measurements.llm_rates import pick_narrations  # noqa: E402

REPEATS = 3
NO_CACHE = ResponseCache(enabled=False)


def arms() -> dict[str, list[list[dict]]]:
    pool = pick_narrations(400)
    homogeneous = pool[20:40]

    # A different dialect: the generator writes ICICI narrations with slash separators and
    # mixed case, against HDFC's hyphens and upper case. Picked by looking for the
    # separator rather than by naming a bank, so this does not depend on generator
    # internals the matcher is not allowed to know.
    other = [r for r in pool if r["narration"].count("/") >= 2][:10]
    if len(other) < 10:
        other = [r for r in pool[200:] if r not in homogeneous][:10]

    interleaved = [x for pair in zip(homogeneous[:10], other, strict=True) for x in pair]

    return {
        "homogeneous": [homogeneous],
        "diversified": [interleaved],
        "halved": [homogeneous[:10], homogeneous[10:]],
    }


def main() -> None:
    choice = get_provider()
    if choice.provider.name != "nvidia":
        raise SystemExit(f"expected the live provider, got {choice.provider.name}")

    print(f"model {choice.provider.model}   {REPEATS} repeats per arm\n", flush=True)
    report: dict = {"model": choice.provider.model, "repeats": REPEATS, "arms": {}}

    for name, batches in arms().items():
        rows = [r for b in batches for r in b]
        per_item_tokens: list[float] = []
        truncations = 0
        failures = 0
        seconds: list[float] = []

        for attempt in range(REPEATS):
            started = time.perf_counter()
            result = run_job("parse", rows, provider=choice.provider, cache=NO_CACHE)
            elapsed = time.perf_counter() - started

            for call in result.usage.call_log:
                if call["retry"]:
                    continue
                items_in_call = len(rows) / max(1, len(batches))
                per_item_tokens.append(call["output_tokens"] / items_in_call)
                truncations += int(call["truncated"])

            failures += len(result.failed)
            seconds.append(elapsed)
            print(
                f"  {name:12} run {attempt + 1}: "
                f"{[c['output_tokens'] for c in result.usage.call_log]} out, "
                f"truncated={[c['truncated'] for c in result.usage.call_log]}, "
                f"failed={len(result.failed)}",
                flush=True,
            )

        calls = len(per_item_tokens)
        report["arms"][name] = {
            "rows": len(rows),
            "batches_per_run": len(batches),
            "first_attempt_calls": calls,
            "truncated_calls": truncations,
            "truncation_rate": round(truncations / calls, 3) if calls else 0.0,
            "items_failed_after_retry": failures,
            "median_output_tokens_per_item": round(statistics.median(per_item_tokens), 1),
            "max_output_tokens_per_item": round(max(per_item_tokens), 1),
            "median_seconds": round(statistics.median(seconds), 1),
        }
        print(f"  -> {json.dumps(report['arms'][name])}\n", flush=True)

    out = Path(__file__).with_name("batch_homogeneity.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
