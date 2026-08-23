"""The API, and the honesty properties the UI depends on.

The screens can only be as honest as what they are served. These tests hold the three
properties the operating-point explorer must have, at the layer that produces them:

*   it offers only operating points the system can actually reach;
*   every precision comes with its interval and its raw counts;
*   moving between points costs no re-run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.service import dashboard, load_run, operating_points, step_sizes

client = TestClient(app)
RUN = "v1-train"


@pytest.fixture(scope="module")
def summary():
    return load_run(RUN)


# ---------------------------------------------------------------------- health


def test_health_reports_the_database_without_depending_on_it() -> None:
    """The screens read runs from the filesystem, so the product works with Postgres down.
    A health endpoint that 500s would take the service out for a dependency it does not
    need to serve a demo."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "database" in body and isinstance(body["database"], bool)
    assert body["llm"] in ("live", "mock")


def test_the_seeded_run_is_present_without_running_anything() -> None:
    """'Live public URL, seeded with a completed run' must survive a cold boot."""
    runs = client.get("/api/runs").json()["runs"]
    assert RUN in {r["run_id"] for r in runs}


# --------------------------------------------- only reachable operating points


def test_every_offered_point_is_one_the_system_can_reach(summary) -> None:
    """No interpolation. A slider that glides through intermediate values implies a
    resolution isotonic calibration does not have."""
    points = operating_points(summary)
    reachable = set(summary.settlement_confidence.values())
    for point in points:
        assert any(abs(point["threshold"] - c) < 1e-9 for c in reachable) or point[
            "threshold"
        ] in {round(p.threshold, 6) for p in summary.curve.points}


def test_no_two_points_admit_the_same_settlements(summary) -> None:
    """Two thresholds admitting the same matches are one operating point. Offering both
    would be the continuum illusion in another form."""
    matched = [p["matched"] for p in operating_points(summary)]
    assert len(matched) == len(set(matched))


def test_points_ascend_in_coverage(summary) -> None:
    """Dragging right buys more automation and more risk, which is the direction the
    trade actually runs."""
    matched = [p["matched"] for p in operating_points(summary)]
    assert matched == sorted(matched)


def test_the_steps_are_not_uniform(summary) -> None:
    """The point of snapping. If every step were the same size a slider could honestly
    be smooth -- it is not, and the largest step must be visibly larger."""
    sizes = [s for s in step_sizes(operating_points(summary))[1:] if s > 0]
    assert sizes, "no steps to size"
    assert max(sizes) > 10 * min(sizes), (
        "steps are near-uniform; check the curve rather than drawing them evenly"
    )


def test_step_sizes_reconcile_with_the_matched_counts(summary) -> None:
    points = operating_points(summary)
    assert sum(step_sizes(points)) == points[-1]["matched"]


# ------------------------------------------------- intervals and raw counts


def test_every_point_carries_its_interval_and_its_counts(summary) -> None:
    """A slider reading 99.5031% makes the same over-precision claim we corrected
    everywhere else. The interval and the counts are what make it honest."""
    for point in operating_points(summary):
        assert point["precision_ci_low"] <= point["precision"] <= point["precision_ci_high"]
        assert 0.0 <= point["precision_ci_low"] <= 1.0
        assert 0.0 <= point["precision_ci_high"] <= 1.0
        assert isinstance(point["false_matches"], int)
        assert isinstance(point["matched"], int)


def test_the_interval_narrows_with_sample_size_at_a_fixed_error_count(summary) -> None:
    """Width falls as n grows -- but only when p is held still.

    The first version of this test compared the smallest sample against the largest and
    asserted the smallest had the wider interval. That is false, and the data says so: the
    widest interval on this curve is at the LARGEST sample (3,700 matched), because
    precision there is 98.49% rather than 99.96%, and Wilson width depends on p as well as
    n -- it is widest near p = 0.5 and collapses as p approaches 1.

    So the property is only meaningful within a fixed error count, which is what this
    asserts. The naive version passed nothing and would have encoded a wrong intuition
    into the suite.
    """
    from collections import defaultdict

    groups: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for point in operating_points(summary):
        width = point["precision_ci_high"] - point["precision_ci_low"]
        groups[point["false_matches"]].append((point["matched"], width))

    compared = 0
    for rows in groups.values():
        rows.sort()
        for (n_small, wide), (n_large, narrow) in zip(rows, rows[1:], strict=False):
            assert n_large > n_small
            assert narrow <= wide + 1e-9, (
                f"interval grew from {wide} to {narrow} as n went {n_small} -> {n_large}"
            )
            compared += 1

    assert compared > 0, "no two points shared an error count; nothing was compared"


def test_money_is_reported_in_absolute_rupees_not_only_a_ratio(summary) -> None:
    """'2 wrong matches, Rs 27,372 mis-posted of Rs 395,349,148 at stake' is the sentence
    a finance operator reasons about."""
    for point in operating_points(summary):
        assert isinstance(point["wrong_money_paise"], int)
        assert "," in point["total_money"] or point["total_money"] == "0.00"


def test_wrong_money_rises_with_coverage(summary) -> None:
    """The trade, visible in the data rather than asserted in prose."""
    points = operating_points(summary)
    assert points[-1]["wrong_money_paise"] >= points[0]["wrong_money_paise"]


# --------------------------------------------------------------- cost per point


def test_cost_falls_as_coverage_rises(summary) -> None:
    """Fewer exceptions to explain. The three consequences do not all move the same way,
    which is exactly why showing one of them alone would mislead."""
    points = operating_points(summary)
    assert points[-1]["cost_low_inr"] <= points[0]["cost_low_inr"]


def test_the_most_permissive_point_costs_nothing_and_that_is_correct(summary) -> None:
    """Accept every resolved candidate and there are no judgement exceptions left to
    explain -- the remainder never had a candidate, and those cost no tokens.

    Asserted because it first appeared as a bug: the cost was derived by subtracting a
    supposedly threshold-independent count, and INVOICE_ALREADY_CLAIMED is not.
    """
    points = operating_points(summary)
    assert points[-1]["cost_low_inr"] == 0.0
    assert points[-1]["llm_bound_exceptions"] == 0


def test_every_settlement_is_accounted_for_at_every_point(summary) -> None:
    for point in operating_points(summary):
        assert point["matched"] + point["to_review"] == summary.n_settlements


# ------------------------------------------------------------------ endpoints


def test_the_dashboard_serves_every_point_in_one_response() -> None:
    """'Updates metrics without a full re-run' is satisfied structurally: the slider reads
    this once and never calls back."""
    body = client.get(f"/api/runs/{RUN}").json()
    assert len(body["operating_points"]) > 1
    assert body["selected_index"] < len(body["operating_points"])
    assert body["reason_breakdown"]


def test_the_dashboard_says_whether_the_run_is_calibrated() -> None:
    """Architecture rule 3: the UI must never render an uncalibrated run as the real curve."""
    body = client.get(f"/api/runs/{RUN}").json()
    assert body["calibrated"] is True
    assert body["calibration_method"]


def test_the_reason_breakdown_marks_which_codes_cost_tokens() -> None:
    body = client.get(f"/api/runs/{RUN}").json()
    by_code = {r["code"]: r for r in body["reason_breakdown"]}
    assert by_code["NO_CANDIDATE"]["needs_llm"] is False
    assert by_code["BELOW_THRESHOLD"]["needs_llm"] is True
    assert all(r["description"] for r in body["reason_breakdown"])


def test_the_review_queue_paginates_and_filters() -> None:
    everything = client.get(f"/api/runs/{RUN}/exceptions", params={"limit": 1}).json()
    assert everything["total"] > 1
    assert len(everything["exceptions"]) == 1

    filtered = client.get(
        f"/api/runs/{RUN}/exceptions", params={"code": "NO_CANDIDATE", "limit": 5}
    ).json()
    assert filtered["total"] < everything["total"]
    assert all(e["reason_code"] == "NO_CANDIDATE" for e in filtered["exceptions"])


def test_every_exception_carries_its_explanation() -> None:
    body = client.get(f"/api/runs/{RUN}/exceptions", params={"limit": 25}).json()
    for row in body["exceptions"]:
        assert row["reason_code"] and row["detail"], row


def test_an_unknown_run_is_a_404_not_a_traceback() -> None:
    assert client.get("/api/runs/no-such-run").status_code == 404
    assert client.get("/api/runs/no-such-run/exceptions").status_code == 404


def test_an_unknown_job_is_a_404() -> None:
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_recon_refuses_a_batch_that_is_not_there() -> None:
    response = client.post("/api/jobs/recon", json={"batch_dir": "data/nope"})
    assert response.status_code == 400


def test_a_submitted_run_returns_a_job_rather_than_blocking() -> None:
    """BUILD.md: long runs are jobs with status polling, never blocking requests."""
    response = client.post(
        "/api/jobs/recon",
        json={"batch_dir": "data/demo", "run_id": "api-smoke", "mock_llm": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] and body["status"] in ("queued", "running", "done")
    assert "result" not in body, "polling must stay cheap; fetch results from the run"


def test_the_dashboard_is_a_pure_read(summary) -> None:
    """Two calls, identical answers. Nothing is recomputed, so nothing can drift."""
    first = dashboard(load_run(RUN))
    second = dashboard(load_run(RUN))
    assert first == second


# ------------------------------------------------- the deployment findings


def test_a_managed_postgres_url_is_given_a_driver(monkeypatch) -> None:
    """Railway and Render inject `postgresql://`; Heroku's legacy form is `postgres://`.

    SQLAlchemy reads the scheme as the driver, and bare `postgresql://` means psycopg2 --
    which this project does not install. A perfectly correct injected URL therefore fails
    to connect, and fails looking like a network problem rather than a driver one.

    Found on the first live deployment: health reported `database: false` with the
    reference correctly set. Locally DATABASE_URL is absent and the default already names
    the driver, and compose sets it explicitly with `+psycopg`, so neither path ever
    exercised the bare form.
    """
    from ledgerloop.config import database_url

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    assert database_url() == "postgresql+psycopg://u:p@host:5432/db"

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert database_url() == "postgresql+psycopg://u:p@host:5432/db"


def test_a_url_that_already_names_a_driver_is_left_alone(monkeypatch) -> None:
    from ledgerloop.config import database_url

    explicit = "postgresql+psycopg://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", explicit)
    assert database_url() == explicit


def test_an_empty_database_url_falls_back_rather_than_producing_a_broken_one(monkeypatch) -> None:
    """Railway sets an empty string when a reference does not resolve, and `os.environ.get`
    with a default returns that empty string rather than the default."""
    from ledgerloop.config import DEFAULT_DATABASE_URL, database_url

    monkeypatch.setenv("DATABASE_URL", "")
    assert database_url() == DEFAULT_DATABASE_URL
