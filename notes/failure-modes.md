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
