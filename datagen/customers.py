"""A ~2,000-name counterparty pool.

The pool started at 16 names, which was wrong in a way that would only have surfaced in
Phase 7. Counterparty-match-frequency is a planned Phase 3 feature and a Phase 4 model
input; with 16 names every counterparty recurs constantly, so that feature would look
far stronger than it ever could in production, and blocking recall would be inflated
because the counterparty bucket barely narrows the candidate set.

Discovering that in Phase 7 would produce an unattributable train/test story. Fixing it
here costs one regeneration.

Names are built by product over sorted component lists and truncated at a fixed count,
so the pool is identical on every run and every platform. Nothing here consumes the
seeded Random -- the pool is a constant, and only the *choice* from it is random.
"""

from __future__ import annotations

from itertools import product

# Indian cities, regions, and rivers -- the usual stock of Indian company prefixes.
_PLACES = [
    "AMRAVATI", "ANANTAPUR", "AURANGABAD", "BELGAUM", "BHAVNAGAR", "BHILAI",
    "BHOPAL", "CALICUT", "CHANDRAPUR", "CHENNAI", "COIMBATORE", "CUTTACK",
    "DEHRADUN", "DHANBAD", "ERNAKULAM", "GANDHINAGAR", "GODAVARI", "GUNTUR",
    "GWALIOR", "HALDIA", "HOSUR", "HUBLI", "INDORE", "JABALPUR",
    "JALANDHAR", "JAMNAGAR", "JODHPUR", "KAKINADA", "KANPUR", "KOLHAPUR",
    "KRISHNA", "LUDHIANA", "MADURAI", "MANGALORE", "MEERUT", "MYSORE",
    "NAGPUR", "NARMADA", "NASHIK", "PANIPAT", "RAIPUR", "RAJKOT",
    "SALEM", "SILIGURI", "SOLAPUR", "SURAT", "TIRUPUR", "TRICHY",
    "UDAIPUR", "VARANASI", "VIJAYAWADA", "WARANGAL",
]

# Sector words that actually appear in Indian B2B trade names.
_SECTORS = [
    "AGRO EXPORTS", "AUTO COMPONENTS", "BEARINGS", "CABLES", "CASTINGS",
    "CERAMICS", "CHEMICALS", "COLD STORAGE", "CONSTRUCTIONS", "CONTAINERS",
    "DIAGNOSTICS", "DISTRIBUTORS", "ELECTRICALS", "ENGINEERING", "ENTERPRISES",
    "FABRICATORS", "FERTILISERS", "FOODS", "FORGINGS", "GARMENTS",
    "HANDICRAFTS", "HOSPITALITY", "INDUSTRIES", "INFRAPROJECTS", "LOGISTICS",
    "MEDIA", "METALS", "MOTORS", "PACKAGING", "PAPER MILLS",
    "PHARMACEUTICALS", "PLASTICS", "POLYMERS", "PRINTERS", "REFRACTORIES",
    "SOFTWARE SERVICES", "SPINNERS", "TEXTILES", "TRADERS", "TRANSPORT",
]

# Corporate suffixes, in roughly their real frequency.
_SUFFIXES = [
    "PRIVATE LIMITED",
    "PVT LTD",
    "LIMITED",
    "PRIVATE LIMITED",
    "LLP",
    "PVT LTD",
    "INDIA PRIVATE LIMITED",
    "AND COMPANY",
]

POOL_SIZE = 2000


def _build_pool() -> tuple[str, ...]:
    """Deterministic product over sorted components, truncated to POOL_SIZE.

    Suffix is chosen by index rather than at random so the pool never depends on RNG
    state -- two callers building the pool must get identical lists regardless of what
    else has drawn from the generator.
    """
    names = []
    for i, (place, sector) in enumerate(product(sorted(_PLACES), sorted(_SECTORS))):
        names.append(f"{place} {sector} {_SUFFIXES[i % len(_SUFFIXES)]}")
        if len(names) == POOL_SIZE:
            break
    return tuple(names)


CUSTOMERS: tuple[str, ...] = _build_pool()
