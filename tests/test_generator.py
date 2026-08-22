"""Everything downstream is measured against this data, so it is tested hard.

The load-bearing assertions here are determinism, distribution, the held-out exclusion,
truth coverage, and exact arithmetic. A defect in any of them silently corrupts every
metric in the submission.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from datagen.generator import (
    BANK_FILE,
    GATEWAY_FILE,
    INVOICE_FILE,
    TRUTH_FILE,
    allocate,
    generate,
    generate_to,
)
from datagen.schemas import (
    BANK_HDFC,
    BANK_ICICI,
    CASE_NAMES,
    FEE_RATE,
    GST_ON_FEE_RATE,
    HELD_OUT_CASES,
    TDS_SECTIONS,
    pct_of,
    rupees,
    target_shares,
)

ROWS = 2000
SEED = 42


def to_paise(text: str) -> int:
    """Parse a rupee string back to integer paise. Decimal, never float."""
    if text == "":
        return 0
    return int(Decimal(text) * 100)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def batch(tmp_path_factory) -> dict:
    """One full batch with all ten case types, generated once and shared."""
    out = tmp_path_factory.mktemp("all_ten")
    generate_to(out_dir=out, rows=ROWS, seed=SEED)
    return {
        "dir": out,
        "gateway": read_csv(out / GATEWAY_FILE),
        "bank": read_csv(out / BANK_FILE),
        "invoices": read_csv(out / INVOICE_FILE),
        "truth": read_csv(out / TRUTH_FILE),
    }


@pytest.fixture(scope="module")
def train_batch(tmp_path_factory) -> dict:
    """A training batch with the two held-out case types excluded."""
    out = tmp_path_factory.mktemp("held_out")
    generate_to(out_dir=out, rows=ROWS, seed=SEED, exclude=HELD_OUT_CASES)
    return {
        "dir": out,
        "gateway": read_csv(out / GATEWAY_FILE),
        "invoices": read_csv(out / INVOICE_FILE),
        "truth": read_csv(out / TRUTH_FILE),
    }


# ------------------------------------------------------------------- determinism


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate_to(out_dir=a, rows=500, seed=7)
    generate_to(out_dir=b, rows=500, seed=7)

    for name in (GATEWAY_FILE, BANK_FILE, INVOICE_FILE, TRUTH_FILE):
        assert (a / name).read_bytes() == (b / name).read_bytes(), f"{name} differs"


def test_different_seeds_produce_different_data_not_a_reshuffle(tmp_path: Path) -> None:
    """A reshuffle would have identical content in a different order. That must fail.

    Compared as a multiset of (customer, amount) pairs: reordering leaves the multiset
    unchanged, so only genuinely different data passes.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    generate_to(out_dir=a, rows=500, seed=7)
    generate_to(out_dir=b, rows=500, seed=8)

    def multiset(path: Path) -> set[tuple[str, str]]:
        return {(r["customer_name"], r["amount"]) for r in read_csv(path / INVOICE_FILE)}

    overlap = multiset(a) & multiset(b)
    assert len(overlap) < 0.05 * len(multiset(a)), (
        f"seeds 7 and 8 share {len(overlap)} invoices - this looks like a reshuffle"
    )


# ------------------------------------------------------------------ distribution


def test_row_count_is_exact(batch: dict) -> None:
    assert len(batch["truth"]) == ROWS


@pytest.mark.parametrize("case", CASE_NAMES)
def test_each_case_type_within_one_percent_of_target(batch: dict, case: str) -> None:
    targets = target_shares()
    actual = sum(1 for r in batch["truth"] if r["case_type"] == case) / len(batch["truth"])
    assert abs(actual - targets[case]) <= 0.01, (
        f"{case}: {actual:.3%} vs target {targets[case]:.3%}"
    )


def test_allocation_sums_to_requested_rows() -> None:
    for rows in (100, 997, 5000):
        assert sum(allocate(rows).values()) == rows
        assert sum(allocate(rows, HELD_OUT_CASES).values()) == rows


def test_excluding_everything_is_rejected() -> None:
    with pytest.raises(ValueError):
        target_shares(CASE_NAMES)


def test_unknown_case_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown case types"):
        generate(rows=10, seed=1, exclude=("not_a_case",))


# ---------------------------------------------------------------------- held-out


@pytest.mark.parametrize("case", HELD_OUT_CASES)
def test_held_out_case_absent_from_train_truth(train_batch: dict, case: str) -> None:
    """Absent from truth.csv, not merely from the data files."""
    present = [r for r in train_batch["truth"] if r["case_type"] == case]
    assert not present, f"{case} leaked into training truth ({len(present)} rows)"


def test_held_out_types_leave_no_trace_in_train_data(train_batch: dict) -> None:
    """tds_deducted implies a TDS section on an invoice; refund_netted implies a refund row."""
    tds_invoices = [r for r in train_batch["invoices"] if r["tds_applicable"] == "true"]
    assert not tds_invoices, f"{len(tds_invoices)} TDS invoices leaked into train"

    refunds = [r for r in train_batch["gateway"] if r["type"] == "refund"]
    assert not refunds, f"{len(refunds)} refund rows leaked into train"


def test_held_out_types_are_present_in_a_full_batch(batch: dict) -> None:
    """The exclusion test is only meaningful if these types exist when not excluded."""
    for case in HELD_OUT_CASES:
        assert any(r["case_type"] == case for r in batch["truth"]), f"{case} missing"


def test_renormalisation_preserves_relative_proportions() -> None:
    """Freed share is redistributed proportionally, not dumped into `clean`."""
    full, held = target_shares(), target_shares(HELD_OUT_CASES)
    ratio_full = full["clean"] / full["fee_deducted"]
    ratio_held = held["clean"] / held["fee_deducted"]
    assert ratio_full == pytest.approx(ratio_held), "relative proportions shifted"
    assert sum(held.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------------ truth


def test_truth_covers_every_invoice_exactly_once(batch: dict) -> None:
    invoice_ids = [r["invoice_id"] for r in batch["invoices"]]
    truth_ids = [r["invoice_id"] for r in batch["truth"] if r["invoice_id"]]

    assert len(truth_ids) == len(set(truth_ids)), "an invoice appears twice in truth"
    assert set(invoice_ids) == set(truth_ids), "invoice ledger and truth disagree"


def test_orphans_have_no_links_but_are_still_recorded(batch: dict) -> None:
    """evals/ must be able to tell a correct refusal from a miss."""
    orphans = [r for r in batch["truth"] if r["case_type"] == "orphan"]
    assert orphans, "no orphans generated"
    for row in orphans:
        assert row["invoice_id"] == ""
        assert row["settlement_id"] == ""
        assert row["txn_id"], "an orphan must still name the unmatchable bank row"


def test_every_truth_link_resolves(batch: dict) -> None:
    settlements = {r["settlement_id"] for r in batch["gateway"]}
    txns = {r["txn_id"] for r in batch["bank"]}
    for row in batch["truth"]:
        if row["settlement_id"]:
            assert row["settlement_id"] in settlements, f"dangling {row['settlement_id']}"
        if row["txn_id"]:
            assert row["txn_id"] in txns, f"dangling {row['txn_id']}"


# ------------------------------------------------------------------- arithmetic


def test_gateway_net_amount_is_exactly_amount_minus_fee_and_tax(batch: dict) -> None:
    for row in batch["gateway"]:
        amount, fee, tax = int(row["amount"]), int(row["fee"]), int(row["tax"])
        if row["type"] == "refund":
            assert int(row["net_amount"]) == -amount
            assert int(row["debit"]) == amount
        else:
            assert int(row["net_amount"]) == amount - fee - tax
            assert int(row["credit"]) == amount - fee - tax


def test_gst_is_exactly_eighteen_percent_of_fee(batch: dict) -> None:
    for row in batch["gateway"]:
        fee = int(row["fee"])
        if fee:
            assert int(row["tax"]) == pct_of(fee, GST_ON_FEE_RATE)


def test_fee_deducted_case_reconciles_to_the_paisa(batch: dict) -> None:
    gateway = {r["settlement_id"]: r for r in batch["gateway"] if r["type"] == "payment"}
    bank = {r["txn_id"]: r for r in batch["bank"]}

    checked = 0
    for row in batch["truth"]:
        if row["case_type"] != "fee_deducted":
            continue
        g, b = gateway[row["settlement_id"]], bank[row["txn_id"]]
        amount, fee, tax = int(g["amount"]), int(g["fee"]), int(g["tax"])
        assert fee == pct_of(amount, FEE_RATE)
        assert to_paise(b["credit"]) == amount - fee - tax
        checked += 1
    assert checked, "no fee_deducted cases found"


def test_tds_case_deducts_a_real_section_rate(batch: dict) -> None:
    invoices = {r["invoice_id"]: r for r in batch["invoices"]}
    gateway = {r["settlement_id"]: r for r in batch["gateway"] if r["type"] == "payment"}

    checked = 0
    for row in batch["truth"]:
        if row["case_type"] != "tds_deducted":
            continue
        inv, g = invoices[row["invoice_id"]], gateway[row["settlement_id"]]
        gross, captured = to_paise(inv["amount"]), int(g["amount"])
        section = inv["tds_section"]
        assert section in TDS_SECTIONS, f"unknown TDS section {section!r}"
        assert gross - captured == pct_of(gross, TDS_SECTIONS[section])
        checked += 1
    assert checked, "no tds_deducted cases found"


def test_refund_is_its_own_row_and_nets_against_the_payout(batch: dict) -> None:
    """The finding that motivated adding `type`: a refund is a type=refund row."""
    bank = {r["txn_id"]: r for r in batch["bank"]}
    by_settlement: dict[str, list[dict]] = {}
    for row in batch["gateway"]:
        by_settlement.setdefault(row["settlement_id"], []).append(row)

    checked = 0
    for row in batch["truth"]:
        if row["case_type"] != "refund_netted":
            continue
        rows = by_settlement[row["settlement_id"]]
        payments = [r for r in rows if r["type"] == "payment"]
        refunds = [r for r in rows if r["type"] == "refund"]
        assert len(payments) == 1 and len(refunds) == 1

        expected = int(payments[0]["net_amount"]) - int(refunds[0]["amount"])
        assert to_paise(bank[row["txn_id"]]["credit"]) == expected
        checked += 1
    assert checked, "no refund_netted cases found"


def test_batched_settlement_credit_equals_sum_of_its_invoices(batch: dict) -> None:
    bank = {r["txn_id"]: r for r in batch["bank"]}
    by_settlement: dict[str, list[dict]] = {}
    for row in batch["gateway"]:
        by_settlement.setdefault(row["settlement_id"], []).append(row)

    seen: set[str] = set()
    for row in batch["truth"]:
        if row["case_type"] != "batched_settlement" or row["txn_id"] in seen:
            continue
        seen.add(row["txn_id"])
        total = sum(int(r["net_amount"]) for r in by_settlement[row["settlement_id"]])
        assert to_paise(bank[row["txn_id"]]["credit"]) == total
    assert seen, "no batched settlements found"


def test_rounding_drift_is_small_and_deliberate(batch: dict) -> None:
    """Drift must be 1-5 paise. Anything larger means float crept into the arithmetic."""
    gateway = {r["settlement_id"]: r for r in batch["gateway"] if r["type"] == "payment"}
    bank = {r["txn_id"]: r for r in batch["bank"]}

    checked = 0
    for row in batch["truth"]:
        if row["case_type"] != "rounding_drift":
            continue
        expected = int(gateway[row["settlement_id"]]["net_amount"])
        drift = to_paise(bank[row["txn_id"]]["credit"]) - expected
        assert 1 <= abs(drift) <= 5, f"drift of {drift} paise is not a rounding artefact"
        checked += 1
    assert checked, "no rounding_drift cases found"


def test_clean_cases_have_no_discrepancy_at_all(batch: dict) -> None:
    """If a clean case drifts by a paisa, float has crept in and rounding_drift is polluted."""
    gateway = {r["settlement_id"]: r for r in batch["gateway"] if r["type"] == "payment"}
    bank = {r["txn_id"]: r for r in batch["bank"]}

    for row in batch["truth"]:
        if row["case_type"] != "clean":
            continue
        g = gateway[row["settlement_id"]]
        assert int(g["fee"]) == 0 and int(g["tax"]) == 0
        assert to_paise(bank[row["txn_id"]]["credit"]) == int(g["net_amount"])


def test_duplicate_utr_really_reuses_a_utr(batch: dict) -> None:
    settlements = {}
    for row in batch["gateway"]:
        settlements.setdefault(row["settlement_utr"], set()).add(row["settlement_id"])

    reused = {utr for utr, ids in settlements.items() if len(ids) > 1}
    assert reused, "no UTR was reused"


# ------------------------------------------------------------------------ money


def test_money_columns_are_always_two_decimal_strings(batch: dict) -> None:
    import re

    pattern = re.compile(r"^-?\d+\.\d{2}$")
    for row in batch["bank"]:
        for column in ("debit", "credit", "balance"):
            if row[column]:
                assert pattern.match(row[column]), f"{column}={row[column]!r}"
    for row in batch["invoices"]:
        assert pattern.match(row["amount"])


def test_gateway_money_columns_are_integer_paise(batch: dict) -> None:
    """Razorpay reports currency subunits as integers, and so do we."""
    for row in batch["gateway"]:
        for column in ("amount", "fee", "tax", "debit", "credit", "net_amount"):
            assert row[column].lstrip("-").isdigit(), f"{column}={row[column]!r} is not an int"


def test_rupees_rejects_a_float() -> None:
    """The one function that converts money must never accept a float."""
    with pytest.raises(TypeError):
        rupees(1234.56)


def test_pct_of_rounds_half_up_not_bankers() -> None:
    """Banker's rounding would bias fee totals across thousands of rows."""
    assert pct_of(50, 0.5) == 25
    assert pct_of(150, 0.5) == 75  # bankers would give 74
    assert pct_of(250, 0.5) == 125  # bankers would give 124


# ---------------------------------------------------------------------- dialects


def test_both_bank_dialects_are_present(batch: dict) -> None:
    banks = {r["bank"] for r in batch["bank"]}
    assert banks == {BANK_HDFC, BANK_ICICI}


def test_the_two_dialects_genuinely_differ(batch: dict) -> None:
    """Not just column headers -- those get normalised away in Phase 3."""
    hdfc = [r for r in batch["bank"] if r["bank"] == BANK_HDFC]
    icici = [r for r in batch["bank"] if r["bank"] == BANK_ICICI]

    # HDFC writes upper case, hyphen-delimited.
    assert all(r["narration"] == r["narration"].upper() for r in hdfc)
    assert sum("-" in r["narration"] for r in hdfc) > 0.8 * len(hdfc)

    # ICICI uses slashes and retains mixed case on some rows.
    assert sum("/" in r["narration"] for r in icici) > 0.5 * len(icici)
    assert any(r["narration"] != r["narration"].upper() for r in icici)

    # Date conventions differ: DD/MM/YY vs DD-MM-YYYY.
    assert all(len(r["value_date"]) == 8 and "/" in r["value_date"] for r in hdfc)
    assert all(len(r["value_date"]) == 10 and "-" in r["value_date"] for r in icici)


def test_narrations_are_not_drawn_from_a_fixed_list(batch: dict) -> None:
    """Templates plus noise, so the matcher cannot memorise a handful of strings."""
    narrations = [r["narration"] for r in batch["bank"]]
    assert len(set(narrations)) > 0.9 * len(narrations)
