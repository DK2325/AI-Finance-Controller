"""Pacing calls to a requests-per-minute ceiling.

The NVIDIA free tier caps at 40 rpm. It is a *throughput* cap, not a quota -- there is no
balance to exhaust, so the only cost of hitting it is time, and the cheapest way to spend
the least time is to never be rejected.

That is not obvious, so it is worth stating: a 429 costs a full round trip *and* a backoff
sleep, then the request is made again. Pacing costs only the wait. Measured across the two
spikes, pacing to 36 rpm produced zero 429s over 150 calls, where firing at concurrency 4
produced four. Backoff stays in the provider as the safety net for the times pacing is not
enough -- a shared account, a provider-side change -- but it should be a path that rarely
runs.

The clock and sleep are injectable so the tests can prove the pacing arithmetic without
spending real seconds asserting it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

# Deliberately under the 40 rpm ceiling. The margin absorbs clock skew between us and the
# provider, and the cost of the margin is 10% throughput on a path that is not the
# bottleneck -- a 25,000-row run is dominated by the number of batches, not by 4 rpm.
DEFAULT_RPM = 36


class TokenBucket:
    """Hands out permission to call, no faster than `rpm` per minute. Thread-safe.

    Not a true token bucket in the burst-allowing sense: this one spaces calls evenly.
    Bursting is exactly what earns a 429, so the smoothing is the point rather than a
    simplification of it.
    """

    def __init__(
        self,
        rpm: int = DEFAULT_RPM,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rpm <= 0:
            raise ValueError(f"rpm must be positive, got {rpm}")
        self.rpm = rpm
        self.interval = 60.0 / rpm
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self._calls = 0
        self._waited = 0.0

    def take(self) -> float:
        """Block until the next call is due. Returns the seconds spent waiting.

        The slot is reserved inside the lock and the sleep happens outside it, so N
        threads queue for distinct slots rather than serialising on one another's sleep.
        """
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
            self._calls += 1

        waited = start - now
        if waited > 0:
            self._waited += waited
            self._sleep(waited)
        return max(0.0, waited)

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def seconds_waited(self) -> float:
        """Total time spent pacing. Reported, so the cost of the limit is visible."""
        return round(self._waited, 3)

    def as_dict(self) -> dict:
        return {
            "rpm": self.rpm,
            "calls": self._calls,
            "seconds_waited": self.seconds_waited,
        }
