"""MQTT-воркер: подписывается на топики событий и статусов агентов, пишет в БД."""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from datetime import datetime, timezone

import aiomqtt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models import AgentStatusModel, CommandLogModel, EventCodeModel, EventModel, ObjectModel, ZoneModel
from app.schemas import IncomingAgentStatus, IncomingEvent
from app.websocket_manager import manager

logger = logging.getLogger(__name__)


def _mqtt_tls_params() -> aiomqtt.TLSParameters | None:
    if not settings.mqtt_use_tls:
        return None
    return aiomqtt.TLSParameters(cert_reqs=ssl.CERT_REQUIRED)


async def _ensure_object(session: AsyncSession, object_id: str) -> None:
    """Создаёт объект, если его ещё нет (для удобства пилота)."""
    obj = await session.get(ObjectModel, object_id)
    if obj is None:
        session.add(ObjectModel(object_id=object_id, name=f"Object {object_id}"))
        await session.flush()


async def _handle_event(payload: bytes, topic: str) -> None:
    """Обрабатывает одно событие от edge-агента."""
    try:
        data = json.loads(payload)
        event_in = IncomingEvent(**data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse event from %s: %s", topic, exc)
        return

    # Парсим ISO-строку timestamp в datetime объект (asyncpg требует datetime)
    try:
        ts = datetime.fromisoformat(event_in.timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        async with session.begin():
            await _ensure_object(session, event_in.object_id)

            # Обновляем последнее состояние зоны
            result = await session.execute(
                select(ZoneModel).where(
                    ZoneModel.object_id == event_in.object_id,
                    ZoneModel.zone_number == event_in.zone_number,
                )
            )
            zone = result.scalar_one_or_none()
            if zone is None:
                zone = ZoneModel(
                    object_id=event_in.object_id,
                    zone_number=event_in.zone_number,
                    zone_name=event_in.zone_name,
                    device_type=event_in.device_type,
                    register_address=event_in.register_address,
                )
                session.add(zone)

            previous_event_type = zone.last_event_type
            previous_label = zone.last_label
            zone.zone_name = event_in.zone_name or zone.zone_name
            zone.device_type = event_in.device_type or zone.device_type
            effective_event_type = event_in.event_type
            effective_label = event_in.label
            if event_in.primary_code is not None:
                code_result = await session.execute(
                    select(EventCodeModel).where(EventCodeModel.code == event_in.primary_code)
                )
                code_config = code_result.scalar_one_or_none()
                if code_config is not None and code_config.enabled:
                    effective_event_type = code_config.event_type
                    effective_label = code_config.label

            zone.last_raw_value = event_in.raw_value
            zone.last_primary_code = event_in.primary_code
            zone.last_event_type = effective_event_type
            zone.last_label = effective_label

            # Записываем событие в историю
            event = EventModel(
                object_id=event_in.object_id,
                zone_number=event_in.zone_number,
                zone_name=event_in.zone_name,
                device_type=event_in.device_type,
                register_address=event_in.register_address,
                raw_value=event_in.raw_value,
                primary_code=event_in.primary_code,
                event_type=effective_event_type,
                label=effective_label,
                timestamp=ts,
            )
            session.add(event)

    # Рассылаем через WebSocket (вне транзакции, чтобы не блокировать)
    await manager.broadcast({
        "type": "event",
        "data": {
            "object_id": event_in.object_id,
            "zone_number": event_in.zone_number,
            "zone_name": event_in.zone_name,
            "device_type": event_in.device_type,
            "register_address": event_in.register_address,
            "raw_value": event_in.raw_value,
            "primary_code": event_in.primary_code,
            "event_type": effective_event_type,
            "label": effective_label,
            "timestamp": ts.isoformat(),
        },
    }, object_id=event_in.object_id)
    if previous_event_type != effective_event_type or previous_label != effective_label:
        await notify_event_users(event_in.object_id, event_in.zone_number, event_in.zone_name, effective_event_type, effective_label, ts)



async def _handle_agent_status(payload: bytes, topic: str) -> None:
    """Обрабатывает статус агента."""
    try:
        data = json.loads(payload)
        status_in = IncomingAgentStatus(**data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse agent status from %s: %s", topic, exc)
        return

    async with async_session_factory() as session:
        async with session.begin():
            await _ensure_object(session, status_in.object_id)

            status = await session.get(AgentStatusModel, status_in.object_id)
            if status is None:
                status = AgentStatusModel(object_id=status_in.object_id)
                session.add(status)

            status.status = status_in.status
            status.detail = status_in.detail

    await manager.broadcast({
        "type": "agent_status",
        "data": {
            "object_id": status_in.object_id,
            "status": status_in.status,
            "detail": status_in.detail,
        },
    }, object_id=status_in.object_id)


async def _handle_command_ack(payload: bytes, topic: str) -> None:
    """Обрабатывает подтверждение выполнения команды edge-агентом."""
    try:
        data = json.loads(payload)
        command_id = str(data.get("command_id") or "").strip()
        object_id = str(data.get("object_id") or "").strip()
        status = str(data.get("status") or "").strip().lower()
        detail = str(data.get("detail") or "")
        zone_number = int(data.get("zone_number"))
        command = str(data.get("command") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse command ACK from %s: %s", topic, exc)
        return

    if not command_id or status not in {"executed", "failed"}:
        logger.warning("Invalid command ACK from %s: %s", topic, data)
        return

    completed_at = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(CommandLogModel).where(CommandLogModel.command_id == command_id)
            )
            command_log = result.scalar_one_or_none()
            if command_log is None:
                logger.warning("Unknown command ACK: %s", command_id)
                return

            if command_log.object_id != object_id or command_log.zone_number != zone_number:
                logger.warning("Command ACK context mismatch for %s", command_id)
                return

            command_log.status = status
            command_log.detail = detail[:1024]
            command_log.completed_at = completed_at
            logged_command = command_log.command

    await manager.broadcast({
        "type": "command_status",
        "data": {
            "command_id": command_id,
            "object_id": object_id,
            "zone_number": zone_number,
            "command": command or logged_command,
            "status": status,
            "detail": detail,
            "timestamp": completed_at.isoformat(),
        },
    }, object_id=object_id)


async def run_mqtt_worker(stop_event: asyncio.Event) -> None:
    """Бесконечный цикл подписки на MQTT с авто-переподключением."""
    while not stop_event.is_set():
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                username=settings.mqtt_username or None,
                password=settings.mqtt_password or None,
                tls_params=_mqtt_tls_params(),
            ) as client:
                prefixes = {settings.mqtt_topic_prefix.strip("/") or "tengsl", "orion"}
                for prefix in prefixes:
                    await client.subscribe(f"{prefix}/+/events", qos=1)
                    await client.subscribe(f"{prefix}/+/agent_status", qos=1)
                    await client.subscribe(f"{prefix}/+/command_ack", qos=1)
                logger.info("MQTT worker subscribed to events, agent_status and command_ack topics")

                async for message in client.messages:
                    if stop_event.is_set():
                        break
                    topic = str(message.topic)
                    try:
                        if topic.endswith("/events"):
                            await _handle_event(message.payload, topic)
                        elif topic.endswith("/agent_status"):
                            await _handle_agent_status(message.payload, topic)
                        elif topic.endswith("/command_ack"):
                            await _handle_command_ack(message.payload, topic)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Error handling MQTT message on %s: %s", topic, exc)
        except aiomqtt.MqttError as exc:
            logger.warning("MQTT connection error: %s, reconnecting in 5s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error in MQTT worker, restarting loop: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
