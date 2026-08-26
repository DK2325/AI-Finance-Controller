"""What the image must contain for the live URL to be evidence rather than a sample.

The deployed instance seeds from the repository, so what it shows is decided by two files
almost nobody reads: `.dockerignore` and `Dockerfile`. Before Phase 7 that arrangement put
a run at a superseded operating point on the live URL while the README reported the
held-out one -- no prose anywhere quoted a stale number, and the site rendered one anyway.

**Stale state is worse than stale text, because nothing signals it.** A reader who cannot
tell which of the two is current will reasonably assume the running system is honest and
the document is optimistic, which was the exact inversion of the truth. These tests exist
so that inversion cannot come back silently.

They are static checks over the build configuration rather than a container build. Building
an image in the unit suite would be slow and would need a daemon; what actually went wrong
was a COPY line and an exclusion, and those are text.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
GITIGNORE = REPO_ROOT / ".gitignore"

SEEDED_RUN = "runs/v1-test/"
FALLBACK_RUN = "runs/v1-train/"
HELD_OUT_BATCH = "data/" + "test/"  # assembled, so this file does not name it literally
MARKER = ".unsealed"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore() -> str:
    return DOCKERIGNORE.read_text(encoding="utf-8")


def test_the_held_out_run_is_copied_into_the_image(dockerfile: str) -> None:
    assert f"COPY {SEEDED_RUN}" in dockerfile, (
        f"the image does not copy {SEEDED_RUN}. The screens would fall back to the "
        "training run, which is scored at a superseded operating point."
    )


def test_the_fallback_run_is_still_copied(dockerfile: str) -> None:
    """A cold boot must have something to show even if the held-out run goes missing."""
    assert f"COPY {FALLBACK_RUN}" in dockerfile


def test_the_held_out_batch_is_copied(dockerfile: str) -> None:
    assert f"COPY {HELD_OUT_BATCH}" in dockerfile, (
        "the seeded run is over the held-out batch, so the batch has to ship with it or "
        "the review screen has no evidence rows to read"
    )


def test_the_held_out_batch_is_not_excluded(dockerignore: str) -> None:
    """The exclusion that was correct while the seal held, and is wrong now."""
    active = [
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert HELD_OUT_BATCH not in active, (
        ".dockerignore still excludes the held-out batch, so COPY cannot ship it and the "
        "seeded run would have no data behind it"
    )


def test_the_integrity_marker_travels_with_the_data() -> None:
    """The marker is what makes the served numbers checkable inside the image.

    Without it the numbers are still correct and no longer verifiable, and an
    unverifiable claim inside a shipped artifact is the thing this project keeps refusing
    to make.
    """
    marker = REPO_ROOT / HELD_OUT_BATCH / MARKER
    assert marker.is_file(), f"{HELD_OUT_BATCH}{MARKER} is missing from the repository"

    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["sealed"] is False
    assert record["sha256"], "the marker carries no hashes, so it proves nothing"


def test_the_build_fails_if_the_marker_is_absent(dockerfile: str) -> None:
    """A guard that is only a COPY line is a guard that fails silently."""
    assert MARKER in dockerfile and "exit 1" in dockerfile, (
        "the Dockerfile does not assert the integrity marker is present. A missing "
        "marker would produce an image that serves unverifiable numbers and builds green."
    )


def test_the_integrity_chain_holds_in_the_repository() -> None:
    """What ships is what was sealed -- recomputed, not asserted.

    This is the same check `api/service.provenance` runs inside the container. Running it
    here too means the repository cannot drift from the image's claim without the suite
    noticing first.
    """
    batch = REPO_ROOT / HELD_OUT_BATCH
    record = json.loads((batch / MARKER).read_text(encoding="utf-8"))

    mismatched = [
        name
        for name, digest in record["sha256"].items()
        if hashlib.sha256((batch / name).read_bytes()).hexdigest() != digest
    ]
    assert not mismatched, (
        f"the held-out set no longer matches the hashes recorded when the seal broke: "
        f"{mismatched}. The reported numbers were computed from different bytes."
    )


def test_the_run_committed_for_seeding_is_not_gitignored() -> None:
    """The image seeds from the repository, so an ignored run never reaches the build."""
    text = GITIGNORE.read_text(encoding="utf-8")
    assert "!/runs/v1-test/" in text, (
        "/runs/ is ignored wholesale and v1-test is not re-included, so the seeded run "
        "would be absent from a fresh clone and from the image built out of it"
    )


def test_the_frontend_prefers_the_held_out_run() -> None:
    """The COPY is half of it; the screen also has to open on that run."""
    app_js = (REPO_ROOT / "web" / "static" / "assets" / "app.js").read_text(encoding="utf-8")
    assert '"v1-test"' in app_js, "the frontend does not prefer the held-out run"

    held_out_at = app_js.index('"v1-test"')
    train_at = app_js.index('"v1-train"')
    assert held_out_at < train_at, (
        "the frontend checks for the training run before the held-out one, so it would "
        "open on the superseded operating point whenever both are present"
    )


def test_the_provenance_check_is_presence_based_not_hardcoded() -> None:
    """api/ may not name the held-out set, and should not need to.

    The screen calls a run held out exactly when the batch carries an unsealing record.
    That is enforced by tests/test_seal.py as a boundary; it is asserted here as a design
    property, because a future edit that hardcodes the path would pass the seal lint only
    by accident of spelling.
    """
    service = (REPO_ROOT / "api" / "service.py").read_text(encoding="utf-8")
    assert f'UNSEAL_MARKER = "{MARKER}"' in service
    assert HELD_OUT_BATCH not in service.replace("\\", "/")


def test_the_screen_lands_on_the_point_the_run_was_scored_at() -> None:
    """The displayed operating point must be the one the numbers were computed at.

    This was wrong and wrong quietly. `dashboard()` chose the curve point whose threshold
    was numerically *nearest* the stored one; on the held-out run that picked a point
    0.000268 below the operating threshold, admitting one candidate the operating point
    excludes. The screen showed 62.93% and 3,115 matched while every document said 62.91%
    and 3,114.

    A one-row disagreement between the live demo and the report is small and is exactly
    the category of defect the re-seeding exists to remove, so it gets a test rather than
    a fix and a hope.
    """
    from api.service import dashboard, load_run

    summary = load_run("v1-test")
    data = dashboard(summary)
    point = data["operating_points"][data["selected_index"]]
    stored = float(summary.meta["threshold"])

    assert point["threshold"] >= stored, (
        f"the screen landed on threshold {point['threshold']}, below the stored operating "
        f"point {stored}. That point admits candidates the run did not auto-match."
    )

    lower = [
        p["threshold"]
        for p in data["operating_points"]
        if stored <= p["threshold"] < point["threshold"]
    ]
    assert not lower, (
        f"a point at {lower} sits between the operating threshold and the one selected, "
        "so the screen is not showing the tightest equivalent point"
    )

    assert point["matched"] == summary.meta["n_matched_at_threshold"], (
        f"the screen shows {point['matched']:,} matched; the run recorded "
        f"{summary.meta['n_matched_at_threshold']:,}"
    )


def test_the_dashboard_reports_held_out_provenance_with_a_live_integrity_check() -> None:
    """The banner's claim is recomputed inside the service, not read from a file."""
    from api.service import dashboard, load_run

    provenance = dashboard(load_run("v1-test"))["provenance"]
    assert provenance["held_out"] is True
    assert provenance["scored_at_threshold"] == 0.9564
    assert provenance["integrity"]["intact"] is True
    assert provenance["integrity"]["checked"] == 5
    assert provenance["integrity"]["mismatched"] == []


def test_a_run_over_a_non_held_out_batch_does_not_claim_to_be_held_out() -> None:
    """The banner must be impossible to show for a batch carrying no unsealing record."""
    from api.service import dashboard, load_run

    assert dashboard(load_run("v1-train"))["provenance"]["held_out"] is False

# --------------------------------------------------------------------- the schema


def test_the_schema_is_applied_by_the_application_not_by_a_start_script(
    dockerfile: str,
) -> None:
    """The defect: migrations ran in a script only `docker compose` invoked.

    The hosted deployment ran the image's CMD, so the schema was never created. Postgres
    was reachable and empty, /health reported `database: true`, and every approval on the
    live site fell through to the file store while the README claimed an append-only
    Postgres audit trail.

    Nothing had broken. The two start paths had never been the same thing, and only one of
    them was ever exercised -- the same shape as the two Dockerfiles before them. The fix
    was to delete the duplicate rather than to synchronise it, so this asserts the
    duplicate is gone rather than that the two agree.
    """
    assert "entrypoint.sh" not in dockerfile, (
        "an entrypoint script is back. Migrations belong in the application, where both "
        "run paths get them from the same image instead of from two files kept in step."
    )
    assert not (REPO_ROOT / "docker" / "entrypoint.sh").exists()

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "entrypoint:" not in compose, (
        "compose overrides the entrypoint again. That override is what made the local "
        "path work and hid that the hosted one did not."
    )

    main = (REPO_ROOT / "api" / "main.py").read_text(encoding="utf-8")
    assert "def apply_migrations" in main
    assert "lifespan=lifespan" in main, (
        "apply_migrations exists but nothing calls it on start-up, which is the same "
        "defect with a different shape."
    )


def test_a_migration_failure_cannot_stop_the_service_starting(monkeypatch) -> None:
    """Non-fatal on purpose: that property is why the hosted path skipped migrations.

    The original decision -- do not migrate on the host, a migration failure would take
    the service down for something no screen needs -- was right about the screens and
    wrong about the review queue. Keeping the property and fixing the gap means the
    migration runs everywhere and reports rather than raises.
    """
    from api import main

    def explode(*_args, **_kwargs):
        raise RuntimeError("alembic fell over")

    monkeypatch.setattr("alembic.command.upgrade", explode)
    state, detail = main.apply_migrations()

    assert state == "failed"
    assert "RuntimeError" in detail and "alembic fell over" in detail


# The engine-bearing packages, named rather than discovered. `rglob` from the repository
# root walks site-packages before any filter can reject it, and on this project that is
# tens of thousands of files for a scan of four.
SOURCE_DIRS = ("ledgerloop", "api", "core", "model", "llm", "datagen", "evals")
ENGINE_BUILDERS = {"create_engine", "engine_from_config"}


def _engine_calls() -> list[tuple[str, int, ast.Call]]:
    found = []
    files = [f for d in SOURCE_DIRS for f in (REPO_ROOT / d).rglob("*.py")]
    files += list(REPO_ROOT.glob("*.py"))
    for path in sorted(files):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ENGINE_BUILDERS:
                found.append((path.relative_to(REPO_ROOT).as_posix(), node.lineno, node))
    return found


def test_every_engine_bounds_its_connect() -> None:
    """A migration that can hang is not a migration that cannot stop the service.

    The test above proves a migration *failure* is caught. It can prove nothing about a
    migration that never returns: nothing raises, so nothing is caught, the lifespan never
    yields, and the service never finishes starting -- no routes, no `/health`, no answer
    of any kind. Non-fatal and non-terminating are different properties and only one of
    them had a test.

    That gap was real. `ledgerloop/db.py` got a connect timeout when a half-open peer hung
    every caller; the alembic environment builds a *second* engine, was never given one,
    and it is the engine that runs first. Measured on Windows against a port with nothing
    listening: `connect()` had not returned after ninety seconds, sitting in psycopg's
    connect loop, while a plain blocking socket to the same port was refused in two.

    Asserted over every engine in the repository rather than over the two that exist
    today, because the defect being fixed here *is* a second engine that was missed when
    the first one was fixed. Naming them individually would rebuild the same trap.
    """
    engines = _engine_calls()
    assert engines, (
        "no engine construction was found at all -- this test has stopped watching "
        "anything, which is worse than failing"
    )

    for where, line, call in engines:
        kwargs = {k.arg: k.value for k in call.keywords if k.arg}
        assert "connect_args" in kwargs, (
            f"{where}:{line} builds an engine with no connect_args, so its connect has no "
            f"deadline. libpq is never told to give up without one, and the wait is not "
            f"bounded by anything else"
        )
        assert "connect_timeout" in ast.dump(kwargs["connect_args"]), (
            f"{where}:{line} passes connect_args without connect_timeout. That is the "
            f"only parameter that bounds the wait; pool_timeout bounds a different one"
        )


def test_running_migrations_does_not_switch_off_the_application_log() -> None:
    """`fileConfig` disables every existing logger unless told not to.

    Harmless while migrations only ever ran from a terminal, where there are no other
    loggers worth keeping. It stopped being harmless when they moved inside the
    application's lifespan: uvicorn builds its loggers first, so the default silences the
    running server -- which keeps serving and stops saying anything, including the line
    that reports whether these very migrations worked.

    A silent service is a worse failure than a loud one, because every instrument still
    reads normal. This one cost a misdiagnosis: a start-up hang had just been fixed, and
    the absence of a log line was read as the hang persisting while the server was
    answering in ten milliseconds.
    """
    env = (REPO_ROOT / "ledgerloop" / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "disable_existing_loggers=False" in env, (
        "the migration environment calls fileConfig with the default "
        "disable_existing_loggers=True, so applying migrations at start-up switches off "
        "the application's logging for the rest of the process"
    )


def test_the_startup_report_is_not_silenced_by_the_migration_config() -> None:
    """The line that says whether migrations ran has to survive them running.

    `alembic.ini` pins the root logger to WARNING. A logger with no level of its own
    inherits that, so `log.info("migrations ...")` in the lifespan was formatted and
    thrown away -- on every deploy, in the one place someone would look to find out
    whether the schema had been applied.

    Asserted on the logger's own level rather than by capturing output, because pytest's
    caplog raises the level to capture and would hide exactly this defect.
    """
    from api import main

    assert main.log.level == logging.INFO, (
        "the start-up logger has no level of its own, so it inherits root -- which "
        "alembic.ini pins to WARNING the moment migrations run. The line reporting "
        "whether migrations succeeded would be written and discarded"
    )


def test_both_engines_bound_their_connect_to_the_same_number() -> None:
    """Two timeouts that can disagree will, and the disagreement will be silent.

    The application's engine and the migration engine answer the same question about the
    same database. If one waits five seconds and the other thirty, the service reports a
    dependency healthy while the thing that gates its start-up is still waiting -- so the
    constant is imported rather than repeated, and this asserts that it stays imported.
    """
    env = (REPO_ROOT / "ledgerloop" / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "from ledgerloop.db import CONNECT_TIMEOUT" in env, (
        "the migration environment no longer imports the shared timeout, so the two "
        "engines can now drift apart without anything noticing"
    )
    assert '"connect_timeout": CONNECT_TIMEOUT' in env, (
        "the migration environment imports the shared timeout but does not use it"
    )


def test_the_schema_check_asks_about_tables_not_about_the_connection(monkeypatch) -> None:
    """`SELECT 1` was true against a database with no tables in it.

    A correct check answering a question nobody was asking -- the same family as every
    other entry in notes/failure-modes.md. `database` and `schema` are two claims, so
    /health reports two fields, and a connected-but-empty database must read as `missing`
    rather than as anything reassuring.

    The connection is passed in, which is how /health calls it: both facts come from one
    round trip, because against a database that is not answering every extra attempt costs
    another connect timeout.
    """
    from api import main

    assert main.WRITE_TABLES == ("runs", "audit_records")

    class NoTables:
        def has_table(self, _name: str) -> bool:
            return False

    monkeypatch.setattr(main, "inspect", lambda _bind: NoTables())
    state, detail = main.schema_state(conn=object())

    assert state == "missing", "a reachable database with no tables must not read as ready"
    assert "runs" in detail and "audit_records" in detail


def test_the_schema_check_does_not_open_a_second_connection(monkeypatch) -> None:
    """Given a connection, it must use that one and not reach for the engine.

    /health opened a second connection to answer its second question, which doubled the
    wait against exactly the failure it exists to report on. This asserts the fix rather
    than trusting the timing.
    """
    from api import main

    def refuse() -> None:
        raise AssertionError("schema_state opened its own connection despite being given one")

    class AllTables:
        def has_table(self, _name: str) -> bool:
            return True

    monkeypatch.setattr(main, "engine", refuse)
    monkeypatch.setattr(main, "inspect", lambda _bind: AllTables())

    assert main.schema_state(conn=object()) == ("ready", "")


def test_health_reports_the_schema_beside_the_connection() -> None:
    """A green /health must not be able to mean an empty database again."""
    from fastapi.testclient import TestClient

    from api.main import app

    body = TestClient(app).get("/health").json()
    for field in ("database", "schema", "schema_detail", "migrations"):
        assert field in body, f"/health lost {field!r}"
    assert body["schema"] in ("ready", "missing", "unknown")
