"""Backend-side MQTT component: receives field traffic and hands it to the processor.

Paho's network thread only enqueues, so a slow handler can never stall the
broker connection. One worker preserves message order.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from app.mqtt import topics
from app.mqtt.bus import BusEndpoint, Handler, LocalHub

logger = logging.getLogger(__name__)


class BackendSubscriber:
    def __init__(
        self,
        hub: LocalHub,
        handler: Handler,
        broker_host: str | None,
        broker_port: int,
        on_connection_change: Callable[[str, bool, str], None] | None = None,
    ) -> None:
        self.handler = handler
        self.endpoint = BusEndpoint(
            "backend", hub, topics.BACKEND_SUBSCRIPTIONS, self._enqueue, broker_host, broker_port, on_connection_change,
        )
        self._queue: queue.Queue = queue.Queue(maxsize=50000)
        self._thread: threading.Thread | None = None
        self._running = False
        self.dropped = 0

    def _enqueue(self, topic: str, payload: dict) -> None:
        try:
            self._queue.put_nowait((topic, payload))
        except queue.Full:
            self.dropped += 1

    @property
    def backlog(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="mqtt-subscriber", daemon=True)
        self._thread.start()
        self.endpoint.start()

    def stop(self) -> None:
        self.endpoint.stop()
        self._running = False
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while self._running:
            item = self._queue.get()
            if item is None:
                return
            try:
                self.handler(*item)
            except Exception:
                logger.exception("Processing failed for %s", item[0])
