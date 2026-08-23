"""Unit economics: measured token cost per 1,000 settlements, at three deterministic shares.

`notes/pricing.md` derives its figures from `data/train`, where 51.06% of exceptions never
reach a model. That share is not a constant -- it is a property of the batch, and it has
now been measured at three values across three batches. Since the deterministic layer is
what keeps the cost down, a cost figure quoted without its deterministic share is quoted
without the variable that drives it.

This measures the third point directly rather than scaling the second one. `data/scale`
gave 3,347 exceptions through the `reason` job with real token counts; `data/test` has 555,
and the per-exception token cost is not assumed to be identical -- the exceptions are drawn
from a different reason-code mix, and `reason` output length varies by code.

**Counting rather than deriving is the whole point of running this.** A cost per 1,000
settlements on `data/test` obtained by scaling the `data/scale` figure would be a number
about `data/scale` wearing another batch's name.

The cache is disabled: a measurement served from cache measures the cache.

**This produces data. The interpretation is not written here.**

    python notes/measurements/unit_economics.py
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

BATCH = Path("data/test")
THRESHOLD = 0.9564


def per_thousand(input_tokens: int, output_tokens: int, settlements: int) -> dict:
    scaled = band(
        round(input_tokens * 1000 / settlements),
        round(output_tokens * 1000 / settlements),
    )
    return {
        "input_tokens": round(input_tokens * 1000 / settlements),
        "output_tokens": round(output_tokens * 1000 / settlements),
        "low_inr": round(scaled.low_inr, 4),
        "high_inr": round(scaled.high_inr, 4),
    }


def main() -> None:
    choice = get_provider()
    if choice.provider.name != "nvidia":
        raise SystemExit(f"expected the live provider, got {choice.provider.name}")

    artifact = Artifact.load("runs/_models/v1")
    outcome = reconcile_batch(BATCH, artifact, threshold=THRESHOLD)
    enumeration = outcome.enumeration
    judgement = [r for r in enumeration.exceptions if needs_llm(r.reason_code)]
    settlements = enumeration.n_settlements

    sources = load_sources(BATCH)
    rows = rows_for_reason(judgement, sources, THRESHOLD)

    print(f"batch          {BATCH}")
    print(f"model          {choice.provider.model}")
    print(f"settlements    {settlements:,}")
    print(f"exceptions     {len(enumeration.exceptions):,}")
    print(f"deterministic  {enumeration.deterministic_share():.2%}")
    print(f"LLM-bound      {len(rows):,} -> {-(-len(rows) // 20)} calls at batch 20")
    print("cache          disabled\n", flush=True)

    started = time.perf_counter()
    result = run_job(
        "reason", rows, provider=choice.provider, cache=ResponseCache(enabled=False)
    )
    wall = time.perf_counter() - started

    usage = result.usage
    cost = band(usage.input_tokens, usage.output_tokens)
    calls = usage.call_log
    stalled = [c for c in calls if c.get("stalled")]

    measured = {
        "batch": str(BATCH).replace("\\", "/"),
        "settlements": settlements,
        "exceptions": len(enumeration.exceptions),
        "deterministic_share": round(enumeration.deterministic_share(), 4),
        "llm_bound": len(rows),
        "by_reason": enumeration.by_reason(),
        "wall_seconds": round(wall, 1),
        "achieved_rpm": result.achieved_rpm,
        "calls_total": len(calls),
        "stalled_calls": len(stalled),
        "stalls_surviving_both_attempts": len(
            [o for o in result.failed if "stalled" in (o.detail or "")]
        ),
        "schema_failure_rate": round(result.schema_failure_rate, 5),
        "overall_failure_rate": round(result.failure_rate, 5),
        "usage": usage.as_dict(),
        "cost_whole_batch": cost.as_dict(),
        "per_1000_settlements": per_thousand(
            usage.input_tokens, usage.output_tokens, settlements
        ),
        "tokens_per_exception": {
            "input": round(usage.input_tokens / len(rows), 1) if rows else 0,
            "output": round(usage.output_tokens / len(rows), 1) if rows else 0,
        },
    }

    print(f"\nwall           {wall:,.1f}s at {result.achieved_rpm} rpm")
    print(f"calls          {len(calls)}, stalled {len(stalled)}, "
          f"schema failures {result.schema_failure_rate:.2%}")
    print(f"tokens         {usage.input_tokens:,} in / {usage.output_tokens:,} out")
    print(f"per exception  {measured['tokens_per_exception']['input']:.1f} in / "
          f"{measured['tokens_per_exception']['output']:.1f} out")
    print(f"whole batch    Rs {cost.low_inr:,.2f} - Rs {cost.high_inr:,.2f}")
    p1k = measured["per_1000_settlements"]
    print(f"per 1,000      Rs {p1k['low_inr']:,.2f} - Rs {p1k['high_inr']:,.2f}")

    # ------------------------------------------------- the three measured points

    scale = json.loads((Path(__file__).with_name("scale_llm.json")).read_text(encoding="utf-8"))
    scale_row = {
        "batch": "data/scale",
        "settlements": 24750,
        "deterministic_share": 0.6542,
        "llm_bound": scale["exceptions_explained"],
        "input_tokens": scale["usage"]["input_tokens"],
        "output_tokens": scale["usage"]["output_tokens"],
        "per_1000_settlements": per_thousand(
            scale["usage"]["input_tokens"], scale["usage"]["output_tokens"], 24750
        ),
        "tokens_per_exception": {
            "input": round(scale["usage"]["input_tokens"] / scale["exceptions_explained"], 1),
            "output": round(scale["usage"]["output_tokens"] / scale["exceptions_explained"], 1),
        },
        "source": "notes/measurements/scale_llm.json",
    }

    # data/train, from notes/pricing.md: 220 in / 168 out per exception over 100 real
    # exceptions, 1,198 LLM-bound of 2,448 exceptions on 4,945 settlements. Carried as a
    # recorded figure rather than re-run, and labelled as the smaller sample it is.
    train_in, train_out = 220 * 1198, 168 * 1198
    train_row = {
        "batch": "data/train",
        "settlements": 4945,
        "deterministic_share": 0.5106,
        "llm_bound": 1198,
        "input_tokens": train_in,
        "output_tokens": train_out,
        "per_1000_settlements": per_thousand(train_in, train_out, 4945),
        "tokens_per_exception": {"input": 220.0, "output": 168.0},
        "source": "notes/pricing.md, per-exception rate measured over 100 exceptions",
        "caveat": "per-exception tokens measured on a 100-exception sample, then scaled",
    }

    test_row = {
        "batch": "data/test",
        "settlements": settlements,
        "deterministic_share": measured["deterministic_share"],
        "llm_bound": len(rows),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "per_1000_settlements": measured["per_1000_settlements"],
        "tokens_per_exception": measured["tokens_per_exception"],
        "source": "measured here, whole population, no scaling",
    }

    points = [train_row, scale_row, test_row]

    print("\nTHE THREE MEASURED POINTS")
    print(f"  {'batch':12} {'settlements':>12} {'determ.':>8} {'LLM-bound':>10} "
          f"{'in/exc':>8} {'out/exc':>8} {'Rs per 1,000 settlements':>26}")
    for row in points:
        p = row["per_1000_settlements"]
        print(f"  {row['batch']:12} {row['settlements']:12,} "
              f"{row['deterministic_share']:8.2%} {row['llm_bound']:10,} "
              f"{row['tokens_per_exception']['input']:8.1f} "
              f"{row['tokens_per_exception']['output']:8.1f} "
              f"{'Rs ' + format(p['low_inr'], ',.2f') + ' - ' + format(p['high_inr'], ',.2f'):>26}")

    report = {
        "measured_on": "2026-08-23",
        "basis": (
            "tokens measured on NVIDIA's free hosted endpoint; priced against published "
            "third-party rates as of 2026-08-23; not billed"
        ),
        "note": (
            "Data only. The written interpretation belongs in notes/pricing.md and is not "
            "generated here."
        ),
        "test_run": measured,
        "points": points,
    }
    out = Path(__file__).with_name("unit_economics.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
