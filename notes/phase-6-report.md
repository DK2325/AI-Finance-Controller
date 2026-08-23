# Phase 6 — API, frontend, chaos mode

**Complete, 23 August 2026.**

Panels do not clone repositories. This phase produced the thing they click.

---

## Exit criteria

| BUILD.md requires | state |
|---|---|
| Live public URL, seeded with a completed run | ✅ [ai-finance-controller-production.up.railway.app](https://ai-finance-controller-production.up.railway.app) — seeded from the repository, no training, no database write |
| `docker compose up` and a stranger understands it in 60 seconds | ✅ 13s from an existing image, ~42s building from source; verified on a genuine cold start |
| Demo batch loads and completes in one click | ✅ 495 settlements → 327 matched, 168 exceptions |
| Operating-point slider updates metrics without a full re-run | ✅ structurally — every point is precomputed in `curve.json` |
| Chaos mode runs live and degrades gracefully | ✅ 8 corruptions, coverage to 5.66%, **zero wrong matches in all eight** |
| Readable on a laptop in a shared video call | ✅ direction carried by arrows and card edges, not by line weight |

**584 tests, ruff clean.**

---

## Four screens

```
Dashboard      the operating-point explorer -- the thesis made interactive
Review queue   every exception with its three source rows, side by side
Run a batch    one-click demo, or upload three CSVs
Chaos          break it on purpose, in plain English
```

### The explorer is honest about three things

**It offers only points the system can reach.** 23 operating points, not a continuum.
Clicking snaps. A gliding slider would imply a resolution isotonic calibration does not
have — 99.7% of candidates share an exact calibrated probability — which is the
interaction-design version of reporting 99.5031% from four events.

**Tick spacing is the step size.** A step admitting 732 settlements is drawn 732× wider
than one admitting 1. Measured on the seeded run: `2495 · 1×6 · 732 · 1×6 · 353 · 1 · 25 ·
1×3 · 16 · 12 · 51`. Drawing them evenly would be a lie about the shape of the model.

**Precision comes with its interval and its counts.** `0 wrong of 361` reads first;
`100.00%, 95% CI 98.95–100%` qualifies it. Where the floor sits inside the interval the
screen says so in words — *at zero errors the estimate cannot distinguish holding the floor
from missing it.*

### The divergence is shown, not implied

Three consequences, each with a sparkline across every operating point and the current
position marked. As coverage rises, **review workload and inference cost fall while wrong
money rises**. The lines visibly run against each other, which is the entire argument for
why the merchant chooses the point rather than the system choosing for them.

---

## What chaos proved

Eight corruptions the generator never produces, applied to a loaded batch. Every one at
100% of bank rows:

```
baseline                      327 matched   66.06%   0 wrong
unmodelled fee structure       28            5.66%   0 wrong
two payouts merged              83           16.77%   0 wrong
narrations truncated           100           20.20%   0 wrong
a third bank's grammar         146           29.49%   0 wrong
names transliterated           146           29.49%   0 wrong
dates a day early              161           32.53%   0 wrong
UTRs split across groups       270           54.55%   0 wrong
amounts in the narration       333           67.27%   0 wrong
```

**Coverage falls as far as 5.66%. Zero wrong matches in all eight.** Under conditions it
was never built for, the system stops matching rather than starts guessing.

Three deliberate choices in that suite:

**Judged on the count, not the percentage.** At 5.66% coverage a precision figure is a
ratio over 28 rows. 1 wrong in 3 is 66.7% and means almost nothing. The screen says so.

**One corruption makes matching *easier* and it stays in.** `currency_symbol_noise` takes
coverage to 67.27%, because writing the amount into the narration hands invoice inference a
signal the clean row lacked. A suite containing only corruptions that hurt is a suite
selected to make the system look robust — and a reviewer who noticed that would be right to
discount the whole table. **That one row is what makes the other seven credible.**

**`wrapped_utr` demonstrates our own documented limitation.** It injects exactly the case
`notes/failure-modes.md` names as where a language model would beat the regex — and where
the provenance gate's whole-digit-run rule would reject the model's correct answer, because
`3000 0000 4412` is not a 12-digit run. The screen explains that when it runs. A limitation
you can demonstrate on demand is worth more than one you can only describe.

Free text is mapped by **deterministic keywords first**; the model is consulted only where
they find nothing, because it cannot improve a correct answer and every call is a chance to
fail during a live demonstration. The response says which path answered.

---

## Four defects found by deploying, three of them unreachable locally

| | why local testing could not reach it |
|---|---|
| `DATABASE_URL` scheme means the **driver** to SQLAlchemy; bare `postgresql://` means psycopg2 | absent locally; compose set it explicitly with `+psycopg` |
| Railway sets `DATABASE_URL` to `""` when a reference fails; `os.environ.get(k, default)` returns the empty string | no managed host to inject it |
| `python:3.12-slim` ships no OpenMP runtime; LightGBM links against it | a dev machine has libgomp, and so does every non-slim base image |
| a cold `docker compose up` **did not build** | the path had not been run end to end in weeks |

The first three were found within an hour of having a real host. **A wheel installing is not
the same as its shared libraries being present**, and the gap is invisible until something
calls into the native layer — so `api/selfcheck.py` now *exercises* ten native dependencies
(training a two-row model rather than importing LightGBM) as a `RUN` step in the Dockerfile.
A missing library fails the **build**, not a screen.

### The fourth is the one worth keeping

Three defects at once, every one a fix that had been applied to the hosted image and not
carried across.

> **Nothing had broken. The two paths had simply stopped being the same thing, one correct
> change at a time.**

Each change was right for the file it touched. Nobody was careless. The response was not to
be more careful — it was to delete the duplicate. One `Dockerfile` now, built by both
compose and the host; the API already served the frontend, so the third container went too.

> **Two things that must stay identical will not, unless something makes them the same
> thing.** A test that fails on divergence is the weaker version; having one thing is the
> stronger one.

With the corollary that matters, because "add a test" always feels available: **reach for
the test when the duplication is forced, and for deletion when it is not.** `resolve_indices`
and `resolve` genuinely cannot be merged — different callers, different data shapes, both
hot paths — so there the property test is the strongest available answer. The two
Dockerfiles had no such excuse.

---

## Deviations from BUILD.md, recorded in BUILD.md

1. **Static HTML/CSS/JS, not Next.js + Tailwind + shadcn/ui.** A React toolchain puts an
   `npm install` and a build step inside the path whose exit criterion is "works on a
   stranger's machine". Three screens and one slider need no routing, SSR or component
   library.
2. **Two compose services, not three.** The API serves the frontend; the third container was
   redundant, and keeping a separate image for it is what caused the drift above. Port 3000
   is still published, because BUILD.md and the README name it and a reviewer who typed it
   should not meet a dead port.
3. **`--difficulty {easy,hard}` removed.** It was accepted and did nothing from Phase 1. A
   flag that lies about what it does is worse than an absent flag, because it invites
   someone to rely on it.

---

## Carried into Phase 7

**The operating point is still unconfirmed.** Documented 0.9989 / 51.31%; measured after the
resolver fix, 0.9564 / 68.16%. Every document still states the first. 16.6 of the 16.85
points rest on a single 196-candidate block calibrating where it did — the threshold itself
sits in a 0.028-wide gap and cannot slip, but the result is one discrete bet. **The seal
decides.**

**The decoder stall is open.** ~1 call in 5–6, cause unknown, retry has rescued every one
across a few dozen calls. Phase 7 runs roughly 20× that volume, and retries compete for the
same token bucket under concurrency.

**The scale run happens locally.** The Railway trial is 1 GB RAM, 2 vCPU, 1 GB disk — enough
to serve the seeded run and the demo batch, not enough to generate 25,000 rows and hold
~37,000 candidates while scoring. Report throughput with the machine named.

**Re-run the cold start in Phase 8.** This path has now been exercised exactly once, and
Phase 7 touches the model artifact and the run directory — two of the three things that
broke it.
