"""Batching database writer.

Producers on the simulation and processing threads only append to a queue. One
thread drains it about once a second, so database latency can never stall the
simulation, MQTT handling or the API event loop.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import insert, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Database
from app.models import (
    CongestionEvent,
    EmergencyVehicle,
    SensorReading,
    SignalDecision,
    SignalStateHistory,
    TrafficAlert,
    TrafficEvent,
    TrafficIncident,
    TrafficMetric,
    Vehicle,
)

logger = logging.getLogger(__name__)

MODELS = {
    "reading": SensorReading,
    "signal_history": SignalStateHistory,
    "decision": SignalDecision,
    "metric": TrafficMetric,
    "trip": Vehicle,
    "alert": TrafficAlert,
    "event": TrafficEvent,
    "incident": TrafficIncident,
    "congestion": CongestionEvent,
    "emergency": EmergencyVehicle,
}
# Parents first, so an update in the same batch finds its row.
INSERT_ORDER = (
    "incident", "congestion", "emergency", "alert", "event", "reading",
    "metric", "signal_history", "decision", "trip",
)
NOTIFY_KINDS = {"alert", "incident", "congestion", "emergency"}
FLUSH_INTERVAL_S = 1.0
MAX_QUEUE = 200000


class BatchWriter:
    def __init__(self, database: Database, on_commit: Callable[[list[str]], None] | None = None) -> None:
        self.database = database
        # Told which kinds of row just became readable, so clients refetch only after commit.
        self.on_commit = on_commit
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._flush_lock = threading.Lock()
        self.rows_written = 0
        self.dropped = 0
        self.failed_batches = 0

    def add(self, kind: str, row: dict) -> None:
        self._put(("add", kind, row))

    def update(self, kind: str, key: object, values: dict) -> None:
        self._put(("update", kind, key, values))

    def _put(self, item: tuple) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="db-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        self.flush()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.flush()
            self._stop.wait(max(0.05, FLUSH_INTERVAL_S - (time.monotonic() - started)))

    def flush(self) -> None:
        # Callers may rely on everything queued so far being committed on return.
        with self._flush_lock:
            self._flush()

    def _flush(self) -> None:
        inserts: dict[str, list[dict]] = defaultdict(list)
        updates: list[tuple] = []
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "add":
                    inserts[item[1]].append(item[2])
                else:
                    updates.append(item[1:])
        except queue.Empty:
            pass
        if not inserts and not updates:
            return
        try:
            with self.database.session() as session, session.begin():
                self._write(session, inserts, updates)
        except SQLAlchemyError as error:
            self.failed_batches += 1
            logger.warning("Batch insert failed (%s), retrying rows individually", type(error).__name__)
            self._write_individually(inserts, updates)
        if self.on_commit is not None:
            self.on_commit(sorted({*inserts, *(item[0] for item in updates)} & NOTIFY_KINDS))

    def _write(self, session, inserts: dict[str, list[dict]], updates: list[tuple]) -> None:
        for kind in INSERT_ORDER:
            rows = inserts.get(kind)
            if not rows:
                continue
            # A multi-row insert needs identical keys, and readings differ by sensor type.
            groups: dict[frozenset, list[dict]] = defaultdict(list)
            for row in rows:
                groups[frozenset(row)].append(row)
            for group in groups.values():
                session.execute(insert(MODELS[kind]), group)
            self.rows_written += len(rows)
        for kind, key, values in updates:
            model = MODELS[kind]
            session.execute(update(model).where(model.__table__.primary_key.columns.values()[0] == key).values(**values))

    def _write_individually(self, inserts: dict[str, list[dict]], updates: list[tuple]) -> None:
        for kind in INSERT_ORDER:
            for row in inserts.get(kind, []):
                self._attempt(lambda s, k=kind, r=row: s.execute(insert(MODELS[k]), [r]))
        for kind, key, values in updates:
            model = MODELS[kind]
            primary = model.__table__.primary_key.columns.values()[0]
            self._attempt(lambda s, m=model, p=primary, k=key, v=values: s.execute(update(m).where(p == k).values(**v)))

    def _attempt(self, action) -> None:
        try:
            with self.database.session() as session, session.begin():
                action(session)
                self.rows_written += 1
        except SQLAlchemyError as error:
            self.dropped += 1
            logger.debug("Dropped row: %s", error)
