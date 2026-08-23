# Resume here

Written 23 Aug 2026, 00:15. Read this first when picking the build back up.

---

## Where the build is

**Phases 0–4 complete.** Phase 5 is planned but not started — `llm/` contains one
docstring and nothing else.

| phase | state |
|---|---|
| 0 foundation, Compose, schema, isolation lint | ✅ |
| 1 synthetic generator, held-out design | ✅ |
| 2 eval harness, risk–coverage curve | ✅ |
| 3 blocking, exact + fuzzy matching, features | ✅ |
| 4 classifier, calibration, operating point | ✅ tagged `v1-working` |
| **5 LLM exception layer + audit trail** | **← in progress, steps 1–3 of 7 done** |
| 6 API, frontend, chaos mode | — |
| 7 sealed test set, failure analysis | — |
| 8 README, video, rehearsal | — |

**Six days ahead of schedule.** BUILD.md put Phase 4 on 29 Aug; it finished on the 23rd.
Buffer is 5 Sept and should stay unspent.

## Headline numbers as of now

```
matcher (rules only)        76.48% coverage at 98.67% precision
v1 calibrated, out-of-sample
  at 99.5% floor            51.31% coverage at 99.5050% precision, 3 false matches
  at 99.0% floor            77.65% coverage at 99.1276% precision, 8 false matches
calibration                 isotonic, ECE 0.0104 out of sample
throughput                  25,000 rows in 6.4s, growth exponent 1.59
tests                       204 passing, ruff clean
```

## Environment: nothing to set up

- `.env` exists with a working `NVIDIA_API_KEY`. Verify with
  `python -c "from ledgerloop.config import key_fingerprint; print(key_fingerprint())"`
- venv at `venv/`, Python 3.12.10, all dependencies installed
- Docker Desktop must be **running** for anything touching Compose
- Repo is pushed and clean; `origin/main` at `d093ef1`

## To resume

Activate the venv and say:

> **"Read notes/RESUME.md and start Phase 5."**

That is enough. Everything decided is written down in `notes/`.

---

## Phase 5 progress

| step | state |
|---|---|
| 1 reason-code enum, frozen ledger | ✅ `2fd55c4` |
| 2 provider seam, mock as fault injector, rate limiter | ✅ `ce930fd` |
| 3 provenance gate + the injection correction | ✅ `831f4c8` |
| 4 three versioned prompts and their schemas | ← next |
| 5 handler: batching, cache, retry-then-exception | |
| 6 `enumerate_exceptions()` retrofit + audit records everywhere | |
| 7 eval reports reason-code accuracy, ₹ per 1,000 rows, injection fixture | |

**Steps 1–4 need no key and no network.** Step 5 is the first live call.

**Open measurement from step 6:** the "43% of exceptions are deterministic" figure came
from a probe, not from real exception objects — those did not exist in code until the
step 6 retrofit. Re-measure it there. If it is 30% rather than 43%, LLM volume and run
time both change, and the README line that carries it is already marked provisional.

**Invariant to assert in step 6:** matched + exceptions == total settlements. Every
settlement accounted for exactly once, no gaps, no double counting. That is what makes
"the exceptions it could not resolve" an honest claim rather than a filtered list.

---

## Phase 5 plan, already agreed

Three prompts, a provider seam, and a gate that makes the LLM's output verifiable.

1. **`LLMProvider` protocol**, with `MockProvider` (no key, keeps `--mock-llm` honest)
   and `NvidiaProvider`. Selected by `LLM_PROVIDER` env var, so a NIM outage on 3 Sept is
   a config change rather than a crisis. Adding a provider is a new class.

2. **The provenance gate — the centrepiece.** Every extracted field is verified against
   its own source narration before it is allowed to matter: the UTR digits appear in
   *that* narration, the counterparty substring is present, amounts match values already
   known. Failures route to `FIELD_PROVENANCE_FAILED` and never reach the ledger. Regex
   and substring only, no second model call.

   *Why it exists:* the batching spike returned 120/120 items with every id echoed and
   order perfectly stable — and a UTR still crossed from one item to another. Structural
   checks verify the envelope, not the contents. Measured at ~1 in 200, which at
   25,000-row scale is ~35 mis-attributed fields per run: too rare for sampling to catch,
   certain to happen in production.

3. **Structured output is locked**: `response_format={"type":"json_schema", strict}`
   only. Measured 0.0% schema failures across 50 calls against 4.0% unconstrained.
   `nvext.guided_json` does not exist on the hosted endpoint. Batch size **20**.

4. **Reason-code enum**, with failure categories kept permanently distinct — a 429 and a
   malformed response demand different responses:
   - deterministic, no LLM: `NO_CANDIDATE`, `NO_INVOICE_LINK`, `SUBSET_SEARCH_CAPPED`
   - LLM path: `BELOW_THRESHOLD`, `LOW_CONFIDENCE`, `AMBIGUOUS_CANDIDATES`
   - failures: `LLM_MALFORMED_RESPONSE`, `LLM_SCHEMA_INVALID`, `LLM_RATE_LIMITED`,
     `LLM_TRANSPORT_FAILED`, `FIELD_PROVENANCE_FAILED`, `LOW_PARSE_CONFIDENCE`

5. **Three versioned prompts** in `llm/prompts/`, each declaring its own
   `enable_thinking` in front-matter (default **off** — thinking costs 5.4x the output
   tokens for identical structured output). The setting lands in the audit record beside
   the prompt version, and is never changed mid-run.

6. **Rate limiting**: 36 rpm token bucket. The free tier caps at 40 rpm and it is a
   throughput cap, not a quota — nothing to exhaust. Pacing produced zero 429s across 150
   calls where concurrency 4 produced four.

7. **Caching**, keyed on `sha256(input) + prompt_version + model_name + thinking_flag`,
   so a prompt edit invalidates correctly. Removes the live-API dependency from the pitch
   video.

8. **Audit records for every decision from every layer**, including the deterministic
   ones. Adds `model_name`, `prompt_version`, `thinking_enabled`, `provider`,
   `cache_hit`, `token_cost_inr`.

9. **Prompt-injection fixture.** Bank narrations are untrusted input.

   **Provenance catches the model's error. Layer ordering catches the adversary's
   intent.** Two controls, two threats — this is the framing for the README and the
   pitch. The gate re-verifies each extracted field against its own source narration,
   which catches mis-attribution (~1 in 200 when batching, envelope perfect). What makes
   *injection* inert is architecture rule 2: an extracted UTR becomes a match only if a
   gateway settlement independently carries it — the attacker does not control the
   gateway file — and only if the classifier then agrees on amount, date and
   counterparty.

   The fixture is `Fault.OBEYS_INJECTION`: the mock *complies* with the attacker, and the
   test asserts the system's output is unchanged. Assuming the model has already lost is
   stronger than any pass rate against hostile prompts.

   > **An earlier version of this file said the opposite** — that injected instructions
   > are not in the source narration and so cannot pass a substring check. That was
   > wrong: being in the narration is what makes text injection. Corrected 23 Aug;
   > `notes/injection.md` carries the full trace and the residual risk. A test asserts
   > the gate *passes* an injected UTR so the claim cannot come back by accident.

**Exit criterion to hold to:** `--mock-llm` runs the whole pipeline with **no key
present**, asserted by a test that unsets the environment variable.

---

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
