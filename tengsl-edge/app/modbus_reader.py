"""Асинхронный Modbus-клиент для опроса и записи регистров."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from app.config import ModbusConfig

logger = logging.getLogger(__name__)


class ModbusReader:
    """Обёртка над pymodbus с сериализацией запросов и переподключением."""

    def __init__(self, config: ModbusConfig) -> None:
        self._config = config
        self._client: AsyncModbusTcpClient | AsyncModbusSerialClient | None = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.connected

    def _build_client(self) -> AsyncModbusTcpClient | AsyncModbusSerialClient:
        if self._config.mode == "tcp":
            return AsyncModbusTcpClient(
                host=self._config.host,
                port=self._config.port,
                timeout=self._config.timeout_seconds,
            )
        if self._config.mode == "serial":
            return AsyncModbusSerialClient(
                port=self._config.serial_port,
                baudrate=self._config.baudrate,
                parity=self._config.parity,
                timeout=self._config.timeout_seconds,
            )
        raise ValueError(f"Unknown modbus mode: {self._config.mode!r}")

    def _close_unlocked(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error closing Modbus client: %s", exc)
            finally:
                self._client = None

    async def connect(self) -> None:
        """Подключается к Modbus. Семантика сохранена совместимой со старым агентом."""
        async with self._lock:
            if self.is_connected:
                return

            self._close_unlocked()
            self._client = self._build_client()

            logger.info(
                "Connecting to Modbus device (%s)...",
                self._config.mode,
            )
            # pymodbus 3.7.x: используем фактическое состояние .connected,
            # как в старом рабочем агенте, а не return value connect().
            await self._client.connect()
            if not self._client.connected:
                raise ConnectionError("Failed to connect to Modbus device")
            logger.info("Connected to Modbus device")

    async def close(self) -> None:
        async with self._lock:
            self._close_unlocked()

    async def read_zone(self, zone: Any) -> int:
        """Читает состояние зоны.

        ВАЖНО: адрес передаётся в pymodbus без преобразования.
        Для С2000-ПП это адреса 40000 + (номер зоны - 1), как в РЭ.
        """
        async with self._lock:
            if not self.is_connected:
                raise ConnectionError("Modbus client is not connected")

            address = int(zone.address)
            register_type = getattr(zone, "register_type", "holding")

            if register_type == "holding":
                response = await self._client.read_holding_registers(
                    address=address,
                    count=1,
                    slave=self._config.unit_id,
                )
            elif register_type == "input":
                response = await self._client.read_input_registers(
                    address=address,
                    count=1,
                    slave=self._config.unit_id,
                )
            else:
                raise ValueError(
                    f"Unsupported register_type={register_type!r} "
                    f"for zone {getattr(zone, 'zone_number', '?')}"
                )

            if response.isError():
                raise IOError(
                    f"Modbus read error for zone {zone}: {response}"
                )
            if not response.registers:
                raise IOError(f"No registers returned for zone {zone}")

            return int(response.registers[0])

    async def write_register(self, address: int, value: int) -> None:
        """Записывает значение в holding register без преобразования адреса."""
        async with self._lock:
            if not self.is_connected:
                raise ConnectionError("Modbus client is not connected")

            address = int(address)
            value = int(value)
            response = await self._client.write_register(
                address=address,
                value=value,
                slave=self._config.unit_id,
            )

            if response.isError():
                raise IOError(f"Modbus write error: {response}")

            logger.debug(
                "Wrote value %s to register %s (unit_id=%s)",
                value,
                address,
                self._config.unit_id,
            )
