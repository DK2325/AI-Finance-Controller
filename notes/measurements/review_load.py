"""The measured inputs to the unit-economics paragraph, and nothing else.

BUILD.md's Phase 7 note is explicit: *"Produce the matrices and the data. The written
interpretation is the human's own work -- it will be defended in person."* So this computes
every quantity the paragraph needs that can be measured, states the formula, and leaves the
two business assumptions as named inputs rather than picking them.

The two this project cannot measure:

    N = settlements an analyst reconciles per hour, manually
    X = fully-loaded analyst cost per hour, in rupees

Both are properties of a merchant's finance team, not of this system. Choosing them here
and presenting the product as a measurement would be the same error as the agreement metric
that turned out to score transcription: a number that looks measured and is assumed.

    python notes/measurements/review_load.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HERE = Path(__file__).parent

# Candidate values for the two unmeasured inputs, spanning a range wide enough that the
# reader can locate their own team rather than accept ours.
ANALYST_RATES_PER_HOUR = (20, 40, 60)
HOURLY_COST_INR = (300, 600, 1000)


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main() -> None:
    sealed = load("sealed_test.json")
    scale = load("scale_scored.json")
    econ = load("unit_economics.json")
    cost_by_batch = {r["batch"]: r["per_1000_settlements"] for r in econ["points"]}

    rows = []
    for label, data, batch in (
        ("data/test (sealed)", sealed, "data/test"),
        ("data/scale", scale, "data/scale"),
    ):
        settlements = data["settlements"]
        matched = data["matched_settlements"]
        exceptions = data["accounting"]["exceptions"]
        assert matched + exceptions == settlements, "accounting does not close"
        per_k = 1000 / settlements
        rows.append(
            {
                "label": label,
                "batch": batch,
                "settlements": settlements,
                "reviewed_without_the_system_per_1000": 1000,
                "reviewed_with_the_system_per_1000": round(exceptions * per_k, 1),
                "removed_from_review_per_1000": round(matched * per_k, 1),
                "share_removed": round(matched / settlements, 6),
                "inference_cost_per_1000_inr": cost_by_batch[batch],
            }
        )

    print("MEASURED REVIEW LOAD, per 1,000 settlements")
    print(f"  {'batch':20} {'reviewed':>10} {'removed':>9} {'share':>8} "
          f"{'inference Rs':>16}")
    for r in rows:
        c = r["inference_cost_per_1000_inr"]
        print(f"  {r['label']:20} {r['reviewed_with_the_system_per_1000']:10.1f} "
              f"{r['removed_from_review_per_1000']:9.1f} {r['share_removed']:8.2%} "
              f"{'Rs ' + format(c['low_inr'], '.2f') + ' - ' + format(c['high_inr'], '.2f'):>16}")

    print("\nTHE FORMULA, with the two inputs the merchant supplies")
    print("    hours removed per 1,000 settlements  =  removed / N")
    print("    value of those hours                 =  (removed / N) * X")
    print("    against inference                    =  Rs 0.68 - 1.14 per 1,000 (sealed)")
    print("    N = settlements reconciled per analyst-hour   <- not measured here")
    print("    X = fully-loaded analyst cost per hour, Rs    <- not measured here")

    # Sensitivity, on the sealed batch. Presented as a grid so no single pair of
    # assumptions is privileged by being the only one shown.
    sealed_row = rows[0]
    removed = sealed_row["removed_from_review_per_1000"]
    dearest = sealed_row["inference_cost_per_1000_inr"]["high_inr"]

    grid = []
    print(f"\nSENSITIVITY on the sealed batch: {removed:.1f} settlements removed from review "
          f"per 1,000, inference Rs {dearest:.2f} at the dearest rate")
    print(f"  {'N (per hour)':>14} {'X (Rs/hour)':>12} {'hours removed':>14} "
          f"{'value Rs':>10} {'value / inference':>18}")
    for n in ANALYST_RATES_PER_HOUR:
        for x in HOURLY_COST_INR:
            hours = removed / n
            value = hours * x
            grid.append(
                {
                    "settlements_per_analyst_hour": n,
                    "cost_per_hour_inr": x,
                    "hours_removed_per_1000": round(hours, 2),
                    "value_inr_per_1000": round(value, 2),
                    "ratio_to_dearest_inference": round(value / dearest, 1),
                }
            )
            print(f"  {n:14} {x:12} {hours:14.2f} {value:10.2f} "
                  f"{value / dearest:17.0f}x")

    print("\n  The ratio column is value over inference cost at the DEAREST provider rate.")
    print("  It is a ratio of a measured cost to an assumed benefit; the assumption is N and X.")

    report = {
        "note": (
            "Data for the unit-economics paragraph. BUILD.md Phase 7: produce the data, "
            "the written interpretation is the human's own work. N and X are deliberately "
            "not chosen here -- they are properties of a merchant's finance team, not of "
            "this system."
        ),
        "measured": rows,
        "unmeasured_inputs": {
            "N": "settlements reconciled per analyst-hour, manually",
            "X": "fully-loaded analyst cost per hour, in rupees",
        },
        "formula": {
            "hours_removed_per_1000": "removed_from_review_per_1000 / N",
            "value_per_1000": "(removed_from_review_per_1000 / N) * X",
            "compare_against": "inference_cost_per_1000_inr",
        },
        "sensitivity_on_sealed_batch": grid,
        "caveats": [
            "Removal from review is not the same as removal of work: 3 false matches in "
            "3,114 auto-matched rows were wrong, so a merchant accepting the auto-matched "
            "set accepts Rs 671,820 of misposted money on this batch.",
            "The exception queue is not uniform. AMBIGUOUS_CANDIDATES is 26.7% actionable, "
            "so some of the reviewed rows cost an analyst time and yield nothing.",
            "Precision at 24,750 settlements is 99.2369%, below the floor. Any value "
            "claimed at production volume inherits that, not the sealed 99.9037%.",
        ],
    }
    out = HERE / "review_load.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
