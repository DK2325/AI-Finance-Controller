# What the inference costs, and why it is a range

**Read 23 August 2026. Rate cards move; a figure with no date is a figure with no meaning
six months later.**

## The problem with a single number

We run against **NVIDIA's free hosted developer tier**, which publishes no price. It has a
rate limit (40 rpm) and no billing. So there is no invoice to read and no rate to cite for
the endpoint we actually used.

The model is open-weight, which means the same weights are served by several providers at
different rates:

| provider | USD / 1M input | USD / 1M output |
|---|---|---|
| DeepInfra / OpenRouter | **$0.085** | **$0.40** |
| Requesty / Inworld | $0.10 | $0.50 |
| Amazon Bedrock | **$0.15** | **$0.65** |

**Output tokens differ by 1.6× between the cheapest and dearest.** Reporting one number
would be reporting the price from whichever provider we happened to pick, presented as the
price of the system. So every cost figure in this project is a band bounded by the
cheapest and dearest published rate.

**The label, used verbatim wherever a figure appears:**

> tokens measured on NVIDIA's free hosted endpoint; priced against published third-party
> rates as of 23 August 2026; not billed

## The second assumption, named separately

Rupee figures need an exchange rate, which is a second assumption with its own volatility.
It is a single constant in `llm/cost.py`:

```python
USD_TO_INR = 88.0    # assumed 2026-08-23
```

Every rupee figure in the project derives from it, so it can be corrected in one place. The
USD figures follow from the cited rates alone and can be checked without trusting our FX
assumption; the rupee figures are the headline because a finance panel reasons in rupees.

## What it actually costs

Measured on `data/train`: 4,945 settlements produce 2,448 exceptions, of which **1,198
reach the model** — the other 1,250 carry deterministic reason codes and cost nothing.
Measured over 100 real exceptions through the `reason` job: **220 input and 168 output
tokens per exception.**

| | tokens | cheapest | dearest |
|---|---|---|---|
| per 1,000 settlements | 53,301 in / 40,703 out | **₹1.83** | **₹3.03** |
| the whole 4,945-row batch | 263,572 in / 201,276 out | ₹9.06 | ₹14.99 |
| a 25,000-row run | ~1.33M in / ~1.02M out | ₹45.79 | ₹75.79 |

### The narrowness is the finding

A 1.65× spread sounds significant until it is denominated. **The difference between the
cheapest and dearest provider, across a 25,000-row monthly reconciliation, is ₹30.**

That is the useful result for unit economics: *provider choice is not a cost decision for
this workload.* It can be made on latency, reliability, data residency or contract terms
without a cost trade-off worth modelling. Any of the three would be affordable if it were
five times dearer.

**Why the denominator is settlements and not exceptions.** A cost per exception would be a
smaller, better-looking number answering a question nobody asks — a merchant knows how many
payouts they take, not how many this system will decline to match. Scaling by settlements
also keeps the deterministic layer visible: the 1,250 exceptions it explains for free are
exceptions the model was never asked about, and that shows up here as a lower cost per
thousand rows rather than vanishing into a per-exception average.

**What is not in these figures.** The `parse` and `journal` jobs. `parse` currently runs
over narrations for evaluation rather than in the reconciliation path, and no journal
entries are proposed in a default run. If both were enabled for every exception, the
figures roughly triple — still under ₹10 per 1,000 settlements at the dearest rate.

---

## Measured at three deterministic shares

*Data. The interpretation of it is not written here — see the note at the end of this
section.*

`data/train`'s figure above rests on a per-exception token rate measured over 100
exceptions and then scaled to 1,198. The other two rows are whole populations with no
scaling: every LLM-bound exception in the batch went through the `reason` job with the
cache disabled, and the tokens are what the endpoint reported.

| batch | settlements | deterministic share | LLM-bound | in/exc | out/exc | ₹ per 1,000 settlements |
|---|---|---|---|---|---|---|
| `data/train` | 4,945 | 51.06% | 1,198 | 220.0 | 168.0 | **₹1.83 – ₹3.03** |
| `data/scale` | 24,750 | 65.42% | 3,347 | 218.5 | 141.6 | **₹0.90 – ₹1.49** |
| `data/test` (sealed) | 4,950 | 69.77% | 555 | 213.1 | 128.2 | **₹0.68 – ₹1.14** |

Raw output: `notes/measurements/unit_economics.json`. The `data/test` run was 28 calls,
166.0s at 10.1 rpm, **0 stalled calls and a 0.00% schema failure rate**.

### Two drivers moved, not one

Worth separating, because the obvious model — cost falls because the deterministic layer
explains more exceptions for free — accounts for only part of the drop.

| | `data/train` | `data/test` | ratio |
|---|---|---|---|
| LLM-bound exceptions per 1,000 settlements | 242.3 | 112.1 | **0.463** |
| output tokens per exception | 168.0 | 128.2 | **0.763** |
| ₹ per 1,000 settlements (cheapest rate) | 1.83 | 0.68 | **0.372** |

0.463 × 0.763 = 0.353, against a measured ratio of 0.372; the residual is input tokens,
which barely moved (220.0 → 213.1). **So roughly two-thirds of the reduction is the
deterministic share and one-third is shorter explanations per exception.** Attributing all
of it to the deterministic share would overstate that layer's contribution by about half
again.

Why the explanations got shorter is **not** established, and the obvious candidate is ruled
out. `reason` output length varies by reason code, so a different code mix would explain
it — but the LLM-bound mix is almost the same on both batches:

| | `BELOW_THRESHOLD` | `AMBIGUOUS_CANDIDATES` |
|---|---|---|
| `data/scale` | 92.44% | 7.56% |
| `data/test` | 94.59% | 5.41% |

A two-point shift in mix cannot produce a 0.763 factor in output tokens. Whatever is
driving it is something else, and it is left open rather than given a plausible-sounding
cause.

### For interpretation

The numbers above are recorded and checkable. What they mean for unit economics — whether
a band this narrow makes provider choice a non-decision, what the right denominator is for
a merchant, and how any of it should be presented to a finance reader — is deliberately not
written here.

---

## What would change these numbers

- **A rate card moving.** Most likely downward; inference prices have only fallen.
- **The exchange rate.** ±10% moves the band by ±10%; it does not change the conclusion.
- **The deterministic share.** Measured at 51.06%, 65.42% and 69.77% across three
  batches, so it is a property of the batch rather than a constant. If it fell to 30%, the
  cost would rise by roughly 40% against the `data/train` figure — to about ₹4.25 per 1,000
  at the dearest rate. Note from the table above that it is not the only driver: output
  tokens per exception moved by a factor of 0.763 across the same three batches.
- **Enabling thinking.** Measured at 5.4× the output tokens for structurally identical
  results. That is the one change here that would matter, and it is the one thing every
  prompt explicitly declares off.
