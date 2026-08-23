# Phase 5 — LLM exception layer and audit trail

**Complete, 23 August 2026.** Six days ahead of BUILD.md's 30–31 August slot.

---

## Exit criteria

| BUILD.md requires | state |
|---|---|
| Reason-code enum fixed and documented | ✅ 14 codes, 3 families, frozen ledger; rename, removal *and* silent addition all fail the build |
| Schema validation failure rate measured and reported | ✅ **0.0%** over 5 batches of real narrations, `json_schema` strict |
| Token cost per 1,000 rows measured and reported in ₹ | ✅ **₹1.83 – ₹3.03**, reported as a band with sources and dates |
| Prompt-injection fixture passes | ✅ 13 tests against the whole pipeline, assuming the model has already complied |
| Audit trail complete end to end and append-only | ✅ 4,945 records for 4,945 settlements, one row shape across four layers |
| `--mock-llm` still runs the full pipeline | ✅ asserted with `NVIDIA_API_KEY` unset |
| `ledgerloop eval` reports reason-code accuracy | ✅ per code, scored against truth, never aggregated alone |

**489 tests, ruff clean.**

---

## What was built

```
llm/codes.py       14 reason codes, 3 families, frozen ledger
llm/provider.py    protocol, mock-as-fault-injector (9 faults), NVIDIA NIM
llm/ratelimit.py   36 rpm token bucket, injectable clock
llm/schemas.py     3 Pydantic schemas, closed vocabularies, decode-time constraints
llm/prompt.py      versioned prompt files; identity = version + content checksum
llm/provenance.py  the gate, with quality AND coverage metrics
llm/handler.py     batch, validate, verify, route; one retry; bounded concurrency
llm/cache.py       content-addressed; failures never cached
llm/cost.py        cost as a band, never a point
core/exceptions.py enumeration + reason-code precedence
ledgerloop/audit.py one record shape for every layer
evals/reasons.py   reason-code actionability, scored against truth
```

## Headline measurements

```
end to end, data/train, 4,945 settlements
  matched + exceptions == 4,945, exactly once each        invariant asserted
  deterministic share                                     51.06% at the documented point
  schema failure rate                                     0.0%
  provenance failure rate                                 0.0% over 366 fields
  reason-code actionability                               97.92% overall
  cost per 1,000 settlements                              Rs 1.83 - Rs 3.03
  throughput                                              27.2 rpm measured, pool of 8
```

The largest engineering result was subtractive. Applying one test — *can the gate's
verification be run in reverse to generate the value?* — deleted three of the four fields
the model was being asked for:

```
280s and 31,956 tokens  ->  102s and 12,073 tokens
```

**A 2.7× speedup and 62% fewer tokens, entirely from deleting fields**, with accuracy on
the deleted fields rising from the model's 68% to a regex's 100%.

---

## Three things Phase 6 and 7 must know

### 1. The operating point has moved, and it is NOT confirmed

**Documented: 0.9989 / 51.31% coverage. Measured after a resolver fix: 0.9564 / 68.16%.**
Every document still states the first. The seal decides.

`model/train.resolve_indices` claimed to mirror `model/predict.resolve` "exactly" and used
an *unstable* sort. With 99.7% of candidates sharing an exact calibrated probability, that
decided nearly every contested invoice during operating-point selection.

**Phase 6 must not build a UI against 68%.** The risk is specific and written up in
`notes/threshold.md`: 16.6 of the 16.85 points come from a **single step holding 196
candidates**. The threshold itself is well placed — a 0.028-wide empty gap, ±0.001 moves
coverage by 0.08% — but the result is a single discrete bet. If the sealed set calibrates
that block differently, coverage falls back toward 51%.

Also: at 4 false in 804 the Wilson interval is **[99.02%, 99.99%]**, so the 99.5% floor sits
*inside* it. The floor is enforced on an estimate, and at these counts that is weaker than
it looks.

### 2. The decoder stall is open, and Phase 7 runs 20× the calls

**Mechanism understood, cause unknown.** Constrained decoding intermittently stalls emitting
legal JSON whitespace until the token budget is gone — measured at 23,973 characters
returned, 23,780 of them whitespace.

```
rate            ~1 call in 5 or 6
rescued         every one observed so far, by the single retry
sample          a few dozen calls
Phase 7         roughly 20x that volume
```

**Two things that have not happened yet and could.** A higher rate under sustained load;
or a stall surviving *both* attempts, producing an exception whose cause we cannot explain.
And under concurrency the retry competes for the same token bucket as first attempts, so a
stall cluster costs throughput as well as tokens.

`Usage.call_log` records `stalled` and `raw_chars` on **every** call, not only failures —
a retry-rescued stall leaves no exception behind and its only other trace is the token
bill. So the scale run will report the true rate whether or not any of them fail.

A per-item token budget is available as a mitigation and deliberately **not** adopted: it
caps the cost of the symptom and would let the cause go uninvestigated.

### 3. LLM-bound volume has dropped sharply — do not re-derive it

The resolver fix converted exceptions into matches, and they were exactly the ones the
model was going to be asked about:

```
                        documented point    after the resolver fix
LLM-bound exceptions              1,198                       459
deterministic share              51.06%                     73.1%
calls per 25,000 rows               303                       116
run time at 27.2 rpm            ~11 min                   ~5 min
```

**The batching design is now heavily over-provisioned rather than tight.** Batch 20 and a
pool of 8 were sized against 1,198; both are comfortable at 459. Nothing needs re-tuning,
and the earlier 8.7-minute figure — an arithmetic upper bound presented as a result — is
superseded in `notes/decisions.md` so it cannot be quoted back.

---

## Explanation checkpoint

BUILD.md says to expect this question verbatim.

### Why is the LLM not allowed to decide matches?

The usual answer is a policy: generation is not adjudication, this is a financial control,
a model that is right 99% of the time posts wrong money 1% of the time. All true, and all
of it is a position you could hold without evidence.

**This phase produced the empirical version, which is better.**

The model was given four fields. We built a gate to verify what it returned — every
extracted field re-checked against its own source narration. Then we noticed the gate had
told us something about itself:

> **If the gate's verification procedure can be run in reverse to generate the value, the
> model was never needed for that field.**

A gate that verifies a UTR by finding a 12-digit run only ever *accepts* values a regex
could have produced. So the model cannot contribute anything the gate would let through —
by construction. And it did not merely fail to help. Over 100 real narrations, 71 of which
carry a UTR: **regex found 71, the model found 48.**

Three of the four fields failed that test and were deleted. `utr`, `reference_number`,
`payment_method` — each verified by a procedure that is trivially reversible into an
extractor. What survived is `counterparty_name`, and it survived for a reason that can be
stated: **token-coverage can check whether `ACME INDUSTRIES` is evidenced by a narration, but
it cannot answer which two of eight tokens are the company name.** There is no generative
form of that check. Verification and generation are different operations, and that field is
where they come apart.

**So the LLM is not barred from deciding matches by policy. It is barred because everything
it could decide, something cheaper decides better, and we measured that.** The layer
ordering is not a rule we imposed on the architecture — the gate we built to check the
model turned out to describe the boundary, and the model's job is the complement of what
the gate can generate.

**What would change the answer.** If narrations carried identifiers a regex could not reach
— a UTR split across a line wrap or grouped in fours, as real bank exports sometimes do —
the boundary would move, and the gate's digit-run rule would have to move with it. Our
generator produces none: zero of 4,528 narrations contain a UTR needing more than a plain
regex, so this measurement cannot see the case where the model would win. Today the gate
and the field would in fact be working against each other, because a UTR written
`3000 0000 4412` is not a whole digit-run and the gate would reject the model's correct
answer. The remedy would be to loosen how the identifier is *compared* — normalising
separators on both sides — never whether a failure blocks. It is written down in
`notes/failure-modes.md` and not implemented, because nothing in this dataset exercises it
and an unexercised code path is a liability.

### And what actually stops an adversary?

Not the gate — and an earlier version of this repo claimed otherwise. Injected text *is* in
the narration; that is what makes it injection, so an injected UTR passes provenance
honestly. **Provenance catches the model's error. Layer ordering catches the adversary's
intent.**

The fixture assumes the model has already lost: `Fault.OBEYS_INJECTION` makes the provider
comply with the attacker, returning the injected value at confidence 1.0, and the tests
assert the system's output is unchanged. That is stronger than any pass rate against hostile
prompts, because it removes hope from the experiment. We do not claim the model is robust.
We claim nothing it returns is trusted enough for its robustness to matter — and since the
identifier fields were deleted, an attacker who compromises the model completely cannot
inject a UTR at all, because the model is never asked for one.

---

## What this phase got wrong

Recorded in full in `notes/failure-modes.md`. Six incidents, one pattern:

> **Every metric answers exactly one question, and the failures in this project have
> consistently come from the questions no metric was asking.**

Not one was a broken instrument. Every one was a correct instrument answering its question
correctly while something moved along an axis it did not measure — 0% provenance failures
while a third of UTRs vanished; a diagnosis built on "8,000 tokens produced 193 characters",
a physically impossible reading that survived two experiments because `.strip()` had already
destroyed the contradiction; excellent ECE hiding the fact that 99.7% of candidates cannot
be told apart.

The worst was an audit record containing a false statement: `NO_CANDIDATE`, whose text reads
*"no bank credit resembled this payout"*, written for settlements blocking had found five
candidates for. **An audit trail containing a false statement is worse than no audit trail,
because it is trusted.** No trail makes someone go and look; a confident wrong one sends
them somewhere else.
