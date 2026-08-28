"""Smart client-side rate limiting for polite API access.

The limiter combines three mechanisms that public scholarly APIs
(Semantic Scholar, Crossref) expect from well-behaved clients:

* **Pacing** — successive requests are spaced by at least ``min_interval``
  seconds, so a burst of references never turns into a burst of requests.
* **Server-directed backoff** — when the server answers ``429``/``503`` with a
  ``Retry-After`` header (seconds *or* HTTP date) that value is honoured,
  capped at ``max_wait``.
* **Adaptive exponential backoff with jitter** — without a header the wait is
  ``backoff_base ** attempt`` (capped) plus a random jitter, and the pacing
  interval itself grows while consecutive failures accumulate.

After ``max_retries`` consecutive failures the limiter gives up and raises
:class:`RateLimitError` so callers can stop instead of hammering the API.

Usage:
    limiter = RateLimiter(min_interval=1.0)
    limiter.wait_before_call()
    ...  # perform the request
    limiter.note_success()
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional


#: Per-source pacing intervals (seconds) used when a :class:`RateLimiter` is built
#: with ``per_source_intervals``. Crossref / OpenAlex polite pools allow high
#: throughput (``--mailto``), Semantic Scholar is genuinely ~100 req / 5 min, and
#: Zotero is a local read. A source absent here falls back to ``min_interval``.
DEFAULT_SOURCE_INTERVALS = {
    "crossref": 0.05,
    "openalex": 0.05,
    "semantic_scholar": 0.6,
    "zotero": 0.0,
}


class RateLimitError(Exception):
    """Raised when an API rate limit could not be worked around."""


def parse_retry_after(header: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header value into seconds.

    Accepts either a delay in (fractional) seconds or an HTTP date. Returns
    ``None`` when the header is missing, malformed, or already in the past.
    """
    if header is None:
        return None
    raw = str(header).strip()
    if not raw:
        return None

    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        return seconds if seconds > 0 else None

    try:
        deadline = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if deadline is None:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
    return seconds if seconds > 0 else None


class RateLimiter:
    """Paces API calls and computes backoff delays."""

    def __init__(
        self,
        min_interval: float = 1.0,
        max_retries: int = 5,
        max_wait: float = 60.0,
        backoff_base: float = 2.0,
        jitter: float = 0.5,
        per_source_intervals: Optional[dict[str, float]] = None,
    ) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.per_source_intervals = per_source_intervals
        self.max_retries = max(1, int(max_retries))
        self.max_wait = float(max_wait)
        self.backoff_base = float(backoff_base)
        self.jitter = max(0.0, float(jitter))
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_failures = 0
        self._last_call_at: Optional[float] = None

    def _interval_for(self, source: Optional[str]) -> float:
        """Pacing interval for *source*, falling back to ``min_interval``."""
        if self.per_source_intervals and source in self.per_source_intervals:
            return self.per_source_intervals[source]
        return self.min_interval

    def wait_before_call(self, source: Optional[str] = None) -> None:
        """Sleep until at least the (source-aware) pacing interval has passed.

        A healthy fast source (e.g. OpenAlex/Crossref) stays fast; a source that
        is currently failing still backs off via adaptive widening.
        """
        base = self._interval_for(source)
        if self.consecutive_failures == 0:
            interval = base
        else:
            widened = base * (self.backoff_base ** self.consecutive_failures)
            interval = min(self.max_wait, widened)
        if self._last_call_at is not None and interval > 0:
            remaining = interval - (time.monotonic() - self._last_call_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_at = time.monotonic()
        self.total_calls += 1

    def backoff_seconds(
        self, attempt: int, retry_after_header: Optional[str] = None
    ) -> float:
        """Return how long to wait before retry number *attempt*."""
        server_hint = parse_retry_after(retry_after_header)
        if server_hint is not None:
            return min(self.max_wait, server_hint)
        exponential = min(self.max_wait, self.backoff_base ** max(0, attempt))
        return exponential + random.uniform(0, self.jitter)

    def note_failure(self) -> None:
        """Record a failed call; raise once the retry budget is spent."""
        self.consecutive_failures += 1
        self.total_failures += 1
        if self.consecutive_failures >= self.max_retries:
            raise RateLimitError(
                f"Giving up after {self.consecutive_failures} consecutive failed calls"
            )

    def note_success(self) -> None:
        """Record a successful call and reset the failure streak."""
        self.consecutive_failures = 0
