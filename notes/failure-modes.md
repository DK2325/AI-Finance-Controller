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

**Confirmed at Phase 7, and worse than "cleaner than reality" suggested.** The sealed run
shows `refund_netted` failing completely on its own -- 0 of 200, because no blocking pass
retrieves a credit that differs from its invoice by an arbitrary amount. A real row carrying
a refund *and* a fee therefore does not sit somewhere between the two case types' rates; it
inherits the total failure, because the candidate is still never generated. **Compound cases
are not interpolations between their components, and reading this confusion matrix as if
they were would overstate the system on exactly the rows a merchant sees most.** That makes
the isolated-signal simplification a bigger caveat on the headline number than it looked
when it was written as a legibility trade.

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

### Instruments that are wrong toward reassurance

The recurring failure of this build, worth naming as a family rather than as four
separate incidents. Every one of these produced a *better* number than the truth, which
is the direction that does not get investigated:

| what | how it flattered | how it surfaced |
|---|---|---|
| isotonic fitted and measured on the same split | ECE of exactly 0.00000 | the number was too good to be real |
| the spike counted 429s as schema failures | reported 8% schema failure for a config whose every completed call was valid | separating transport from conformance |
| the batching contamination classifier compared raw output to bare digits | filed a real cross-contamination as harmless mis-extraction | normalising before comparing |
| the secret guard's own fixture | would have flagged itself, and the natural fix was to exempt the file most likely to grow a real key | assembling the fixture at runtime instead |

**A near-miss, designed against rather than discovered (Phase 5).** A mock LLM provider
with a hardcoded response shape belongs in exactly this family. The moment a prompt gains
a field, the mock stops resembling the real response — and it does so *silently*, with
every test still passing, because the mock is what the tests compare against. Coverage
would look unchanged while the thing being covered had moved.

`MockProvider` is therefore schema-driven: it builds its response by walking the JSON
Schema it was handed, so it cannot fall behind the schema it is imitating. This one was
caught by asking "how would this instrument lie to me?" before writing it, rather than by
noticing an implausible number afterwards.

**The general lesson:** a measurement that improves without a corresponding change to the
thing being measured is a bug until proven otherwise. Three of the four above were found
by disbelieving a good result.

### The worse one: an instrument that flattered a *diagnosis*

Filed separately, because it is not the same failure and it is more dangerous.

Every entry above flattered a **result**. A wrong result is eventually checked against
something — a held-out split, a baseline, a reviewer asking where the number came from.
A wrong **diagnosis** has no such check. It just gets acted on.

**What happened (Phase 5, 23 Aug 2026).** A batch truncated at the 8,000-token ceiling.
The provider reported *8,000 tokens produced 193 characters*. That reading is impossible —
no tokeniser yields 0.02 characters per token — and instead of stopping on the impossible
number, I built an explanation for it: the tokens must be going somewhere invisible, so
the model must be reasoning despite `enable_thinking: false`, or looping on twenty
near-identical narrations.

I then confirmed the looping hypothesis with a three-arm experiment across nine live
calls. The experiment agreed. It also had a bug that made one arm identical to its
control, which nobody would have caught because the arm agreed too.

One call that printed the response body ended it. The response was **23,973 characters,
23,780 of them trailing whitespace**. There was no loop and no hidden reasoning. The
constrained decoder stalls emitting spaces and newlines — legal JSON whitespace, so the
grammar is never violated and the decoder never advances — until the budget is gone.

**Why the contradiction was invisible.** `NvidiaProvider` did `.strip()` on the response
before recording anything. The 23,780 characters that would have explained everything were
destroyed at the boundary, and what survived was a plausible, impossible number.

**The fix, and the general form of it.** `LLMResponse.raw_chars` records the length before
stripping, and `stalled` distinguishes a stall from an honest overrun — a real truncation
fills its budget with content, so raw length tracks token count; a stall does not.

> **Normalisation at a boundary destroys the evidence you need when the thing behind that
> boundary misbehaves.** Anywhere the pipeline normalises before recording, keep the raw
> value alongside the clean one.

Places that rule now applies, and should keep applying: response text before `.strip()`
(done); narration text before `normalize_narration()`; amounts before `to_paise()`; dates
before `parse_date()`. Each of those is a boundary where a malformed input becomes a
well-formed value, and where the malformation is exactly what a future investigation will
need.

**The distinguishing question**, worth asking before building an explanation for any
number: *is this reading physically possible?* Two experiments were spent because it was
not, and nobody asked.

### The same error, four times: a message that names the wrong investigation

This is now the most repeated mistake in the project, and every instance cost the same
thing — time spent looking where the message pointed.

| the message said | what was actually true | what it cost |
|---|---|---|
| "8,000 tokens produced 193 characters" | 23,973 characters were produced; `.strip()` had destroyed 23,780 of them before anything recorded a length | two experiments confirming a hypothesis about reasoning tokens |
| "8% schema failure rate" | every completed call was valid; the failures were 429s, counted in the same bucket | a retry path made to look load-bearing when the rate limiter was talking |
| "`NO_CANDIDATE`: no bank credit resembled this payout" | blocking had produced five candidates and every rule tier declined | an audit record that would send an investigator to the payment gateway |
| "postgres unreachable" | Postgres was up; a foreign key refused the row | the first minutes of the wrong investigation |

**None of these was a broken system.** In every case something failed for a real reason and
then *described itself inaccurately*, and the description is what the next person acts on.
A wrong number gets checked against something eventually. A wrong explanation gets believed
and followed.

**A fifth instance turned up at Phase 7, in a different form: not an error message but a
defect estimate.** A note sizing an invoice-inference bug at "two rows in 4,945" measured
almost correctly -- five in 4,950 out of sample -- while naming the wrong cause for three of
the five. Written up under *The two smaller defects, logged and not chased*. It is listed
here because it extends the pattern beyond error strings: **anything that explains a number
can name the wrong investigation, and being roughly right about the number is what stops
anyone re-examining the explanation.**

**The fix is the same shape every time: classify rather than generalise.** The database
error handler now separates unreachable, refused-by-constraint and schema-mismatch, because
those are three different investigations and one label served none of them. The token
counter records the raw length beside the stripped one. The spike counts transport
separately from conformance. The reason code says "scored zero on rule tiers" when that is
what happened.

> **An error message is a hypothesis about what went wrong, and it deserves the same
> scepticism as any other hypothesis.** If it is a guess, it should say so; if it can be
> narrowed by looking at the exception type, it should be.

### And the sixth: an instrument that was correct and measured a different axis

Distinct from the `.strip()` entry above, and the distinction is the useful part.

| | what went wrong |
|---|---|
| `.strip()` | the instrument was correct; **the evidence had been destroyed** before it reached the instrument |
| this one | the instrument was correct; **it was measuring a different axis** from the one that moved |

**What happened.** Removing an array field from the parse schema fixed a decoder stall.
Every number improved: schema failure rate 0.0%, provenance 0 failures over 280 fields,
wall time down 3x, tokens down 41%. It read as an unqualified win.

Meanwhile UTR extraction had fallen from 63 of 71 to **48 of 71**. A third of the UTRs
went missing and not one metric moved, because the provenance gate answers *"of the claims
that were made, how many were true?"* — and a field that is never answered makes no claim.
A miss is an `EMPTY` verdict, never `ABSENT`. The gate was working perfectly. It is simply
not a coverage instrument and was never designed to be.

> **Every quality metric needs a coverage metric beside it.** A rate computed over claims
> made says nothing about the claims that were never attempted.

**The fix, implemented rather than noted.** `ProvenanceStats` now reports `claim_rate` and
`claim_rate_by_field` alongside the failure rate:
`tests/test_provenance.py::test_a_field_that_stops_being_answered_moves_the_claim_rate`
holds two runs with identical, perfect quality scores and different coverage, so the blind
spot cannot come back silently.

**It caught its author's error on its first run, which is the best argument for it.**

A rule I wrote into `parse.v3` — *"payment_method is `unknown` unless the narration
indicates one; guessing is worse than declining"* — made the model decline 60 cases where
the method is written literally in the narration:

| | claim rate | failure rate |
|---|---|---|
| `parse.v1` (no such rule) | 0.98 | 0.0% |
| `parse.v3` (rule added) | **0.38** | 0.0% |
| `parse.v4` (rule corrected) | 0.98 | 0.0% |

Ground truth: 98 of 100 narrations name a method explicitly. Sixty per cent of the claims
disappeared and **every failure rate read 0.0% throughout**. Nothing else in the pipeline
would have noticed — not the schema check, not the provenance gate, not the eval harness.

A control that catches the mistake of the person who wrote it, on the first run after
being added, has earned its place more convincingly than any argument for it could.

*(`payment_method` was deleted entirely a commit later, for an unrelated and stronger
reason — see the theorem in the README. The metric's value is unaffected: it caught a real
60% regression in a field that was live at the time.)*

**The audit this prompts.** Every quality rate in the codebase, checked for a coverage
counterpart:

| rate | coverage beside it | |
|---|---|---|
| `Score.precision` | `Score.coverage` | ✅ already paired |
| `RiskCoverageCurve` | coverage *is* the x-axis | ✅ by construction |
| operating point precision | selected against coverage | ✅ |
| `SubsetSumStats.cap_hit_rate` | reports buckets skipped | ✅ |
| `JobResult.schema_failure_rate` | every row returns an outcome, asserted | ✅ |
| `ProvenanceStats.field_failure_rate` | **had none** | ❌ → fixed |

One gap, now closed.

---

## Known limitations of the system

Measured on the sealed test set, `data/test`, scored once at the pre-committed threshold.
Full numbers in `notes/phase-7-report.md`; raw output in `notes/measurements/`.

### It cannot reconcile a netted refund, at all

**`refund_netted`: 0 auto-matched of 200. Wilson upper bound 1.88%.**

This is the sharpest limit the system has, and the one a merchant is most likely to hit,
because netting a refund against the same payout is ordinary gateway behaviour rather than
an edge case. It is 4% of the modelled distribution.

**Zero events in 200 is a decisive result, not an uncertain one**, and the instinct to treat
a small denominator as uninformative is wrong here in a way worth spelling out. The interval
is *narrow* -- 1.88 points wide -- precisely because nothing happened. `data/scale` produced
0 of 1,000 on the same case type before the seal was broken, so this is two independent
measurements at 5x different volumes agreeing exactly. No larger sample changes the reading.

**The mechanism, which is the useful part.** Set beside the other held-out type, the two
results say something more precise than "it does not generalise":

| unseen case type | matched | what the arithmetic looks like |
|---|---|---|
| `tds_deducted` | 68.00% | credit = invoice x (1 - fixed rate) |
| `refund_netted` | 0.00% | credit = invoice - an unrelated refund amount |

The blocking `rate_amount` pass retrieves any credit differing from an invoice by a *rate*,
and TDS is a rate. It was never told about TDS specifically and finds it anyway. A netted
refund is not a rate: the shortfall is an arbitrary amount belonging to a different
transaction, so no rate-based pass retrieves the pair and no candidate is ever generated for
the classifier to score. **The failure is at blocking, not at the model.** The model cannot
rank a candidate that does not exist.

So the honest statement of the generalisation limit:

> It generalises to an unseen deduction expressible as a rate on the amount, and fails
> completely on an unseen netting structure, where no rate links the invoice to the credit.

That is a claim about *which* unseen things transfer, and it is falsifiable. It also names
the fix -- a subset-sum pass over recent refunds against the shortfall, which
`core/subsetsum.py` already has the machinery for -- and that fix is deliberately **not
made**, because building it after reading the test set is the tuning the seal exists to
prevent.

### `tds_deducted` is not a success story, it is an unresolved one

68.00%, 95% CI [61.98%, 73.47%]. The interval covers `rounding_drift` (64.00%),
`partial_payment` (65.00%) and `clean` (70.70%) -- three case types the model trained on.
**250 rows cannot distinguish this from ordinary performance in either direction.** The
defensible claim is that it is indistinguishable at this sample size, not that the model
generalises well. Reporting it as a success would be reading a point estimate off an
interval that does not support one.

### Batched settlements are where coverage is actually lost

`batched_settlement` is the weakest seen case type at **34.72%** (208 of 599) and the single
largest source of missed links. It is not a new finding -- `data/train` gave 106 of 659 --
but the sealed run states it most clearly: 599 truth rows produce **391 of the 1,836
exceptions, 21.30%**, second only to `clean`, which is 4.6x larger. A payout batch covering several invoices has to be split before any one
invoice can be matched, and subset-sum over the batch is doing that work imperfectly.

### Precision is measured at two batch sizes and they disagree

| batch | settlements | precision | false matches |
|---|---|---|---|
| `data/test` (sealed) | 4,950 | **99.9037%** | 3 in 3,114 |
| `data/scale` | 24,750 | **99.2369%** | 115 in 15,071 |

Same threshold, same model, same code. One clears the 99.5% floor and one does not.
**Neither number is quotable alone.** The plausible mechanism is invoice contention rising
with batch size -- more settlements competing for the same invoices means more chances for
the wrong one to claim first -- and *that mechanism is untested*. What is not a hypothesis:
the only figure this project has at production-like volume is the one below the floor.

### An abstention that is not free

**`resolve()` consumes invoices before the operating point is applied, so a candidate the
system does not trust enough to act on can still deny the invoice to the settlement that
owns it.**

Found by chasing the deferred invoice-inference defect above and discovering that three of
its five cases had a different cause. Measured on the sealed set: of 5 settlements wrongly
told `INVOICE_ALREADY_CLAIMED`, **three had their invoice consumed by a candidate scoring
0.945205 -- below the 0.9564 threshold, and therefore never auto-matched at all.**

```
INV-2026-002746  consumed by pay_000000002383  p=0.945205  not matched  owner setl_000000002315
INV-2026-003234  consumed by pay_000000001869  p=0.945205  not matched  owner setl_000000002803
INV-2026-003464  consumed by pay_000000002941  p=0.945205  not matched  owner setl_000000003033
```

The sequence in `model/predict.py:reconcile_batch` is `resolve(scored)` and *then* filter by
threshold. Resolution is greedy over every scored candidate regardless of confidence, so the
invoice is gone by the time the operating point rejects the claimant. Both settlements end
up as exceptions: one for being below threshold, the other for an invoice "already claimed"
by a settlement the system itself declined to act on.

**This is the abstention argument failing in a direction it did not anticipate.** The design
argues that declining is cheap -- a miss costs a human thirty seconds, a false match posts
wrong money. That holds for the settlement doing the declining. It does not hold for the one
whose invoice was taken on the way to the decline, and nothing in the risk-coverage framing
accounts for that cost. All three sit at an identical calibrated probability, which is the
isotonic step function's coarseness surfacing in a third place.

**Not fixed, and the reason is not an oversight.** Two things make fixing it here the wrong
move, and both are worth stating so that "not fixed" does not read as "not noticed":

1.  Applying the threshold before resolution, or releasing invoices held by below-threshold
    candidates, would change every number in `notes/phase-7-report.md`. The report would
    then describe a system that no longer exists.
2.  **A fix validated against the batch that exposed it is a fix tuned to that batch.**
    Three cases on one dataset is enough to establish that the defect is real and nowhere
    near enough to establish that a given reordering improves anything. Measuring the
    remedy on `data/test` would make its apparent benefit a property of the rows that
    revealed the problem.

So it is logged with its measurement and belongs against a fresh batch, after submission.
The sequence that keeps it honest is: generate a new batch, reproduce the defect on it,
change `resolve()`, and re-measure there -- not here.

### The other two are the false matches, seen from the other side

The remaining two of the five are `INV-2026-003290` and `INV-2026-003495` -- both invoices
that appear among the three false matches. **A false match and a spurious
`INVOICE_ALREADY_CLAIMED` are one event counted twice**: one settlement takes an invoice it
does not own, and the rightful owner is then sent to chase a duplicate that does not exist.
The wrong money and the wasted operator time are the same defect billed to two different
people, which is worth knowing before treating them as independent line items.

**Concretely: the false-match count and the exception count are not independent, and
anywhere both appear they should not be added or reasoned about separately.** On the sealed
set, 2 of the 3 false matches each also generate one of the 1,836 exceptions. Improving
resolution would reduce both at once, and a cost model that treats "3 false matches" and
"1,836 exceptions to review" as separate quantities double-counts the overlap. It is small
here -- 2 rows -- and the structure, not the size, is the reason to state it.

### Confidence 1.0 does not mean safe

Two of the three false matches scored **exactly p = 1.000000**. Isotonic regression's top
step is saturated, so the highest calibrated probability the system can emit is shared by a
large population containing a few wrong answers. Calibration is a statement about a bucket,
not about a row, and the bucket at 1.0 is 98.05% correct rather than 100%. No confidence
value this system can produce licenses skipping review of an individual high-value match.

### Calibration degrades only where the model is out of its distribution

ECE 0.010436 on the training eval split against **0.012031** on the sealed set. Removing the
two unseen case types gives **0.007497** -- better than the split it is being compared
against -- while those types alone give **0.113699**. They are 5.60% of candidates and
concentrate in the middle bins, where the calibrator has least data. The two bins holding
98% of candidates carry gaps of 0.0076 and 0.0109.

**Where the system operates it is calibrated; where it is out of distribution it is not, and
it cannot tell which it is in.** That last clause is the limitation. Nothing in the output
distinguishes a confident score on a familiar case type from a confident score on an
unfamiliar one.

---

## Where the regex-dominance finding does not hold

`README.md` states a theorem: if the provenance gate's verification can be run in reverse
to generate the value, the model was never needed for that field. On this data it holds
completely — regex 71/71 against the model's 48/71 on UTR, and zero of 4,528 narrations in
`data/train` carry a UTR needing more than a plain regex.

**That last clause is the boundary, and it is a property of our generator.**

Real bank narrations carry UTRs that a plain regex misses: digits split across a line
wrap, grouped in fours, broken by an inserted space from a fixed-width export. A language
model reads those correctly and a `\d{12}` does not.

Our generator does not produce them, so the measurement above cannot see the case where
the model would win.

**And there is a second-order point that is more interesting than the first.** If such a
narration arrived today, the model would extract the UTR correctly and **the provenance
gate would reject it anyway** — because the gate requires a whole 12-digit run in the
source text, and a UTR written `3000 0000 4412` is not one. The gate and the field would
be working against each other: the model does the thing only it can do, and the check
throws the answer away.

So the honest statement of the finding is narrower than the headline:

> On data where every identifier is regex-extractable, the model is strictly dominated on
> identifier fields. On data where it is not, our gate would currently discard the model's
> advantage, and the remedy would be to loosen how the identifier is *compared* —
> normalising separators on both sides before matching — not to loosen whether a failure
> blocks.

That change is not made now, because there is nothing in this dataset to justify it and an
unexercised code path is a liability. It is written down so the decision is visible rather
than absent, and so a reviewer who raises it finds it already answered.

---

## Closed at Phase 7: the decoder stall

**Status: CLOSED at Phase 7, measured across 168 calls. The estimate was 7.6x too high,
and a different failure appeared that the spike never produced.**

```
estimated from ~30 calls        ~1 in 5-6  = 17-20%
measured over 168 calls          4 in 168  =  2.38%     overstated 7.6x
stalls surviving both attempts          0              <- the thing that had not happened
schema failure rate                  0.0%
```

Both Phase 7 risks named below are answered. The rate did **not** rise under sustained
load, it fell -- the small sample was the problem, not the load. And no stall survived both
attempts, so no exception carries a cause we cannot explain.

**The mitigation stays unadopted.** A per-item token budget was held in reserve for "if the
Phase 7 rate is bad". It was not bad, so adopting it now would be carrying a code path that
exists for a rate measured at a seventh of the estimate that motivated it.

**What appeared instead was `LLM_BATCH_MISMATCH`, on 4 items of 3,347 (0.120%).** The
envelope check that catches it was kept despite 120/120 holding in the spike, on the
grounds that the cost of checking was a set comparison. At scale it fired. The general
point is the one worth keeping: a check whose spike result is *perfect* is the check most
tempting to remove, and 120 observations cannot distinguish 0% from 0.1%.

**The original Phase 5 write-up follows, unedited, because a resolved risk is only
instructive if what was believed at the time is still legible.**

*Status at the end of Phase 5: understood in mechanism, not in cause. Not closed.*

**What is known.** Under `json_schema` strict decoding, a call can stop producing content
and emit whitespace until its token budget is gone. JSON grammar permits arbitrary
whitespace between tokens, so the grammar is never violated and the decoder never
advances. Measured directly: 23,973 characters returned, 23,780 of them whitespace, the
content ending mid-object.

**What was fixed.** One reproducible trigger — an array field, `list[str]` — stalled a
particular batch 5 times out of 5, always at the same key, always at an identical byte
count. Removing the array took that batch to 0 stalls in 5. That field is gone for
unrelated reasons anyway.

**What is not fixed, and is the open risk.** Stalls still occur on other batches with no
array in the schema at all, at roughly **1 call in 5 or 6**, non-deterministically. The
single retry has rescued **every one observed so far** — but "so far" is a few dozen calls,
which is not a sample that supports a claim.

**The specific Phase 7 risk.** A scale run is on the order of 600 calls rather than 30.
Two things could happen that have not yet:

1. The stall rate could be higher under sustained load than in short runs.
2. A stall could survive **both** attempts — original and retry. That produces an
   exception whose reason code says `LLM_MALFORMED_RESPONSE` with detail
   `decoder stalled`, which is honest, but the underlying cause is one we do not fully
   understand. An exception we cannot explain is worse than one we can.

**Instrumentation in place for it.** `Usage.call_log` records `stalled` and `raw_chars` on
every call, not only on failures, because a stall a retry rescues leaves no exception
behind and its only other trace is the token bill. So the Phase 7 run will report the true
stall rate whether or not any of them fail.

**Mitigations considered and not taken.** A per-item token budget would cap what a stall
costs — a batch of 20 at ~120 tokens each needs ~2,400, not the 5,000 currently allowed.
That is a reasonable safety net and it is deliberately not being adopted as *the fix*,
because it treats the cost of the symptom and would let the cause go uninvestigated. It
remains available if the Phase 7 rate is bad.

---

## Proxies drift from the thing they proxy

Three times now, a probe and a real measurement have disagreed. That is no longer a run of
bad luck; it is a pattern with a cause, and the cause is worth naming because the fix is
always the same and is always available earlier than it was taken.

| what was estimated | how | estimate | measured | gap |
|---|---|---|---|---|
| exceptions reachable without an LLM | proportions from a probe over case types | **43%** | **51.06%** | +8 points |
| schema failure rate under batching | 50 single calls | 0.0% | 20% of batches, then 0% after a schema change | qualitative |
| run time for a 25,000-row batch | 36 rpm assumed saturated | 8.7 min | 11 min measured, 101 min as actually built | 12x |

**The 43% is the clearest case.** It came from taking the case-type distribution and
reasoning about which types would produce which reason codes. It was wrong for a reason
that could not have been spotted from inside the estimate: two of the deterministic codes
did not exist yet. `INVOICE_ALREADY_CLAIMED` accounts for 102 of the 2,448 exceptions on
`data/train` and was invented only when the enumeration was built, because it is a
structural refusal — an invoice is paid once — that nobody thinks to enumerate in the
abstract. Without it the share would have been 46.9%; the rest of the gap is the
difference between reasoning about proportions and counting objects.

**The general form.** A proxy is a model of the thing, and a model omits whatever its
author did not think of. That is precisely the class of error a proxy cannot reveal,
because the omission is invisible from inside it. Constructing the object is not a more
careful version of estimating — it is a different operation, and it is the only one that
can surface a category nobody had in mind.

**The rule this build now follows:** when a number is load-bearing, build the object it
counts before designing against it. The 43% drove the batch size, the rate limiter and the
run-time claim, and all three were designed against a figure that had never existed as a
set of rows.

**Direction matters, though it does not excuse the method.** All three estimates were wrong
in the safe direction — fewer exceptions reach the model than designed for, and the run is
slower than claimed rather than faster. Being over-provisioned is the right way to be
wrong. It is still being wrong, and the next proxy has no obligation to fail politely.

---

## An anchored metric: reason-code agreement

**Reported as 100 out of 100 in the first end-to-end run. It means almost nothing, and the
reason is a flaw in how the measurement was designed rather than in the result.**

The `reason` prompt hands the model the pipeline's own conclusion:

```
system_reason_code: {reason_code}
```

and then asks it to tag the exception. The prompt even says *"The row tells you which of
these the system used. Agreeing with it is normal."* So a 100% agreement rate measures
whether the model can copy a field, which it can.

**Why the field is in the prompt at all.** The model is being asked to *explain* a decision
already taken, and an explanation that does not know what it is explaining is worse than
useless. Withholding the code would improve the metric and degrade the product.

**So the metric has to change, not the prompt.** Two options were open, and **option 1 was
taken and is now measured on held-out data** -- `evals/reasons.py` scores every exception's
code against the answer key, and the sealed test set gives **97.55% actionability, 95% CI
[96.74%, 98.16%], 1,791 justified against 45 unjustified in 1,836 exceptions**. On
`data/train` the same measure gave 97.92%, so it held out of sample.

The per-code breakdown is where it earns its keep, because the aggregate hides the one bad
code:

| code | total | justified | unjustified | actionability |
|---|---|---|---|---|
| `NO_CANDIDATE` | 315 | 315 | 0 | 100.00% |
| `NO_INVOICE_LINK` | 859 | 858 | 1 | 99.88% |
| `BELOW_THRESHOLD` | 525 | 511 | 14 | 97.33% |
| `INVOICE_ALREADY_CLAIMED` | 107 | 99 | 8 | 92.52% |
| **`AMBIGUOUS_CANDIDATES`** | **30** | **8** | **22** | **26.67%** |

`AMBIGUOUS_CANDIDATES` is unactionable roughly three times in four, out of sample as it was
in training (21.43% there). It is the smallest code by volume and by far the worst by
quality: it sends an operator to choose between candidates that mostly cannot contain the
answer, because blocking never retrieved the true credit. **That is a blocking-recall
number wearing a reason code**, and the remedy is candidate generation rather than a
different label -- which is exactly the distinction `evals/reasons.py` was built to make
visible, working as intended.

The two options as they were written, for the record:

1. **Score against truth, not against ourselves.** `evals/` can see the answer key: for
   each exception, does the assigned code correctly describe why the settlement was not
   matched? A `NO_CANDIDATE` on a settlement that truth says had a real link is a
   *correct* code describing a real miss; a `NO_CANDIDATE` on an orphan is correct in a
   different way. That is a real accuracy measure and it needs no model agreement at all.
2. **A held-out arm.** Run a sample with `system_reason_code` withheld and measure
   agreement there. That is informative, and it costs a second prompt version to maintain.

Option 1 is the one BUILD.md actually asks for (*"`ledgerloop eval` reports reason-code
accuracy"*), and reason-code accuracy against truth is a different and better number than
agreement with ourselves. Option 2 was not built: a second prompt version to maintain, to
measure something option 1 already answers better.

**The general shape, which is the reusable part:** a metric that compares two things where
one was told the other's answer is not measuring agreement, it is measuring transcription.
Worth checking, for any agreement metric, what the second opinion had access to.

---

## An audit record that contained a false statement

**The most serious defect found in Phase 5, and it would have shipped.**

`NO_CANDIDATE` carries this text, written into every audit record that uses it:

> no bank credit resembled this payout on any blocking pass

It was being applied to settlements for which blocking had produced several candidates.
The enumeration built its evidence only from candidates a *rule tier had scored*, so a
settlement that blocking found five credits for and every tier declined arrived at the
classifier with no evidence row at all — indistinguishable from one blocking never saw.
On `data/demo`, 15 settlements carried the code and only 8 deserved it.

**Why this is worse than an ordinary bug.** A wrong number in a metric gets checked against
something eventually. A false sentence in an audit record does not, because the record
*is* the check. Someone investigating an unmatched payout would read "no bank credit
resembled this payout", conclude the money never arrived, and go looking at the payment
gateway — when in fact five credits resembled it and the matcher rejected all five, which
is a completely different investigation with a completely different fix.

**An audit trail containing a false statement is worse than no audit trail, because it is
trusted.** No audit trail makes someone go and look. A confident wrong one sends them
somewhere else.

**The fix.** Evidence is now built from every blocking candidate, with a zero score where
no rule fired. A settlement with candidates and no rule hit is `LOW_CONFIDENCE` — "scored
zero on rule tiers", which is true — and `NO_CANDIDATE` now means what it says.

**What let it in.** The invariant tests were passing throughout: every settlement had
exactly one code, no gaps, no double counts. The invariant checks that *a* code was
assigned, and cannot check that the *right* one was. Completeness and correctness are
different properties and the first is much easier to test, which is exactly why it is the
one that gets tested.

**The general form:** for any generated text that will be read as fact, ask what a reader
would *do* on the strength of it. `NO_CANDIDATE` and `LOW_CONFIDENCE` send an investigator
to two different places, so the difference between them is not a labelling nicety — it is
the entire value of the record. Every reason code's text has been re-read against that
question.

---

## A question with no possible bad answer is not a metric

The same error, made twice in Phase 5 in different clothes. Worth stating once as a class.

**First: reason-code agreement.** The `reason` prompt hands the model
`system_reason_code` and the metric scored whether the model's tag matched it. 100 out of
100. It measured transcription, because one party had been told the other's answer.

**Second: reason-code "accuracy" as first designed.** The intended check was *"did the
system pick the right code given what it knew?"* Every code would have scored correct, and
not by luck — each code is a *true statement about our own computation*. `BELOW_THRESHOLD`
says the best candidate scored below the threshold, and it always did. `NO_CANDIDATE` says
no candidate survived, and none had. The question was unfalsifiable by construction.

**What made the second one a real metric** was changing the question from one about our
internal state to one about a consequence in the world:

> Does this code send the operator to the right place?

That can fail, and it did: `AMBIGUOUS_CANDIDATES` scores 21% actionable, and
`INVOICE_ALREADY_CLAIMED` 87%, surfacing a resolution defect that nothing else had seen.

**The diagnostic to apply to any proposed metric:** *what result would count as bad, and
could it actually occur?* If the answer is "none" or "not really", the metric is a
formality. Two symptoms mark them out:

*   **the mirror** — comparing two things where one was told the other's answer;
*   **the tautology** — asking whether a system did what it did, phrased as a question
    about correctness.

Both feel like measurement, produce a number, and go in a report. Neither can be failed.

---

## Resolution by list order, and the two smaller defects behind it

**Found by the reason-code actionability metric; `INVOICE_ALREADY_CLAIMED` scored 87.3%.**
Thirteen settlements were told their invoice belonged elsewhere when truth says it was
theirs.

### The cause: ties, and there are almost nothing but ties

```
candidates sharing an exact calibrated probability   7,283 of 7,305   99.7%
contested invoices decided by an exact tie              23 of   449    5.1%
resolve() stable under input reordering                          False
```

Isotonic calibration is a step function. It gives excellent calibration and coarse
discrimination, so "sort by probability" leaves almost every contest undecided, and
`resolve()` settled them by whichever candidate came first in the list. **Ten of the
thirteen were exact ties.** Reproducible, because candidate enumeration is deterministic --
and not principled, which is the property that matters. "Why did this settlement get the
invoice and not that one?" had no answer better than "it came first."

**Fixed** by breaking ties on evidence -- date proximity, then rule tier, then
invoice-link strength, with the ids as a deterministic backstop -- and recording the
deciding rule on the winner. Stability under input reordering is now asserted in a test.

### What the fix was worth, stated plainly

| | before | after |
|---|---|---|
| matched | 2,497 | **2,497** |
| coverage | 50.4954% | **50.4954%** |
| `INVOICE_ALREADY_CLAIMED` actionable | 87.25% | 88.24% |
| overall actionability | 97.88% | 97.92% |

**One row of thirteen.** Coverage is byte-identical, and it was always going to be: a
tiebreak *reallocates* a contested invoice, it does not create one. Both parties to a tie
share a probability, so both clear the threshold or neither does.

The reason to fix it was never the number. It was that a financial control should not
decide who gets paid by list order, and that the sealed test set in Phase 7 would otherwise
have been measured with a coin flip inside it -- leaving the most credible figure in the
submission partly a function of enumeration order.

Three of the surviving matches were decided by a tiebreak, and one of those fell all the
way through to the id backstop. Its audit record says so: *"resolved on entity id"*, which
is visibly the weakest available answer and is meant to look like one.

### The two smaller defects, logged and not chased

**One genuine model-ranking error.** In one of the thirteen, the settlement that took the
invoice scored *strictly higher* -- the model preferred the wrong candidate on evidence.
Not a resolution bug; a discrimination one, and the same root cause as the section above.

**Two invoice-inference errors.** In two cases the settlement's best candidate carried a
*different* invoice, which genuinely belonged to the settlement that claimed it. The reason
code is technically true and still unactionable: it sends an operator to check a duplicate
that is not one, when the real fault is that `core/invoices.py` inferred the wrong invoice
through the narration. Left for Phase 7, sized at two rows in 4,945.

**Chased at Phase 7, and it was not what the note assumed.** Out of sample the count is
**5 in 4,950 settlements, 0.1010%, 95% CI [0.0432%, 0.2363%]** -- and in three of the five
the cause is not invoice inference at all. See *An abstention that is not free* below.
Sizing a defect before finding its cause put the wrong name on it. The number was roughly
right and the diagnosis was wrong, which is the more expensive half to get wrong.

**This is the fifth instance of the pattern in *The same error, four times* above, and the
first in an estimate rather than an error string.** The four recorded there were messages
that named the wrong investigation. This was a defect note that named the wrong cause while
sizing it almost correctly -- two rows against a measured five. The sizing being roughly
right is what made it safe to leave alone, and the diagnosis being wrong is what would have
sent the fix into `core/invoices.py`, where three of the five cases have nothing to fix.

The generalisation is worth carrying: **a number that happens to be right can conceal a
wrong explanation, and the explanation is the half that gets acted on.** A wrong number
eventually fails a check. A wrong cause attached to a right number is never checked at all,
because the number keeps agreeing.

---

# How do you know your numbers are real?

The honest answer, and the one worth giving to a panel:

> **Every metric answers exactly one question. The failures in this project have
> consistently come from the questions no metric was asking.**

Not one of the incidents below was a broken instrument. Every one was a correct instrument
answering its question correctly while something moved along an axis it did not measure.
That is a harder failure to catch than a bug, because there is nothing to notice: the
dashboard is green and it is telling the truth.

| the metric | answered correctly | while this moved, unwatched |
|---|---|---|
| ECE on a single split | how well calibrated, in sample | out-of-sample calibration -- 0.00000 vs 0.0104 |
| spike schema-failure rate | conformance *and* transport, merged | which of the two was failing -- 8% was the rate limiter |
| batch contamination check | whether raw strings matched | whether a *normalised* value matched -- a real cross-contamination filed as harmless |
| provenance failure rate | of the claims made, how many were true | how many claims were made -- 0.0% failures while a third of UTRs vanished |
| reason-code agreement | whether two parties said the same thing | whether either was right; one had been told the other's answer |
| ECE and reliability diagrams | whether probabilities mean what they say | whether they can *separate* two candidates -- 99.7% are exact ties |

The last one is the sharpest. Isotonic was chosen on measured ECE and it is the better
calibrator; **calibration and discrimination are different properties**, we optimised hard
for the first, and the second was invisible until a resolution defect was traced backwards
into it.

## What follows from this

**A metric is a question, so ask what question it is not asking.** Every rate here now has
a deliberate counterpart:

| quality | coverage |
|---|---|
| precision | coverage, and the whole risk-coverage curve |
| provenance failure rate | claim rate, per field |
| calibration (ECE) | discrimination -- *named as the open gap, not yet measured* |
| reason-code actionability | scored against truth, per code, never aggregated alone |

**The three diagnostics, applied before a number is believed:**

1. *Is this reading physically possible?* — 8,000 tokens producing 193 characters was not,
   and two experiments were spent because nobody asked.
2. *What result would count as bad, and could it actually occur?* — a question with no
   possible bad answer is a formality, not a measurement.
3. *What moved that this cannot see?* — the one that catches everything in the table above.

**And a structural habit:** normalisation at a boundary destroys the evidence you need when
the thing behind that boundary misbehaves, so keep the raw value alongside the clean one.

None of this makes the numbers in this README certain. It makes the *uncertainty* the
subject of measurement rather than a matter of confidence, which is the only version of
the claim worth making.

---

## A failing test is a system. Reading a docstring isn't.

Three claims *about our own code* have been wrong in the reassuring direction:

| the claim | how wrong | how it was caught |
|---|---|---|
| "the provenance gate makes injection structurally hard" | backwards -- the narration is where injected text lives | thinking about the adversary's actual move |
| "`NO_CANDIDATE`: no bank credit resembled this payout" | written for settlements with five candidates | a truth-scored metric |
| "`resolve_indices` mirrors `model/predict.py` exactly" | unstable sort against a stable one; worth 17 points of coverage | reading the docstring next to the code |

The first two were caught by measurement. The third was caught by someone happening to read
a comment carefully on the right afternoon.

**That is not a system.** A property test that runs both implementations over randomised
inputs and fails when they disagree is. It now exists
(`tests/test_model.py::test_the_training_resolver_and_the_inference_resolver_agree`), and it
is built with deliberately repeated probabilities -- three distinct values across sixty
candidates -- because the real distribution has 99.7% of candidates tied, and a random test
using distinct floats would never exercise the path that matters.

That last point is the recurring one. Three tests in this project were nearly useless until
they were built to be capable of failing:

*   the **exclusion test**, which needed a vacuity guard or it would have passed against an
    empty package;
*   **experiment A**, which needed a control arm, because a non-deterministic failure can
    produce a clean removal arm by luck;
*   **this one**, which needed tied inputs, because ties are the path being tested.

> **A test that cannot fail is documentation with a green tick.**

---

## Guards that passed for the wrong reason

**A distinct category from the error messages above, and a worse one.** Those instruments
*reported* wrongly, so anyone who read the output had a chance to notice. These behaved
exactly as intended while being broken, and produced the correct outcome for five phases
by coincidence. **A guard that passes for the wrong reason is invisible until the reason
changes**, and the reason is usually something nobody is watching.

Both surfaced at Phase 7, neither by being looked for. Two further defects are recorded
with them: one of a different kind, found in the same pass, which makes the same point
about small discrepancies being expensive out of proportion to their size — and one at the
end that is a guard of the same family and is worse than either, for a reason worth
isolating.

### `.gitignore`: every exception was inert

```
/runs/                 <- excludes the DIRECTORY
!/runs/v1-train/       <- inert; git does not descend into an excluded directory
!/runs/v1-train/**     <- inert
```

`runs/v1-train/` and `runs/_models/` are in the repository, the deployed image seeds from
them, and the arrangement worked. It worked because those files had been added to the index
*before* the ignore rule existed, and a tracked file is not affected by `.gitignore`. The
exception never did anything. The comment beside it explained a mechanism that was not
operating.

It surfaced only when a *new* directory needed the same treatment: `runs/v1-test/` was
refused by `git add`, and the pattern that had "worked" three times failed the first time
it was actually asked to work. `/runs/*` excludes the contents rather than the directory,
which git can re-include from, and all three exceptions now work for the reason the comment
gives.

**The tell, in hindsight:** the rule had never been exercised. Every path it supposedly
protected predated it. The same shape as the three deployment findings in the README — a
path that stopped being exercised is a path that has stopped being checked, whether or not
anything about it changed.

### A reproducibility test that was partly asserting on the clock

`test_two_concurrent_runs_report_identically` runs the same job twice and compares the
reports. It strips `wall_seconds` and `achieved_rpm` before comparing, because those
obviously differ. It did not strip `usage.seconds`, which is accumulated wall-clock time
rounded to two decimals — so a scheduling difference of five milliseconds failed the test.

It failed intermittently, on a machine doing other work, in a suite otherwise green. **The
figure reported at the end of several phases — "507 passing" — was therefore partly luck**,
and reporting it as a clean result was reporting an outcome without knowing why it was
clean.

The fix strips timing fields at every depth. The important half is the second test added
beside it: **a strip that removes every differing field passes unconditionally**, which is
the failure mode of the fix itself, so there is now a test asserting the comparison still
notices a real difference. That guard immediately earned itself — it caught a wrong
assumption in the fix, that `call_log` was in the compared payload when it is not.

### A third, smaller, and worth recording for the size of the gap rather than its cause

`api/service.dashboard` chose which operating point the screen opens on by taking the curve
point whose threshold was numerically *nearest* the one the run was scored at. On the
held-out run the stored threshold `0.9564` sits 0.000268 below a point at `0.956132` and
0.009323 below the point at `0.965723`. Nearest picks the first — which admits one candidate
the operating point excludes.

The screen would have shown **62.93% and 3,115 matched**, against a README saying **62.91%
and 3,114**.

**One row, in the last digit, and it mattered more than its size suggests.** A reader who
sees 62.93% on a live page and 62.91% in a document does not conclude they are two
measurements of different things. They conclude somebody was careless, and then they have
no way to tell which of the other numbers to trust — every figure in the submission
inherits the doubt raised by the cheapest-looking one. A large discrepancy invites the
question *"what is different about these two?"* and gets an answer. A last-digit
discrepancy invites the question *"is any of this checked?"* and does not.

That is why this was worth fixing properly rather than rounding away: the correct point is
the lowest threshold at or above the operating point, which by construction selects the
identical set, and there is now a test asserting the screen and the run agree on the count.

---

### What the pair has in common

Neither was found by inspection, and neither could have been. A guard that is passing looks
identical to a guard that is working, and nothing distinguishes them until something asks
the guard to do its job. Both were exposed by *change* — a new directory, a busier machine.

The practical form: **when a check has never failed, ask what would make it fail, and
whether that has ever happened.** A green result from a check that has never been exercised
is not evidence, and the count of passing tests is not either.

### The third guard is worse than the pair, and the difference is the whole point

Found while reading the README end to end, in a sentence promising a measurement that had
never been taken. The README said the provenance gate would be re-measured on the sealed
test set. A number was available and looked like exactly the right one:

```json
"provenance": {
  "items": 3343,
  "items_failed": 0,
  "item_failure_rate": 0.0,
  "fields_checked": 0
}
```

**3,343 items. Zero failures. Zero fields checked.** The only LLM job that ran against the
sealed set writes exception reasons; it extracts nothing, so the gate had nothing to
verify. Writing *"re-measured on the sealed test set, 0% provenance failure over 3,343
items"* would have been **literally true and entirely empty** — and the reason it is
dangerous is that the number looks like precisely what anyone would hope to find. A
perfect record, at five times the volume of the original measurement, from a job that
extracts nothing.

**Why this is worse than the two above, and not merely another instance.** Those two failed
the moment they were finally asked to work: a new directory made `.gitignore` refuse an
add, a busier machine made the reproducibility test go red. Change exposed them, because
in both cases there was a real job the guard would eventually be handed and visibly fail.

There is no such moment here. **A job with no fields to check will report zero failures
over any volume, forever.** No future run makes `fields_checked: 0` fail; running it on ten
times the data produces a rate ten times as reassuring and exactly as meaningless. The pair
were latent — waiting for a trigger. This one is permanent, and nothing about the system
will ever surface it.

**And there is nothing to fix.** The instrument is correct: a job that extracts no fields
*should* record no failures. `item_failure_rate: 0.0` is a true statement about the reason
job. The defect is entirely in the reading — which puts this with the general finding
below rather than with the broken guards above, and is why it went into the README as a
stated absence rather than being quietly dropped.

**The tell was in the record the whole time.** Both numbers are there: `items: 3343` and
`fields_checked: 0`, four lines apart. The instrument reported its own denominator
faithfully. Only one of the two was ever going to be read, because a rate is what someone
came looking for.

> **A rate is not a result until its denominator is quoted beside it, and a denominator of
> zero is not the absence of failures — it is the absence of a result.** Rates are the
> shape a number takes when it is about to be trusted, which is exactly when the count
> underneath it stops being looked at.

---

## The evidence was on the page, and the question decided what could be seen

**This is the most general finding in the project, and the only one with nothing to
blame.** Every other entry in this document has a culprit: an instrument that described
itself inaccurately, a metric that measured the wrong axis, a guard that was not
guarding. Here the instrument was correct, the number was correct, the annotation was
correct, the analysis was careful — and it was still incomplete.

`notes/threshold.md` contains this table, written during operating-point selection:

```
value      on step   cum n   coverage   precision   false
0.992754      196     803     67.99%    99.5019%       4   <- THE STEP
0.956431        1     804     68.08%    99.5025%       4   <- selected
0.945205       98     903     76.46%    99.3355%       6   <- breaches the floor
```

The `0.945205` row is annotated *"breaches the floor"*. It is the same step that, on the
sealed set, consumes invoices below the operating point and denies them to the settlements
that own them. The defect was sitting in a table that was studied closely, annotated by
hand, and reasoned about at length — and it was invisible, because **the question being
asked of that table was only ever "what does coverage do here?"**

Under that question, `0.945205` is a row you decline to cross. Under the question "what
happens to the candidates on this step when we decline to cross it?", it is a defect. The
data supported both readings the whole time. Only one was asked.

**Why this is worse than the four error messages above, and more useful.** Those were
instruments that *described themselves inaccurately*; the fix each time was to classify
rather than generalise, and a reader who checks the instrument finds the error. Here the
instrument was correct, the number was correct, the annotation was correct, and the
analysis was still incomplete — because completeness is relative to a question, and the
question is the one thing a table cannot record.

There is no mechanism that fixes this, which is why it is written down rather than
converted into a test. The nearest thing to a remedy is a habit: **when a row is excluded
from an analysis, ask what happens to the things it contains, not only to the metric it
would have moved.** An abstention has a denominator too.

---

## When a number can be derived or counted, count it

Three instances now, and they are the same mistake wearing different clothes. Each time a
quantity was computed from another quantity by an argument that was *nearly* right, and the
gap between nearly and exactly was where the bug lived.

| what was derived | from what | how it was wrong |
|---|---|---|
| the deterministic share of exceptions | case-type proportions, reasoned about | **43% estimated, 51.06% counted.** Missed `INVOICE_ALREADY_CLAIMED` entirely, because that code did not exist when the estimate was made |
| settlements matched at a threshold | the curve's `n_predicted` | `n_predicted` counts **triples**, and several rows of one payout batch produce distinct triples sharing a `settlement_id`. Wrong by a handful of rows |
| LLM-bound exceptions at a threshold | total exceptions minus a fixed deterministic count | assumed the deterministic codes are threshold-independent. `INVOICE_ALREADY_CLAIMED` is not: it is assigned from a settlement's **best** candidate, and a settlement whose best candidate lost its invoice can still be accepted through its second choice |

Each derivation rested on a claim about how something behaves — which reason codes are
stable under a threshold, what a count is counting. Claims like that are exactly the ones
nobody re-examines, because they sound like definitions rather than assertions.

**The rule:** when a number can be derived or counted, count it. Deriving is a claim;
counting is an observation, and only one of them can be wrong in a way the arithmetic
hides.

**The related habit that actually fixed the third one:** replace an assumption about
behaviour with a *structural* fact. Not "deterministic codes do not depend on the
threshold" — which is almost true and therefore dangerous — but "**a settlement with no
accepted candidate cannot be matched at any threshold**", which follows from what
resolution does and needs no claim about codes at all. The first is a generalisation about
a system; the second is a property of it.

Every one of the three was found by an implausible reading rather than by review: a share
that jumped 8 points, a cost of ₹0.00, a row count off by five. That is the same
diagnostic as everywhere else in this file — *is this reading physically possible?*

---

## The path you exercise is the only one that works

**A cold `docker compose up` from a fresh clone did not build at all.** Not "started with an
empty dashboard" — the build failed on its first step. This was the primary run path
BUILD.md promises and the one a reviewer would try first.

Three separate defects, all present at once:

| defect | cause |
|---|---|
| the web container could not build | `web/server.js` was added to `.dockerignore` for the hosted image |
| the dashboard would have been empty | `docker/api.Dockerfile` copied no `runs/`, `data/` or `web/static/` |
| any model load would raise `OSError` | `docker/api.Dockerfile` installed no `libgomp1` |

Every one of them is a fix that *was* made — to the hosted image — and not carried across.
The hosted path was exercised several times a day while building against a live URL. The
compose path had not been run end to end since Phase 0.

**Nothing had broken. The two paths had simply stopped being the same thing**, one change at
a time, each of them correct in isolation.

### The fix is not more discipline

The obvious response is to remember to update both Dockerfiles. That is the response that
produced this, because the drift did not come from carelessness — each change was right for
the file it touched, and there was no moment at which someone chose not to update the other.

So the duplication is gone instead. One `Dockerfile`, built by both `docker compose` and
Railway; the API serves the static frontend, which removes the third container entirely;
compose overrides the entrypoint to run migrations first and nothing else differs. **Local
and hosted are now the same artifact running the same code**, and drift has nowhere to live.

### The same shape, for the third time

| what claimed to match | what actually diverged | how it was caught | could it have been merged? |
|---|---|---|---|
| `resolve_indices` "mirrors model/predict.py exactly" | an unstable sort against a stable one — worth 17 points of coverage | reading the docstring next to the code | **no** |
| `docker/api.Dockerfile` and the root `Dockerfile` | three fixes applied to one and not the other | running the cold path for the first time in weeks | **yes** |
| the mock provider and the real response shape | *designed against* — the mock walks the schema so it cannot fall behind | asking how the instrument would lie | n/a — never duplicated |

Two of the three were caught by luck. The third was caught by design, and it is the only
one that could not have gone unnoticed.

**The last column is the one that decides the fix, and the two cases genuinely differ.**

`resolve_indices` takes `(list[dict], np.ndarray)` because it runs inside training over a
scored matrix; `resolve` takes `list[ScoredCandidate]` because it runs at inference over
objects. Merging them would mean one of the two callers marshalling its data into the
other's shape on every call, in the hot path of both. So there the property test *is* the
right answer — it is the strongest available response, not a weaker substitute for one.

The two Dockerfiles had no such excuse. They described the same image for the same
application and differed only in which paths they copied. Merging cost nothing and removed
the failure mode rather than detecting it.

> **Reach for the test when the duplication is forced. Reach for deletion when it is not —
> and check which one you are looking at before deciding, because "add a test" is the
> answer that always feels available.**

> **Two things that must stay identical will not, unless something makes them the same
> thing.** A test that fails on divergence is the weaker version of this; having one thing
> is the stronger one. Prefer deleting the duplicate to synchronising it.

**The practical rule this leaves:** whichever path you run daily is the one that works. Any
other path is a claim, and it decays silently from the moment it stops being exercised.

Which makes the standing obligation on this repository specific rather than general: **the
cold `docker compose up` from a fresh clone must be run from a clone before anyone is
invited to try it, and again whenever the image or the compose file changes** — not because
something is expected to break, but because the finding above is precisely that nothing
breaks. Things stop being the same, one correct change at a time.

### The obligation was discharged, and it paid for itself immediately

`git clone` into a scratch directory, `docker compose build --no-cache`, `up`, and hit every
endpoint. **Everything worked.** Two services, no `.env` required, all ten native
dependencies loading under `/health/native`, the frontend served on both 3000 and 8000, and
both seeded runs visible — `v1-test` at 4,950 settlements, the held-out one the README
reports.

**And the README's timing table was wrong by 5.7×.**

| | claimed | measured from a fresh clone |
|---|---|---|
| build, "no cached layers" | 29s | **165s** |
| `up` on an empty volume | 10s | 7–9s |
| first answer from the web tier | 3s | 3s |
| **total, from source** | **~42s** | **~177s** |
| total, image already built | 13s | 10s |

**Three of the five rows were right.** The two that were wrong were the two that require
starting from nothing — which is the condition nobody starts from twice. 87 of the missing
136 seconds are pip downloading numpy, scipy, pandas, polars, matplotlib and LightGBM; 44
are Docker exporting a 1.27 GB image. Neither is the application, and neither is paid again.

**This is the same shape as everything else in this document.** Not a broken
instrument: 29 seconds was almost certainly a real reading of a real build. It was a build with the base image and the dependency layers already sitting on
the machine — which is a *rebuild*, and no flag on the command changes what was already
present before it ran. The label said "no cached layers" and described an intention rather
than a measured condition.

**The rule it leaves is narrower than "measure your build" and more useful:** a timing that
depends on what is *absent* from the machine cannot be verified on the machine that has it.
The only place to measure a first build is somewhere that has never built it. That is the
same argument as the sealed test set, applied to a stopwatch — and it is why the number was
wrong for months while every individual thing about it was honest.

The claim that survives, and is now measured rather than asserted: **a reviewer who has
never seen this repository is running it about three minutes after typing `git clone`, and
ten seconds after that on every subsequent start.**

### And then the same thing again, one layer further in, found by a human clicking a button

Approving an exception on the live site returned this:

```
Recorded as escalated by dushyant -- stored in file,
postgres schema mismatch (ProgrammingError) -- migrations may not have run;
appended to approvals.jsonl
```

`/health` reported `database: true`. The hosted database had **no tables in it**.

**The cause is the entrypoint, and it was a deliberate decision written down in the
Dockerfile.** Migrations ran in `docker/entrypoint.sh`, which only `docker compose`
invoked, through an `entrypoint:` override. The hosted deployment ran the image's own
`CMD` and applied nothing. The comment beside it said why:

> Railway's Postgres is managed and the demo reads runs from the filesystem, so a migration
> failure there would take the service down for something no screen needs.

**That is a good argument about every screen that reads, and the review queue writes.** The
reasoning was sound and the inventory was incomplete — which is the same failure as the
`0.945205` row in `notes/threshold.md`: nothing was hidden, nothing was miscounted, and the
question being asked ("what do the screens need in order to render?") could not surface the
one case that did not fit it.

**What it cost is the worst part.** The README claims an append-only Postgres audit trail
enforced by a database trigger. That claim was false in the only place a reviewer would
check it, and it is load-bearing — an audit trail is the thing a finance panel asks about.

#### Merging the two Dockerfiles was reported as finished, and it was not

The entry above this one ends: *"Local and hosted are now the same artifact running the
same code, and drift has nowhere to live."* Both halves of that sentence were true. The
artifact was identical and the code was identical.

**The invocation was not**, and nothing in "same artifact, same code" covers how the
process is started. Deleting the duplicated *image* left a duplicated *entrypoint*, and the
second one was harder to see precisely because the first had just been fixed — the question
"are these two paths the same?" had been asked, answered yes, and closed.

> **A duplicate removed is not a duplicate class removed.** Ask again at the next layer
> down: same image, same code, same command, same environment. The one that bites is the
> one below wherever you stopped looking.

#### Two instruments behaved well, and both were built for this

**The reason classifier earned its keep on a failure nobody constructed.** It said *schema
mismatch, migrations may not have run* — not *postgres unreachable*. The distinction exists
because the first version of that code said "unreachable" for every exception and sent an
investigation to the wrong place while Postgres was up and a foreign key was doing its job.
It had only ever been exercised against fixtures. **This is the first time it classified a
real production failure, and it named the cause precisely enough that the fix was found
without a single log being read.**

**The fallback meant nothing was lost.** The approval was recorded to the append-only file
beside the run and the response said which store took it. A demo that silently dropped a
human decision would have been the worse failure, and refusing the decision would have made
the screen unusable. Saying which store holds it was the right third option, and it held.

#### And one instrument reported green on a question nobody asked

`/health` ran `SELECT 1` and returned `database: true`. **That was correct.** The
connection worked, the credentials were right, the driver prefix normalisation from the
deployment findings above was doing its job. `SELECT 1` proves a connection and says
nothing whatever about whether any table this application writes to exists.

So the health endpoint sat green for as long as the defect lived, in front of a database
that could not accept a single audit record. It belongs with the guards above and with
every error message in the first half of this document: **a correct instrument, correctly
reporting, answering a question that was not the one being asked of it.** A connection and
a schema are two claims. They are now two fields, and `/health` reports `schema: ready`,
`missing` or `unknown` beside the connection.

#### The fix deletes the duplicate rather than synchronising it

Migrations run in `api.main`, on start, in both paths — because both paths run the same
image and now also run the same code to start it. `docker/entrypoint.sh` is gone and the
compose override with it.

**Non-fatal, which keeps the property the original decision was protecting.** A migration
failure is recorded and served on `/health` rather than raised. The service still comes up
with Postgres broken, and now says so specifically instead of reporting a healthy
connection to an empty database. The original concern was legitimate; it was the conclusion
drawn from it that was too narrow.

Verified against a genuinely empty database — the state the live site was actually in:

```
/health          database: true   schema: ready   migrations: applied
tables           alembic_version, approvals, audit_records, exceptions,
                 model_versions, runs
trigger          trg_audit_records_append_only
UPDATE           ERROR: audit_records is append-only: UPDATE is not permitted
DELETE           ERROR: audit_records is append-only: DELETE is not permitted
approval         stored_in: postgres -- "append-only, enforced by a trigger"
fallback file    not created; Postgres took it
```

The append-only claim in the README is now demonstrated rather than asserted, including the
half that matters: **the trigger refuses, rather than the application declining to ask.**

Two regression guards exist, and both were checked by reintroducing the defect and watching
them go red: one fails if an entrypoint script or a compose `entrypoint:` override comes
back, and one fails if `apply_migrations` exists but nothing calls it at start-up — which
is the same defect wearing a different shape.

#### Verifying that fix surfaced a worse one, in the health check itself

The test suite stopped finishing. Not failing — **stopping**, at the same point every time,
with no error and no timeout.

The cause was not the change. `docker compose down` had left a stale port-forward on 5432
that **accepted** connections to a database that no longer existed, and `create_engine`
carried no connect timeout. So libpq completed the TCP handshake, waited for a Postgres
server that was never going to answer, and waited without a bound.

**Three ways a database can be unavailable, and only one of them hangs:**

| | what the socket does | how long the caller waits |
|---|---|---|
| refused | RST, immediately | milliseconds |
| unreachable | no route, eventually errors | seconds, bounded by the OS |
| **half-open** | **accepts, then silence** | **forever** |

Every previous deployment failure in this project was one of the first two, which is why
this had never been felt. A stale forward, a firewall that drops rather than rejects, a
load balancer in front of a dead backend, a container that has exited while its published
port lingers — all produce the third, and all of them are *more* likely on a managed host
than on a laptop.

> **A health check that can hang is not a health check.** It converts a degraded dependency
> into an unresponsive service — which is the single distinction it exists to make. `/health`
> answering `database: false` in five seconds is the product working. `/health` never
> answering is the product down, for a reason that had nothing to do with the product.

`connect_args={"connect_timeout": 5}` bounds the TCP connect and the startup handshake
together, which is what makes it cover the half-open case rather than only the first two.
`pool_timeout` is bounded for the same reason: pool exhaustion under load should surface as
an error rather than as a request that never returns.

**And the endpoint was asking twice.** `/health` opened one connection to run `SELECT 1`
and a second to ask whether the tables existed — two questions, two round trips, and
against a database that is not answering, *two* connect timeouts. Twenty seconds to report
a fact that one connection establishes. It makes a single attempt now and reads both
answers from it, with a test asserting the connection is reused rather than trusting the
timing to reveal it if it is not.

**What this one has that the others do not is that it was never going to be found by
reading.** The entrypoint defect was visible in two files that anyone could compare. The
timing table was checkable by anyone who ran a build. This needed a peer that behaved in a
specific wrong way, at a moment when something was watching — and what was watching was the
verification of an unrelated fix. **Checking one thing properly is how the next thing gets
found**, which is an argument for verifying rather than asserting that has nothing to do
with the thing being verified.
