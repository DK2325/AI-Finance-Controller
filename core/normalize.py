"""Turning three files of records into comparable values.

Pure functions only. Nothing here reads a file, and nothing here knows anything about
how the data was produced -- the matcher sees exactly what a real system would see.

Dates are the first thing that has to survive two bank dialects: HDFC writes DD/MM/YY and
ICICI writes DD-MM-YYYY. The `bank` column is a hint, not a requirement, because a real
merchant's third bank would arrive with neither format and must still parse.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

# Tried in order. Day-first throughout: every Indian bank statement is day-first, and
# guessing wrong silently shifts a transaction by months.
_DATE_FORMATS = (
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d-%m-%y",
    "%Y-%m-%d",
    "%d %b %Y",
    "%d-%b-%Y",
)

# Razorpay UTRs are 12-digit numerics. Bounded by non-digits so a longer reference
# number cannot masquerade as one.
_UTR = re.compile(r"(?<!\d)(\d{12})(?!\d)")

# Corporate suffixes carry no identifying information and are written inconsistently.
_SUFFIXES = (
    "INDIA PRIVATE LIMITED",
    "PRIVATE LIMITED",
    "AND COMPANY",
    "PVT LTD",
    "P LTD",
    "LIMITED",
    "LTD",
    "LLP",
    "PVT",
)

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_NON_ALNUM_KEEP_SPACE = re.compile(r"[^A-Z0-9 ]+")

# Suffix words dropped before keying: they carry no identifying information and would
# otherwise become the second word for a single-word company name.
_SUFFIX_WORDS = frozenset(
    {"PRIVATE", "LIMITED", "LTD", "PVT", "LLP", "INDIA", "AND", "COMPANY", "P"}
)
_MULTISPACE = re.compile(r"\s+")


def to_paise(text: str | int | None) -> int:
    """Money as integer paise. Decimal, never float."""
    if text is None or text == "":
        return 0
    if isinstance(text, int):
        return text
    return int(Decimal(str(text).replace(",", "").strip()) * 100)


def parse_date(text: str) -> date | None:
    """Parse a bank date without being told its format. None when nothing fits."""
    if not text:
        return None
    candidate = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def from_timestamp(value: str | int) -> date | None:
    """Gateway timestamps are unix ints."""
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).date()
    except (TypeError, ValueError, OSError):
        return None


def date_delta(left: date | None, right: date | None) -> int | None:
    """Signed day difference, or None when either side failed to parse."""
    if left is None or right is None:
        return None
    return (left - right).days


def within_days(left: date | None, right: date | None, days: int) -> bool:
    delta = date_delta(left, right)
    return delta is not None and abs(delta) <= days


def extract_utrs(narration: str) -> list[str]:
    """Every 12-digit token in a narration, in order, deduplicated."""
    if not narration:
        return []
    return list(dict.fromkeys(_UTR.findall(narration)))


def normalize_counterparty(name: str) -> str:
    """Reduce a counterparty to a comparable token.

    Uppercase, drop the corporate suffix, strip everything non-alphanumeric. This is what
    makes 'Haldia Garments Pvt Ltd', 'HALDIA GARMENTS PRIVATE LIMITED' and
    'HALDIAGARMENTS' the same bucket.
    """
    if not name:
        return ""
    out = _MULTISPACE.sub(" ", name.upper().strip())
    for suffix in _SUFFIXES:
        if out.endswith(" " + suffix) or out == suffix:
            out = out[: -len(suffix)].strip()
            break
    return _NON_ALNUM.sub("", out)


def counterparty_key(name: str) -> str:
    """A blocking key built from the first TWO words, not the first six characters.

    Single-token prefixes collapse badly: a pool of 2,000 counterparties whose names
    begin with one of 52 city words yields only 52 buckets, so the key does almost no
    work and candidate generation stays quadratic. Measured at 25,000 rows that was 31
    candidates per settlement.

    Four characters of the first word plus three of the second is deliberate. It survives
    the truncation banks apply -- "AMRAVATI AGRO EXPORTS" and its truncation
    "AMRAVATI AGR" both yield AMRAAGR -- while multiplying the bucket count by the number
    of distinct second words.
    """
    if not name:
        return ""
    cleaned = _MULTISPACE.sub(" ", _NON_ALNUM_KEEP_SPACE.sub(" ", name.upper()).strip())
    words = [w for w in cleaned.split() if w not in _SUFFIX_WORDS]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:7]
    return words[0][:4] + words[1][:3]


def counterparty_prefix(name: str, length: int = 10) -> str:
    """A blocking key tolerant of truncation.

    Bank statements truncate names to a field width, so the full normalised name is not
    a reliable key. The leading characters usually survive.
    """
    return normalize_counterparty(name)[:length]


def normalize_narration(narration: str) -> str:
    """Flatten a narration for fuzzy comparison.

    Both dialects' delimiters become spaces, so HDFC's hyphens and ICICI's slashes stop
    being a difference before similarity is computed.
    """
    if not narration:
        return ""
    out = narration.upper()
    out = re.sub(r"[^A-Z0-9]+", " ", out)
    return _MULTISPACE.sub(" ", out).strip()


def narration_tokens(narration: str) -> set[str]:
    """Alphabetic tokens of length >= 3, which is what carries the counterparty name."""
    return {t for t in normalize_narration(narration).split() if len(t) >= 3 and t.isalpha()}


def day_bucket(value: date | None, window: int = 3) -> list[int]:
    """Ordinal buckets a date belongs to, given a +/- window.

    Returning several buckets rather than one is what lets a date_skew pair collide: the
    settlement and the credit disagree by up to three days, so each is indexed into every
    bucket it could plausibly meet the other in.
    """
    if value is None:
        return []
    base = value.toordinal()
    return [base + offset for offset in range(-window, window + 1)]


def shift_days(value: date | None, days: int) -> date | None:
    return None if value is None else value + timedelta(days=days)
