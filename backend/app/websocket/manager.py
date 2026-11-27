"""WebSocket fan-out. A slow browser drops old frames instead of holding anything back."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

CLIENT_QUEUE = 64


class Client:
    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE)

    def offer(self, text: str) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(text)


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def count(self) -> int:
        return len(self.clients)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def broadcast(self, kind: str, data: object) -> None:
        """Call on the event loop."""
        if not self.clients:
            return
        text = json.dumps({"type": kind, "data": data}, separators=(",", ":"), default=str)
        for client in self.clients:
            client.offer(text)

    def broadcast_threadsafe(self, kind: str, data: object) -> None:
        """Call from any worker thread."""
        loop = self.loop
        if loop is not None and not loop.is_closed() and self.clients:
            loop.call_soon_threadsafe(self.broadcast, kind, data)

    async def serve(self, socket: WebSocket, hello: list[tuple[str, object]]) -> None:
        await socket.accept()
        client = Client(socket)
        self.clients.add(client)
        for kind, data in hello:
            client.offer(json.dumps({"type": kind, "data": data}, default=str))
        sender = asyncio.create_task(self._send_loop(client))
        try:
            while True:
                await socket.receive_text()
        except Exception:
            pass
        finally:
            self.clients.discard(client)
            sender.cancel()

    @staticmethod
    async def _send_loop(client: Client) -> None:
        try:
            while True:
                await client.socket.send_text(await client.queue.get())
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("WebSocket send failed", exc_info=True)
