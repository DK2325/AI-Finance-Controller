# Resume here

Last updated partway through Phase 8. Read this first when picking the build back up.

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

**Phases 0–7 complete.** Phase 8 is next: README polish, video, rehearsal, cleanup.

**Phase 7 is closed.** The seal is broken, scored, and reported; do not re-score
`data/test`.

| phase | state |
|---|---|
| 0 foundation, Compose, schema, isolation lint | ✅ |
| 1 synthetic generator, held-out design | ✅ |
| 2 eval harness, risk–coverage curve | ✅ |
| 3 blocking, exact + fuzzy matching, features | ✅ |
| 4 classifier, calibration, operating point | ✅ tagged `v1-working` |
| 5 LLM exception layer + audit trail | ✅ report deleted; findings live in notes/failure-modes.md |
| 6 API, frontend, chaos mode | ✅ report deleted; findings live in the README |
| 7 scale, sealed test set, failure analysis | ✅ see notes/phase-7-report.md |
| **8 README, video, rehearsal** | **← in progress** |

## Carried into Phase 8 — the short version

**All three risks carried out of Phase 5 are now closed.** The operating point is confirmed
against held-out data, the decoder stall is measured and closed, and the LLM-bound volume
is measured rather than estimated. The list below is kept because a reader tracing how the
numbers moved needs it; nothing in it is still open.

| carried risk | outcome |
|---|---|
| operating point unconfirmed (0.9989 vs 0.9564) | **closed** — 0.9564 pre-committed, sealed set gives 62.91% at 99.9037% |
| decoder stall open, cause unknown | **closed** — 2.38% over 168 calls, 0 surviving both attempts |
| LLM-bound volume uncertain | **closed** — measured at three deterministic shares, 51.06% / 65.42% / 69.77% |

### What Phase 8 must not undo

1. **Do not re-score `data/test` and do not retune against it.** It was read once. A second
   scored run is not out of sample, whatever it says.
2. **Do not fix the abstention defect or build the `refund_netted` subset-sum pass.** Both
   are diagnosed in `notes/failure-modes.md` and deliberately left. A fix validated against
   the batch that exposed it is tuned to that batch; they belong against a fresh batch,
   after submission.
3. **Do not quote 99.9037% without 99.2369% beside it.** The two batches disagree and the
   larger one is below the floor. Every document that carries one carries both.

### The original Phase 5 note, for provenance

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

### All Phase 7 exit criteria met

| criterion | how |
|---|---|
| test-set metrics reported, unretuned | 62.91% at 99.9037%, scored once at 0.9564 |
| held-out types separately and honestly | 68.00% [61.98%, 73.47%] and 0.00% [0.00%, 1.88%], never averaged |
| 25,000-row run, throughput and ₹ cost | 105 settlements/s named machine, ₹22.15 – ₹36.76 |
| per-case-type confusion matrix | README table, report §5 with intervals, `sealed_test.json` |
| unit economics paragraph | **data produced, paragraph deliberately not written** — BUILD.md's Phase 7 note says the written interpretation is the human's own work. Measured half is in the README; `N` and `X` named as inputs |
| `notes/failure-modes.md` written and honest | nine measured entries plus three general findings |

**Cut as planned:** the Settlement Q&A layer. BUILD.md ranked it last.

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

on the sealed set, at the pre-committed point
  matched + exceptions == 4,950 settlements, exactly once each
  deterministic share  69.77% of exceptions never reach a model
  reason-code actionability 97.55%  (95% CI 96.74%-98.16%, 1,791 of 1,836)
    weakest code       AMBIGUOUS_CANDIDATES 26.7% -- blocking recall, not a labelling bug
  cost per 1,000 settlements  Rs 0.68 - Rs 1.14   (measured, whole population)
  review load per 1,000        370.9 reviewed, 629.1 removed

on data/train, for comparison
  precision            99.96%  (95% CI 99.77%-99.99%, 1 false in 2,497) at 0.9989
  deterministic share  51.06%   cost Rs 1.83 - Rs 3.03 per 1,000

cold clone             165s build --no-cache, 9s up, 3s first answer  (was claimed 29s)
                       10s total with the image already built
live site              schema fixed and REDEPLOYED; confirmed storing to postgres,
                       six tables present, footer shows v1-test / data/test
throughput             105 settlements/s at 24,750 rows, AMD Ryzen 5 5600H, six cores
                       reason job 10.1 rpm; parse 27.2 rpm -- quote the job, not "the" rate
tests                  535 total, ruff clean. What a given machine sees depends on
                       what it has, and all three counts are honest:
                         535 passed            here -- dataset present, Postgres up
                         507 passed, 28 skip   fresh clone after `docker compose up`
                         506 passed, 29 skip   fresh clone, nothing started
                       The 28 are tests/test_model.py, which needs
                       runs/_datasets/train.csv -- a gitignored regenerable
                       intermediate, so they skip until `ledgerloop train` has run.
                       The 29th is the audit-trail round trip, which skips when no
                       Postgres is reachable. Quote 507/28 to a reviewer: it is what
                       the documented setup actually produces.
                       Wall clock depends on whether a half-open 5432 is present --
                       each DB connect then costs the bounded 5s x 2 resolved addresses.
                       Not to be confused with the earlier 507: an unrelated episode
                       where the total was reported as 507 and the count was partly
                       luck -- failure-modes.md, "Guards that passed for the wrong
                       reason". The 507 above is a skip-adjusted count, not that one.
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

> **"Read notes/conventions.md and notes/RESUME.md, then start Phase 8."**

That is enough. Everything decided is written down in `notes/`.

### Phase 8

**Done:**

1. **Cleanup, in part.** `BUILD.md` was stripped to the specification rather than deleted —
   33 files cite it by name as the reason a rule exists, and orphaning 64 citations to
   remove a calendar was the wrong trade. What went: the operating rules, the gate
   ceremony, the dates, the schedule, the slip plan. What stayed: thesis, architecture
   rules, data contracts, repository layout, database rules, out-of-scope, and the numbered
   requirements the code quotes. `notes/phase-5-report.md` and `notes/phase-6-report.md`
   deleted.
2. **The `difficulty` flag needed no decision** — it was already removed during Phase 6,
   and `tests/test_cli.py` asserts it is refused rather than ignored. The entry in this file
   was stale. What was *not* fixed: the README still documented the flag in its CLI block,
   so the first command a reviewer would copy did not run.
3. **README read end to end.** Findings and fixes are listed below.

4. **The cold clone check — done, and it found something.** `git clone` into a scratch
   directory, `docker compose build --no-cache`, `up`, hit every endpoint. Everything
   worked: two services, no `.env`, all ten native dependencies loading, frontend on 3000
   and 8000, both seeded runs visible. **And the README's cold-start timing table was wrong
   by 5.7×** — 165s to build from a fresh clone against a claimed 29s. Three of the five
   rows were right; the two that were wrong were the two that require starting from
   nothing, which is the condition nobody starts from twice. Corrected in the README and
   recorded in `notes/failure-modes.md`.
5. **The provenance finding is filed as a third guard**, and named as worse than the two
   above it: those failed the moment they were finally asked to work, so change exposed
   them. A job with no fields to check reports zero failures over any volume, forever —
   nothing will ever surface it, and there is nothing to fix, because the instrument is
   correct.

6. **A real defect on the live site, found by using it, fixed and verified.** Approving
   an exception returned *"stored in file, postgres schema mismatch (ProgrammingError) --
   migrations may not have run"*. Cause: migrations ran in `docker/entrypoint.sh`, invoked
   only by compose's `entrypoint:` override; the hosted deployment ran the image's `CMD`
   and applied nothing. Merging the two Dockerfiles removed the duplicated *image* and left
   a duplicated *invocation*. Fixed by deleting the duplicate rather than synchronising it:
   `api.main` applies migrations on start, non-fatally, in both paths. `/health` now
   reports `schema` beside `database`, because `SELECT 1` was returning true against a
   database with no tables in it. Verified from an empty database — six tables, the
   append-only trigger refusing both UPDATE and DELETE, an approval returning
   `stored_in: postgres`, no fallback file. Two regression guards, both checked by
   reintroducing the defect.

   **Note for the video:** the live site needs a redeploy for this to take effect there.
   Confirm `/health` shows `schema: ready` before recording anything that touches the
   review queue.

   **Verifying that fix found a worse one.** The suite stopped finishing — not failing,
   stopping. `docker compose down` had left a stale port-forward on 5432 that *accepted*
   connections to a database that no longer existed, and `create_engine` had no connect
   timeout, so `/health` waited on a server that was never going to answer. Refused is
   fast, unreachable is bounded, **half-open hangs**, and a health check that can hang
   converts a degraded dependency into an unresponsive service — the one distinction it
   exists to make. `connect_timeout` and `pool_timeout` are now set, and `/health` makes
   one connection attempt instead of two. **The suite runs in 57 seconds.**

7. **Review-queue empty states were red.** `.ev-card.absent` -- shown when blocking
   produced no bank candidate, or no invoice could be named -- used a red tint, while the
   card's own copy said *"the finding is the absence, not a gap in this screen"*. Colour is
   read before text, so it said error where nothing had gone wrong. Amber now, with a
   `--warn` left border. The genuinely red states are untouched: `.chaos-verdict.fail`, and
   the `#cliff` banner that fires only when precision drops below the 99.5% floor.

**Left -- all of it the human's own work:**

8. **Video and rehearsal.**
9. **The unit-economics paragraph.** The data is measured and in the README; the written
   interpretation is deliberately not written, per BUILD.md.
10. **The last deletion.** `notes/RESUME.md` (this file) and `notes/conventions.md`, after
   the video. Before deleting RESUME.md, rewrite the two citations to it in
   `notes/injection.md`, which quotes it as the source of a claim it then corrects.
   `BUILD.md` stays — see above.
11. **Flip the repo public.** It is private; a panel cannot see a private repo.

**Decided and not to be revisited:** the ~78 backward-looking `Phase N` references in the
kept notes stay. They resolve to numbered sections of BUILD.md rather than to a calendar,
and a sweep is a large diff for no gain.

### What the README read-through found

Every item below was a defect a reviewer would have hit, not a matter of taste.

| | |
|---|---|
| `ledgerloop generate ... --difficulty hard` | the flag was removed; the first documented command exited non-zero |
| `ledgerloop chaos --run RUN_ID` | `chaos` takes `--in <batch>`; the fourth documented command exited non-zero |
| a second Docker section | said **three** services and `copy .env.example .env`, contradicting the section above it, which correctly says two services and no `.env`. Stale from before the two Dockerfiles became one |
| "The original finding, in full" | repeated the driver-prefix finding almost verbatim, 30 lines after it |
| the provenance gate | carried *"to be re-measured on the sealed test set in Phase 7"* — a promise to a phase that had closed |
| no roadmap | BUILD.md's README order asks for one and there was none |
| the Status table | listed plan phases, with "8 README, video, rehearsal — in progress" visible to a reviewer |

Both corrected CLI commands were run before being written down.

**The provenance one is worth reading in full in the README.** The gate was never
re-measured on the sealed set, because the only LLM job that ran there writes exception
reasons and extracts no fields. Its record reads *3,343 items, 0 failed* — over **0 fields
checked**. That summary looks like evidence and is not, and quoting it would have been the
easiest available mistake in the document. It is now stated rather than quietly dropped.

### Phase 8 must not

- **Re-score `data/test`.** Read once, reported once.
- **Fix the abstention defect or add the `refund_netted` subset-sum pass.** Diagnosed and
  deliberately left; they belong against a fresh batch after submission.
- **Quote 99.9037% alone.** 99.2369% at 24,750 settlements goes with it, every time.
- **Re-seed the deployment away from `v1-test`.** The live URL now shows the same numbers
  the README reports, verified against a real image build. `tests/test_deployment.py`
  enforces it.

---

## Phases 5 and 6 are closed

Both phase reports were deleted in Phase 8. Nothing was lost: every finding in them was
already recorded in `notes/failure-modes.md` or the README, which was checked line by line
before deleting rather than assumed.

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

Resolved:

- `BUILD.md` — **kept, stripped to the specification.** The line above says management goes
  and how-the-system-works stays; BUILD.md was both, so it was split rather than judged as
  a whole. 33 files cite it by name.
- `notes/phase-5-report.md`, `notes/phase-6-report.md` — **deleted**, findings verified as
  already recorded elsewhere first.
- `notes/phase-7-precommitment.md` — **kept.** It is the only proof the operating point was
  fixed before the test set was read, and the README links to it.
- `notes/phase-7-report.md` — **kept.** It is the held-out measurement itself, and three
  files that are being kept as evidence cite it.
- `notes/RESUME.md` (this file), `notes/conventions.md` — **delete last**, after the video.

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
- **The cold clone check is not a one-off.** Run it again if the `Dockerfile` or
  `docker-compose.yml` changes. A timing or a build that depends on what is *absent* from a
  machine cannot be verified on the machine that has it.

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
