# Resume here

Last updated 23 Aug 2026, end of Phase 5. Read this first when picking the build back up.

---

## Where the build is

**Phases 0–5 complete.** Phase 6 is next: API, frontend, chaos mode.

| phase | state |
|---|---|
| 0 foundation, Compose, schema, isolation lint | ✅ |
| 1 synthetic generator, held-out design | ✅ |
| 2 eval harness, risk–coverage curve | ✅ |
| 3 blocking, exact + fuzzy matching, features | ✅ |
| 4 classifier, calibration, operating point | ✅ tagged `v1-working` |
| 5 LLM exception layer + audit trail | ✅ see notes/phase-5-report.md |
| **6 API, frontend, chaos mode** | **← next** |
| 7 sealed test set, failure analysis | — |
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

## Phase 7 — the scale run happens locally, not on Railway

**The 25,000-row scale run is a local run. Report its numbers from there.**

The Railway trial gives **1 GB RAM, 2 vCPU, 1 GB disk**. That is enough for what the
deployed instance is actually for — serving the seeded run and the one-click demo batch —
and it is not enough to generate 25,000 rows, train against them, and hold the candidate
set in memory while scoring.

Concretely, the shapes that do not fit:

| | `data/train` today | at 25,000 settlements |
|---|---|---|
| candidates held in memory | 7,305 | ~37,000, each with a 23-feature dict |
| generated batch on disk | 2.1 MB | ~11 MB |
| run artifacts (predictions + exceptions) | 1.3 MB | ~7 MB |

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
DOCUMENTED (out of sample, at the 99.5% floor)   see the caveat above
  coverage 51.31% at precision 99.5050%
MEASURED after the resolver fix, unconfirmed
  coverage 68.16% at precision 99.5031%          seal decides

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
- Repo is pushed and clean; `origin/main` at `d093ef1`

## To resume

Activate the venv and say:

> **"Read notes/RESUME.md and notes/phase-5-report.md, then start Phase 6."**

That is enough. Everything decided is written down in `notes/`.

---

## Phase 5 is closed

`notes/phase-5-report.md` has the exit criteria, the measurements, the explanation
checkpoint, and the three things Phase 6 and 7 must not re-derive. The step-by-step plan
that used to live here is history now and lives in git.

**Phase 6 is different work: assembly, not investigation.** The engine is built. If Phase 6
starts turning up findings at Phase 5's rate, that is a signal the approach is wrong rather
than a good sign — what is left is making the thing visible.

---

## Phase 8 cleanup list — record only, do not act before Phase 8

**The line: anything about how the project was managed goes; anything about how the system
works or why it works stays.**

Delete before the repo goes public:

- `BUILD.md` — an internal plan with dates and phase slots. Not part of the deliverable.
- `notes/RESUME.md` — this file. A session handoff, not evidence.

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
- **`data/test` is sealed and unread.** `tests/test_seal.py` enforces it. Phase 7 breaks
  the seal by deleting the marker in the same commit that reports the test numbers, so
  the unsealing is an auditable event.
- **Do not retune against the test set** when the seal breaks. Report what it says.
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
