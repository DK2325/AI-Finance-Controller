"""The 25,000-row scale run: throughput, blocking growth, and what reaches the model.

Run locally, not on the hosted instance. The Railway trial is 1 GB RAM and 2 vCPU -- enough
to serve the seeded run and the demo batch, not enough to hold ~37,000 candidates with
their feature vectors while scoring. A scale run that gets OOM-killed halfway produces no
number at all.

**Throughput is reported with the machine named.** A rows-per-second figure with no hardware
beside it is not a measurement, and with a live URL in the README a reader would otherwise
assume it describes the deployed service. It does not.

This runs BEFORE the seal on data/test is broken. Deliberately: tuning blocking for
throughput after reading the test set would let test knowledge inform a decision that then
shapes every test number. Doing it first makes that contamination path impossible rather
than merely unlikely.

    python notes/measurements/scale.py [rows]
"""

import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.blocking import generate_candidates  # noqa: E402
from core.candidates import export_candidates  # noqa: E402
from core.pipeline import load_sources  # noqa: E402
from llm.codes import ReasonCode, needs_llm  # noqa: E402
from model.artifact import Artifact  # noqa: E402
from model.predict import reconcile_batch  # noqa: E402

BATCH = Path("data/scale")
MODEL = Path("runs/_models/v1")

# The pre-committed operating point. Chosen and written down before the seal breaks; see
# notes/phase-7-precommitment.md.
THRESHOLD = 0.9564


class Stage:
    """Wall time and peak memory for one stage, because 1 GB was the constraint that
    decided where this runs."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        tracemalloc.start()
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self.started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.peak_mb = peak / 1024 / 1024
        return False


def main() -> None:
    artifact = Artifact.load(MODEL)

    machine = {
        "cpu": platform.processor() or "unknown",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    print(f"machine   {machine['platform']}")
    print(f"python    {machine['python']}")
    print(f"model     {artifact.model_version}, threshold {THRESHOLD}\n")

    with Stage("load") as load:
        sources = load_sources(BATCH)
    settlements = len(sources.payments)
    print(f"load           {load.seconds:7.2f}s  {settlements:,} settlements, "
          f"{len(sources.bank):,} bank rows  (peak {load.peak_mb:.0f} MB)")

    with Stage("blocking") as blocking:
        candidates, stats = generate_candidates(sources)
    print(f"blocking       {blocking.seconds:7.2f}s  {len(candidates):,} candidates  "
          f"(peak {blocking.peak_mb:.0f} MB)")

    with Stage("features") as features:
        rows = export_candidates(sources)
    print(f"features       {features.seconds:7.2f}s  {len(rows):,} rows featurised  "
          f"(peak {features.peak_mb:.0f} MB)")

    with Stage("score+resolve") as scoring:
        outcome = reconcile_batch(BATCH, artifact, threshold=THRESHOLD)
    print(f"score+resolve  {scoring.seconds:7.2f}s  {len(outcome.matches):,} matched  "
          f"(peak {scoring.peak_mb:.0f} MB)")

    total = load.seconds + blocking.seconds + features.seconds + scoring.seconds
    print(f"\ntotal          {total:7.2f}s  =  {settlements / total:,.0f} settlements/second")

    enumeration = outcome.enumeration
    judgement = [r for r in enumeration.exceptions if needs_llm(r.reason_code)]

    print(f"\nsettlements    {enumeration.n_settlements:,}")
    print(f"  matched      {len(outcome.matches):,}  ({len(outcome.matches)/settlements:.2%})")
    print(f"  exceptions   {len(enumeration.exceptions):,}")
    for code, count in enumeration.by_reason().items():
        marker = "-> LLM" if needs_llm(ReasonCode(code)) else "free  "
        print(f"      {marker}  {code:26} {count:6,}")
    print(f"  deterministic share  {enumeration.deterministic_share():.2%}")
    print(f"  LLM-bound            {len(judgement):,}  "
          f"= {-(-len(judgement) // 20)} calls at batch 20")

    report = {
        "machine": machine,
        "batch": str(BATCH).replace("\\", "/"),
        "model_version": artifact.model_version,
        "threshold": THRESHOLD,
        "settlements": settlements,
        "bank_rows": len(sources.bank),
        "candidates": len(candidates),
        "blocking": stats.as_dict(),
        "seconds": {
            "load": round(load.seconds, 3),
            "blocking": round(blocking.seconds, 3),
            "features": round(features.seconds, 3),
            "score_resolve": round(scoring.seconds, 3),
            "total": round(total, 3),
        },
        "peak_mb": {
            "load": round(load.peak_mb, 1),
            "blocking": round(blocking.peak_mb, 1),
            "features": round(features.peak_mb, 1),
            "score_resolve": round(scoring.peak_mb, 1),
        },
        "settlements_per_second": round(settlements / total, 1),
        "matched": len(outcome.matches),
        "exceptions": len(enumeration.exceptions),
        "by_reason": enumeration.by_reason(),
        "deterministic_share": round(enumeration.deterministic_share(), 4),
        "llm_bound": len(judgement),
        "llm_calls_at_batch_20": -(-len(judgement) // 20),
    }

    out = Path(__file__).with_name("scale.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
