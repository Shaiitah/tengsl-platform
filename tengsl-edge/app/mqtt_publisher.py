"""Публикация нормализованных событий в MQTT."""

from __future__ import annotations

import json
import logging
import ssl
from datetime import datetime, timezone

import aiomqtt

from app.config import MqttConfig

logger = logging.getLogger(__name__)


class MqttPublisher:
    """Держит соединение с MQTT-брокером и автоматически переподключается."""

    def __init__(self, config: MqttConfig) -> None:
        self._config = config
        self._client: aiomqtt.Client | None = None
        self._closed = False

    def _tls_params(self) -> aiomqtt.TLSParameters | None:
        if not self._config.use_tls:
            return None
        return aiomqtt.TLSParameters(cert_reqs=ssl.CERT_REQUIRED)

    async def _connect(self) -> None:
        client = aiomqtt.Client(
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username or None,
            password=self._config.password or None,
            identifier=self._config.client_id,
            tls_params=self._tls_params(),
        )
        await client.__aenter__()
        self._client = client
        logger.info(
            "Connected to MQTT broker %s:%s",
            self._config.host,
            self._config.port,
        )

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error closing MQTT client: %s", exc)

    async def __aenter__(self) -> "MqttPublisher":
        self._closed = False
        await self._connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        self._closed = True
        await self._disconnect()

    async def _publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if self._closed:
            raise RuntimeError("MqttPublisher is closed")

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                if self._client is None:
                    await self._connect()
                assert self._client is not None
                await self._client.publish(
                    topic,
                    payload=payload,
                    qos=self._config.qos,
                    retain=retain,
                )
                return
            except (aiomqtt.MqttError, OSError) as exc:
                last_exc = exc
                logger.warning(
                    "MQTT publish failed (attempt %d/2) on %s: %s",
                    attempt + 1,
                    topic,
                    exc,
                )
                await self._disconnect()

        raise last_exc or RuntimeError("MQTT publish failed")

    async def publish_event(self, object_id: str, event: dict) -> None:
        topic = self._config.events_topic(object_id)
        payload = json.dumps(event, ensure_ascii=False)
        await self._publish(topic, payload)
        logger.debug("Published to %s: %s", topic, payload)

    async def publish_agent_status(
        self, object_id: str, status: str, detail: str = ""
    ) -> None:
        topic = self._config.status_topic(object_id)
        payload = json.dumps(
            {
                "object_id": object_id,
                "status": status,
                "detail": detail,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        await self._publish(topic, payload, retain=True)
        logger.info("Agent status -> %s: %s", status, detail or "-")


    async def publish_command_ack(
        self,
        object_id: str,
        command_id: str,
        zone_number: int,
        command: str,
        status: str,
        detail: str = "",
    ) -> None:
        topic = self._config.command_ack_topic(object_id)
        payload = json.dumps(
            {
                "command_id": command_id,
                "object_id": object_id,
                "zone_number": zone_number,
                "command": command,
                "status": status,
                "detail": detail,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        await self._publish(topic, payload)
        logger.info(
            "Command ACK -> object=%s command_id=%s status=%s detail=%s",
            object_id, command_id, status, detail or "-",
        )
