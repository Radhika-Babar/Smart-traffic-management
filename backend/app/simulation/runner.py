"""Real-time pacing of the simulation engine on its own thread.

A single runner owns a single thread, and every lifecycle call is idempotent,
so repeated start requests can never create a second simulation worker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from app.simulation.engine import TrafficEngine

logger = logging.getLogger(__name__)

STOPPED = "STOPPED"
RUNNING = "RUNNING"
PAUSED = "PAUSED"

ALLOWED_SPEEDS = (1, 2, 5, 10)
MAX_CATCH_UP_TICKS = 40


class SimulationRunner:
    def __init__(self, engine: TrafficEngine, on_status: Callable[[str], None] | None = None) -> None:
        self.engine = engine
        self.on_status = on_status or (lambda status: None)
        self.state = STOPPED
        self.speed = 1
        self.lag_ms = 0.0
        self.tick_ms = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._shutdown = threading.Event()

    @property
    def worker_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.state == RUNNING:
                return
            self.state = RUNNING
            self._ensure_thread()
        self.on_status(RUNNING)

    def pause(self) -> None:
        with self._lock:
            if self.state != RUNNING:
                return
            self.state = PAUSED
        self.on_status(PAUSED)

    def resume(self) -> None:
        self.start()

    def stop(self) -> None:
        with self._lock:
            self.state = STOPPED
        self.on_status(STOPPED)

    def set_speed(self, speed: int) -> None:
        if speed not in ALLOWED_SPEEDS:
            raise ValueError(f"speed must be one of {ALLOWED_SPEEDS}")
        self.speed = speed

    def shutdown(self) -> None:
        self._shutdown.set()
        with self._lock:
            self.state = STOPPED
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _ensure_thread(self) -> None:
        if not self.worker_alive:
            self._shutdown.clear()
            self._thread = threading.Thread(target=self._loop, name="simulation", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        engine = self.engine
        last = time.perf_counter()
        owed = 0.0
        while not self._shutdown.is_set():
            now = time.perf_counter()
            elapsed = now - last
            last = now
            if self.state != RUNNING:
                owed = 0.0
                time.sleep(0.05)
                continue
            owed += elapsed * self.speed / engine.dt
            ticks = int(owed)
            if ticks > MAX_CATCH_UP_TICKS:
                # Overloaded: drop simulated time instead of spiralling.
                self.lag_ms = (ticks - MAX_CATCH_UP_TICKS) * engine.dt / self.speed * 1000
                owed = float(MAX_CATCH_UP_TICKS)
                ticks = MAX_CATCH_UP_TICKS
            owed -= ticks
            started = time.perf_counter()
            try:
                for _ in range(ticks):
                    engine.step()
            except Exception:
                logger.exception("Simulation tick failed; pausing")
                with self._lock:
                    self.state = PAUSED
                self.on_status(PAUSED)
                continue
            if ticks:
                self.tick_ms = (time.perf_counter() - started) * 1000 / ticks
            time.sleep(max(0.002, min(0.05, engine.dt / self.speed / 2)))
