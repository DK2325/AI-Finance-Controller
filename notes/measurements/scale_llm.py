"""The LLM layer at scale: the decoder stall's real measurement.

The stall was observed at roughly 1 call in 5 or 6 across a few dozen calls, and the single
retry rescued every one. That is not a sample that supports a claim. This runs the whole
judgement-exception population of the 25,000-row batch, which is an order of magnitude more
calls, with retries competing for the same token bucket under concurrency.

**Two things that have not happened yet and could:**

1.  A higher stall rate under sustained load than in short bursts.
2.  A stall surviving *both* attempts — producing an exception whose reason code is honest
    (`decoder stalled`) but whose cause we do not understand. An exception we cannot
    explain is worse than one we can, and it would need reporting as such rather than
    being absorbed into a rate.

`Usage.call_log` records `stalled` and `raw_chars` on every call rather than only on
failures, because a stall the retry rescues leaves no exception behind and its only other
trace is the token bill.

    python notes/measurements/scale_llm.py [limit]
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.pipeline import load_sources  # noqa: E402
from llm.cache import ResponseCache  # noqa: E402
from llm.codes import needs_llm  # noqa: E402
from llm.cost import band  # noqa: E402
from llm.handler import run_job  # noqa: E402
from llm.provider import get_provider  # noqa: E402
from model.artifact import Artifact  # noqa: E402
from model.predict import reconcile_batch  # noqa: E402
from notes.measurements.end_to_end import rows_for_reason  # noqa: E402

BATCH = Path("data/scale")
THRESHOLD = 0.9564


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    choice = get_provider()
    if choice.provider.name != "nvidia":
        raise SystemExit(f"expected the live provider, got {choice.provider.name}")

    artifact = Artifact.load("runs/_models/v1")
    outcome = reconcile_batch(BATCH, artifact, threshold=THRESHOLD)
    judgement = [r for r in outcome.enumeration.exceptions if needs_llm(r.reason_code)]
    if limit:
        judgement = judgement[:limit]

    sources = load_sources(BATCH)
    rows = rows_for_reason(judgement, sources, THRESHOLD)

    print(f"model      {choice.provider.model}")
    print(f"exceptions {len(rows):,} judgement exceptions -> "
          f"{-(-len(rows) // 20)} calls at batch 20")
    print("cache      disabled: a measurement served from cache measures the cache\n",
          flush=True)

    started = time.perf_counter()
    result = run_job(
        "reason", rows, provider=choice.provider, cache=ResponseCache(enabled=False)
    )
    wall = time.perf_counter() - started

    calls = result.usage.call_log
    first_attempts = [c for c in calls if not c["retry"]]
    retries = [c for c in calls if c["retry"]]
    stalled = [c for c in calls if c.get("stalled")]
    truncated = [c for c in calls if c.get("truncated")]

    # A stall that survived BOTH attempts leaves an exception behind. That is the outcome
    # the previous sample never produced.
    unrescued = [
        o for o in result.failed if "stalled" in (o.detail or "")
    ]

    cost = band(result.usage.input_tokens, result.usage.output_tokens)

    report = {
        "batch": str(BATCH).replace("\\", "/"),
        "exceptions_explained": len(rows),
        "wall_seconds": round(wall, 1),
        "achieved_rpm": result.achieved_rpm,
        "calls_total": len(calls),
        "first_attempts": len(first_attempts),
        "retries": len(retries),
        "stalled_calls": len(stalled),
        "stall_rate_first_attempt": (
            round(len([c for c in stalled if not c["retry"]]) / len(first_attempts), 4)
            if first_attempts else None
        ),
        "truncated_calls": len(truncated),
        "stalls_surviving_both_attempts": len(unrescued),
        "schema_failure_rate": round(result.schema_failure_rate, 5),
        "overall_failure_rate": round(result.failure_rate, 5),
        "by_reason": result.by_reason(),
        "usage": result.usage.as_dict(),
        "cost": cost.as_dict(),
        "provenance": result.provenance.as_dict(),
    }

    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("usage", "cost", "provenance")}, indent=2))
    print(f"\nusage : {json.dumps(report['usage'])}")
    print(f"cost  : Rs {cost.low_inr:,.2f} - Rs {cost.high_inr:,.2f} "
          f"for {len(rows):,} exceptions")
    per_k = band(
        round(result.usage.input_tokens * 1000 / 24750),
        round(result.usage.output_tokens * 1000 / 24750),
    )
    print(f"        Rs {per_k.low_inr:,.2f} - Rs {per_k.high_inr:,.2f} per 1,000 settlements")

    if unrescued:
        print(f"\n  {len(unrescued)} stall(s) survived BOTH attempts. Reporting as an "
              "exception whose cause is not understood:")
        for outcome_row in unrescued[:3]:
            print(f"    {outcome_row.item_id}: {outcome_row.detail[:120]}")
    else:
        print(f"\n  every stall was rescued by the single retry "
              f"({len(stalled)} stalled call(s) across {len(calls)})")

    out = Path(__file__).with_name("scale_llm.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
