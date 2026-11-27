"""Field-side MQTT component: virtual sensors and signal cabinets publish through this."""

from __future__ import annotations

from collections.abc import Callable

from app.mqtt import topics
from app.mqtt.bus import BusEndpoint, Handler, LocalHub


def create_field_gateway(
    hub: LocalHub,
    on_command: Handler,
    broker_host: str | None,
    broker_port: int,
    on_connection_change: Callable[[str, bool, str], None] | None = None,
) -> BusEndpoint:
    return BusEndpoint(
        "field-gateway", hub, topics.FIELD_SUBSCRIPTIONS, on_command, broker_host, broker_port, on_connection_change,
    )
