"""FastAPI application: the three screens' data, plus jobs for anything slow.

WHAT THIS SERVES AND WHY IT IS ONE PROCESS

The API also serves the static frontend. Locally, `docker compose up` still brings up
three containers -- db, api, web -- because that is the documented run path and it works.
Hosted, one service is cheaper, has one fewer thing to be down during a demo, and removes
a cross-origin hop. The two paths are independent so neither can break the other.

THE SEEDED RUN COMES FROM THE REPOSITORY

`runs/v1-train/` is committed. A cold start therefore has a completed run to show without
having run anything and without a single database write, which is what makes "live public
URL, seeded with a completed run" impossible to fail on a cold boot.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.jobs import REGISTRY, Job
from api.service import dashboard, list_runs, load_run
from ledgerloop import __version__
from ledgerloop.config import llm_available
from ledgerloop.db import engine

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
DEMO_BATCH = "data/demo"
DEMO_MODEL = "runs/_models/v1"

app = FastAPI(title="LedgerLoop", version=__version__)


@app.get("/health")
def health() -> dict:
    """Liveness plus a real database round-trip, so a green check means the stack works.

    `database: false` is reported rather than raised: the demo reads runs from the
    filesystem, so the product is usable with Postgres down, and a health endpoint that
    500s would take the whole service out for a dependency the screens do not need.
    """
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok",
        "version": __version__,
        "database": db_ok,
        "llm": "live" if llm_available() else "mock",
    }


# ------------------------------------------------------------------------ runs


@app.get("/api/runs")
def runs() -> dict:
    return {"runs": list_runs()}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    """Everything the dashboard needs, including every achievable operating point.

    The slider reads this once and never calls back -- "updates metrics without a full
    re-run" is satisfied structurally, because every point was computed when the run was
    scored.
    """
    try:
        return dashboard(load_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/exceptions")
def run_exceptions(
    run_id: str,
    code: str | None = Query(default=None, description="Filter to one reason code."),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """The review queue. Paginated, because 2,448 exceptions is not a page."""
    try:
        summary = load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rows = summary.exceptions
    if code:
        rows = [r for r in rows if r.get("reason_code") == code]

    return {
        "run_id": run_id,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "exceptions": rows[offset : offset + limit],
    }


# ------------------------------------------------------------------------ jobs


class ReconRequest(BaseModel):
    batch_dir: str = Field(default=DEMO_BATCH)
    run_id: str = Field(default="")
    model_dir: str = Field(default=DEMO_MODEL)
    threshold: float | None = None
    mock_llm: bool = True


@app.post("/api/jobs/recon")
def submit_recon(request: ReconRequest) -> dict:
    """Run a reconciliation in the background. Never blocks the request."""
    batch = Path(request.batch_dir)
    if not batch.is_dir():
        raise HTTPException(status_code=400, detail=f"no batch at {request.batch_dir}")

    def work(job: Job) -> dict:
        from evals.harness import evaluate
        from evals.models import Prediction, Run, Triple
        from evals.runs import FilesystemRunStore
        from model.artifact import Artifact
        from model.predict import reconcile_batch

        job.step = "loading model"
        artifact = Artifact.load(request.model_dir)
        point = (
            request.threshold
            if request.threshold is not None
            else artifact.operating_point["threshold"]
        )

        job.step = "reconciling"
        job.progress = 0.2
        outcome = reconcile_batch(batch, artifact, threshold=point)

        job.step = "storing run"
        job.progress = 0.7
        run_id = request.run_id or f"run-{batch.name}"
        FilesystemRunStore().save(
            Run(
                run_id=run_id,
                batch_dir=str(batch).replace("\\", "/"),
                predictions=[
                    Prediction(Triple(*s.triple), s.probability, "model", s.row.entity_id)
                    for s in outcome.all_resolved
                ],
                meta={
                    "calibrated": True,
                    "model_version": artifact.model_version,
                    "calibration_method": artifact.calibration["method"],
                    "mock_llm": request.mock_llm,
                    "n_settlements": outcome.enumeration.n_settlements,
                    "n_matched_at_threshold": len(outcome.matches),
                    "threshold": point,
                },
                exceptions=[r.as_dict() for r in outcome.enumeration.exceptions],
            )
        )

        job.step = "scoring"
        job.progress = 0.85
        # readme=None: a hosted run must never rewrite a file in the repository.
        evaluate(run_id=run_id, threshold=point, readme=None, chart=False)

        job.step = "done"
        return {"run_id": run_id, "matched": len(outcome.matches),
                "exceptions": len(outcome.enumeration.exceptions)}

    job = REGISTRY.submit("recon", work)
    return job.as_dict()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    payload = job.as_dict()
    if job.status == "done" and isinstance(job.result, dict):
        payload["result"] = job.result
    return payload


@app.get("/api/jobs")
def job_list() -> dict:
    return {"jobs": [j.as_dict() for j in REGISTRY.recent()]}


# ---------------------------------------------------------------------- static

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
