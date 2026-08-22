# LedgerLoop — Build Plan

**Razorpay AI Buildathon, Track 04 (AI Finance Controller).**
Submission deadline: 5 September 2026. Build completes 4 September.

---

## READ THIS FIRST

You are building this project with a human partner over roughly thirteen days.
This file is the complete specification: architecture rules, phase order, and
exit gates.

**Operating rules, in force for the entire build:**

1. Read this entire file before writing any code.
2. Work **one phase at a time**. At the end of every phase you **stop**, produce
   the Phase Report described below, and wait. Do not begin the next phase, do
   not ask "shall I continue?", do not do preparatory work for the next phase.
   Stop and hand back control.
3. Plan before you implement. Show the plan, wait for approval, then build.
4. Stay inside the phase's declared scope. If a change outside it seems
   necessary, stop and say so rather than making it.
5. Commit at every passing test. Small commits, present tense, one logical
   change. The commit history is part of the submission.
6. The environment is **Windows**. No `make`. The Typer CLI is the canonical
   interface. All paths must work on Windows.
7. **No API key exists until Phase 5.** Every phase before that must run fully
   with `--mock-llm`. Design for this from the start; do not stub it in later.

### Phase Report format

At every gate, output exactly this and then stop:

```
PHASE N COMPLETE

Exit criteria:   [each one, PASS or FAIL with evidence]
Metrics:         [current numbers, if the phase produced any]
Decisions:       [design choices made, and what was rejected and why]
Deviations:      [anything built differently from this plan, and why]
Shortcuts:       [anything that would not survive production]
Risks:           [what could bite us in a later phase]
Next phase:      [one line on what Phase N+1 will do]

Awaiting approval to proceed.
```

### On using your own judgment

You are not required to follow this plan literally where you can see a better
route. If a different technique, library, or ordering produces a measurably
stronger result, **propose it during planning**, state the trade-off, and
implement it once approved.

That latitude is bounded by three things, which are not negotiable:

- The architecture rules below.
- The generator/matcher isolation boundary.
- The exit criteria of the current phase. Improve *how* they are met; never
  weaken *what* is met.

Improvements that add **depth** are welcome. Improvements that add **scope** are
not — this is a thirteen-day build judged on one loop closed properly. When
unsure which a suggestion is, ask.

---

## THE THESIS

Most submissions to this track will build a matcher and report a match rate.
That is the baseline, not the win.

This project's claim is different: **it is a reconciliation system that knows
when it is wrong.** The headline artifact is not an accuracy number, it is a
calibrated **risk–coverage curve** — at 70% coverage it is 99.99% precise, at
90% coverage 99.4% — with the merchant choosing where on that curve their risk
appetite sits.

The brief says verification capacity, not generation speed, is the bottleneck.
Take that literally. Every design decision below serves the claim that this
system's uncertainty is trustworthy.

Three things carry that claim, and they are the difference between a good
submission and a winning one. Do not cut them:

- **Held-out case types** — the model is trained on eight failure modes and
  evaluated on ten. Two are entirely unseen. This proves generalisation rather
  than memorisation, and pre-empts the obvious attack ("you only solve the cases
  you designed") with evidence instead of argument.
- **Chaos mode** — a live button that injects novel, unmodelled corruption into
  a running batch. The system must route the unknown to the exception queue with
  honest reason codes rather than confidently mis-matching. This is the demo
  moment the panel remembers.
- **The honest failure list** — documented cases the system cannot solve, with
  reasons. The brief rewards this twice.

---

## ARCHITECTURE RULES

Not negotiable. Every phase is checked against these.

1. **Deterministic before probabilistic before generative.** Exact rules first,
   fuzzy matching second, learned classifier third, LLM last and only on the
   residue. Never send a row to an LLM that a rule can settle.

2. **The LLM never decides a match.** It parses unstructured narration into
   structured fields, proposes journal entries, and writes exception reasons.
   Match/no-match is always a scored decision from the deterministic or learned
   layers. This is a financial control; generation is not adjudication.

3. **Confidence must be calibrated.** Classifier output is a calibrated
   probability (isotonic or Platt on a validation split), never a raw margin.
   Uncalibrated scores make the risk–coverage curve meaningless, and that curve
   is the entire thesis.

4. **Precision over coverage.** A false auto-match posts wrong money to a
   ledger. A missed match costs a human thirty seconds. Tune for near-zero false
   auto-match and let coverage fall where it falls.

5. **Nothing happens without an audit record.** Every decision writes: input row
   hashes, layer that fired, feature vector, calibrated confidence, model
   version, prompt version, token cost, timestamp, and approver where
   human-gated. Audit records are append-only.

6. **All LLM output is schema-validated.** Pydantic models, one retry on
   validation failure, then route to the exception queue. Never parse free text.

7. **No agent framework for control flow.** Plain Python state machine. A
   reviewer must be able to follow the orchestration in five minutes.

### The isolation boundary

`datagen/` produces the synthetic data **and** the ground-truth answer key.

`core/`, `model/`, `llm/`, and `api/` must never import from `datagen/`, read
`truth.csv`, or depend on any generator internal — seed, ordering, ID scheme,
or injected-case tag. The matcher sees exactly what a real system would see:
three files of records.

Only `evals/` may read both sides, and only to score predictions after the fact.

If the matcher can see the answer key, every number in the submission is
worthless. Enforce this with an import-linting test.

---

## REPOSITORY LAYOUT

```
ledgerloop/
├── datagen/     synthetic generator + ground truth (isolated)
├── core/        blocking, exact rules, fuzzy matching, feature extraction
├── model/       classifier, calibration, threshold and coverage selection
├── llm/         exception handler, Pydantic schemas, versioned prompts
├── api/         FastAPI
├── web/         Next.js + Tailwind + shadcn/ui
├── evals/       metrics harness, reports
├── notes/       threshold rationale, failure modes, decisions
├── data/        generated batches (train/ test/ demo/)
├── BUILD.md     this file
└── README.md
```

**CLI (canonical interface, Windows-safe):**

```
ledgerloop generate --rows N --seed S --difficulty {easy,hard} --out DIR
ledgerloop recon --in DIR [--mock-llm] [--threshold T]
ledgerloop eval --run RUN_ID
ledgerloop chaos --run RUN_ID --corruption TYPE
```

**Stack:** Python 3.11+, `uv`, `ruff`, Typer, Polars/pandas, RapidFuzz,
scikit-learn, LightGBM, Pydantic, FastAPI, SQLAlchemy, Alembic,
**PostgreSQL 16**, Next.js, Tailwind, shadcn/ui, Recharts.
**Docker Compose is the primary run path**, not an optional extra.

### Database rules

- **PostgreSQL 16**, running as a service in Docker Compose. Not hosted, not
  external — the demo must not depend on a network call to a third party.
- **Money is stored as `NUMERIC(14,2)`.** Never `float`, never `double`. Any
  float arithmetic on an amount is a correctness bug in a reconciliation system
  and will be treated as one.
- Audit record feature vectors are stored as `JSONB`, queryable in SQL.
- Schema is managed by **Alembic**. Migrations must be idempotent and must run
  automatically on container start.
- Connection via `DATABASE_URL`. No hardcoded credentials anywhere.
- The API container must not start until Postgres passes a healthcheck. This is
  the single most common cause of a failed `docker compose up` on a reviewer's
  machine — get it right in Phase 0, not Phase 6.

---

## DELIBERATELY OUT OF SCOPE

Say no now. Live bank API integration. Authentication and multi-tenancy. The
forward cash forecaster. The tax-line matcher. Any second finance-ops loop.

The brief asks for **one loop closed properly**. Four loops half-built is the
most common way to lose this.

---

# PHASES

---

## PHASE 0 — Foundation
**22 Aug · half day · scope: repo root, `pyproject.toml`, CLI skeleton**

### Tasks
- Scaffold the directory layout above. Initialise git, `uv`, `ruff`.
- Typer CLI skeleton with all four commands registered as no-ops.
- **`docker-compose.yml` with three services: `db` (postgres:16-alpine), `api`,
  `web`.** Get this fully working now — a broken Compose discovered on 4 Sept is
  unrecoverable.
  - `db` has a healthcheck using `pg_isready`
  - `api` uses `depends_on: db: condition: service_healthy`
  - named volume for Postgres data so it survives `docker compose down`
  - Alembic migrations run automatically on `api` start, idempotently
- SQLAlchemy models for: `audit_records`, `exceptions`, `approvals`,
  `model_versions`, `runs`. Money columns `NUMERIC(14,2)`. Feature vectors
  `JSONB`. Audit table append-only — no UPDATE or DELETE paths in code.
- Import-lint test that fails if `core/`, `model/`, `llm/`, or `api/` import
  from `datagen/` or reference `truth.csv`. This test must exist before any
  matching code does.
- `.env.example` with `ANTHROPIC_API_KEY=` and `DATABASE_URL=`. Never commit a
  real key.

### Exit criteria
- [ ] `ledgerloop --help` lists all four commands
- [ ] `ruff check` clean
- [ ] Import-lint test exists and passes
- [ ] **`docker compose up` from a clean clone starts all three services and
      applies migrations with no manual step**
- [ ] **`docker compose down && docker compose up` preserves data**
- [ ] API waits for Postgres correctly — verified by starting on a cold machine
- [ ] No money column is a float type anywhere in the schema
- [ ] Initial commit pushed to a public GitHub repo

**STOP. Phase Report. Await approval.**

---

## PHASE 1 — Synthetic data generator
**23–24 Aug · scope: `datagen/`, `data/`**

Everything downstream is measured against this. Correctness here is
load-bearing.

### Use real schemas
Do not invent column names. Mirror **Razorpay's published settlement and
recon report format** for the gateway file, and **two real Indian bank
statement export formats** for the bank file. This is a two-hour cost that
changes the conversation at the panel: the data is in the shape their systems
actually produce, not a shape you invented.

### Data contracts

**gateway_settlements.csv** — Razorpay settlement report shape:
`settlement_id, utr, payment_id, order_id, gross_amount, fee, gst_on_fee,
net_amount, settled_on, merchant_ref`

**bank_statement.csv**:
`txn_id, value_date, narration, debit, credit, balance, bank_ref`

**invoice_ledger.csv**:
`invoice_id, customer_name, invoice_date, due_date, amount, tds_applicable,
tds_section, status`

**truth.csv** — never read outside `evals/`:
`invoice_id, settlement_id, txn_id, case_type, notes`

### Case types and distribution

| case_type | share | description |
|---|---|---|
| clean | 55% | 1:1:1, exact amounts, same day |
| batched_settlement | 12% | one bank credit covers N invoices |
| fee_deducted | 10% | gateway fee + 18% GST reduces the credit |
| partial_payment | 6% | customer short-pays |
| tds_deducted | 5% | B2B receipt net of TDS |
| refund_netted | 4% | refund netted against the same payout |
| date_skew | 3% | sources disagree by 1–3 days |
| duplicate_utr | 2% | same UTR reused |
| rounding_drift | 2% | ₹0.01–0.05 discrepancy |
| orphan | 1% | genuinely unmatchable |

**Held-out design (critical):** tag two case types — `tds_deducted` and
`refund_netted` — as held-out. The generator must support
`--exclude-cases tds_deducted,refund_netted` so the training batch can be
produced without them while the test batch contains all ten.

### Realism requirements
- Narrations generated from templates with noise, not a fixed list. Shapes:
  `NEFT-AXISCN0483821-ACME RETAIL PVT-/RRN/`, `UPI/CR/40382910/ACME/PAYTM`,
  truncated names, inconsistent casing, missing separators.
- Gateway fee ~2%, GST 18% on fee, TDS 1–10% by section. **Amounts must
  reconcile arithmetically** — a reviewer will hand-check one row.

### Generate now, and do not touch again
- `data/train/` — seed 42, `--exclude-cases tds_deducted,refund_netted`
- `data/test/` — seed 7, all ten case types. **Sealed until Phase 7.**
- `data/demo/` — seed 99, 500 rows, for the live demo

### Exit criteria
- [ ] Same seed produces byte-identical output; different seeds produce
      genuinely different data, not a reshuffle
- [ ] Test asserts each case type within 1% of target share
- [ ] `--exclude-cases` verified to fully remove the named types
- [ ] `truth.csv` covers every non-orphan record exactly once
- [ ] Column names match the real Razorpay/bank formats, sources cited in
      `notes/schemas.md`
- [ ] One instance of each case type hand-verifiable from the CSVs
- [ ] All three batches generated and committed

**STOP. Phase Report. Await approval.**

---

## PHASE 2 — Evaluation harness
**25 Aug · scope: `evals/`**

Built before the matcher so there is a measurable target from day one.

### Metrics required
- Auto-match rate (coverage at the operating threshold)
- Precision on auto-matched pairs — the headline safety metric
- Recall of true links
- **Money-weighted precision**: ₹ incorrectly matched ÷ ₹ total. This is the
  number a finance-ops reviewer actually cares about.
- **Risk–coverage curve** — precision at every coverage level from 50% to 100%.
  This is the project's headline artifact; build it as a first-class output,
  not a derived chart.
- Per-case-type confusion matrix, with held-out types reported separately
- Exception reason-code accuracy
- Throughput (rows/sec), p95 latency, ₹ cost per 1,000 rows

### Tasks
- `ledgerloop eval --run RUN_ID` outputs JSON plus a markdown table, and
  rewrites the README metrics table between marker comments. Never hand-edit
  that table.
- Include a deliberately weak baseline (exact-UTR-only) as a regression floor.

### Exit criteria
- [ ] `ledgerloop eval` runs end to end against the baseline
- [ ] Baseline scores plausibly low — a scorer reporting near-perfect results
      on a weak baseline is broken; investigate before proceeding
- [ ] Money-weighted precision computed from amounts, not row counts
- [ ] Risk–coverage curve renders as a committed chart
- [ ] Scoring logic unit-tested with hand-built fixtures
- [ ] README table regenerates automatically

**STOP. Phase Report. Await approval.**

---

## PHASE 3 — Blocking, exact and fuzzy matching
**26–27 Aug · scope: `core/`**

Target: ~75% match rate with zero LLM calls.

### Tasks
- **Blocking first.** Bucket by amount band, date window, and normalised
  counterparty token. Pair scoring only within buckets. No O(n²) candidate
  generation anywhere.
- **Exact layer:** UTR/RRN + amount + date. Should clear ~55%.
- **Fuzzy layer:** amount tolerance bands, ±3-day windows, RapidFuzz
  `token_set_ratio` over normalised narration, and a **bounded** subset-sum
  search for batched settlements. Cap the search space explicitly and document
  the cap — unbounded subset-sum will hang on realistic data.
- **Feature extraction** per surviving pair: absolute and percentage amount
  delta, date delta, narration similarity, UTR edit distance, counterparty
  historical match frequency, whether the amount delta matches a known
  fee/GST/TDS rate, and whether the pair participates in a plausible subset sum.
- Everything in `core/` is a pure function. No I/O below orchestration.

### Exit criteria
- [ ] Match rate ≥ 70% on train with no model and no LLM
- [ ] 25,000 rows in under 60 seconds
- [ ] Every matching rule has a named unit test describing its case
- [ ] Import-lint still passes
- [ ] Feature extraction returns a documented, stable schema

### Explanation checkpoint
Before the gate, the human writes out from memory how blocking works and why
it is necessary, then diffs against the implementation. Include a one-paragraph
plain-language explanation of blocking in the Phase Report to support this.

**STOP. Phase Report. Await approval.**

---

## PHASE 4 — Classifier, calibration, selective prediction
**28–29 Aug · scope: `model/`**

The thesis lives here. A complete working v1 exists at the end of this phase.

### Tasks
- LightGBM (or sklearn `HistGradientBoosting`) over Phase 3 feature vectors.
- Train on `data/train/` only. Hold out a validation split from within it for
  calibration. **The test set is not touched.**
- **Calibrate** with isotonic regression or Platt scaling. Verify with a
  reliability diagram committed to `notes/`.
- Produce the **risk–coverage curve** and select an operating point optimising
  for near-zero false auto-match. Record the point, the reasoning, and the full
  curve in `notes/threshold.md`.
- Version the model artifact; the version goes into every audit record.

### Exit criteria
- [ ] Reliability diagram committed; calibration visibly holds
- [ ] Auto-match precision ≥ 99% at the operating point on train
- [ ] Risk–coverage curve generated across 50–100% coverage
- [ ] `ledgerloop eval` reports classifier results, beating the baseline
- [ ] The test set has still never been read
- [ ] Commit tagged `v1-working` — this is the safety net

### Explanation checkpoint
Why calibration matters and what breaks without it. This is the single most
likely technical question at the panel. Include the explanation in the report.

**STOP. Phase Report. Await approval.**

---

## PHASE 5 — LLM exception layer and audit trail
**30–31 Aug · scope: `llm/`**

**The API key arrives at the start of this phase.** Everything built so far must
still run under `--mock-llm` when it is absent.

### Tasks
- Three jobs, three versioned prompts in `llm/prompts/`:
  1. Parse pathological narration into structured fields.
  2. Propose a journal entry for resolvable exceptions (Dr/Cr lines, GL codes,
     TDS split).
  3. Write a human-readable reason for each unresolved exception, tagged to a
     **fixed reason-code enum**.
- Pydantic schema on every response. One retry on validation failure, then route
  to the exception queue. Never parse free text.
- Prompts referenced by version in the audit record. No inline prompt strings.
- **Audit records** append-only across the whole pipeline, including the
  deterministic layers — retrofit them if needed. Fields per rule 5.
- **Treat bank narration as untrusted input.** A narration containing
  instruction-like text must not alter behaviour. Test with an adversarial
  fixture.
- `--mock-llm` remains fully functional after this phase.

### Exit criteria
- [ ] Reason-code enum fixed and documented
- [ ] Schema validation failure rate measured and reported
- [ ] Token cost per 1,000 rows measured and reported in ₹
- [ ] Prompt-injection fixture passes
- [ ] Audit trail complete end to end and append-only
- [ ] `--mock-llm` still runs the full pipeline
- [ ] `ledgerloop eval` reports reason-code accuracy

### Explanation checkpoint
Why the LLM is not allowed to decide matches. Expect this question verbatim.

**STOP. Phase Report. Await approval.**

---

## PHASE 6 — API, frontend, chaos mode
**1–2 Sep · scope: `api/`, then `web/`**

Panels do not clone repositories. This phase produces the thing they click.

### API
FastAPI. Upload, run, results, exception queue, approve/reject, chaos-inject.
Long runs are jobs with status polling, never blocking requests.

### Frontend — exactly three screens
1. **Upload** — three files, plus a one-click "load demo batch". The demo path
   must work instantly with no file hunting.
2. **Dashboard** — match rate, money reconciled vs unreconciled, throughput,
   cost, reason-code breakdown, and the **risk–coverage curve with a live
   operating-point slider**. This slider is the thesis made visible. Build it
   properly; it is the strongest interaction in the product.
3. **Review queue** — each exception with evidence side by side, the proposed
   journal entry, approve/reject/edit. Approval writes an audit record with
   approver and timestamp.

### Chaos mode
A control that injects novel, unmodelled corruption into a live batch:
unseen narration formats, a bank reporting dates differently, amounts off by an
unmodelled fee structure. The system must route the unknown to exceptions with
honest reason codes rather than confidently mis-matching.

Support a free-text corruption spec so the panel can name one on the spot.
Failing gracefully under chaos **is** the point — a graceful failure proves the
thesis as well as a success does.

### Exit criteria
- [ ] Live public URL, seeded with a completed run
- [ ] `docker compose up` and a stranger understands the product in 60 seconds
- [ ] Demo batch loads and completes in one click
- [ ] Operating-point slider updates metrics without a full re-run
- [ ] Chaos mode runs live and degrades gracefully
- [ ] Readable on a laptop screen in a shared video call

**STOP. Phase Report. Await approval.**

---

## PHASE 7 — Scale, sealed test set, failure analysis
**3 Sep · scope: all, plus `notes/`**

### Tasks
- **Break the seal on `data/test/` — once.** Run it. Report those numbers,
  including performance on the two held-out case types the model has never
  seen. If results are materially worse than train, **say so and diagnose it.
  Do not retune against the test set.** The held-out result is the most
  technically credible number in the submission whatever it says.
- Run 25,000 rows. Tune blocking for throughput. Record actual ₹ inference cost.
- Per-case-type confusion matrix, held-out types called out separately.
- **Unit economics**, one paragraph in the README: an analyst reconciles ~N
  transactions/hour at ₹X fully-loaded; at the operating point this removes Y
  hours/month per merchant against ₹Z of inference. Fintech panels think in unit
  economics and almost no student submission does the arithmetic.
- **Settlement Q&A layer** — natural-language questions answered **strictly
  from the audit records**, never free-form generation. "Why is ₹4.2L
  unreconciled for ACME in July?" resolves to stored decisions. Cut this
  without hesitation if the day is tight; everything above it ranks higher.
- `notes/failure-modes.md`: which cases fail, why, what would fix them, what
  remains unsolved. Plain language.

### Exit criteria
- [ ] Test-set metrics reported, unretuned
- [ ] Held-out case-type performance reported separately and honestly
- [ ] 25,000-row run with recorded throughput and ₹ cost
- [ ] Per-case-type confusion matrix committed
- [ ] Unit economics paragraph written
- [ ] `notes/failure-modes.md` written and honest

### Note
Produce the matrices and the data. The **written interpretation is the human's
own work** — it will be defended in person.

**STOP. Phase Report. Await approval.**

---

## PHASE 8 — README, video, rehearsal
**4 Sep · 5 Sep is buffer and submission**

### README order
1. One-line problem statement
2. **Metrics table** (auto-generated) — above the fold
3. The risk–coverage curve
4. Architecture diagram
5. Why deterministic-first, LLM-last
6. Reproduce in three commands
7. **What it gets wrong** — the honest failure list
8. Unit economics
9. Roadmap

### Pitch video — five minutes
The brief specifies a **5 minute** pitch video. Use all five.

| Time | Content |
|---|---|
| 0:00–0:30 | Problem, opening on the headline number |
| 0:30–1:30 | Architecture: four layers, LLM last, why |
| 1:30–2:45 | Live run on the **full** batch — say it is the full batch |
| 2:45–3:30 | Risk–coverage curve, money-weighted precision, ₹ cost |
| 3:30–4:30 | **Chaos mode, live.** Novel corruption, graceful degradation |
| 4:30–5:00 | What it gets wrong, and what's next |

Close on the exception queue, not the dashboard. The honest list is the point.

### Rehearse these
- Why not just prompt an LLM for the whole thing?
- What is your false-match rate, and what does a false match cost the merchant?
- How did you choose the operating point on the curve?
- How do you know your synthetic data resembles real settlement data?
- Your metrics are on data you generated — why should I trust them?
- What breaks at 10 million rows?
- How do you handle a prompt-injected bank narration?
- How do you know a retrained model hasn't regressed?
- Which part of this are you least confident in?

The last one is answered honestly. It lands better than a deflection, and
`notes/failure-modes.md` already contains the answer.

**STOP. Phase Report. Await approval.**

---

## SCHEDULE

| Date | Phase | Milestone |
|---|---|---|
| 22 Aug | 0 | Scaffold, isolation lint |
| 23–24 Aug | 1 | Generator, real schemas, held-out design |
| 25 Aug | 2 | Eval harness, risk–coverage curve |
| 26–27 Aug | 3 | ~75% match, no LLM |
| 28–29 Aug | 4 | **v1 working — safety net** |
| 30–31 Aug | 5 | Exceptions, audit trail (API key needed) |
| 1–2 Sep | 6 | Deployed, three screens, chaos mode |
| 3 Sep | 7 | Sealed test set, held-out results, honest failures |
| 4 Sep | 8 | README, video, rehearsal |
| 5 Sep | — | Buffer and submit |

Buffer is 5 September. Do not spend it early.

---

## IF THE SCHEDULE SLIPS

Cut in this order:
1. Settlement Q&A layer
2. Frontend polish on the review-queue screen
3. The 25,000-row scale run (report 5,000 instead)
4. The deployed public URL — fall back to Compose plus the recorded video

Never cut, in any circumstance: the sealed test set, the held-out case types,
calibration, the audit trail, chaos mode, `notes/failure-modes.md`, or a
working `docker compose up`. Those are the submission.

Note that Docker Compose is no longer cuttable — with Postgres it is the run
path, not a convenience. If Compose breaks, the project does not run for
anyone but you.

---

**Begin with Phase 0. Plan first. Stop at the gate.**
