# What this system gets wrong

Started in Phase 1, completed in Phase 7. BUILD.md rewards this list twice, and it is the
part of the submission most likely to be believed — a system that can name its own
weaknesses is easier to trust on its strengths.

Entries are added as they are discovered, not retrofitted at the end. Nothing here is
softened.

---

## Known limitations of the synthetic data

### Invoice amounts are uniform, not log-normal

`datagen/cases.py` draws invoice values uniformly over Rs 1,000–5,00,000. Real B2B
invoice values are roughly log-normal, with heavy clustering on round numbers
(Rs 50,000, Rs 1,00,000) and a long tail of large contracts.

**Effect:** amount-band blocking in Phase 3 is easier here than in production. Real data
piles many invoices onto identical round amounts, so an amount bucket narrows the
candidate set less than it does here, and the subset-sum search for batched settlements
faces more collisions.

**Why it was not fixed:** unlike the counterparty pool, this does not feed a named model
feature — it affects blocking efficiency, which is reported as measured throughput rather
than as an accuracy claim. Deliberately left as-is and disclosed.

**What would fix it:** sample from a log-normal fitted to published invoice-value
distributions, with an explicit round-number mass. Roughly an hour, plus a regeneration.

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
