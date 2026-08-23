# Resume here

Last updated 23 Aug 2026, end of Phase 5. Read this first when picking the build back up.

---

## Read `notes/conventions.md` first

Standing rules that are **not** to be re-derived from a conversation: commit-message
convention, author identity, how the seal is handled, the isolation boundary, integer
paise, secrets. They live in a file because a new session reads files, not history.

The two that catch people immediately:

- **No attribution trailers and no schedule references in commit messages.** Describe the
  change and the reasoning; end at the last line of the body.
- **Author identity is repo-local and a fresh clone loses it.** Re-set
  `user.name` / `user.email` before the first commit in a new clone, or it lands under
  whatever global identity the machine has. That has happened once and needed a history
  rewrite to fix.

## Where the build is

**Phases 0–6 complete.** Phase 7 is next: scale, the sealed test set, failure analysis.

| phase | state |
|---|---|
| 0 foundation, Compose, schema, isolation lint | ✅ |
| 1 synthetic generator, held-out design | ✅ |
| 2 eval harness, risk–coverage curve | ✅ |
| 3 blocking, exact + fuzzy matching, features | ✅ |
| 4 classifier, calibration, operating point | ✅ tagged `v1-working` |
| 5 LLM exception layer + audit trail | ✅ see notes/phase-5-report.md |
| 6 API, frontend, chaos mode | ✅ see notes/phase-6-report.md |
| **7 scale, sealed test set, failure analysis** | **← in progress; scale done, seal BROKEN and scored** |
| 8 README, video, rehearsal | — |

## Carried into Phase 6 and 7 — read notes/phase-5-report.md

Three things that must not be re-derived or accidentally assumed:

1. **The operating point has moved and is NOT confirmed.** Documented 0.9989 / 51.31%;
   measured after a resolver fix, 0.9564 / 68.16%. **Phase 6 must not build a UI against
   68%.** 16.6 of the 16.85 points rest on one 196-candidate block; the seal decides.
2. **The decoder stall is open.** ~1 call in 5-6, retry has rescued every one across a few
   dozen calls, cause unknown. Phase 7 runs ~20x that volume, and retries compete for the
   same token bucket under concurrency.
3. **LLM-bound exceptions fell 1,198 -> 459** after the resolver fix. Batch 20 and a pool of
   8 are now over-provisioned rather than tight. Do not re-tune; do not re-derive.

## Phase 7 — WHERE IT STANDS

**The seal on `data/test` is BROKEN. It was read once, scored once, and reported.** The
irreversible action is done and cannot be repeated. `data/test/.sealed` was replaced by
`data/test/.unsealed`, which carries the same sha256 map forward so integrity is still
enforced by `tests/test_seal.py`.

**Results: `notes/phase-7-report.md`, raw output `notes/measurements/sealed_test.json`.**

Do not re-score `data/test`. The first scored run is the reported run, and a second one
would not be out of sample.

### Done, and committed

| | |
|---|---|
| 25,000-row scale run | **105 settlements/second**, 24,750 settlements in 234.8s |
| blocking growth | exponent **1.590** across two batch sizes — sub-quadratic, and checked at more than one size for the first time |
| machine | AMD Ryzen 5 5600H, 6 physical cores, 15.3 GB, Python 3.12.10 — **named, because throughput without hardware is not a measurement** |
| coverage on `data/scale` | **60.89%** at the pre-committed threshold, all ten case types |
| deterministic share | 65.42% — 3,347 of 9,679 exceptions reach the model |
| pre-commitment | committed at `a733ad4`, **seal intact at that commit** |
| LLM at scale | 168 calls, results in `notes/measurements/scale_llm.json` |

### The pre-committed operating point

**Threshold `0.9564`.** Chosen by the standard procedure on the evaluation split, before any
test data was read. `0.9989` was rejected because it came from the same procedure run
against a resolver known to be broken.

**The registered prediction:** test coverage lands nearer 61% than 68%, and does not
collapse to 51%. **The declared failure condition:** coverage near 51% means the
196-candidate block did not reproduce; precision below 99.5% means the floor did not hold
out of sample, and is reported as the floor failing rather than explained away.

Full text and the reasoning: `notes/phase-7-precommitment.md`.

### The decoder stall is CLOSED, and it closed well

Carried from Phase 5 as the open risk. Measured across 168 calls:

```
estimated from ~30 calls        ~1 in 5-6  = 17-20%
measured over 168 calls          4 in 168  =  2.38%     overstated 7.6x
stalls surviving both attempts          0              <- the thing that had not happened
schema failure rate                  0.0%
```

Every stall was rescued by the single retry. No exception carries a cause we cannot explain.
The per-item token budget stays unadopted — it was a mitigation for a rate that turned out
to be a seventh of what a small sample suggested.

**A different failure appeared instead**, one the spike never produced: `LLM_BATCH_MISMATCH`
on 4 items of 3,347 (0.120%). The envelope check was kept despite 120/120 holding in the
spike, on the grounds that the cost of checking was a set comparison. At scale it fired.

**Throughput does not transfer between jobs.** The `reason` job achieved **10.1 rpm** at a
41s mean call latency, against `parse`'s 27.2 rpm at ~20s — it emits 2,755 output tokens per
call and is latency-bound rather than rate-limited. Quote the job, not "the" rate.

**Cost, measured:** ₹22.15 – ₹36.76 for 3,347 exceptions = **₹0.90 – ₹1.49 per 1,000
settlements**, lower than the earlier ₹1.83 – ₹3.03 because this batch's deterministic share
is 65.42% rather than 51.06%.

### Done in the unsealing commit

- Seal broken: `.sealed` deleted, `.unsealed` written, numbers in the same commit ✅
- Held-out types reported separately, with intervals ✅ — and the "expect wide intervals"
  guidance held for `tds_deducted` (11.49 pts) but **not** for `refund_netted`, where 0 of
  200 gives a *narrow* [0%, 1.88%] and settles the question decisively
- Reliability on test beside train; prior shift located by measurement ✅
- Per-case-type confusion matrix, held-out called out ✅

### Still to do

- Unit economics data (the written interpretation is the human's own work)
- `notes/failure-modes.md` completion
- README rewrite: it still quotes 51.31% / 68.16%, both superseded by 62.91% at 99.9037%
- **Cut** the Settlement Q&A layer — agreed, BUILD.md ranks it last

### Why the scale run happened first

Tuning blocking for throughput *after* reading the test set would let test knowledge inform
a decision that then shapes every test number. Doing it first makes that contamination path
impossible rather than merely unlikely.

---

## The scale run happens locally, not on Railway

The Railway trial gives **1 GB RAM, 2 vCPU, 1 GB disk**. That is enough for what the
deployed instance is actually for — serving the seeded run and the one-click demo batch —
and it is not enough to generate 25,000 rows, train against them, and hold the candidate
set in memory while scoring.

Concretely, the shapes that do not fit:

| | `data/train` | at 25,000 settlements | |
|---|---|---|---|
| candidates held in memory | 7,305 | **94,555** | estimated ~37,000 — 2.6x under |
| peak Python allocation | — | **238 MB** | estimated to exceed 1 GB — wrong |
| generated batch on disk | 2.1 MB | 11 MB | as estimated |

**The memory argument was wrong and the conclusion still holds.** The real reasons to run
locally are CPU time — 235s on six cores, and the trial box has two — and that `tracemalloc`
counts only Python allocations, not the native ones numpy and LightGBM make, so 238 MB is a
floor rather than the figure.

None of that is large in absolute terms, but it lands on a 1 GB box that is also running
Postgres and serving HTTP, and a scale run that gets OOM-killed halfway produces no number
at all. There is nothing to gain by proving throughput on the smallest machine in the
project.

**So the split is:**

- **Locally** — generate the 25,000-row batch, break the seal on `data/test`, run at
  scale, and record throughput, the stall rate, cost, and the held-out numbers.
- **On Railway** — the seeded run and the demo batch, unchanged. Nothing about the
  deployment needs to change for Phase 7.

**When reporting throughput, say which machine.** A rows-per-second figure with no hardware
beside it is not a measurement, and the honest form is "25,000 rows in Xs on a laptop"
rather than an unqualified number that a reader will assume describes the live service.

## Headline numbers as of now

```
SEALED TEST SET (data/test, 4,950 settlements, scored once at 0.9564)
  coverage 62.91% at precision 99.9037%  (95% CI 99.7171%-99.9672%, 3 false in 3,114)
  Rs incorrectly matched 671,820.00 of 416,605,821.54 at stake
  held out  tds_deducted 68.00% [61.98%, 73.47%]   refund_netted 0.00% [0.00%, 1.88%]
  ECE 0.012031 vs 0.010436 on train's eval split; 0.007497 excluding held-out types
  CAVEAT: precision at 24,750 settlements (data/scale) is 99.2369% -- under the floor.
          Quote the two together; see notes/phase-7-report.md section 2.
SUPERSEDED
  coverage 51.31% at precision 99.5050%   documented before the resolver fix
  coverage 68.16% at precision 99.5031%   eval split after it

on data/train, at the documented point
  matched + exceptions == 4,945 settlements, exactly once each
  precision            99.96%  (95% CI 99.77%-99.99%, 1 false in 2,497)
  Rs incorrectly matched  27,372.50 of 395,349,148.43 at stake
  deterministic share  51.06% of exceptions never reach a model
  reason-code actionability 97.92%
  cost per 1,000 settlements  Rs 1.83 - Rs 3.03
  throughput           27.2 rpm measured, pool of 8; matcher 25,000 rows in 6.4s
tests                  489 passing, ruff clean
```

## Environment: nothing to set up

- `.env` exists with a working `NVIDIA_API_KEY`. Verify with
  `python -c "from ledgerloop.config import key_fingerprint; print(key_fingerprint())"`
- venv at `venv/`, Python 3.12.10, all dependencies installed
- Docker Desktop must be **running** for anything touching Compose
- Repo is pushed and clean
- **Live:** https://ai-finance-controller-production.up.railway.app
  (`database: true`, `llm: live`; redeploys on push to main)
- Locally: `docker compose up`, then http://localhost:3000 — two services, one image,
  no `.env` required

## To resume

Activate the venv and say:

> **"Read notes/phase-7-report.md and notes/RESUME.md, then pick up the remaining
> Phase 7 items."**

That is enough. Everything decided is written down in `notes/`.

---

## Phases 5 and 6 are closed

`notes/phase-5-report.md` and `notes/phase-6-report.md` carry the exit criteria, the
measurements, and the things Phase 7 must not re-derive.

**Phase 6 was assembly, and it still turned up four defects** — three of them reachable only
from a real host, and one a cold `docker compose up` that had stopped building entirely.
None was a mistake in new code. All four were about paths that had stopped being exercised.

**Phase 7 is measurement, and the seal is the point.** Break it once, record what it says,
and do not retune against it. The most credible number in the submission is credible
precisely because nothing was tuned to produce it.

---

## Phase 8 cleanup list — record only, do not act before Phase 8

**The line: anything about how the project was managed goes; anything about how the system
works or why it works stays.**

Delete before the repo goes public:

- `BUILD.md` — an internal plan with dates and phase slots. Not part of the deliverable.
- `notes/RESUME.md` — this file. A session handoff, not evidence.
- `notes/conventions.md` — how the work was done, not how the system works.
- `notes/phase-5-report.md`, `notes/phase-6-report.md`, `notes/phase-7-precommitment.md` —
  **decide deliberately.** The reports are phase-gate artifacts and read as management. The
  pre-commitment is different: it is *evidence* that the operating point was fixed before
  the test set was read, and deleting it would remove the only proof of that ordering. Keep
  the pre-commitment; fold anything worth keeping from the reports into the README.

Keep, and link from the README, because they are evidence:

- `notes/schemas.md` — where the data schemas come from, with sources
- `notes/failure-modes.md` — what this gets wrong, and how the mistakes were found
- `notes/threshold.md` — the operating point, calibration, why Wilson
- `notes/injection.md` — untrusted input, and the correction
- `notes/pricing.md` — the cost band, its sources and its date
- `notes/decisions.md`, `notes/metrics.md`, `notes/distribution.md`,
  `notes/worked-examples.md`
- `notes/spikes/` and `notes/measurements/` — the raw results, including the experiments
  whose conclusions turned out to be wrong

Also: **no schedule or pacing references** anywhere — commit messages, file contents or
documentation. Commit messages describe the change and the reasoning.

## Standing reminders

- **Flip the repo public before 5 Sept.** It is private now; the panel cannot see a
  private repo. Do it on the 4th, not the 5th.
- **`data/test` has been read, once.** The seal is broken and `.unsealed` records it.
  `tests/test_seal.py` still enforces the sha256 map, which is what keeps the reported
  numbers tied to specific bytes now that the marker is gone.
- **Do not re-score `data/test` and do not retune against it.** It has been reported.
- **Never commit `.env`.** `tests/test_secrets.py` fails the build if it is tracked, or
  if any tracked file grows a credential-shaped string.
- `difficulty` is still a no-op flag. Wire it in Phase 7 or drop it — do not ship
  something that lies about what it does.

## Where the reasoning lives

| file | what it answers |
|---|---|
| `notes/decisions.md` | every design choice and what was rejected |
| `notes/threshold.md` | the operating point, calibration, the prior |
| `notes/metrics.md` | metric definitions, orphan scoring semantics |
| `notes/distribution.md` | case-type shares, train/test prior shift |
| `notes/failure-modes.md` | what this gets wrong, honestly |
| `notes/schemas.md` | where the data schemas come from, with sources |
| `notes/spikes/` | the two LLM spikes, scripts and raw results |
