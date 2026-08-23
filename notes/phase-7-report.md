# The sealed test set, scored once at the pre-committed operating point

`data/test` was sealed at Phase 1, regenerated once at Phase 3 before anything had been
read from it, and read for the first time to produce the numbers below. The seal marker
was deleted in the same commit as this file, so the unsealing is an event in git history
rather than a claim in prose.

Scored at threshold **0.9564**, fixed in `notes/phase-7-precommitment.md` and committed at
`a733ad4` with the seal intact. One threshold, one run, no retuning. Raw output:
`notes/measurements/sealed_test.json`.

**Integrity.** All five files matched the sha256 map recorded in `.sealed` at the moment
the seal broke. The map is carried forward unchanged into `data/test/.unsealed`, and
`tests/test_seal.py` still enforces it — the numbers below are tied to specific bytes, and
that tie survives the marker's deletion. It matters more now than it did before: with the
seal gone, this check is the only thing standing between the out-of-sample claim and a
quiet regeneration.

---

## 1. The headline

| | |
|---|---|
| settlements | 4,950 |
| decidable truth links | 4,950 |
| auto-matched | 3,114 |
| **coverage** | **62.91%** |
| **precision** | **99.9037%** — 95% CI **[99.7171%, 99.9672%]**, **3 false in 3,114** |
| recall | 62.85% |
| ₹ at stake | 416,605,821.54 |
| ₹ auto-matched | 259,476,049.60 |
| **₹ incorrectly matched** | **671,820.00** |
| money-weighted precision | 99.7411% |
| money error ratio | 0.1613% |
| orphans | 50, all 50 refused (100%) |
| exceptions | 1,836, of which 69.77% never reach a model |

Coverage is quoted under `notes/metrics.md`'s definition, `|P(t)|` over non-orphan truth
triples. `notes/measurements/scale.py` reported `matched/settlements` instead, and the
pre-commitment compared one against the other. On this batch the two agree to four
decimal places — 3,114/4,950 either way — so the comparison below is like for like. That
was checked on `data/scale` before the seal broke, not discovered afterwards.

## 2. The registered prediction held

> *Test coverage will land nearer 61% than 68%, because the two unseen case types pull it
> down, and it will not collapse to 51%, because the 196-candidate block reproduced on
> `data/scale`.*

**62.91%.** Nearer 61 (1.91 points away) than 68 (5.09 points away), and inside the
declared 58–63% band that the pre-commitment labelled *as predicted*. The block reproduced;
the fragility `notes/threshold.md` warned about did not bite here.

Against the failure table written in advance:

| declared outcome | reading | what happened |
|---|---|---|
| coverage near 51% | the 196-candidate block did not reproduce | **did not occur** — 62.91% |
| coverage 58–63% | as predicted | **this one.** 62.91% |
| precision below 99.5% | the floor did not hold out of sample | **did not occur** — 99.9037%, and the *entire* interval [99.7171%, 99.9672%] sits above the floor |
| held-out types near zero | the model cannot generalise to unseen types at all | **half occurred.** See §3 — one type at 68.00%, the other at 0.00% |

The precision floor did not merely hold, it held with room. The pre-commitment expected
this to be the shakiest claim, since the floor had been enforced on an eval-split estimate
whose interval contained 99.0%. Out of sample the point estimate improved from 99.5031% to
99.9037%.

**One caveat that belongs beside that number, not below it.** The same threshold on
`data/scale` — 24,750 settlements, scored before the seal broke — gave precision
**99.2369%**, which is *below* the floor: 115 false matches in 15,071
(`notes/measurements/scale_scored.json`). The sealed batch is
five times smaller and produced 3 in 3,114. Both are measurements; they disagree, and the
disagreement is not noise on these counts. The plausible mechanism is invoice contention
growing with batch size, since `resolve` consumes an invoice globally and a larger batch
offers more chances for the wrong settlement to claim one first. **That is a hypothesis and
has not been tested.** What can be said without testing anything is that 99.9037% is a
measurement at 4,950 settlements, and the only figure this project has at 24,750
settlements is worse and under the floor. Quoting the sealed number without the scale
number beside it would be selecting the friendlier of two runs, which is the thing this
whole exercise exists to prevent.

### The three false matches

| ₹ | invoice case type | what went wrong | p |
|---|---|---|---|
| 75,000.00 | clean | invoice attached to settlement 2415 / txn 2415; truth says settlement 1952 / txn 1952 | 1.000000 |
| 96,820.00 | clean | invoice attached to settlement 3873 / txn 3923; truth says settlement 2859 / txn 2859 | 1.000000 |
| 500,000.00 | date_skew | **invoice and txn both correct** (txn 3064); settlement row wrong — predicted 2936, truth 3064 | 0.992754 |

The third is 74% of all wrong money and is a triple-equality artifact worth stating
plainly. `notes/metrics.md` counts a prediction correct only if all three ids match, and
that convention was chosen to stop "right settlement, wrong invoice" from scoring as
partial credit. Here it fires in the other direction: the credit and the invoice are both
right, only the settlement row is misattributed, and the full ₹500,000 lands in the wrong-
money numerator. **It is reported as a false match because that is the convention, and
changing the convention after seeing which way it cut would be the same sin as changing the
threshold.** A reader should know that ₹671,820 of wrong money is two genuinely wrong links
worth ₹171,820 and one misattributed row worth ₹500,000.

Two of the three were scored at exactly p = 1.000000. Isotonic calibration's top step is
saturated, so maximum confidence is not evidence of a maximally safe match — a known
property of the calibrator, visible here in the only three cases where it mattered.

## 3. The held-out case types, reported separately

Neither type was in training. The model's recorded class prior describes eight case types,
not ten, and `model/artifact.py` says so at load time.

| case type | truth rows | matched | missed | matched rate | 95% CI | width |
|---|---|---|---|---|---|---|
| `tds_deducted` | 250 | 170 | 80 | **68.00%** | [61.98%, 73.47%] | 11.49 pts |
| `refund_netted` | 200 | 0 | 200 | **0.00%** | [0.00%, 1.88%] | 1.88 pts |

**These do not average, and the average is not reported.** 450 rows across two types
producing 68% and 0% is not "34% on unseen types"; it is one type the model handles about
as well as the types it trained on, and one it cannot handle at all. Folding them together
would describe a system that does not exist.

**On whether 450 rows can distinguish anything — the honest answer differs per type, and
the instruction to expect wide intervals turns out to be right for one and wrong for the
other.**

*`tds_deducted` cannot be resolved by 250 rows.* The interval spans 11.49 points, which
covers `rounding_drift` (64.00%), `clean` (70.70%), and `partial_payment` (65.00%) among
the seen types. The defensible claim is "indistinguishable from the seen-type rates at this
sample size" — not "generalises well", which the data does not support, and not "degrades",
which it also does not support.

*`refund_netted` is resolved decisively, and 200 rows are enough.* 0 successes in 200 gives
a Wilson upper bound of 1.88%: the true rate is below 2% with 95% confidence. That is a
narrow interval, not a wide one, because zero events is an informative outcome rather than
an uncertain one. This is a **total generalisation failure on a case type representing 4%
of the distribution**, and no larger sample would change the reading. `data/scale`
independently produced 0 of 1,000 on the same type before the seal broke
(`notes/measurements/scale_scored.json`), a second measurement on 5× the rows agreeing
exactly.

So the pre-commitment's fourth row — "held-out types near zero: the model cannot generalise
to unseen case types at all" — is **half realised, and the half is the informative half**.
The correct statement of the limit is narrower and more useful than the one written in
advance: the model generalises to an unseen deduction structure it can see in the amount
(TDS shifts the credit by a fixed rate, which the `rate_amount` blocking pass already
retrieves) and fails completely on an unseen *netting* structure, where a refund is
subtracted inside the payout and the arithmetic linking invoice to credit is not a rate at
all. That is a statement about which unseen things transfer, which is worth more than a
number.

## 4. Reliability: test beside train, and where the degradation comes from

`model/chart.py` renders the diagram; `notes/measurements/reliability-test.png` is the test
batch. Recorded train-side bins live in `runs/_models/v1/model.json`.

| population | n | ECE | MCE | Brier | base rate |
|---|---|---|---|---|---|
| train eval split (recorded, 8 types) | 1,958 | 0.010436 | 0.364118 | 0.016449 | 0.4775 |
| **test, all candidates** | 7,538 | **0.012031** | 0.679533 | 0.017542 | 0.4812 |
| test, seen case types only | 7,116 | **0.007497** | 0.536747 | 0.013369 | 0.4689 |
| test, held-out types only | 422 | **0.113699** | 0.750000 | 0.087911 | 0.6872 |

**ECE degrades from 0.0104 to 0.0120 out of sample — 1.6 thousandths — and the degradation
is entirely attributable to the two unseen case types.** That is a measurement, not an
inference from the direction of the change:

- remove the held-out candidates and test ECE is **0.007497**, which is *better* than the
  train eval split it is being compared against;
- the held-out candidates alone score **0.113699**, 10.9× the train-side figure and 15.2×
  the seen-type figure on the same batch;
- they are 422 of 7,538 candidates (5.60%), so a small badly-calibrated population moves
  the aggregate by roughly the amount observed.

The diagnosis is prior shift of a specific kind, and it can be located rather than asserted.
The held-out candidates sit at **mean p 0.5735** against a batch-wide base rate of 0.4812,
and **40.28% of them fall at or above the operating point** — they are not uniformly
distributed across the score range. By bin, they are over-represented in exactly the middle
of the distribution where the calibrator has the least data:

| bin | n | mean predicted | observed | gap | held-out n | held-out share |
|---|---|---|---|---|---|---|
| [0.0, 0.1) | 3,803 | 0.00002 | 0.00763 | 0.0076 | 153 | 4.02% |
| [0.2, 0.3) | 82 | 0.25000 | 0.42683 | 0.1768 | 19 | 23.17% |
| [0.6, 0.7) | 62 | 0.66563 | 0.75806 | 0.0924 | 23 | **37.10%** |
| [0.9, 1.0) | 3,581 | 0.99133 | 0.98045 | 0.0109 | 224 | 6.26% |

Bins of one to three candidates are omitted from the table above and are the reason **MCE
should be ignored on this batch**: test MCE of 0.679533 comes from the `[0.3, 0.4)` bin,
which holds exactly one candidate. A worst-bucket statistic over buckets of size 1 measures
the binning, not the model. ECE is population-weighted and does not have this problem,
which is why it is the number quoted.

The two populous bins — 98% of all candidates between them — carry gaps of 0.0076 and
0.0109. **Where the system actually operates, it is calibrated.** The middle of the range,
where it is not, is thinly populated and disproportionately made of case types the
calibrator never saw.

## 5. Per-case-type confusion

`false` attributes each false match to the case type of the invoice it wrongly claimed;
`confusion_by_case_type` keys off truth rows and cannot see false positives at all, so
without this column the wrong answers would be invisible per type.

| case type | total | matched | missed | refused | false | rate | 95% CI |
|---|---|---|---|---|---|---|---|
| `batched_settlement` | 599 | 208 | 391 | 0 | 0 | 34.72% | [31.02%, 38.62%] |
| `clean` | 2,751 | 1,945 | 806 | 0 | 2 | 70.70% | [68.97%, 72.37%] |
| `date_skew` | 150 | 76 | 74 | 0 | 1 | 50.67% | [42.75%, 58.55%] |
| `duplicate_utr` | 100 | 75 | 25 | 0 | 0 | 75.00% | [65.70%, 82.45%] |
| `fee_deducted` | 500 | 378 | 122 | 0 | 0 | 75.60% | [71.65%, 79.16%] |
| `orphan` | 50 | 0 | 0 | 50 | 0 | 100.00% *(refusal)* | [92.87%, 100.00%] |
| `partial_payment` | 300 | 195 | 105 | 0 | 0 | 65.00% | [59.44%, 70.18%] |
| `rounding_drift` | 100 | 64 | 36 | 0 | 0 | 64.00% | [54.24%, 72.73%] |
| **`tds_deducted`** *(held out)* | **250** | **170** | **80** | **0** | **0** | **68.00%** | **[61.98%, 73.47%]** |
| **`refund_netted`** *(held out)* | **200** | **0** | **200** | **0** | **0** | **0.00%** | **[0.00%, 1.88%]** |

For `orphan` the rate is the refusal rate, since the correct outcome is to refuse. All 50
were refused, and none was auto-matched — the hazard `notes/metrics.md` describes, where a
system is rewarded for quietly auto-matching orphans out of the denominator, did not
materialise.

`batched_settlement` is the weakest seen type at 34.72% and the largest single source of
missed links (391 of 1,836 exceptions). It is not a new finding — `data/train` showed 106
of 659 — but it is the clearest statement yet of where coverage is actually lost. Neither
held-out type contributes a single false match.

## 6. What this does and does not license

**Reported as measured.** The prediction held on coverage and on the precision floor. The
seal was broken once, at one threshold, and nothing was tuned afterwards.

Three things a reader should carry away with the headline:

1. **99.9037% precision is a 4,950-settlement measurement.** The 24,750-settlement
   measurement, taken before the seal broke, is 99.2369% and under the floor. Do not quote
   the first without the second.
2. **`refund_netted` fails completely** — 0 of 200, upper bound 1.88%, confirmed by 0 of
   1,000 on `data/scale`. A case type worth 4% of the distribution is not handled.
3. **Calibration out of sample is intact where the system operates** and degrades only on
   the unseen types, by a measured 1.6 thousandths of ECE overall.

The README stated 51.31% coverage at 99.5050% as the documented figure and 68.16% as
measured-but-unconfirmed. Both are superseded by 62.91% at 99.9037% on held-out data. The
README was rewritten in a later commit rather than this one — the numbers land first and
the prose that quotes them follows, so the two are separable in history.

**One inconsistency this exposed, recorded here because it is not a documentation problem.**
The deployed instance is seeded from `runs/v1-train/`, a run scored at threshold
**0.998921** — the operating point the pre-commitment rejected. No prose on the site quotes
a superseded number, but the site *renders* one: 50.50% coverage at 99.96% on `data/train`.
Re-seeding is a deployment change and is not made as part of reporting the test set.
