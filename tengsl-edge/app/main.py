"""Мульти-объектный edge-агент.

Агент при старте опрашивает backend (GET /api/objects) и получает список
всех объектов. Для каждого объекта:
- создаёт свой ModbusReader (с настройками из backend)
- создаёт свой StateTracker
- запускает hot-reload зон (GET /api/objects/{id}/zones)
- подписывается на команды управления (tengsl/{object_id}/commands)
- публикует события в MQTT

Каждые N секунд агент проверяет, не изменился ли список объектов,
и автоматически добавляет/удаляет poller'ы.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiomqtt
import httpx

from app.config import AgentConfig, ModbusConfig, load_config_or_exit
from app.modbus_reader import ModbusReader
from app.mqtt_publisher import MqttPublisher
from app.remote_config import (
    ObjectConfig,
    ZoneConfig,
    fetch_objects,
    fetch_zones,
    poll_objects,
    poll_zones,
)
from app.state_tracker import StateTracker

logger = logging.getLogger("edge_agent")

MAX_CONSECUTIVE_ERRORS_BEFORE_WARN = 5

# Команды управления зонами (коды для Modbus write_register)
COMMAND_CODES = {
    # Коды из РЭ «С2000-ПП», функция Modbus 06, регистр зоны 40000 + M.
    "arm": 24,       # Взять ШС под охрану
    "disarm": 109,   # Снять ШС с охраны
    "enable": 111,   # Включить контроль ШС
    "disable": 112,  # Выключить контроль ШС
}


def decode_zone_register(raw_value: int) -> tuple[int, int]:
    """Разбирает 16-битный регистр состояния зоны на два кода по приоритету."""
    high_byte = (raw_value >> 8) & 0xFF
    low_byte = raw_value & 0xFF
    return high_byte, low_byte


def build_event(config: AgentConfig, object_id: str, zone, raw_value: int) -> dict:
    high_code, low_code = decode_zone_register(raw_value)
    primary_code = high_code if high_code != 0 else low_code
    status = config.status_for(primary_code)
    return {
        "object_id": object_id,
        "zone_number": zone.zone_number,
        "zone_name": zone.name,
        "device_type": zone.device_type,
        "register_address": zone.address,
        "raw_value": raw_value,
        "primary_code": primary_code,
        "secondary_code": low_code if high_code != 0 else 0,
        "event_type": status.event_type,
        "label": status.label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@dataclass
class Zone:
    """Zone-объект для ModbusReader."""
    zone_number: int
    name: str
    device_type: str
    address: int
    register_type: str = "holding"


def zone_config_to_zone(zc: ZoneConfig) -> Zone:
    return Zone(
        zone_number=zc.zone_number,
        name=zc.zone_name,
        device_type=zc.device_type,
        address=(
            zc.register_address
            if zc.register_address is not None
            else 40000 + (zc.zone_number - 1)
        ),
        register_type=zc.register_type,
    )


@dataclass
class ObjectPoller:
    """Опрашивает Modbus-регистры одного объекта и публикует изменения в MQTT."""
    object_config: ObjectConfig
    config: AgentConfig
    publisher: MqttPublisher
    http_client: httpx.AsyncClient
    
    reader: ModbusReader = field(init=False)
    tracker: StateTracker = field(init=False)
    zones: list[Zone] = field(default_factory=list, init=False)
    zones_config: list[ZoneConfig] = field(default_factory=list, init=False)
    _poll_task: asyncio.Task | None = field(default=None, init=False)
    _zones_poll_task: asyncio.Task | None = field(default=None, init=False)
    _commands_task: asyncio.Task | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        modbus_cfg = ModbusConfig(
            mode=self.config.modbus.mode,
            host=self.object_config.modbus_host,
            port=self.object_config.modbus_port,
            unit_id=self.object_config.modbus_unit_id,
            timeout_seconds=self.config.modbus.timeout_seconds,
            reconnect_delay_seconds=self.config.modbus.reconnect_delay_seconds,
            serial_port=self.config.modbus.serial_port,
            baudrate=self.config.modbus.baudrate,
            parity=self.config.modbus.parity,
        )
        self.reader = ModbusReader(modbus_cfg)
        self.tracker = StateTracker()

    async def start(self) -> None:
        if self._started:
            return

        self._started = True
        self._stop_event.clear()

        logger.info(
            "[%s] Starting poller (host=%s:%s, unit_id=%s)",
            self.object_config.object_id,
            self.object_config.modbus_host,
            self.object_config.modbus_port,
            self.object_config.modbus_unit_id,
        )

        try:
            await self._refresh_zones()
            await self.publisher.publish_agent_status(
                self.object_config.object_id,
                "starting",
                detail="Starting Modbus poller",
            )
            self._poll_task = asyncio.create_task(self._poll_loop())
            self._zones_poll_task = asyncio.create_task(self._zones_poll_loop())
            self._commands_task = asyncio.create_task(self._commands_listener())
        except Exception:
            self._stop_event.set()
            for task in (self._poll_task, self._zones_poll_task, self._commands_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (
                        self._poll_task, self._zones_poll_task, self._commands_task
                    )
                    if task is not None
                ),
                return_exceptions=True,
            )
            self._poll_task = None
            self._zones_poll_task = None
            self._commands_task = None
            self._started = False
            await self.reader.close()
            raise

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._stop_event.set()
        
        for task in (self._poll_task, self._zones_poll_task, self._commands_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        try:
            await self.publisher.publish_agent_status(
                self.object_config.object_id, "stopped"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Failed to publish stopped status: %s",
                self.object_config.object_id,
                exc,
            )
        finally:
            await self.reader.close()
            logger.info("[%s] Poller stopped", self.object_config.object_id)

    async def _refresh_zones(self) -> None:
        new_zones_config = await fetch_zones(
            self.http_client, self.config.backend_url, self.object_config.object_id
        )
        if new_zones_config is not None:
            self.zones_config = new_zones_config
            self.zones = [zone_config_to_zone(zc) for zc in new_zones_config]
            self.tracker.clear()
            logger.info("[%s] Loaded %d zones from backend", 
                       self.object_config.object_id, len(self.zones))

    async def _zones_poll_loop(self) -> None:
        interval = self.config.polling.zones_poll_interval_seconds
        while not self._stop_event.is_set():
            try:
                new_zones_config = await poll_zones(
                    self.http_client, self.config.backend_url,
                    self.object_config.object_id, self.zones_config,
                )
                if new_zones_config is not None:
                    self.zones_config = new_zones_config
                    self.zones = [zone_config_to_zone(zc) for zc in new_zones_config]
                    self.tracker.clear()
                    logger.info("[%s] Zones updated: %d zones", 
                               self.object_config.object_id, len(self.zones))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Zones poll error: %s", 
                             self.object_config.object_id, exc)
            
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

    async def _poll_loop(self) -> None:
        logger.info("[%s] _poll_loop started", self.object_config.object_id)
        interval = self.config.polling.interval_seconds
    
        while not self._stop_event.is_set():
            logger.debug("[%s] _poll_loop iteration, zones=%d, is_connected=%s", 
                        self.object_config.object_id, len(self.zones), self.reader.is_connected)
        
            if not self.zones:
                logger.info("[%s] No zones, waiting and refreshing", self.object_config.object_id)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    try:
                        await self._refresh_zones()
                    except Exception as refresh_exc:  # noqa: BLE001
                        logger.warning(
                            "[%s] Failed to refresh zones: %s",
                            self.object_config.object_id,
                            refresh_exc,
                        )
                    continue
        
            try:
                logger.info("[%s] Attempting to connect to Modbus", self.object_config.object_id)
                if not self.reader.is_connected:
                    await self.reader.connect()
                    try:
                        await self.publisher.publish_agent_status(
                            self.object_config.object_id,
                            "online",
                            detail="Modbus connected",
                        )
                    except Exception as status_exc:  # noqa: BLE001
                        logger.warning(
                            "[%s] Failed to publish online status: %s",
                            self.object_config.object_id,
                            status_exc,
                        )
                logger.info("[%s] Connected, polling zones", self.object_config.object_id)
                await self._poll_once()
                logger.info("[%s] Poll completed", self.object_config.object_id)
            except Exception as exc:
                logger.error("[%s] Polling cycle failed: %s", self.object_config.object_id, exc, exc_info=True)
                await self.reader.close()
                try:
                    await self.publisher.publish_agent_status(
                        self.object_config.object_id,
                        "offline",
                        detail=str(exc),
                    )
                except Exception as status_exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] Failed to publish offline status: %s",
                        self.object_config.object_id,
                        status_exc,
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.modbus.reconnect_delay_seconds,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> None:
        for zone in self.zones:
            try:
                raw_value = await self.reader.read_zone(zone)
            except Exception as exc:  # noqa: BLE001
                error_count = self.tracker.mark_error(zone.zone_number)
                if error_count == MAX_CONSECUTIVE_ERRORS_BEFORE_WARN:
                    logger.warning(
                        "[%s] Zone %s (%s) failed %d times: %s",
                        self.object_config.object_id, zone.zone_number, 
                        zone.name, error_count, exc,
                    )
                continue

            if self.tracker.has_changed(zone.zone_number, raw_value):
                event = build_event(
                    self.config, self.object_config.object_id, zone, raw_value
                )
                logger.info(
                    "[%s] Zone %s (%s) changed -> %s (raw=%s)",
                    self.object_config.object_id, zone.zone_number, 
                    zone.name, event["event_type"], raw_value,
                )
                await self.publisher.publish_event(
                    self.object_config.object_id, event
                )
                self.tracker.update(zone.zone_number, raw_value)

    async def _commands_listener(self) -> None:
        """Подписка на команды управления зонами через MQTT."""
        topic = self.config.mqtt.commands_topic(self.object_config.object_id)
        
        while not self._stop_event.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=self.config.mqtt.host,
                    port=self.config.mqtt.port,
                    username=self.config.mqtt.username or None,
                    password=self.config.mqtt.password or None,
                    identifier=f"{self.config.mqtt.client_id}-{self.object_config.object_id}",
                    tls_params=(
                        aiomqtt.TLSParameters(cert_reqs=ssl.CERT_REQUIRED)
                        if self.config.mqtt.use_tls
                        else None
                    ),
                ) as client:
                    await client.subscribe(topic, qos=1)
                    logger.info("[%s] Subscribed to commands topic: %s", 
                               self.object_config.object_id, topic)
                    
                    async for message in client.messages:
                        if self._stop_event.is_set():
                            break
                        
                        try:
                            data = json.loads(message.payload)
                            zone_number = data.get("zone_number")
                            command = data.get("command")
                            
                            if zone_number is not None and command:
                                await self._execute_command(zone_number, command, data.get("command_id"))
                        except Exception as exc:  # noqa: BLE001
                            logger.error("[%s] Failed to process command: %s", 
                                       self.object_config.object_id, exc)
            
            except aiomqtt.MqttError as exc:
                logger.warning("[%s] Commands listener MQTT error: %s, reconnecting in 5s", 
                             self.object_config.object_id, exc)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("[%s] Unexpected error in commands listener: %s", 
                               self.object_config.object_id, exc)
                await asyncio.sleep(5)

    async def _execute_command(self, zone_number: int, command: str, command_id: str | None = None) -> None:
        """Выполняет команду управления зоной через Modbus."""
        zone = next((z for z in self.zones if z.zone_number == zone_number), None)
        if not zone:
            detail = f"Zone {zone_number} not found"
            logger.warning("[%s] %s for command %s", self.object_config.object_id, detail, command)
            if command_id:
                await self.publisher.publish_command_ack(
                    self.object_config.object_id, command_id, zone_number, command, "failed", detail
                )
            return
        
        if command not in COMMAND_CODES:
            detail = f"Unknown command: {command}"
            logger.warning("[%s] %s", self.object_config.object_id, detail)
            if command_id:
                await self.publisher.publish_command_ack(
                    self.object_config.object_id, command_id, zone_number, command, "failed", detail
                )
            return
        
        # В С2000-ПП управление состоянием зоны использует регистр
        # 40000 + (№ зоны - 1), функция Modbus 06.
        control_register = 40000 + (zone_number - 1)
        
        try:
            if not self.reader.is_connected:
                await self.reader.connect()
            
            await self.reader.write_register(control_register, COMMAND_CODES[command])

            logger.info("[%s] Command '%s' sent to zone %s (register %s, value 0x%04X)", 
                       self.object_config.object_id, command, zone_number,
                       control_register, COMMAND_CODES[command])
            if command_id:
                await self.publisher.publish_command_ack(
                    self.object_config.object_id, command_id, zone_number, command, "executed",
                    f"Modbus команда записана в регистр {control_register}"
                )
        
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            logger.error("[%s] Failed to execute command '%s' for zone %s: %s", 
                        self.object_config.object_id, command, zone_number, exc)
            if command_id:
                try:
                    await self.publisher.publish_command_ack(
                        self.object_config.object_id, command_id, zone_number, command, "failed", detail
                    )
                except Exception:
                    logger.exception("[%s] Failed to publish command ACK", self.object_config.object_id)


async def run(config: AgentConfig, stop_event: asyncio.Event) -> None:
    pollers: dict[str, ObjectPoller] = {}
    current_objects: list[ObjectConfig] = []
    
    async with MqttPublisher(config.mqtt) as publisher:
        async with httpx.AsyncClient() as http_client:
            # Повторяем попытку загрузки объектов до 10 раз с интервалом 5 сек
            initial_objects = None
            for attempt in range(1, 11):
                initial_objects = await fetch_objects(http_client, config.backend_url)
                if initial_objects is not None:
                    break
                logger.warning("Failed to fetch objects (attempt %d/10), retrying in 5s...", attempt)
                await asyncio.sleep(5)
            
            if initial_objects is None:
                logger.error("Failed to fetch initial objects list from backend")
                initial_objects = []
            
            current_objects = initial_objects
            
            for obj in current_objects:
                if obj.modbus_host:
                    poller = ObjectPoller(
                        object_config=obj, config=config,
                        publisher=publisher, http_client=http_client,
                    )
                    pollers[obj.object_id] = poller
                    try:
                        await poller.start()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("[%s] Failed to start poller: %s", obj.object_id, exc)
                else:
                    logger.warning("[%s] Skipping: modbus_host is empty", obj.object_id)
            
            logger.info("Started %d object pollers (total objects in backend: %d)", 
                       len(pollers), len(current_objects))

            async def control_listener() -> None:
                topic = config.mqtt.agent_control_topic()
                client = aiomqtt.Client(
                    hostname=config.mqtt.host, port=config.mqtt.port,
                    username=config.mqtt.username or None, password=config.mqtt.password or None,
                    identifier=f"{config.mqtt.client_id}-control",
                    tls_params=(aiomqtt.TLSParameters(cert_reqs=ssl.CERT_REQUIRED) if config.mqtt.use_tls else None),
                )
                try:
                    async with client:
                        await client.subscribe(topic, qos=1)
                        logger.info("Subscribed to agent control topic: %s", topic)
                        async for message in client.messages:
                            try:
                                payload = json.loads(message.payload.decode("utf-8"))
                            except Exception:
                                continue
                            if payload.get("action") == "restart":
                                logger.warning("Administrative restart requested")
                                stop_event.set()
                                return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.error("Agent control listener stopped: %s", exc)

            control_task = asyncio.create_task(control_listener())
            
            objects_interval = config.polling.objects_poll_interval_seconds
            try:
                while not stop_event.is_set():
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=objects_interval)
                        break
                    except asyncio.TimeoutError:
                        pass
                    
                    new_objects = await poll_objects(http_client, config.backend_url, current_objects)
                    if new_objects is None:
                        continue
                    
                    old_ids = {o.object_id for o in current_objects}
                    new_ids = {o.object_id for o in new_objects}
                    added_ids = new_ids - old_ids
                    removed_ids = old_ids - new_ids
                    maybe_changed_ids = old_ids & new_ids
                    
                    for object_id in removed_ids:
                        logger.info("[%s] Object removed, stopping poller", object_id)
                        poller = pollers.pop(object_id, None)
                        if poller:
                            await poller.stop()
                    
                    old_map = {o.object_id: o for o in current_objects}
                    new_map = {o.object_id: o for o in new_objects}
                    for object_id in maybe_changed_ids:
                        if old_map[object_id] != new_map[object_id]:
                            logger.info("[%s] Object config changed, restarting poller", object_id)
                            poller = pollers.pop(object_id, None)
                            if poller:
                                await poller.stop()
                            removed_ids.add(object_id)
                            added_ids.add(object_id)
                    
                    for object_id in added_ids:
                        obj = new_map[object_id]
                        if not obj.modbus_host:
                            logger.warning("[%s] Skipping: modbus_host is empty", object_id)
                            continue
                        logger.info("[%s] New object detected, starting poller", object_id)
                        poller = ObjectPoller(
                            object_config=obj, config=config,
                            publisher=publisher, http_client=http_client,
                        )
                        try:
                            await poller.start()
                            pollers[object_id] = poller
                        except Exception as exc:  # noqa: BLE001
                            logger.error("[%s] Failed to start poller: %s", object_id, exc)
                    
                    current_objects = new_objects
                    logger.info("Objects updated: %d active pollers", len(pollers))
            
            finally:
                control_task.cancel()
                await asyncio.gather(control_task, return_exceptions=True)
                logger.info("Stopping all pollers...")
                for poller in pollers.values():
                    await poller.stop()


def print_brand_banner() -> None:
    """Печатает минималистичный знак TENGSL в терминале."""
    print(r"""
  ●━━━━━━━━●
        ╲
         ╲
          ╲
           ●

  TENGSL · edge agent · multi-object
""")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="TENGSL edge agent (multi-object)")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    print_brand_banner()
    setup_logging(args.verbose)
    config = load_config_or_exit(args.config)

    stop_event = asyncio.Event()

    def _handle_signal(*_args) -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        loop.run_until_complete(run(config, stop_event))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
