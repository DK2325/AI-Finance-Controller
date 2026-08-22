# Metric definitions

Written before the scorer, because a scoring convention chosen implicitly is a scoring
convention nobody can audit. Every number in the submission is produced by
`evals/metrics.py` against these definitions.

---

## The unit of prediction

Ground truth links three records: an invoice, a settlement, and a bank transaction. One
truth row is one **triple** `(invoice_id, settlement_id, txn_id)`.

A prediction is that same triple plus a confidence in `[0, 1]`. A prediction is correct
only if all three ids match a truth triple — matching the settlement but attaching it to
the wrong invoice is wrong, not partially right.

Batched settlements produce N truth rows sharing one `txn_id`, so a batch of five
invoices is five separate predictions that can each be right or wrong independently.

---

## Orphans

Orphans are bank credits with no invoice and no settlement behind them: genuinely
unmatchable, 1% of every batch. They are written into `truth.csv` with empty
`invoice_id` and `settlement_id` precisely so the scorer can tell a **correct refusal**
from a **miss**.

| Outcome | Precision | Recall | Money-weighted |
|---|---|---|---|
| Correct link predicted | TP | TP | — |
| Wrong link predicted | FP | — | full amount into numerator |
| Link predicted for an orphan | **FP** | — | full amount into numerator |
| Orphan correctly refused | **excluded** | **excluded** | — |
| Real link missed (sent to exceptions) | excluded | FN | — |

Two choices carry the weight here:

**Orphans are excluded from precision's denominator.** Precision is measured over
auto-matched pairs, and a refusal is not a pair.

**Orphans are excluded from recall's denominator.** There is no link to recall.

Both exclusions create the same hazard: a system that quietly auto-matches orphans would
be *rewarded* by a shrinking denominator rather than punished. So orphan handling gets
its own reported metric:

```
orphan_refusal_rate = orphans correctly refused / total orphans
```

An orphan that gets auto-matched is counted twice against the system — once as a false
positive in precision, once as a miss in `orphan_refusal_rate`. That is deliberate. In
production a false auto-match posts wrong money to a ledger, and an orphan is the case
where the system had no correct answer available and invented one anyway.

---

## Core metrics

Let `D` be the non-orphan truth triples (the links that genuinely exist), and `P(t)` the
predictions at or above threshold `t`.

```
coverage(t)   = |P(t)| / |D|          fraction of decidable links the system decided
precision(t)  = TP(t) / |P(t)|        of what it decided, how much was right
recall(t)     = TP(t) / |D|           of what existed, how much it found

recall = precision * coverage
```

`coverage` counts every prediction, right or wrong. A system that auto-matches everything
reaches coverage 1.0 with poor precision, which is exactly the trade the risk–coverage
curve exists to make visible.

---

## Money-weighted precision

BUILD.md defines this as *"₹ incorrectly matched ÷ ₹ total"*. Taken literally that is an
**error ratio** — lower is better — which would be a trap under a name containing the
word "precision", where every other metric improves as it rises.

So both are computed and reported under distinct names:

```
money_error_ratio        = wrong_money / total_money        (BUILD.md's definition, lower better)
money_weighted_precision = 1 - wrong_money / matched_money  (higher better, comparable to precision)
```

Where:

- `total_money` — sum of invoice amounts over all non-orphan truth triples. The money at
  stake in the batch.
- `matched_money` — sum over predictions at threshold of the money that would be posted.
- `wrong_money` — the same sum restricted to false positives.

Money for a prediction is the **invoice amount** where an invoice is named, and the
**bank credit** where the prediction involves an orphan (there is no invoice, and the
bank credit is what would be wrongly posted).

All money arithmetic is integer paise, as everywhere else in this system.

**Why it matters more than row-weighted precision:** a system can be right about 99% of
rows and still be catastrophic if the 1% it gets wrong are the large ones. Row-weighted
precision cannot see that; money-weighted can. `tests/test_metrics.py` contains a fixture
built for exactly this case — healthy row-weighted precision, poor money-weighted —
because it is the scenario the metric exists to expose.

---

## The risk–coverage curve

The headline artifact, and a first-class object rather than a chart: `evals/curve.py`
builds a `RiskCoverageCurve` of points, one per distinct confidence threshold, each
carrying `threshold, coverage, precision, recall, money_weighted_precision,
money_error_ratio, n_predicted, n_true_positives, n_false_positives`.

It is consumed by Phase 4 (operating-point selection), Phase 6 (the live slider) and
Phase 8 (the video). The chart is a consumer; tests assert on the curve data, never on
rendered pixels, because matplotlib output varies by version.

**A deterministic system has a degenerate curve.** The Phase 2 baseline emits near-fixed
confidence, so it produces very few points and cannot trade coverage for precision at
all. That is the correct result, not a defect — and it is the argument for Phase 4.
A calibrated classifier is what turns a single operating point into a curve the merchant
can choose from.

---

## The baseline

`evals/baseline.py` is a deliberately weak exact-UTR-only matcher, and exists as a
regression floor: any real matcher that fails to beat it is broken.

It reads only the three input files. Its public function takes **no `truth` parameter at
all**, and `tests/test_baseline.py` asserts both that the signature cannot accept one and
that the module never names the answer key. The isolation boundary is structural here,
the same as `tests/test_import_lint.py` and `tests/test_seal.py`.

If the scorer ever reports near-perfect results for this baseline, the scorer is broken
and must be investigated before any real matcher is built.
