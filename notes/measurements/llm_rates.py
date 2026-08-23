"""Measure the rates Phase 5 claims, against the live endpoint and real narrations.

Three numbers that do not exist until this runs:

1.  **Schema failure rate, batched.** The spike measured 0.0% over 50 *single* calls.
    Batched at 20 the response is a different shape and the constraint may behave
    differently. Conformance only -- a 429 is not a schema failure.
2.  **Provenance failure rate**, broken down by field and by verdict. Aggregate is not
    enough: if counterparty dominates and everything else is near zero, the remedy is a
    targeted change to one comparison rule rather than a loosening of the gate.
3.  **Token spend**, recorded per call, so the rupee figure in Phase 7 is computed from a
    measurement rather than an estimate.

For every counterparty failure it also dumps the narration, the extracted value and the
missing tokens, so a human can judge the distinction that matters:

    the model was wrong        -- it invented or misread a name        -> exception routing
    the check was too literal  -- ACME INDS -> ACME INDUSTRIES         -> better comparison

Those need different remedies, and an aggregate rate cannot tell them apart.

    python notes/measurements/llm_rates.py [n] [concurrency]
"""

import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm.cache import ResponseCache  # noqa: E402
from llm.handler import run_job  # noqa: E402
from llm.provenance import Verdict  # noqa: E402
from llm.provider import get_provider  # noqa: E402

DEFAULT_N = 100
SAMPLE_FAILURES = 15
UTR_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")


def pick_narrations(n: int) -> list[dict]:
    """Real narrations from data/train, ugliest-weighted, deterministically.

    Same selection rule as the two spikes, so the numbers compare like with like.
    """
    path = Path(__file__).resolve().parents[2] / "data" / "train" / "bank_statement.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r["narration"] for r in csv.DictReader(handle) if r.get("narration")]

    def ugliness(text: str) -> tuple:
        return ("  " in text, not any(c.isspace() for c in text),
                sum(c.isdigit() for c in text), len(text))

    ranked = sorted(set(rows), key=ugliness, reverse=True)
    hard = ranked[: (n * 2) // 3]
    step = max(1, len(ranked) // max(1, n - len(hard)))
    rest = ranked[len(hard) :: step][: n - len(hard)]
    chosen = (hard + rest)[:n]
    return [{"id": f"EX{i:04d}", "narration": t} for i, t in enumerate(chosen)]


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 8

    choice = get_provider()
    if choice.provider.name != "nvidia":
        raise SystemExit(
            f"expected the live provider, got {choice.provider.name} ({choice.reason})"
        )

    rows = pick_narrations(n)
    print(f"model      {choice.provider.model}")
    print(f"narrations {len(rows)} real, from data/train, ugliest-weighted")
    print("cache      disabled for this measurement\n", flush=True)

    started = time.perf_counter()
    # Cache off: a measurement served from cache measures the cache.
    result = run_job(
        "parse", rows, provider=choice.provider,
        cache=ResponseCache(enabled=False), concurrency=concurrency,
    )
    wall = time.perf_counter() - started

    # ---- verdict counts per field, across every check that was made ----------
    per_field: dict[str, Counter] = {}
    for outcome in result.outcomes:
        if outcome.provenance is None:
            continue
        for check in outcome.provenance.checks:
            per_field.setdefault(check.field, Counter())[str(check.verdict)] += 1

    # ---- the counterparty sample, for the judgement call ---------------------
    samples = []
    for outcome in result.outcomes:
        if outcome.provenance is None:
            continue
        for check in outcome.provenance.checks:
            if check.failed and "name" in check.field or check.field == "counterparty":
                narration = next(r["narration"] for r in rows if r["id"] == outcome.item_id)
                samples.append({
                    "id": outcome.item_id,
                    "extracted": check.value,
                    "missing_tokens": check.detail,
                    "narration": narration,
                })
    samples = samples[:SAMPLE_FAILURES]

    report = {
        "model": choice.provider.model,
        "n": len(rows),
        "wall_seconds": round(wall, 1),
        "batches": result.batches,
        "concurrency": concurrency,
        "achieved_rpm": result.achieved_rpm,
        "schema_failure_rate": round(result.schema_failure_rate, 5),
        "overall_failure_rate": round(result.failure_rate, 5),
        "by_reason": result.by_reason(),
        "provenance": result.provenance.as_dict(),
        "provenance_by_field": {k: dict(sorted(v.items())) for k, v in sorted(per_field.items())},
        "usage": result.usage.as_dict(),
        "call_log": result.usage.call_log,
        "failure_details": [
            {"id": o.item_id, "code": str(o.reason_code), "detail": o.detail}
            for o in result.failed
        ][:6],
        "tokens_per_item": round(result.usage.billed_tokens / max(1, len(rows)), 1),
        "counterparty_failures_sample": samples,
    }

    print(json.dumps({k: v for k, v in report.items()
                      if k != "counterparty_failures_sample"}, indent=2))

    if samples:
        print(f"\n--- {len(samples)} counterparty failures, for the wrong-vs-too-literal call ---")
        for s in samples:
            print(f"\n  id        {s['id']}")
            print(f"  narration {s['narration'][:110]}")
            print(f"  extracted {s['extracted']!r}")
            print(f"  {s['missing_tokens']}")

    out = Path(__file__).with_name("llm_rates.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")

    # Verdict counts must reconcile with the item count, or the breakdown is not a
    # breakdown of anything.
    checked = sum(
        c[str(Verdict.PRESENT)] + c[str(Verdict.ABSENT)] for c in per_field.values()
    )
    assert checked == result.provenance.fields_checked, "the per-field split does not reconcile"


if __name__ == "__main__":
    main()
