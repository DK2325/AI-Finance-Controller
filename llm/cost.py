"""What the inference costs, as a range and never as a point.

WHY A RANGE

We run against NVIDIA's free hosted developer tier, which publishes no price. The model is
open-weight, so the same weights are served by several providers at rates that differ by
**1.8x** on output tokens. Reporting one number would imply a precision that does not
exist -- it would be the price from whichever provider happened to be picked, presented as
the price of the system.

So every figure here is a band, bounded by the cheapest and dearest published rate, and
labelled with what it actually is:

    tokens measured on NVIDIA's free hosted endpoint; priced against published
    third-party rates as of 23 August 2026; not billed

Rates and their sources are in notes/pricing.md with the date they were read, because rate
cards move and a figure with no date is a figure with no meaning six months later.

WHY BOTH USD AND INR

The USD figures follow from the cited rates and nothing else. The rupee figures need one
further input -- an exchange rate -- which is a second assumption with its own volatility,
and it is named rather than folded in silently. A finance panel reasons in rupees, so the
rupee figure is the headline; the USD figure is the one that can be checked against the
sources without trusting our FX assumption too.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- published rates
#
# USD per million tokens, read 23 August 2026. See notes/pricing.md for sources.
# Bounds only: the middle provider is cited there but does not affect a min/max band.


@dataclass(frozen=True)
class Rate:
    name: str
    usd_per_m_input: float
    usd_per_m_output: float

    def usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.usd_per_m_input
            + output_tokens / 1_000_000 * self.usd_per_m_output
        )


CHEAPEST = Rate("DeepInfra / OpenRouter", 0.085, 0.40)
DEAREST = Rate("Amazon Bedrock", 0.15, 0.65)

RATES_READ_ON = "2026-08-23"

# A second assumption, kept separate from the first and named as one. Adjust here; every
# rupee figure in the project derives from this single constant, so it can be corrected in
# one place and every number moves with it.
USD_TO_INR = 88.0
FX_ASSUMED_ON = "2026-08-23"


@dataclass(frozen=True)
class CostBand:
    """A cost expressed as what it is: a range, with its provenance attached."""

    input_tokens: int
    output_tokens: int
    low_usd: float
    high_usd: float

    @property
    def low_inr(self) -> float:
        return self.low_usd * USD_TO_INR

    @property
    def high_inr(self) -> float:
        return self.high_usd * USD_TO_INR

    @property
    def spread(self) -> float:
        """How much the provider choice matters. 1.0 means it does not."""
        return self.high_usd / self.low_usd if self.low_usd else 0.0

    def rupees(self) -> str:
        return f"Rs {self.low_inr:,.2f} - Rs {self.high_inr:,.2f}"

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "low_usd": round(self.low_usd, 6),
            "high_usd": round(self.high_usd, 6),
            "low_inr": round(self.low_inr, 4),
            "high_inr": round(self.high_inr, 4),
            "spread": round(self.spread, 3),
            "basis": (
                "tokens measured on NVIDIA's free hosted endpoint; priced against "
                f"published third-party rates as of {RATES_READ_ON}; not billed"
            ),
            "cheapest_rate": CHEAPEST.name,
            "dearest_rate": DEAREST.name,
            "usd_to_inr": USD_TO_INR,
        }


def band(input_tokens: int, output_tokens: int) -> CostBand:
    return CostBand(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        low_usd=CHEAPEST.usd(input_tokens, output_tokens),
        high_usd=DEAREST.usd(input_tokens, output_tokens),
    )


def per_thousand_rows(
    input_tokens: int,
    output_tokens: int,
    settlements: int,
) -> CostBand:
    """Cost per 1,000 settlements, scaled from a measured run.

    The denominator is **settlements, not exceptions**. A cost per exception would look
    better and answer a question nobody asks: a merchant knows how many payouts they take,
    not how many of them this system will decline to match. Scaling by settlements also
    keeps the deterministic layer's contribution visible -- the exceptions it explains for
    free are exceptions the model was never asked about, and that shows up as a lower cost
    per thousand rows rather than disappearing into a per-exception figure.
    """
    if settlements <= 0:
        return band(0, 0)
    scale = 1000 / settlements
    return band(round(input_tokens * scale), round(output_tokens * scale))
