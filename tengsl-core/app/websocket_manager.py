"""Рассылка событий всем подключённым WebSocket-клиентам дашборда."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # object_id_filter=None означает "подписан на все объекты"
        self._connections: dict[WebSocket, str | None] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, object_id_filter: str | None = None) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[websocket] = object_id_filter
        logger.info("WebSocket client connected (total=%d)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)
        logger.info("WebSocket client disconnected (total=%d)", len(self._connections))

    async def broadcast(self, message: dict, object_id: str | None = None) -> None:
        """Отправляет сообщение подписчикам. Если у сообщения есть object_id,
        клиенты с фильтром на другой объект его не получат."""
        async with self._lock:
            targets = list(self._connections.items())

        dead: list[WebSocket] = []
        for connection, subscribed_object_id in targets:
            if subscribed_object_id is not None and object_id is not None:
                if subscribed_object_id != object_id:
                    continue
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001 - клиент мог отвалиться в любой момент
                dead.append(connection)

        if dead:
            async with self._lock:
                for connection in dead:
                    self._connections.pop(connection, None)


manager = ConnectionManager()
