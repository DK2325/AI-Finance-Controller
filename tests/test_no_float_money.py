"""Money is NUMERIC(14,2), everywhere, forever.

Float arithmetic on an amount is a correctness bug in a reconciliation system. BUILD.md
makes "no money column is a float type anywhere in the schema" a Phase 0 exit criterion;
this turns that one-time check into a standing regression test, so a Float added in
Phase 6 fails the build rather than quietly corrupting a ledger.
"""

from __future__ import annotations

from sqlalchemy import Float, Numeric

from ledgerloop.models import Base

EXPECTED_TABLES = {"runs", "audit_records", "exceptions", "approvals", "model_versions"}

# Columns holding rupee amounts. These must be NUMERIC(14,2) exactly.
MONEY_COLUMNS = {
    ("runs", "amount_total"),
    ("runs", "amount_reconciled"),
    ("audit_records", "amount"),
    ("exceptions", "amount"),
}


def test_all_five_tables_are_defined() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables), (
        f"missing tables: {EXPECTED_TABLES - set(Base.metadata.tables)}"
    )


def test_no_float_column_anywhere_in_the_schema() -> None:
    offenders = [
        f"{table.name}.{column.name} is {type(column.type).__name__}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, Float)
    ]
    assert not offenders, "float columns found in a financial schema:\n  " + "\n  ".join(
        offenders
    )


def test_money_columns_are_numeric_14_2() -> None:
    for table_name, column_name in sorted(MONEY_COLUMNS):
        column = Base.metadata.tables[table_name].columns[column_name]
        assert isinstance(column.type, Numeric), (
            f"{table_name}.{column_name} is {type(column.type).__name__}, expected Numeric"
        )
        assert (column.type.precision, column.type.scale) == (14, 2), (
            f"{table_name}.{column_name} is NUMERIC({column.type.precision},"
            f"{column.type.scale}), expected NUMERIC(14,2)"
        )


def test_numeric_columns_do_not_silently_return_floats() -> None:
    """asdecimal=False on a Numeric would hand back floats and defeat the whole point."""
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, Numeric) and not column.type.asdecimal
    ]
    assert not offenders, "Numeric columns configured to return float:\n  " + "\n  ".join(
        offenders
    )


def test_confidence_is_not_money_but_is_still_exact() -> None:
    """Calibrated confidence is a probability, not currency - but it drives money
    decisions, so it is stored exactly rather than as a float."""
    column = Base.metadata.tables["audit_records"].columns["calibrated_confidence"]
    assert isinstance(column.type, Numeric)
    assert not isinstance(column.type, Float)
