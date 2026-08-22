"""Batch generation: allocate case types, run the builders, write four CSVs.

Determinism is the contract. One seeded Random is threaded through every builder; nothing
reads the clock or global random state, and rows are written in generation order. The
same seed produces byte-identical files, which tests/test_generator.py asserts.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from datagen.cases import BUILDERS, Ctx, Emission
from datagen.schemas import (
    BANK_COLUMNS,
    CASE_NAMES,
    GATEWAY_COLUMNS,
    INVOICE_COLUMNS,
    TRUTH_COLUMNS,
    target_shares,
)

GATEWAY_FILE = "gateway_settlements.csv"
BANK_FILE = "bank_statement.csv"
INVOICE_FILE = "invoice_ledger.csv"
TRUTH_FILE = "truth.csv"
MANIFEST_FILE = "manifest.json"

# Case types that emit more than one truth row per instance, so the allocator knows it
# cannot simply run one instance per required row.
_ROWS_PER_INSTANCE_MIN = {"batched_settlement": 2, "duplicate_utr": 2}


def allocate(rows: int, exclude: tuple[str, ...] = ()) -> dict[str, int]:
    """Split `rows` across case types by target share, using largest remainder.

    Largest-remainder rather than naive rounding: rounding each share independently loses
    or gains rows and would push a case type outside the 1% tolerance the exit criteria
    require. This distributes the leftover deterministically, so the counts sum to `rows`
    exactly.
    """
    shares = target_shares(exclude)
    exact = {name: rows * share for name, share in shares.items()}
    counts = {name: int(value) for name, value in exact.items()}

    shortfall = rows - sum(counts.values())
    if shortfall:
        # Sort by remainder descending, then by name for a stable tie-break.
        order = sorted(exact, key=lambda n: (-(exact[n] - counts[n]), n))
        for name in order[:shortfall]:
            counts[name] += 1

    return counts


def generate(
    rows: int,
    seed: int,
    exclude: tuple[str, ...] = (),
) -> tuple[Emission, dict]:
    """Build one batch in memory. Returns the emission and a manifest describing it."""
    unknown = set(exclude) - set(CASE_NAMES)
    if unknown:
        raise ValueError(f"unknown case types to exclude: {sorted(unknown)}")

    rng = random.Random(seed)
    ctx = Ctx(rng=rng)
    counts = allocate(rows, exclude)

    out = Emission()
    produced: dict[str, int] = dict.fromkeys(counts, 0)

    # Iterate case types in a fixed order so generation is reproducible.
    for name in sorted(counts):
        builder = BUILDERS[name]
        target = counts[name]
        minimum = _ROWS_PER_INSTANCE_MIN.get(name, 1)

        while produced[name] < target:
            remaining = target - produced[name]
            # A multi-row case cannot emit a partial instance -- the bank credit would no
            # longer equal the sum of its invoices. When too few rows remain to fit one,
            # stop and let the top-up below make the batch total exact.
            if remaining < minimum:
                break
            emission = builder(ctx, remaining)
            out.extend(emission)
            produced[name] += len(emission.truth)

    # Top up any shortfall left by an indivisible multi-row case, so `--rows N` produces
    # exactly N truth rows. Recorded in the manifest rather than absorbed silently: it
    # nudges the topped-up case's share, and the distribution test must see it.
    topped_up = 0
    if "clean" in produced:
        while sum(produced.values()) < rows:
            emission = BUILDERS["clean"](ctx, 1)
            out.extend(emission)
            produced["clean"] += len(emission.truth)
            topped_up += len(emission.truth)

    manifest = {
        "seed": seed,
        "rows_requested": rows,
        "excluded_cases": list(exclude),
        "target_counts": counts,
        "actual_counts": produced,
        "topped_up_clean_rows": topped_up,
        "totals": {
            "invoices": len(out.invoices),
            "gateway_rows": len(out.gateway),
            "bank_rows": len(out.bank),
            "truth_rows": len(out.truth),
        },
    }
    return out, manifest


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Write with explicit LF endings so output is byte-identical across platforms."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_batch(out_dir: Path, emission: Emission, manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / GATEWAY_FILE, GATEWAY_COLUMNS, emission.gateway)
    _write_csv(out_dir / BANK_FILE, BANK_COLUMNS, emission.bank)
    _write_csv(out_dir / INVOICE_FILE, INVOICE_COLUMNS, emission.invoices)
    _write_csv(out_dir / TRUTH_FILE, TRUTH_COLUMNS, emission.truth)
    # newline="" so the manifest gets LF like the CSVs. Path.write_text would emit CRLF
    # on Windows, and a regenerated manifest would then differ from the committed one on
    # every line -- a determinism failure that only shows up on one platform.
    with (out_dir / MANIFEST_FILE).open("w", newline="", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def generate_to(out_dir: Path, rows: int, seed: int, exclude: tuple[str, ...] = ()) -> dict:
    emission, manifest = generate(rows=rows, seed=seed, exclude=exclude)
    write_batch(out_dir, emission, manifest)
    return manifest
