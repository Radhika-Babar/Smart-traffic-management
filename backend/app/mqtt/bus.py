"""Messaging layer.

A `BusEndpoint` is one participant on the network with its own MQTT client. The
simulator's field gateway and the backend each own one, so every reading really
crosses the broker. When the broker is unreachable the endpoint falls back to
`LocalHub`, an in-process dispatcher. That mode is always reported as
LOCAL_FALLBACK and never presented as MQTT.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections import Counter
from collections.abc import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

logger = logging.getLogger(__name__)

Handler = Callable[[str, dict], None]

MODE_MQTT = "MQTT"
MODE_FALLBACK = "LOCAL_FALLBACK"


class LocalHub:
    """In-process delivery. Threaded by default; synchronous for tests and headless runs."""

    def __init__(self, synchronous: bool = False) -> None:
        self.synchronous = synchronous
        self._endpoints: list[BusEndpoint] = []
        self._queue: queue.Queue = queue.Queue(maxsize=20000)
        self._thread: threading.Thread | None = None
        self._running = False

    def register(self, endpoint: BusEndpoint) -> None:
        self._endpoints.append(endpoint)

    def start(self) -> None:
        if self.synchronous or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="local-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=2)
            self._thread = None

    def dispatch(self, sender: BusEndpoint, topic: str, payload: dict) -> None:
        if self.synchronous:
            self._deliver(sender, topic, payload)
            return
        try:
            self._queue.put_nowait((sender, topic, payload))
        except queue.Full:
            logger.warning("Local hub queue full, dropping %s", topic)

    def _deliver(self, sender: BusEndpoint, topic: str, payload: dict) -> None:
        for endpoint in self._endpoints:
            if endpoint is not sender and endpoint.wants(topic):
                endpoint.receive(topic, payload)

    def _loop(self) -> None:
        while self._running:
            item = self._queue.get()
            if item is None:
                return
            try:
                self._deliver(*item)
            except Exception:
                logger.exception("Local hub handler failed")


class BusEndpoint:
    def __init__(
        self,
        name: str,
        hub: LocalHub,
        subscriptions: tuple[str, ...],
        handler: Handler,
        broker_host: str | None = None,
        broker_port: int = 1883,
        on_connection_change: Callable[[str, bool, str], None] | None = None,
    ) -> None:
        self.name = name
        self.hub = hub
        self.subscriptions = subscriptions
        self.handler = handler
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.on_connection_change = on_connection_change
        self.connected = False
        self.reconnects = 0
        self.ever_connected = False
        self.published: Counter[str] = Counter()
        self.received: Counter[str] = Counter()
        self.malformed = 0
        self._client: mqtt.Client | None = None
        hub.register(self)

    @property
    def mode(self) -> str:
        return MODE_MQTT if self.connected else MODE_FALLBACK

    def wants(self, topic: str) -> bool:
        return any(mqtt.topic_matches_sub(pattern, topic) for pattern in self.subscriptions)

    def start(self) -> None:
        if self.broker_host is None:
            return
        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"traffic-{self.name}-{uuid.uuid4().hex[:6]}",
        )
        client.reconnect_delay_set(min_delay=1, max_delay=15)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_connect_fail = self._on_connect_fail
        client.on_message = self._on_message
        client.max_queued_messages_set(5000)
        self._client = client
        client.connect_async(self.broker_host, self.broker_port, keepalive=15)
        client.loop_start()

    def stop(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            client.disconnect()
            client.loop_stop()
        self.connected = False

    def publish(self, topic: str, payload: dict) -> None:
        self.published[_group(topic)] += 1
        client = self._client
        if self.connected and client is not None:
            client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=0)
        else:
            self.hub.dispatch(self, topic, payload)

    def receive(self, topic: str, payload: dict) -> None:
        self.received[_group(topic)] += 1
        self.handler(topic, payload)

    # ---- paho callbacks --------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.warning("MQTT %s refused: %s", self.name, reason_code)
            return
        for pattern in self.subscriptions:
            client.subscribe(pattern, qos=0)
        self.connected = True
        if self.ever_connected:
            self.reconnects += 1
        self.ever_connected = True
        logger.info("MQTT %s connected to %s:%s", self.name, self.broker_host, self.broker_port)
        if self.on_connection_change:
            self.on_connection_change(self.name, True, "connected")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        was_connected = self.connected
        self.connected = False
        if was_connected:
            logger.warning("MQTT %s disconnected: %s", self.name, reason_code)
            if self.on_connection_change:
                self.on_connection_change(self.name, False, str(reason_code))

    def _on_connect_fail(self, client, userdata) -> None:
        logger.debug("MQTT %s connection attempt failed", self.name)

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except ValueError as error:
            self.malformed += 1
            logger.warning("Malformed MQTT payload on %s: %s", message.topic, error)
            return
        try:
            self.receive(message.topic, payload)
        except Exception:
            logger.exception("MQTT handler failed for %s", message.topic)


def _group(topic: str) -> str:
    parts = topic.split("/")
    if len(parts) >= 2 and parts[1] == "sensors":
        return "sensors"
    if len(parts) >= 2 and parts[1] == "signals":
        return f"signals/{parts[-1]}"
    return parts[1] if len(parts) >= 2 else topic
