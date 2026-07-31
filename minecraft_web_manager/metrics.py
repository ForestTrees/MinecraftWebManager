"""In-memory tiered time-series history for host and Minecraft resource usage."""

from __future__ import annotations

import collections
import math
import threading
import time
from typing import Any, Callable

# 1s samples cover the most recent hour; 1min averages cover the most recent 7 days.
# Keeping 1s resolution for 7 days would be ~600k samples per field, so anything
# longer than an hour is served from the minute tier instead.
SECONDS_TIER_SPAN = 3600
MINUTES_TIER_SPAN = 7 * 24 * 3600

RANGES: dict[str, int] = {
    "10m": 600,
    "30m": 1800,
    "1h": 3600,
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "1d": 24 * 3600,
    "3d": 3 * 24 * 3600,
    "7d": 7 * 24 * 3600,
}

# Order matters: it is the layout of every stored row after the leading timestamp.
FIELDS = ("cpu", "mem_used", "mem_total", "mc_mem", "mc_cpu", "net_rx", "net_tx")


class MetricsHistory:
    """Samples the host once per second and answers downsampled range queries.

    Sampling runs in its own daemon thread so history keeps accumulating whether or
    not a browser is connected. History is memory-only: it starts empty after a
    plugin reload or MCDR restart.
    """

    def __init__(self, sampler: Callable[[], dict[str, Any]], logger=None, interval: float = 1.0):
        self._sampler = sampler
        self._logger = logger
        self._interval = interval
        self._lock = threading.Lock()
        self._seconds: collections.deque[tuple] = collections.deque(maxlen=int(SECONDS_TIER_SPAN / interval))
        self._minutes: collections.deque[tuple] = collections.deque(maxlen=MINUTES_TIER_SPAN // 60)
        self._bucket_sums: list[float] | None = None
        self._bucket_counts: list[int] | None = None
        self._bucket_index: int | None = None
        self._previous_net: tuple[float, float, float] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="Minecraft Web Manager metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._collect()
            except Exception:  # a bad sample must never kill the sampler thread
                if self._logger is not None:
                    self._logger.exception("Minecraft Web Manager metrics sampling failed")
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - started)))

    # ---------- collection ----------

    def _collect(self) -> None:
        raw = self._sampler()
        now = time.time()
        row = (
            now,
            raw.get("cpu"),
            raw.get("mem_used"),
            raw.get("mem_total"),
            raw.get("mc_mem"),
            raw.get("mc_cpu"),
            *self._network_rates(now, raw.get("net_recv"), raw.get("net_sent")),
        )
        with self._lock:
            self._seconds.append(row)
            self._accumulate(row)

    def _network_rates(self, now: float, recv: Any, sent: Any) -> tuple[float | None, float | None]:
        """Convert cumulative byte counters into per-second rates."""
        if recv is None or sent is None:
            return (None, None)
        rates: tuple[float | None, float | None] = (None, None)
        if self._previous_net is not None:
            previous_at, previous_recv, previous_sent = self._previous_net
            span = now - previous_at
            if span > 0:
                # max(0, ...) guards against counter resets (interface restart, wrap-around)
                rates = (max(0.0, (recv - previous_recv) / span), max(0.0, (sent - previous_sent) / span))
        self._previous_net = (now, float(recv), float(sent))
        return rates

    def _accumulate(self, row: tuple) -> None:
        """Fold a 1s row into the current 1-minute bucket, flushing on rollover."""
        index = int(row[0] // 60)
        if self._bucket_index is None:
            self._bucket_index = index
        elif index != self._bucket_index:
            self._flush_bucket()
            self._bucket_index = index
        if self._bucket_sums is None:
            self._bucket_sums = [0.0] * len(FIELDS)
            self._bucket_counts = [0] * len(FIELDS)
        for position, value in enumerate(row[1:]):
            if value is not None:
                self._bucket_sums[position] += float(value)
                self._bucket_counts[position] += 1

    def _flush_bucket(self) -> None:
        if self._bucket_sums is None or self._bucket_counts is None or self._bucket_index is None:
            return
        averaged = [
            (total / count) if count else None
            for total, count in zip(self._bucket_sums, self._bucket_counts)
        ]
        self._minutes.append((self._bucket_index * 60 + 30, *averaged))
        self._bucket_sums = None
        self._bucket_counts = None

    # ---------- query ----------

    def query(self, range_key: str, max_points: int) -> dict[str, Any]:
        span = RANGES[range_key]
        now = time.time()
        width = span / max_points
        # Anchor buckets to absolute multiples of the bucket width. With a plain
        # `now - span` start the boundaries slide a second on every poll, so samples
        # keep migrating between buckets and the whole line reshuffles even when no
        # new data arrived. Aligning makes the window advance one whole bucket at a
        # time instead, so a given sample always lands in the same bucket.
        start = math.floor(now / width) * width - span
        with self._lock:
            rows = list(self._seconds) if span <= SECONDS_TIER_SPAN else list(self._minutes)
            earliest = None
            if self._seconds:
                earliest = self._seconds[0][0]
            if span > SECONDS_TIER_SPAN and self._minutes:
                earliest = min(earliest, self._minutes[0][0]) if earliest is not None else self._minutes[0][0]

        sums = [[0.0] * len(FIELDS) for _ in range(max_points)]
        counts = [[0] * len(FIELDS) for _ in range(max_points)]
        for row in rows:
            if row[0] < start:
                continue
            index = min(max_points - 1, max(0, int((row[0] - start) / width)))
            for position, value in enumerate(row[1:]):
                if value is not None:
                    sums[index][position] += float(value)
                    counts[index][position] += 1

        # Empty buckets stay null so the chart draws a gap instead of inventing a value.
        result: dict[str, Any] = {
            "range": range_key,
            "start": start,
            "end": now,
            "resolution_seconds": width,
            "first_sample_at": earliest,
            "t": [start + (index + 0.5) * width for index in range(max_points)],
        }
        for position, field in enumerate(FIELDS):
            result[field] = [
                (sums[index][position] / counts[index][position]) if counts[index][position] else None
                for index in range(max_points)
            ]
        return result
