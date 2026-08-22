"""Bank narration generation, in two real dialects.

The narration column is where reconciliation actually gets hard. Column headers get
normalised away in the first five minutes of Phase 3; narration grammar does not.

HDFC and ICICI write the same transaction differently — different delimiters, casing,
token order, and date convention — and both degrade the counterparty name in ways a
merchant's ledger never does. Templates are sampled and then noised, never drawn from a
fixed list, so the matcher cannot memorise a small set of strings.

Source formats and the dialect comparison table are in notes/schemas.md.
"""

from __future__ import annotations

import random

from datagen.schemas import BANK_HDFC, BANK_ICICI

# Bank branch/IFSC-ish tokens that appear inside narrations.
_HDFC_IFSC = ["HDFC0000123", "HDFC0004567", "HDFC0009812", "HDFC0002341"]
_ICICI_REF = ["ICICR52023081", "ICICR52024117", "ICICR52023994", "ICICR52024560"]
_SENDER_BANKS = ["HDFC", "ICIC", "SBIN", "AXIS", "KKBK", "UTIB"]
_UPI_HANDLES = ["ybl", "okaxis", "paytm", "okhdfcbank", "ibl", "apl"]

# HDFC writes hyphen-delimited, upper case.
_HDFC_TEMPLATES = [
    "NEFT-{ifsc}-{name}-{ref}",
    "NEFT CR-{ifsc}-{name}-UTR{ref}",
    "UPI-{name}-{vpa}-{ref}",
    "UPI-{name}-{vpa}-{ref}-PAYMENT FROM {short}",
    "IMPS-{ref}-{name}-{sender}",
    "RTGS-{ifsc}-{name}-{ref}",
    "ACH C-{name}-{ref}",
]

# ICICI writes slash-delimited, mixed case, with MMT/ prefixes on IMPS.
_ICICI_TEMPLATES = [
    "NEFT/{iref}/{name}",
    "NEFT-{iref}-{name}",
    "UPI/{ref}/Payment from/{vpa}/{sender}",
    "UPI/{ref}/{short}/{vpa}",
    "MMT/IMPS/{ref}/{name}/{sender}",
    "RTGS/{iref}/{name}",
    "BIL/ONL/{ref}/{short}",
]


def _corrupt_name(name: str, rng: random.Random) -> str:
    """Degrade a counterparty name the way a bank statement does.

    Truncation, casing loss, and separator loss are the three that actually appear, and
    each one defeats a naive exact-string match while leaving the name recoverable by a
    fuzzy one.
    """
    out = name
    roll = rng.random()

    if roll < 0.30:
        # Truncated to a field width, mid-word.
        width = rng.choice([12, 16, 18, 20])
        out = out[:width].rstrip()
    elif roll < 0.45:
        # Corporate suffix dropped or abbreviated.
        for suffix in (" PRIVATE LIMITED", " PVT LTD", " LIMITED", " LTD", " LLP"):
            if out.upper().endswith(suffix):
                out = out[: -len(suffix)]
                if rng.random() < 0.5:
                    out += rng.choice([" PVT", " P LTD", ""])
                break
    elif roll < 0.55:
        # Separators lost entirely.
        out = out.replace(" ", "")

    casing = rng.random()
    if casing < 0.55:
        out = out.upper()
    elif casing < 0.75:
        out = out.title()
    elif casing < 0.85:
        out = out.lower()

    if rng.random() < 0.08:
        # Doubled space, which survives naive whitespace handling.
        parts = out.split(" ")
        if len(parts) > 1:
            i = rng.randrange(1, len(parts))
            out = " ".join(parts[:i]) + "  " + " ".join(parts[i:])

    return out


def _vpa(name: str, rng: random.Random) -> str:
    handle = rng.choice(_UPI_HANDLES)
    stem = "".join(ch for ch in name.lower() if ch.isalnum())[:10]
    return f"{stem}@{handle}"


def make_narration(
    bank: str,
    counterparty: str,
    utr: str,
    rng: random.Random,
) -> str:
    """Render one narration in the given bank's dialect.

    `utr` is threaded in so the exact layer has something real to find in a subset of
    rows — real narrations sometimes carry the UTR and sometimes do not, and which is
    which is part of the difficulty.
    """
    name = _corrupt_name(counterparty, rng)
    short = "".join(w[0] for w in counterparty.split()[:3]).upper()

    fields = {
        "name": name,
        "short": short,
        "ref": utr if rng.random() < 0.65 else str(rng.randrange(10**9, 10**10)),
        "ifsc": rng.choice(_HDFC_IFSC),
        "iref": rng.choice(_ICICI_REF) + str(rng.randrange(100, 999)),
        "sender": rng.choice(_SENDER_BANKS),
        "vpa": _vpa(counterparty, rng),
    }

    if bank == BANK_HDFC:
        text = rng.choice(_HDFC_TEMPLATES).format(**fields)
        text = text.upper()
    elif bank == BANK_ICICI:
        text = rng.choice(_ICICI_TEMPLATES).format(**fields)
    else:
        raise ValueError(f"unknown bank dialect: {bank}")

    if rng.random() < 0.05:
        # Trailing separator, as seen in real exports.
        text += rng.choice(["-", "/", "/ ", " "])

    return text


def format_date(bank: str, date) -> str:
    """Dates as each bank writes them. HDFC uses DD/MM/YY, ICICI uses DD-MM-YYYY."""
    if bank == BANK_HDFC:
        return date.strftime("%d/%m/%y")
    if bank == BANK_ICICI:
        return date.strftime("%d-%m-%Y")
    raise ValueError(f"unknown bank dialect: {bank}")


def make_bank_ref(bank: str, rng: random.Random) -> str:
    """The Chq./Ref.No. (HDFC) or reference token (ICICI) column."""
    if bank == BANK_HDFC:
        return str(rng.randrange(10**9, 10**10))
    return f"S{rng.randrange(10**7, 10**8)}"
