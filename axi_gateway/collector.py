"""Nonblocking callback queue, one batching writer, best-effort visible loss."""

import queue
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from axi_gateway.events import Event, Projector
from axi_gateway.sinks import BatchSink, Sink

LOSS_REASONS = (
    "queue_full",
    "projection_failed",
    "sink_failed",
    "rejected_after_close",
)


class Collector:
    """Only the consumer performs I/O; short memory locks never wait on the sink."""

    def __init__(
        self,
        projector: Projector,
        sink: Sink | BatchSink,
        capacity: int = 1024,
        *,
        batch_size: int = 64,
        drain_timeout: float = 2.0,
        debug_sink: Sink | None = None,
    ) -> None:
        if not 1 <= capacity <= 65536 or not 1 <= batch_size <= 1024:
            raise ValueError("invalid queue or batch capacity")
        if not 0 <= drain_timeout <= 30:
            raise ValueError("invalid drain timeout")
        self.projector, self.sink, self.debug_sink = projector, sink, debug_sink
        self.queue: queue.Queue[Event] = queue.Queue(capacity)
        self.batch_size, self.drain_timeout = batch_size, drain_timeout
        self.counters = dict.fromkeys(
            (
                "accepted",
                "written",
                "auth_rejected",
                "storage_failed",
                "debug_failed",
                *LOSS_REASONS,
            ),
            0,
        )
        self.lock = threading.Lock()
        self.gaps: dict[str, dict] = {}
        self.persisted = dict.fromkeys(self.counters, 0)
        self.run_id = str(uuid4())
        self.stopping = threading.Event()
        self.deadline: float | None = None
        self.thread = threading.Thread(
            target=self._consume, name="axi-event-sink", daemon=True
        )
        self.thread.start()

    def _count(self, name: str, count: int = 1) -> None:
        with self.lock:
            self.counters[name] += count
            if count and name in LOSS_REASONS:
                now = datetime.now(UTC).isoformat()
                gap = self.gaps.setdefault(
                    name,
                    dict(
                        run_id=self.run_id,
                        reason=name,
                        count=0,
                        first_at=now,
                        last_at=now,
                    ),
                )
                gap["count"] += count
                gap["last_at"] = now

    def submit(
        self, kwargs: dict, response: object, start: object, end: object, success: bool
    ) -> None:
        """Project metadata then put_nowait; contain errors without sink access."""
        if self.stopping.is_set():
            self._count("rejected_after_close")
            return
        try:
            if self.projector.rejected_before_upstream(kwargs, success):
                self._count("auth_rejected")
                return
            event = self.projector.project(kwargs, response, start, end, success)
            # The stop check and enqueue are atomic with close's stop transition.
            with self.lock:
                if self.stopping.is_set():
                    closed = True
                else:
                    closed = False
                    self.queue.put_nowait(event)
                    self.counters["accepted"] += 1
            if closed:
                self._count("rejected_after_close")
        except queue.Full:
            self._count("queue_full")
        except Exception:
            self._count("projection_failed")

    def metrics(self) -> dict[str, object]:
        """Process-local counters; durable cross-run values live in SQLite."""
        with self.lock:
            snapshot = self.counters.copy()
        return {
            "sink_kind": getattr(self.sink, "kind", "development_jsonl_non_product"),
            **snapshot,
            "queue_depth": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "loss_count": sum(snapshot[k] for k in LOSS_REASONS),
        }

    def _write(self, batch: list[Event]) -> None:
        method = getattr(self.sink, "write_batch", None)
        if method is None:
            # Optional development/test sinks have no product status file.
            for event in batch:
                try:
                    self.sink.write(event)
                    self._count("written")
                except Exception:
                    self._count("sink_failed")
            return
        with self.lock:
            snapshot = self.counters.copy()
            gaps = [g.copy() for g in self.gaps.values()]
        snapshot["written"] += len(batch)
        delta = {k: snapshot[k] - self.persisted[k] for k in snapshot}
        try:
            method(batch, delta, gaps)
        except Exception:
            self._count("sink_failed", len(batch))
            self._count("storage_failed")
            return
        self.persisted = snapshot
        self._count("written", len(batch))
        if self.debug_sink is not None:
            for event in batch:
                try:
                    self.debug_sink.write(event)
                except Exception:
                    self._count("debug_failed")

    def _consume(self) -> None:
        try:
            while True:
                batch = []
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    # Drop only queued work, without starting further blocking I/O.
                    while True:
                        try:
                            self.queue.get_nowait()
                            self.queue.task_done()
                            self._count("sink_failed")
                        except queue.Empty:
                            break
                    return
                try:
                    batch.append(self.queue.get(timeout=0.1))
                    while len(batch) < self.batch_size:
                        batch.append(self.queue.get_nowait())
                except queue.Empty:
                    pass
                self._write(batch)
                for _ in batch:
                    self.queue.task_done()
                if self.stopping.is_set() and self.queue.empty():
                    # One final empty transaction persists late losses/debug counters.
                    self._write([])
                    return
        finally:
            close = getattr(self.sink, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass

    def close(self, timeout: float | None = None) -> None:
        """Wait at most the configured budget; hard exit can lose memory-only state."""
        timeout = self.drain_timeout if timeout is None else max(0.0, timeout)
        with self.lock:
            self.deadline = time.monotonic() + timeout
            self.stopping.set()
        self.thread.join(timeout)
