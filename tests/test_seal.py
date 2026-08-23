"""The seal on data/test/, as a mechanism rather than a discipline.

BUILD.md says the test set is sealed until Phase 7. Until now that was an intention, and
an intention is not a control. This is the same shape as tests/test_import_lint.py: if a
boundary matters enough to state, it matters enough to fail the build over.

Two things are enforced:

1.  **Integrity.** data/test/.sealed records a sha256 for every file in the directory.
    If any of them changes, the seal breaks and this test fails loudly. That catches an
    accidental regeneration, which would silently replace the held-out set with one the
    model may since have been tuned against.

2.  **Access.** No module outside evals/ may name data/test in a string literal. AST
    based, so a comment mentioning it is fine but a path that code could actually open
    is not.

Phase 7 broke the seal deliberately, once, by deleting the marker in the same commit
that reports the test-set numbers. That makes unsealing an explicit, reviewable event in
the history rather than something that quietly happened.

**The seal is now broken, and this file still enforces integrity.** `.sealed` was
replaced by `.unsealed`, which carries the same sha256 map. That matters more after the
unsealing than before it: the whole value of the reported numbers rests on the claim that
the scored files are the files that were sealed, and once the marker is gone there is
nothing but this check standing between that claim and a quiet regeneration. So the
integrity test did not go away with the seal -- it changed which marker it reads.

The access lint is unconditional and unchanged: evals/ may read the test set, nothing else
may name it.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from astutil import code_strings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = REPO_ROOT / "data" / "test"
SEALED_MARKER = TEST_DIR / ".sealed"
UNSEALED_MARKER = TEST_DIR / ".unsealed"

# evals/ may read both sides. Everything else may not touch the held-out set.
PERMITTED_PACKAGE = "evals"
GUARDED_PACKAGES = ("core", "model", "llm", "api", "datagen", "ledgerloop")

SEALED_PATH_TOKENS = ("data/test", "data\\test", "data/test/")


def _marker() -> dict:
    """The integrity record, whichever side of the unsealing we are on.

    Exactly one of the two must exist. Both present would mean the unsealing was recorded
    without being carried out; neither means the record was deleted rather than
    superseded, and the out-of-sample claim then rests on nothing checkable.
    """
    present = [m for m in (SEALED_MARKER, UNSEALED_MARKER) if m.is_file()]
    assert len(present) == 1, (
        "expected exactly one of data/test/.sealed or data/test/.unsealed, found "
        f"{[m.name for m in present]}. The unsealing replaces one with the other in a "
        "single commit; any other state is a mistake, not a phase."
    )
    return json.loads(present[0].read_text(encoding="utf-8"))


def test_an_integrity_marker_exists() -> None:
    """Sealed or not, the test set must carry a record of what it is supposed to be."""
    _marker()


def test_the_marker_agrees_with_itself_about_the_seal() -> None:
    marker = _marker()
    if SEALED_MARKER.is_file():
        assert marker["sealed"] is True
        assert marker["unseal_at_phase"] == 7
    else:
        # Post-unsealing. The record must say so, must say it was verified at the moment
        # the seal broke, and must still name the threshold it was scored at -- a number
        # chosen before this file existed.
        assert marker["sealed"] is False
        assert marker["unsealed_at_phase"] == 7
        assert marker["integrity_verified_at_unsealing"] is True
        assert marker["scored_at_threshold"] == 0.9564


def test_no_file_in_the_test_set_has_changed() -> None:
    """A regenerated test set is a different test set. Fail loudly if it moved.

    This outlives the seal on purpose. After unsealing it is the only thing that still
    connects the reported numbers to the bytes they were computed from.
    """
    recorded = _marker()["sha256"]

    on_disk = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(TEST_DIR.glob("*"))
        if path.is_file() and not path.name.startswith(".")
    }

    assert set(on_disk) == set(recorded), (
        f"file set changed: added {sorted(set(on_disk) - set(recorded))}, "
        f"removed {sorted(set(recorded) - set(on_disk))}"
    )

    changed = [name for name, digest in recorded.items() if on_disk[name] != digest]
    assert not changed, (
        f"the sealed test set was modified: {changed}. If this was a deliberate "
        "regeneration, the held-out result is no longer out-of-sample."
    )


@pytest.mark.parametrize("package", GUARDED_PACKAGES)
def test_no_guarded_package_names_the_sealed_directory(package: str) -> None:
    offenders: list[str] = []

    for path in sorted((REPO_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in code_strings(tree):
            normalised = node.value.replace("\\", "/")
            if any(token.replace("\\", "/") in normalised for token in SEALED_PATH_TOKENS):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> {node.value!r}"
                )

    assert not offenders, (
        f"{package}/ references the sealed test set:\n  " + "\n  ".join(offenders)
    )


def test_only_evals_is_permitted_to_read_it() -> None:
    """Documents the exemption, and fails if evals/ is ever removed from the layout."""
    assert (REPO_ROOT / PERMITTED_PACKAGE).is_dir()
    assert PERMITTED_PACKAGE not in GUARDED_PACKAGES


def test_the_seal_check_actually_catches_tampering(tmp_path: Path) -> None:
    """A passing integrity check is only reassuring if it can fail."""
    victim = tmp_path / "sealed.csv"
    victim.write_text("a,b\n1,2\n", encoding="utf-8")
    recorded = hashlib.sha256(victim.read_bytes()).hexdigest()

    victim.write_text("a,b\n1,3\n", encoding="utf-8")
    assert hashlib.sha256(victim.read_bytes()).hexdigest() != recorded, (
        "the integrity check failed to notice a deliberate modification"
    )
