# Working conventions

**Read this before making any commit in this repository.** These are standing rules, not
preferences to be re-derived. They exist here rather than in a conversation because a new
session reads files, not history.

**Internal.** This file is project management, not evidence, so it is deleted before the
repo goes public — see the Phase 8 cleanup list in `notes/RESUME.md`.

---

## Commit messages

**Describe the change and the reasoning. Nothing else.**

**No attribution trailers of any kind.** No `Co-Authored-By:`, no session links, no tool
credits. End the message at the last line of the body. If a tool's default is to append
one, that default is overridden here.

*Applied retroactively on 23 Aug 2026 with a `git filter-branch --msg-filter` over all 37
commits at the time — message bodies only, every hash changed, every tree identical. The
history is clean; keep it that way.*

**No schedule or pacing references.** Not "ahead of schedule", not "finished early", not
phase dates, not progress against a plan. The plan is internal and a reviewer never sees
it, so pacing commentary is noise at best.

The line that decides it: **anything about how the project was managed goes; anything about
how the system works or why it works stays.**

A date used as technical reasoning is fine when the date itself is incidental — rewrite
*"the repo goes public on 5 September and a committed key is permanent"* as *"the repo will
be public and a committed key is permanent"*. A rate-card date or a measurement date stays,
because it qualifies the number.

**The same rules apply to file contents and documentation**, not only to commit messages.

## Author identity

```
Dushyant Kumar <dushyant.usict@gmail.com>
```

Set repo-locally:

```
git config user.name  "Dushyant Kumar"
git config user.email "dushyant.usict@gmail.com"
```

**This lives in `.git/config`, which is not committed — a fresh clone loses it.** Re-set it
before the first commit in any new clone, or commits land under whatever global identity the
machine has. That has happened once and required a history rewrite to fix.

## Pushing

**The assistant does not push.** The shell has no way to answer a credential prompt, so
`git push` is always the human's action. Commit, then say what needs pushing.

Force-pushes are the human's call and need a reason stated. `--force-with-lease`, never bare
`--force`.

---

## The sealed test set

**`data/test/` is a held-out set. Do not read it outside a deliberate Phase 7 unsealing.**

`tests/test_seal.py` enforces this: it checks the marker exists, says it is still sealed,
that no file's sha256 has changed, and that no package outside `evals/` so much as names the
directory. It also proves it can fail, on a synthetic tampering fixture.

Breaking the seal is **the only irreversible action in this project**. When it happens:

- delete `data/test/.sealed` **in the same commit as the numbers it produced**, so the
  unsealing is an auditable event in git history rather than a claim in a README;
- report what it says, including if it is worse than the training numbers;
- **do not retune against it** — not the threshold, not blocking, not the features, not the
  calibrator.

The operating point is pre-committed in `notes/phase-7-precommitment.md`, at a commit where
the seal was still intact. Read that file from the repository before unsealing, and verify
`tests/test_seal.py` passes at commit `a733ad4`. That verification is what lets a new session
trust the pre-commitment without trusting anybody's memory.

## Isolation

`core/`, `model/`, `llm/` and `api/` must never import `datagen/` or name `truth.csv`.
`tests/test_import_lint.py` enforces it by AST, catches dynamic imports, and proves it can
fail. Only `evals/` may hold both sides.

## Money

**Integer paise everywhere.** `NUMERIC(14,2)` in Postgres, `int` in Python, and no float
arithmetic on an amount at any point between them.
`tests/test_no_float_money.py` walks the SQLAlchemy metadata and fails the build if a
`Float` appears. Formatted rupee strings are for display only; nothing computes with them.

## Secrets

**Never commit `.env`.** `tests/test_secrets.py` fails the build if it becomes tracked, if
`.env.example` grows a value, or if any tracked file contains a credential-shaped string.

Test fixtures that need a credential-shaped string assemble it at runtime — `"nvapi-" +
"F4KE" * 8` — rather than being excused from the scan. Excusing the file would leave the one
most likely to grow a real key as the one nobody checks.

---

## Two habits this project learned the hard way

Both are written up with evidence in `notes/failure-modes.md`. They are here because they
change how work is done, not only what is recorded.

**Verify, do not print success.** A script that reports `"ignored"` outside the condition it
describes tells you nothing, and cost 11 MB of generated data in a commit. Check the thing
itself: `git check-ignore -v <path>`, `grep -q`, an assertion. A success message that is not
conditional on success is the smallest version of the recurring mistake in this project.

**Count, do not derive.** When a number can be derived from another number or counted
directly, count it. Three defects here came from derivations that were *nearly* right — the
share of exceptions reachable without a model, settlements matched at a threshold, and
LLM-bound exceptions. Each rested on a claim about behaviour, and claims like that are the
ones nobody re-examines because they sound like definitions.
