# LedgerLoop — the specification

**Razorpay AI Buildathon, Track 04 (AI Finance Controller).**

This is the document the system was built against, and it is quoted by name from 33 files
in this repository — docstrings, tests, and `docker-compose.yml` all cite it as the reason
a rule exists. It is kept for that reason: a comment saying *"BUILD.md forbids O(n²)
candidate generation"* is worth less if the reader cannot go and check.

What it contains is the architecture rules, the data contracts, the metric definitions and
the requirements, numbered in the order they were built. What it no longer contains is the
project management — the calendar, the gate ceremony, the contingency plan for running
late. Those described how the work was organised, not how the system works, and a reviewer
has no use for them.

Where the build deviated from what is written here, the deviation is recorded here too,
beside the thing it deviates from.

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

> **Measured against the above, after the fact.** The two figures in this section were
> illustrative when they were written and are left in place rather than tidied away, because
> a specification edited to match its results is not a specification. What was actually
> measured, once, on a sealed set at a threshold fixed beforehand: **62.91% coverage at
> 99.9037% precision**, and 99.2369% on a batch five times larger — below the floor this
> system is designed around. Both are in the README, and neither is quotable without the
> other.
>
> **The held-out claim needs the sharper correction.** "This proves generalisation rather
> than memorisation" is not what happened. One unseen case type generalised (`tds_deducted`,
> 68.00%, indistinguishable from ordinary performance at this sample size); the other did
> not generalise at all (`refund_netted`, 0 of 200). The reason is mechanical and is the
> more useful result: the system generalises to an unseen deduction expressible as a *rate*
> on the amount, and fails completely where no rate links the invoice to the credit, because
> blocking never generates a candidate for the classifier to score. Held-out case types
> turned out to prove something more specific than the claim they were included to support.

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
ledgerloop generate --rows N --seed S --out DIR
ledgerloop recon --in DIR [--mock-llm] [--threshold T]
ledgerloop eval --run RUN_ID
ledgerloop chaos --run RUN_ID --corruption TYPE
```

**Stack:** Python 3.11+, `uv`, `ruff`, Typer, Polars/pandas, RapidFuzz,
scikit-learn, LightGBM, Pydantic, FastAPI, SQLAlchemy, Alembic,
**PostgreSQL 16**, ~~Next.js, Tailwind, shadcn/ui, Recharts~~ static HTML/CSS/JS.
**Docker Compose is the primary run path**, not an optional extra.

> **Two deviations from this file, recorded here so the spec and the code do not disagree.**
>
> **1. The frontend is static HTML/CSS/JS, not Next.js + Tailwind + shadcn/ui.** Phase 6's
> exit criterion is *"`docker compose up` and a stranger understands the product in 60
> seconds."* A React toolchain puts an `npm install` and a build step inside that path, and
> Phase 0 had already ruled that out for the same reason (`web/server.js`: "zero
> dependencies on purpose: the compose stack must never block on an npm install").
> Reversing that decision in the phase whose criterion is "works on a stranger's machine"
> would trade demo reliability for a stack line. Three screens and one slider need no
> routing, SSR or component library; the slider is ~60 lines of vanilla JS over a JSON
> endpoint.
>
> **2. `docker compose` runs two services, not three.** BUILD.md's layout is db + api +
> web. The API serves the static frontend, so the third container was redundant — and
> keeping a separate image for it meant keeping two Dockerfiles in sync by hand, which
> failed: a cold `docker compose up` from a fresh clone stopped building entirely, in three
> ways at once, all of them fixes that had been made to the hosted image and not carried
> across. There is now one `Dockerfile`, built by both compose and the hosted deployment.
> Port 3000 is still published alongside 8000, because BUILD.md and the README both name it
> and a reviewer who typed it should not meet a dead port. See `notes/failure-modes.md`,
> "The path you exercise is the only one that works".
>
> **3. `--difficulty {easy,hard}` is removed from the CLI.** It was accepted and did
> nothing from Phase 1 onward. Wiring it means compound-case generation — a `datagen/`
> feature this submission does not need — and a flag that lies about what it does is worse
> than an absent flag, because it invites someone to rely on it.

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

# REQUIREMENTS

Numbered in the order they were built. The numbering is load-bearing:
code and tests cite these sections by number.

---

## 0. Foundation
### Tasks
- Scaffold the directory layout above. Initialise git, `uv`, `ruff`.
- Typer CLI skeleton with all four commands registered as no-ops.
- **`docker-compose.yml` with three services: `db` (postgres:16-alpine), `api`,
  `web`.** *(Two as built — see the deviation under Repository layout.)* Get this fully working at the start rather than at the end. A run path that
  is only exercised for the first time when it is needed is not a run path.
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
- `.env.example` with `NVIDIA_API_KEY=` and `DATABASE_URL=`. (Superseded 23 Aug 2026:
  the provider is NVIDIA NIM, not Anthropic. See notes/decisions.md.) Never commit a
  real key.

### Must be true before this is done
- [ ] `ledgerloop --help` lists all four commands
- [ ] `ruff check` clean
- [ ] Import-lint test exists and passes
- [ ] **`docker compose up` from a clean clone starts all three services and
      applies migrations with no manual step** — *two services as built; the third
      was redundant and became a maintenance hazard. Recorded under Repository
      layout above.*
- [ ] **`docker compose down && docker compose up` preserves data**
- [ ] API waits for Postgres correctly — verified by starting on a cold machine
- [ ] No money column is a float type anywhere in the schema
- [ ] Initial commit pushed to a public GitHub repo

---

## 1. Synthetic data generator
Everything downstream is measured against this. Correctness here is
load-bearing.

### Use real schemas
Do not invent column names. Mirror **Razorpay's published settlement and
recon report format** for the gateway file, and **two real Indian bank
statement export formats** for the bank file. This is a two-hour cost that
changes the conversation at the panel: the data is in the shape their systems
actually produce, not a shape you invented.

### Data contracts

> **Superseded, 22 Aug 2026.** The contract originally written here was illustrative and
> used field names Razorpay does not publish. This section now records the real names,
> per this phase's own exit criterion ("column names match the real Razorpay/bank
> formats"). Full field-by-field mapping, units, and source URLs are in
> `notes/schemas.md`; the single definition used by code is `datagen/schemas.py`.

**gateway_settlements.csv** — Razorpay Settlement Recon report shape.
**Money is integer paise, as Razorpay itself reports it.** Timestamps are unix ints:
`entity_id, type, payment_id, order_id, order_receipt, settlement_id, settlement_utr,
amount, fee, tax, debit, credit, net_amount, currency, method, created_at, settled_at`

- `type` is `payment | refund | transfer | adjustment`. It is load-bearing: a refund is
  its own row carrying a debit, which is how `refund_netted` manifests in real reports.
- `tax` is GST charged on `fee`.
- `net_amount` is **not** a Razorpay field. It is a derived convenience column,
  `amount - fee - tax` for payments and `-amount` for refunds.

**bank_statement.csv** — one normalised file whose rows are drawn from two real dialects
(HDFC and ICICI), marked by `bank`. The dialects differ in narration grammar and date
convention, not merely in column headers:
`txn_id, value_date, narration, debit, credit, balance, bank_ref, bank`

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

### Must be true before this is done
- [ ] Same seed produces byte-identical output; different seeds produce
      genuinely different data, not a reshuffle
- [ ] Test asserts each case type within 1% of target share
- [ ] `--exclude-cases` verified to fully remove the named types
- [ ] `truth.csv` covers every non-orphan record exactly once
- [ ] Column names match the real Razorpay/bank formats, sources cited in
      `notes/schemas.md`
- [ ] One instance of each case type hand-verifiable from the CSVs
- [ ] All three batches generated and committed

---

## 2. Evaluation harness
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

### Must be true before this is done
- [ ] `ledgerloop eval` runs end to end against the baseline
- [ ] Baseline scores plausibly low — a scorer reporting near-perfect results
      on a weak baseline is broken; investigate before proceeding
- [ ] Money-weighted precision computed from amounts, not row counts
- [ ] Risk–coverage curve renders as a committed chart
- [ ] Scoring logic unit-tested with hand-built fixtures
- [ ] README table regenerates automatically

---

## 3. Blocking, exact and fuzzy matching
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

### Must be true before this is done
- [ ] Match rate ≥ 70% on train with no model and no LLM
- [ ] 25,000 rows in under 60 seconds
- [ ] Every matching rule has a named unit test describing its case
- [ ] Import-lint still passes
- [ ] Feature extraction returns a documented, stable schema

### Explanation checkpoint
Write out from memory how blocking works and why it is necessary, then diff that
against the implementation. Anything that cannot be explained from memory is
either not understood or not needed.

---

## 4. Classifier, calibration, selective prediction
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

### Must be true before this is done
- [ ] Reliability diagram committed; calibration visibly holds
- [ ] Auto-match precision ≥ 99% at the operating point on train
- [ ] Risk–coverage curve generated across 50–100% coverage
- [ ] `ledgerloop eval` reports classifier results, beating the baseline
- [ ] The test set has still never been read
- [ ] Commit tagged `v1-working` — this is the safety net

### Explanation checkpoint
Why calibration matters and what breaks without it. This is the single most
likely technical question at the panel. Include the explanation in the report.

---

## 5. LLM exception layer and audit trail
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

### Must be true before this is done
- [ ] Reason-code enum fixed and documented
- [ ] Schema validation failure rate measured and reported
- [ ] Token cost per 1,000 rows measured and reported in ₹
- [ ] Prompt-injection fixture passes
- [ ] Audit trail complete end to end and append-only
- [ ] `--mock-llm` still runs the full pipeline
- [ ] `ledgerloop eval` reports reason-code accuracy

### Explanation checkpoint
Why the LLM is not allowed to decide matches. Expect this question verbatim.

---

## 6. API, frontend, chaos mode
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

> **Deviation: four screens shipped, not three.** Chaos mode is specified below as "a
> control" and was built as a screen of its own rather than a button on the dashboard,
> because a corruption run produces a before/after comparison table that has nowhere to
> live on a screen already carrying the slider. The three screens above are unchanged;
> the fourth is chaos.

### Chaos mode
A control that injects novel, unmodelled corruption into a live batch:
unseen narration formats, a bank reporting dates differently, amounts off by an
unmodelled fee structure. The system must route the unknown to exceptions with
honest reason codes rather than confidently mis-matching.

Support a free-text corruption spec so the panel can name one on the spot.
Failing gracefully under chaos **is** the point — a graceful failure proves the
thesis as well as a success does.

### Must be true before this is done
- [ ] Live public URL, seeded with a completed run
- [ ] `docker compose up` and a stranger understands the product in 60 seconds
- [ ] Demo batch loads and completes in one click
- [ ] Operating-point slider updates metrics without a full re-run
- [ ] Chaos mode runs live and degrades gracefully
- [ ] Readable on a laptop screen in a shared video call

---

## 7. Scale, sealed test set, failure analysis
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
  unreconciled for ACME in July?" resolves to stored decisions. **The lowest-ranked
  item in this document — cut it before anything above it.** It was cut.
- `notes/failure-modes.md`: which cases fail, why, what would fix them, what
  remains unsolved. Plain language.

### Must be true before this is done
- [ ] Test-set metrics reported, unretuned
- [ ] Held-out case-type performance reported separately and honestly
- [ ] 25,000-row run with recorded throughput and ₹ cost
- [ ] Per-case-type confusion matrix committed
- [ ] Unit economics paragraph written
- [ ] `notes/failure-modes.md` written and honest

### Note
Produce the matrices and the data. The **written interpretation is the human's
own work** — it will be defended in person.
