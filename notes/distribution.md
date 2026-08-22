# Case-type distribution, and the train/test prior shift

Two of the ten case types — `tds_deducted` and `refund_netted` — are held out of the
training batch so Phase 7 can report performance on failure modes the model has never
seen. That is a deliberate design choice and the strongest evidence in the submission
that the system generalises rather than memorises.

It has a consequence that must be measured rather than discovered: **train and test carry
different class priors.**

---

## Targets

| case_type | test / demo (all ten) | train (renormalised) | change |
|---|---|---|---|
| clean | 55.00% | 60.44% | +5.44 |
| batched_settlement | 12.00% | 13.19% | +1.19 |
| fee_deducted | 10.00% | 10.99% | +0.99 |
| partial_payment | 6.00% | 6.59% | +0.59 |
| **tds_deducted** | **5.00%** | **held out** | −5.00 |
| **refund_netted** | **4.00%** | **held out** | −4.00 |
| date_skew | 3.00% | 3.30% | +0.30 |
| duplicate_utr | 2.00% | 2.20% | +0.20 |
| rounding_drift | 2.00% | 2.20% | +0.20 |
| orphan | 1.00% | 1.10% | +0.10 |

The 9% freed by the two held-out types is redistributed **proportionally** across the
survivors, preserving their relative structure: `clean / fee_deducted` is 5.50 in both
columns.

**The rejected alternative was dumping all 9% into `clean`.** That would have made the
training batch materially easier than the test batch, and the resulting train/test gap in
Phase 7 would have been unattributable — impossible to separate "the held-out types are
hard" from "we made train easier". Proportional redistribution keeps the only intended
difference between the batches the presence of the two held-out types.

## Measured

Generated at 2,000 rows, seed 42. Every case type lands within 0.09% of target, well
inside the 1% tolerance the exit criteria require.

## The consequence: calibration is fitted to the training prior

Phase 4 fits calibration (isotonic or Platt) on a validation split taken **from train**.
That split carries the renormalised prior, not the test prior. When the seal on
`data/test/` breaks in Phase 7, calibration may degrade even if the classifier still
ranks candidate pairs correctly — because ranking and calibration fail independently.

This is not a reason to change the design. It is a reason to expect the effect and to
measure it:

1. **Phase 4** — fit and validate calibration on train, and record in `notes/threshold.md`
   that the reliability diagram reflects the training prior.
2. **Phase 7** — plot the reliability diagram on **test alongside train**. If calibration
   drifts, the diagram shows it as drift rather than leaving it to be discovered as an
   unexplained precision drop.

A degraded-but-measured calibration curve, reported honestly with its cause named, is
worth more at the panel than a clean number that hides a prior shift.

## Where the numbers come from

`datagen/schemas.py::target_shares()` is the single definition; the renormalisation lives
there. `tests/test_generator.py` asserts every type is within 1% of target, that
relative proportions survive renormalisation, and that the held-out types leave no trace
in the training batch — not in `truth.csv`, not as a TDS-flagged invoice, and not as a
`type=refund` recon row.

---

## Counterparty pool: 16 names -> 2,000 (23 Aug 2026)

The first generator drew counterparties from a hardcoded list of 16 companies. That was
wrong in a way that would only have surfaced in Phase 7, as an unattributable train/test
gap.

**Why it mattered.** Counterparty historical match frequency is a planned Phase 3 feature
and a Phase 4 model input. With 16 names every counterparty recurs hundreds of times in a
5,000-row batch, so that feature carried far more signal than it ever could against a real
merchant's ledger. Blocking recall was inflated for the same reason: bucketing by
normalised counterparty token barely narrowed the candidate set when there were only 16
possible values, which would have made Phase 3's blocking look more effective than it is.

Had this survived to Phase 7, the honest answer to *"was that feature strong because of
your data?"* would have been *"probably, and I can't separate it out now."*

**What changed.** `datagen/customers.py` builds a 2,000-name pool as a deterministic
product over 52 Indian places and 40 sector words, with corporate suffixes assigned by
index. Nothing in the pool consumes the seeded `Random`, so it is a constant: two callers
get identical lists regardless of what else has drawn from the generator.

All three batches were regenerated. The case-type distribution is unaffected — the pool
changes *who* transacts, not *what happens* — and every distribution assertion still
holds.

**Still flagged, deliberately not fixed:** invoice amounts remain uniform over
Rs 1,000-5,00,000 where real B2B values are log-normal with round-number clustering. This
makes amount-band blocking slightly easier than reality. Recorded in
`notes/failure-modes.md` rather than fixed, because unlike the counterparty pool it does
not feed a named model feature.
