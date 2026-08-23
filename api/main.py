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

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.jobs import REGISTRY, Job
from api.review import ACTIONS, decisions_for, evidence_for, record_decision
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


@app.get("/api/runs/{run_id}/exceptions/{entity_id}")
def exception_evidence(run_id: str, entity_id: str) -> dict:
    """The three source rows behind one exception, side by side.

    Not summarised: an operator is comparing an amount against an amount and a name against
    a narration, and a summary removes exactly the detail they need.
    """
    try:
        summary = load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    row = next((e for e in summary.exceptions if e["entity_id"] == entity_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no exception for {entity_id!r}")

    try:
        return evidence_for(row, summary.batch_dir)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"the batch {summary.batch_dir!r} this run scored is not present",
        ) from exc


class Decision(BaseModel):
    action: str = Field(description="approve, reject or edit")
    approver: str = Field(min_length=1, max_length=128)
    note: str = ""
    edited_journal_entry: dict | None = None


@app.post("/api/runs/{run_id}/exceptions/{entity_id}/decision")
def decide(run_id: str, entity_id: str, decision: Decision) -> dict:
    """Record a human verdict as an audit record with an approver and a timestamp."""
    if decision.action not in ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown action {decision.action!r}; known: {', '.join(ACTIONS)}",
        )
    try:
        batch_dir, counts = "", {}
        try:
            summary = load_run(run_id)
            batch_dir = summary.batch_dir
            counts = {
                "settlements": summary.n_settlements,
                "matched": int(summary.meta.get("n_matched_at_threshold") or 0),
                "exceptions": len(summary.exceptions),
            }
        except FileNotFoundError:
            pass
        return record_decision(
            run_id=run_id,
            entity_id=entity_id,
            batch_dir=batch_dir,
            run_counts=counts,
            action=decision.action,
            approver=decision.approver,
            note=decision.note,
            edited_entry=decision.edited_journal_entry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/decisions")
def run_decisions(run_id: str) -> dict:
    return {"run_id": run_id, "decisions": decisions_for(run_id)}


@app.post("/api/runs/{run_id}/exceptions/{entity_id}/journal")
def propose_journal(run_id: str, entity_id: str, mock: bool = Query(default=False)) -> dict:
    """Ask the model to propose a journal entry for one exception.

    On demand rather than for every exception in the queue: it is the only screen action
    that spends tokens, and a review queue that bills for scrolling would be a poor design
    whatever the rate card says.

    The chart of accounts is a closed Literal, so the model cannot propose a posting to an
    account that does not exist, and the entry must balance or Pydantic refuses it.
    """
    try:
        summary = load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    row = next((e for e in summary.exceptions if e["entity_id"] == entity_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no exception for {entity_id!r}")

    evidence = evidence_for(row, summary.batch_dir)
    settlement = evidence.get("settlement") or {}
    invoice = evidence.get("invoice") or {}
    txn = evidence.get("bank_txn") or {}

    from llm.handler import run_job

    item = {
        "id": entity_id,
        "invoice_amount": invoice.get("amount_paise", 0),
        "gross_amount": settlement.get("gross_paise", 0),
        "fee": settlement.get("fee_paise", 0),
        "tax": settlement.get("tax_paise", 0),
        "net_amount": settlement.get("net_paise", 0),
        "bank_credit": txn.get("credit_paise", 0),
        "difference": evidence.get("difference_paise") or 0,
        "counterparty": invoice.get("customer_name", "unknown"),
        "tds_section": invoice.get("tds_section", ""),
        "narration": txn.get("narration", ""),
    }

    result = run_job("journal", [item], mock=mock)
    outcome = result.outcomes[0]
    return {
        "entity_id": entity_id,
        "proposed": outcome.fields,
        "reason_code": str(outcome.reason_code) if outcome.reason_code else None,
        "detail": outcome.detail,
        "audit": outcome.as_audit_fields(),
        "usage": result.usage.as_dict(),
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


# ---------------------------------------------------------------------- upload

UPLOAD_ROOT = Path("data/uploads")
REQUIRED_FILES = {
    "gateway_settlements.csv": "gateway",
    "bank_statement.csv": "bank",
    "invoice_ledger.csv": "invoices",
}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

# Module-level singletons: FastAPI needs File() as a default, and evaluating it in the
# signature is flagged by ruff's B008 for the usual mutable-default reason.
_GATEWAY_FILE = File(...)
_BANK_FILE = File(...)
_INVOICES_FILE = File(...)


@app.post("/api/upload")
async def upload(
    gateway: UploadFile = _GATEWAY_FILE,
    bank: UploadFile = _BANK_FILE,
    invoices: UploadFile = _INVOICES_FILE,
) -> dict:
    """Three files in, a batch directory out. The run is submitted separately as a job.

    Written under a fresh directory each time rather than a fixed one, so an upload can
    never overwrite the batch a previous run was scored against -- a run whose inputs
    changed underneath it is a run whose numbers cannot be reproduced.

    No truth.csv is accepted, and none is looked for. An uploaded batch has no answer key,
    so `ledgerloop eval` cannot score it -- the dashboard reports what the system decided
    and declines to claim accuracy it cannot measure.
    """
    from uuid import uuid4

    target = UPLOAD_ROOT / uuid4().hex[:10]
    target.mkdir(parents=True, exist_ok=True)

    for upload_file, name in (
        (gateway, "gateway_settlements.csv"),
        (bank, "bank_statement.csv"),
        (invoices, "invoice_ledger.csv"),
    ):
        body = await upload_file.read()
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{name} exceeds 32 MB")
        if not body.strip():
            raise HTTPException(status_code=400, detail=f"{name} is empty")
        (target / name).write_bytes(body)

    return {
        "batch_dir": str(target).replace("\\", "/"),
        "files": sorted(REQUIRED_FILES),
        "has_truth": False,
        "note": "no answer key was uploaded, so accuracy cannot be scored for this batch",
    }


# ---------------------------------------------------------------------- static

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
