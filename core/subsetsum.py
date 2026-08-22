"""Bounded subset-sum for batched settlements.

One bank credit can cover several invoices, so the only way to recognise a batch is to
find a set of settlements whose payouts sum to the credit. Unbounded, this hangs on
realistic data: a bucket of 40 settlements has more than a trillion subsets.

Three caps, all explicit, all reported:

    MAX_SUBSET_SIZE     5    the generator batches 2-5, and a real payout batch is small
    MAX_BUCKET_SIZE    25    beyond this the bucket is not searched at all
    PAISE_TOLERANCE     5    a batch may still carry a rounding drift

**A bucket that exceeds the cap is not silently dropped.** It is reported as skipped, and
the pipeline raises an exception with reason code SUBSET_SEARCH_CAPPED for every
settlement in it. A cap that quietly swallows work would make the coverage number a lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

MAX_SUBSET_SIZE = 5
MAX_BUCKET_SIZE = 25
PAISE_TOLERANCE = 5

REASON_CAPPED = "SUBSET_SEARCH_CAPPED"


@dataclass
class SubsetSumStats:
    buckets_searched: int = 0
    buckets_skipped: int = 0
    combinations_examined: int = 0
    subsets_found: int = 0
    skipped_settlement_ids: list[str] = field(default_factory=list)

    @property
    def cap_hit_rate(self) -> float:
        total = self.buckets_searched + self.buckets_skipped
        return self.buckets_skipped / total if total else 0.0

    def as_dict(self) -> dict:
        return {
            "buckets_searched": self.buckets_searched,
            "buckets_skipped": self.buckets_skipped,
            "cap_hit_rate": round(self.cap_hit_rate, 5),
            "combinations_examined": self.combinations_examined,
            "subsets_found": self.subsets_found,
            "n_settlements_capped": len(self.skipped_settlement_ids),
            "caps": {
                "max_subset_size": MAX_SUBSET_SIZE,
                "max_bucket_size": MAX_BUCKET_SIZE,
                "paise_tolerance": PAISE_TOLERANCE,
            },
        }


def find_subset(
    amounts: list[int],
    target: int,
    *,
    max_size: int = MAX_SUBSET_SIZE,
    tolerance: int = PAISE_TOLERANCE,
    stats: SubsetSumStats | None = None,
) -> tuple[int, ...] | None:
    """Indices of a subset summing to `target` within `tolerance`, or None.

    Sizes are tried smallest first: a two-invoice batch is far more common than a
    five-invoice one, and the smaller explanation is the better one when both fit.
    """
    if not amounts or target <= 0:
        return None

    n = len(amounts)
    indices = range(n)

    for size in range(2, min(max_size, n) + 1):
        for combo in combinations(indices, size):
            if stats is not None:
                stats.combinations_examined += 1
            total = sum(amounts[i] for i in combo)
            if abs(total - target) <= tolerance:
                if stats is not None:
                    stats.subsets_found += 1
                return combo
    return None


def search_bucket(
    amounts: list[int],
    target: int,
    *,
    settlement_ids: list[str] | None = None,
    stats: SubsetSumStats | None = None,
) -> tuple[int, ...] | None:
    """Search one bucket, honouring the bucket-size cap.

    Returns None both when nothing was found and when the bucket was too large to
    search. The two are distinguished in `stats`, and the caller raises
    SUBSET_SEARCH_CAPPED for the latter -- they must never look the same downstream.
    """
    stats = stats if stats is not None else SubsetSumStats()

    if len(amounts) > MAX_BUCKET_SIZE:
        stats.buckets_skipped += 1
        if settlement_ids:
            stats.skipped_settlement_ids.extend(settlement_ids)
        return None

    stats.buckets_searched += 1
    return find_subset(amounts, target, stats=stats)
