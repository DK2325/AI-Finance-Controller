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

---

## Known limitations of the system

*Populated from Phase 3 onward as real failures are measured.*
