# The operating point, and why it sits where it does

The risk–coverage curve is the submission's headline artifact. This file records which
point on it was chosen, what the alternatives cost, and what the model was calibrated
against — so Phase 7 compares against recorded numbers rather than inferring them.

---

## The one number that matters most

**Moving the precision floor from 99.5% to 99.0% buys 26 points of coverage for 0.38
points of precision.**

Measured out-of-sample (a split neither the classifier nor the calibrator saw):

| precision floor | coverage | precision | false auto-matches |
|---|---|---|---|
| 99.5% *(selected)* | **51.31%** | 99.5050% | 3 |
| 99.0% | 77.65% | 99.1276% | 8 |
| none | 82.98% | 94.8980% | 50 |

That is the entire thesis in one table. A merchant is not choosing between "accurate" and
"inaccurate"; they are choosing how much manual review to buy with how much risk. At
99.5% the system hands back nearly half the batch for a human to look at. At 99.0% it
hands back a fifth, and posts five more wrong matches out of ~600.

**Selected: the 99.5% floor**, per architecture rule 4 — precision over coverage, because
a false auto-match posts wrong money to a ledger while a miss costs a human thirty
seconds. But the choice is a policy, not a fact, and the Phase 6 slider exists so the
merchant can make it themselves.

## The selected point

```
threshold   0.9989
coverage    51.40%   of settlements auto-matched
precision   99.5058%
recall      64.95%   of the links that exist
false       3        auto-matches wrong, out of 607
```

Selected as *the highest coverage that holds precision at or above the floor*, on the
evaluation split. Not the highest precision available — that would be a system that
decides almost nothing and reports a perfect number.

### Two things that were nearly wrong here

**The floor was measured on the wrong population at first.** Selecting on unresolved
candidates gave 99.34% max precision and reported the floor as unreachable. But
resolution — one transaction per settlement, one settlement per invoice — discards most
wrong candidates *before* anything is auto-matched. Measured after resolution, the same
model reaches 99.51%. Candidate-level precision describes a system no merchant ever
experiences.

**Coverage is expressed against settlements, not candidates.** "51% of payouts were
auto-matched" is a sentence a finance team can act on. "51% of surviving candidate pairs"
is not.

---

## Calibration

**Isotonic regression**, selected over Platt scaling on measured Expected Calibration
Error:

| method | ECE | MCE | Brier |
|---|---|---|---|
| **isotonic** | **0.00000** | 0.00000 | 0.00607 |
| platt | 0.00185 | 0.77732 | 0.00726 |

Those are *in-sample* figures on the calibration split, and they are near-zero by
construction — isotonic fits any sample it is handed almost perfectly. They are recorded
only to show the gap.

**The honest number, measured on a third split neither the classifier nor the calibrator
saw:**

```
ECE    0.01044
MCE    0.36412
Brier  0.01645
n      1958
```

### Why there are three splits and not two

The first implementation used two: fit the classifier on one, then fit *and measure*
calibration on the other. It reported an ECE of exactly 0.00000 and a flawless reliability
diagram. That number was meaningless — isotonic reproduces its own fitting sample.

The split is now fit → calibrate → evaluate, with every boundary drawn by `settlement_id`
so no near-duplicate candidate crosses one. A random split would have leaked
near-identical candidates across the boundary and inflated the single number this
submission rests on.

### Reading the MCE honestly

MCE of 0.364 looks alarming next to an ECE of 0.010. It comes from a bin holding **9
samples**. The population that matters is the top bin:

| bin | n | predicted | observed | gap |
|---|---|---|---|---|
| [0.9, 1.0) | 917 | 0.9914 | 0.9913 | **0.0001** |
| [0.0, 0.1) | 43 | 0.0009 | 0.0465 | 0.0456 |
| [0.2, 0.3) | 9 | 0.2500 | 1.0000 | 0.7500 |

93% of predictions land in the top bin, where predicted and observed agree to four
decimal places. The middle of the range is sparse because the system is mostly either
confident or not — which is what a well-separated classifier looks like, and is also why
MCE is the wrong summary statistic for it.

---

## What the model was calibrated against

**These figures describe eight case types, not ten.**

`tds_deducted` and `refund_netted` are held out of training entirely. The classifier has
seen **zero** examples of either. Its behaviour on them in Phase 7 is genuinely unknown —
that is the point of the design, and it is the strongest evidence in the submission that
the system generalises rather than memorises.

Recorded in the artifact (`model.json`) so Phase 7 compares against a number rather than
a memory:

```
trained_on              data/train
excluded_cases          tds_deducted, refund_netted
base_rate_all           0.499932
base_rate_fit           per-split, recorded
negatives_per_positive  1.0
rebalancing             none - the candidate set is already near-balanced
```

**On class balance:** candidates are near-balanced (3,652 positive, 3,653 negative),
because blocking is tight — 1.48 candidates per settlement. No reweighting or resampling
was applied, so the calibrated probabilities are not distorted by a correction. Had
rebalancing been needed it would have changed the probabilities directly, which is why
the artifact records that none was done.

**Expect the Phase 7 numbers to be lower**, and expect calibration in particular to
degrade: the test set carries a different class prior (all ten case types, per
`notes/distribution.md`) and calibration is fitted to the training prior. Phase 7 plots
the reliability diagram on test *alongside* train so the drift is measured rather than
discovered.

---

## Reproducing

```
ledgerloop train --batch data/train --out runs/_models/v1
ledgerloop recon --in data/train --mock-llm --model runs/_models/v1 --run v1-train
ledgerloop eval --run v1-train --threshold 0.9989
```

Feature schema **1.1.0**, 23 features. The artifact records it and `Artifact.load`
**refuses to return a model** whose schema does not match the running code — a mismatch
would feed the model columns it never trained on and produce plausible, wrong numbers
silently.

---

## The operating point has three consequences, not two

The floor was chosen on precision against coverage. That framing is incomplete: the same
threshold also sets the inference bill, because everything not auto-matched becomes an
exception, and a share of exceptions cost an LLM call.

Measured on `data/train` at 25,000-row scale:

| precision floor | coverage | residue | LLM-worthy exceptions | run time at 40 rpm |
|---|---|---|---|---|
| **99.5%** *(selected)* | 51.4% | 48.6% | ~6,990 | ~9 min batched |
| 99.0% | 77.7% | 22.4% | ~3,220 | ~4 min batched |

Not every exception needs a model. 43% of them resolve to a reason code the pipeline
already knows -- `NO_CANDIDATE` (blocking produced nothing) and `NO_INVOICE_LINK` (no
invoice inferable). Sending those to an LLM would be generative work a rule can settle,
which architecture rule 1 forbids. The cost saving is a *consequence* of the rule, not a
motivation for bending it.

**The floor stays at 99.5%.** The point of recording this is that a merchant choosing an
operating point is choosing three things at once:

1. **review workload** -- how many exceptions a human must look at
2. **wrong matches** -- how much money is misposted
3. **inference cost** -- what the residue costs to explain

Phase 6's slider should surface all three. A curve showing coverage and precision alone
asks the merchant to optimise half a problem; showing estimated cost alongside makes the
unit-economics argument interactive rather than a paragraph in the README, and it is the
same number Phase 7 reports as cost per 1,000 rows.
