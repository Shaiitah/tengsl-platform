"""Получение актуального списка объектов и зон с backend."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
import os

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    zone_number: int
    zone_name: str
    device_type: str
    register_address: int | None
    register_type: str = "holding"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ZoneConfig):
            return NotImplemented
        return (
            self.zone_number == other.zone_number
            and self.zone_name == other.zone_name
            and self.device_type == other.device_type
            and self.register_address == other.register_address
            and self.register_type == other.register_type
        )

    def __hash__(self) -> int:
        return hash((
            self.zone_number, self.zone_name, self.device_type,
            self.register_address, self.register_type,
        ))


@dataclass(frozen=True, slots=True)
class ObjectConfig:
    object_id: str
    name: str
    modbus_host: str
    modbus_port: int
    modbus_unit_id: int

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ObjectConfig):
            return NotImplemented
        return (
            self.object_id == other.object_id
            and self.modbus_host == other.modbus_host
            and self.modbus_port == other.modbus_port
            and self.modbus_unit_id == other.modbus_unit_id
        )

    def __hash__(self) -> int:
        return hash((self.object_id, self.modbus_host, self.modbus_port, self.modbus_unit_id))


def zones_changed(old: list[ZoneConfig], new: list[ZoneConfig]) -> bool:
    if len(old) != len(new):
        return True
    old_map = {z.zone_number: z for z in old}
    new_map = {z.zone_number: z for z in new}
    if old_map.keys() != new_map.keys():
        return True
    for zone_number, old_zone in old_map.items():
        if old_zone != new_map[zone_number]:
            return True
    return False


def objects_changed(old: list[ObjectConfig], new: list[ObjectConfig]) -> bool:
    if len(old) != len(new):
        return True
    old_map = {o.object_id: o for o in old}
    new_map = {o.object_id: o for o in new}
    if old_map.keys() != new_map.keys():
        return True
    for object_id, old_obj in old_map.items():
        if old_obj != new_map[object_id]:
            return True
    return False


async def fetch_objects(
    client: httpx.AsyncClient,
    backend_url: str,
    timeout: float = 10.0,
) -> list[ObjectConfig] | None:
    url = f"{backend_url.rstrip('/')}/api/objects"
    try:
        headers = {"X-TENGSL-Agent-Token": os.getenv("TENGSL_AGENT_API_TOKEN", "")}
        response = await client.get(url, timeout=timeout, headers=headers if headers["X-TENGSL-Agent-Token"] else None)
        response.raise_for_status()
        data: list[dict[str, Any]] = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch objects from %s: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse objects response: %s", exc)
        return None

    objects: list[ObjectConfig] = []
    for item in data:
        try:
            objects.append(
                ObjectConfig(
                    object_id=str(item["object_id"]),
                    name=str(item.get("name", "")),
                    modbus_host=str(item.get("modbus_host", "")),
                    modbus_port=int(item.get("modbus_port", 502)),
                    modbus_unit_id=int(item.get("modbus_unit_id", 1)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed object entry %r: %s", item, exc)
            continue
    return objects


async def fetch_zones(
    client: httpx.AsyncClient,
    backend_url: str,
    object_id: str,
    timeout: float = 10.0,
) -> list[ZoneConfig] | None:
    url = f"{backend_url.rstrip('/')}/api/objects/{object_id}/zones"
    try:
        headers = {"X-TENGSL-Agent-Token": os.getenv("TENGSL_AGENT_API_TOKEN", "")}
        response = await client.get(url, timeout=timeout, headers=headers if headers["X-TENGSL-Agent-Token"] else None)
        response.raise_for_status()
        data: list[dict[str, Any]] = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch zones from %s: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse zones response: %s", exc)
        return None

    zones: list[ZoneConfig] = []
    for item in data:
        try:
            zones.append(
                ZoneConfig(
                    zone_number=int(item["zone_number"]),
                    zone_name=str(item.get("zone_name", "")),
                    device_type=str(item.get("device_type", "")),
                    register_address=item.get("register_address"),
                    register_type=str(item.get("register_type", "holding")),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed zone entry %r: %s", item, exc)
            continue
    return zones


async def poll_zones(
    client: httpx.AsyncClient,
    backend_url: str,
    object_id: str,
    current_zones: list[ZoneConfig],
) -> list[ZoneConfig] | None:
    new_zones = await fetch_zones(client, backend_url, object_id)
    if new_zones is None:
        return None
    if zones_changed(current_zones, new_zones):
        logger.info("[%s] Zones changed: %d -> %d zones", object_id, len(current_zones), len(new_zones))
        return new_zones
    return None


async def poll_objects(
    client: httpx.AsyncClient,
    backend_url: str,
    current_objects: list[ObjectConfig],
) -> list[ObjectConfig] | None:
    new_objects = await fetch_objects(client, backend_url)
    if new_objects is None:
        return None
    if objects_changed(current_objects, new_objects):
        logger.info("Objects changed: %d -> %d objects", len(current_objects), len(new_objects))
        return new_objects
    return None