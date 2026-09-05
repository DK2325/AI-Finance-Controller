# Pre-commitment, written before the seal on `data/test` is broken

**Nothing in this file was written with any knowledge of the sealed test set.** It is
committed before `data/test/.sealed` is deleted, so git history shows the order.

The point of writing it down first: a prediction that either holds or does not is worth
something. The same sentence written afterwards is a rationalisation, and a reader who has
seen a lot of model reports can tell which one they are looking at only from the order.

---

## 1. The operating point, chosen now

**Threshold `0.9564`.** Selected by the standard procedure — the highest-coverage point
holding precision ≥ 99.5% on the evaluation split, which neither the classifier nor the
calibrator was fitted on.

**Why not 0.9989**, which every document currently states: that number came from the same
procedure run against a resolver we now know was broken — `resolve_indices` used an
unstable sort, and with 99.7% of candidates sharing an exact calibrated probability it
decided nearly every contested invoice by list order. The procedure is what we trust, not
either number it produced. Carrying 0.9989 forward because the documents say so would be
preferring a stale artifact of a bug to the current honest output.

**Scoring the test set at both thresholds and reporting the better one would be tuning
against the test set**, and would make the whole exercise worthless. One threshold, chosen
here.

## 2. What we expect, and what would count as failure

Out of sample on the evaluation split, 0.9564 gives **68.16% coverage at 99.5031%
precision**. But 16.6 of those 16.85 points over the old operating point come from a
**single step holding 196 candidates** calibrating at 0.9928. That is one discrete bet, and
`notes/threshold.md` says so.

**The scale run sharpens the prediction.** On `data/scale` — 24,750 settlements, a fresh
seed, all ten case types including the two the model has never seen — the same threshold
gives **60.89% coverage**. That batch is not the sealed set, but it is the first time this
model has met a ten-case distribution.

So the pre-registered prediction:

> **Test coverage will land nearer 61% than 68%**, because the two unseen case types pull
> it down, and it will not collapse to 51%, because the 196-candidate block reproduced on
> `data/scale`.

**What would count as failure, declared in advance:**

| outcome | reading |
|---|---|
| coverage near 51% | the 196-candidate block did not reproduce — the fragility `threshold.md` predicted, realised |
| coverage 58–63% | as predicted; the unseen case types cost roughly what `data/scale` suggested |
| precision below 99.5% | the floor did not hold out of sample. The floor was enforced on an estimate whose interval contains 99.0%, so this is possible and would be reported as the floor failing rather than explained away |
| held-out types near zero | the model cannot generalise to unseen case types at all — a real and reportable limit |

**Whatever it says gets reported.** Including if the number is worse than what the README
currently claims.

## 3. What will not happen after the seal breaks

- **No retuning.** Not the threshold, not the blocking passes, not the features, not the
  calibrator. The seal is broken once.
- **No re-generation** of `data/test`. It was regenerated once, at Phase 3, before anything
  had been read from it, and `.sealed` records why. That was the only permitted one.
- **No selection among runs.** The first scored run is the reported run.

## 4. Why the scale run happened first

Tuning blocking for throughput *after* reading the test set would let test knowledge inform
a decision that then shapes every test number. Doing the scale work first makes that
contamination path impossible rather than merely unlikely.

The scale run is complete and its numbers are in `notes/measurements/scale.json`.

## 5. Two corrections to my own estimates, made by the scale run

Recorded here because they were wrong in an earlier working note and the corrections arrived
before the seal broke:

| estimated | measured |
|---|---|
| ~37,000 candidates at 25,000 settlements | **94,555** — 2.6× under |
| peak memory implied to exceed 1 GB | **238 MB** peak Python allocation |

**The memory argument for running locally was wrong.** The real reasons are CPU time — 235s
on six cores, and the trial box has two — and that `tracemalloc` counts only Python
allocations, not the native ones numpy and LightGBM make, so 238 MB is a floor rather than
the true figure. The conclusion stands; the reasoning given for it did not.

Blocking growth across the two batch sizes: **exponent 1.590**, comfortably sub-quadratic.

---

*Committed before `data/test/.sealed` was deleted. The deletion and the resulting numbers
land in a single later commit, as designed in Phase 1.*
