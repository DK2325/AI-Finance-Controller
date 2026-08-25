"""Engine and session factory.

WHY THERE IS A CONNECT TIMEOUT

Without one, `connect()` waits indefinitely on a TCP peer that accepts the connection and
then never completes the Postgres handshake. "Refused" is fast and "unreachable" is a
routing failure that eventually errors, but *half-open* is neither: the socket is
established and nothing arrives, so libpq waits forever and every caller waits with it.

That is not hypothetical here. A stale Docker port-forward on 5432, left behind by a
compose stack that had been brought down, accepted connections to a database that no
longer existed. `/health` -- whose entire purpose is to answer quickly about the database
-- hung instead of reporting `database: false`, and so did every test that called it.

**A health check that can hang is not a health check.** It converts a degraded dependency
into an unresponsive service, which is the failure it exists to distinguish from.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ledgerloop.config import database_url

# Seconds. Long enough for a cold managed Postgres to accept a connection, short enough
# that a caller waiting on it gets an answer rather than a hang.
CONNECT_TIMEOUT = 5

_engine = None
_SessionLocal = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,
            future=True,
            # psycopg passes this to libpq: it bounds the TCP connect and the startup
            # handshake together, which is what makes it cover the half-open case.
            connect_args={"connect_timeout": CONNECT_TIMEOUT},
            # And bound the wait for a pooled connection too, so exhaustion under load
            # surfaces as an error rather than as a request that never returns.
            pool_timeout=CONNECT_TIMEOUT,
        )
    return _engine


def session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=engine(), expire_on_commit=False, future=True)
    return _SessionLocal()
