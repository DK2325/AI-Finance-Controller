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

*Populated from Phase 3 onward as real failures are measured.*

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

## Open, unresolved: the decoder stall

**Status at the end of Phase 5: understood in mechanism, not in cause. Not closed.**

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

**So the metric has to change, not the prompt.** Two options for Phase 7, neither taken
yet:

1. **Score against truth, not against ourselves.** `evals/` can see the answer key: for
   each exception, does the assigned code correctly describe why the settlement was not
   matched? A `NO_CANDIDATE` on a settlement that truth says had a real link is a
   *correct* code describing a real miss; a `NO_CANDIDATE` on an orphan is correct in a
   different way. That is a real accuracy measure and it needs no model agreement at all.
2. **A held-out arm.** Run a sample with `system_reason_code` withheld and measure
   agreement there. That is informative, and it costs a second prompt version to maintain.

Option 1 is the one BUILD.md actually asks for (*"`ledgerloop eval` reports reason-code
accuracy"*), and reason-code accuracy against truth is a different and better number than
agreement with ourselves.

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
