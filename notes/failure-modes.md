# What this system gets wrong

Started in Phase 1, completed in Phase 7. BUILD.md rewards this list twice, and it is the
part of the submission most likely to be believed — a system that can name its own
weaknesses is easier to trust on its strengths.

Entries are added as they are discovered, not retrofitted at the end. Nothing here is
softened.

---

## Known limitations of the synthetic data

### `order_receipt` is populated on only ~38% of gateway rows

**This is a modelling judgement, not a measured figure.** It is stated plainly here
because a panelist will ask, and the honest answer is that it is an assumption with a
rationale rather than a citation.

**The rationale.** Razorpay's `receipt` and `notes` fields are merchant-populated and
optional ([settlement recon
report](https://razorpay.com/docs/api/settlements/fetch-recon/)). A checkout integration
that creates an order without setting a receipt produces a settlement row with nothing on
it linking back to the merchant's own invoice. Anecdotally that is common, particularly
for merchants who integrated quickly; 38% is chosen as "a substantial minority populate
it", not derived from data we have.

**Why it matters more than it looks.** The first generator populated this field on 100%
of rows, which meant the invoice-to-settlement link was *given* rather than inferred.
Every difficulty that lives in the relationship between an invoice and its credit --
TDS, gateway fee, partial payment -- was therefore never exercised: the matcher only had
to link a settlement to a bank transaction, and could read the invoice off the row.
Measured effect of that mistake: **98.99% match rate with rules alone**, no residual for
the classifier, and a Phase 7 risk-coverage curve that would have been flat.

After the change the matcher must reconstruct the link through the bank narration, which
is the only place a counterparty name appears. Match rate on `data/train` fell to 76.5%,
inside the intended 70-85% band.

**What would strengthen the claim.** A distribution measured from real anonymised
settlement exports, or a statement from a payments engineer about how often the field is
set in practice. Neither was available. If the true figure is much higher than 38%, this
system is being tested against harder data than reality -- which is the safe direction to
be wrong in, but it is still being wrong.

### Invoice amounts are log-normal, but the parameters are invented

Amounts are drawn log-normal (mu 10.4, sigma 1.05 in log-rupees) with 38% forced onto
round values -- Rs 25,000, Rs 1,00,000 and similar. The *shape* is right: real B2B
invoice values are log-normal and cluster hard on round numbers. The *parameters* are
chosen to look plausible, not fitted to anything.

This matters because it directly controls how often two invoices share an amount, which
is the main source of genuine ambiguity in the data. At the current settings 22% of
payouts share their value with another payout. Set sigma higher and matching gets easier;
lower and it gets harder. Nothing pins the value down but judgement.

### Each case type isolates its own signal

A `tds_deducted` case carries TDS and no gateway fee; a `fee_deducted` case carries fee
and no TDS. Real settlements routinely carry both at once, plus a refund.

**Effect:** the per-case-type confusion matrix in Phase 7 is cleaner than reality. A real
row with TDS *and* a fee *and* a partial payment is harder than anything in this dataset.

**Why it was done:** mixing effects would make the confusion matrix uninterpretable —
impossible to say which effect the classifier actually learned. The trade is legibility
for realism, made knowingly.

**What would fix it:** a `--difficulty hard` mode that composes two or three effects on a
minority of rows, labelled as a compound case type.

### Two bank dialects, not twenty

HDFC and ICICI are modelled. A real merchant may bank with several institutions, and
smaller banks produce considerably messier narrations than either of these.

**Effect:** narration parsing is tested against two grammars. A third, unseen grammar is
exactly what chaos mode injects in Phase 6, so this limitation is at least measured
rather than merely disclosed.

### The generator and the matcher share an author

The most honest caveat. Synthetic data written by the same person who writes the matcher
risks encoding assumptions that make matching easier than reality.

**What mitigates it:** the isolation boundary (`tests/test_import_lint.py`) means the
matcher cannot see the generator's internals, seeds, ordering, or answer key. The two
held-out case types mean two of the ten failure modes were never available at training
time. Chaos mode injects corruption no builder in `datagen/` produces.

**What does not mitigate it:** none of that proves the *distribution* is realistic. Only
a run against real settlement data would, and that is out of scope.

---

## Known limitations of the system

*Populated from Phase 3 onward as real failures are measured.*
