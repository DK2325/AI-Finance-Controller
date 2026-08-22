"""FastAPI application. Phase 0 exposes health only; real endpoints arrive in Phase 6."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from ledgerloop import __version__
from ledgerloop.db import engine

app = FastAPI(title="LedgerLoop", version=__version__)


@app.get("/health")
def health() -> dict:
    """Liveness plus a real database round-trip, so a green check means the stack works."""
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "version": __version__, "database": db_ok}
