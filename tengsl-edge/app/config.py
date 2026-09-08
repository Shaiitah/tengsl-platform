"""Загрузка и валидация конфигурации edge-агента из YAML.

В мульти-объектном режиме конфиг содержит только общие настройки:
- MQTT (общий для всех объектов)
- Интервалы polling
- URL backend'а (источник правды для объектов и зон)
- Таблица расшифровки кодов событий

Modbus-настройки и список зон для каждого объекта берутся из backend'а
через GET /api/objects и GET /api/objects/{id}/zones.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModbusConfig:
    """Настройки Modbus-подключения (используются в ObjectPoller).
    
    В мульти-объектном режиме host/port/unit_id берутся из backend'а
    для каждого объекта отдельно, но базовые параметры (таймаут, режим
    переподключения) остаются общими.
    """
    mode: str = "tcp"  # "tcp" | "serial"
    host: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1
    timeout_seconds: float = 3.0
    reconnect_delay_seconds: float = 5.0
    serial_port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    parity: str = "N"

    def validate(self) -> None:
        if self.mode not in ("tcp", "serial"):
            raise ValueError(f"modbus.mode must be 'tcp' or 'serial', got {self.mode!r}")


@dataclass
class MqttConfig:
    host: str
    port: int = 8883
    use_tls: bool = True
    username: str = ""
    password: str = ""
    topic_prefix: str = "tengsl"
    client_id: str = "edge-agent"
    qos: int = 1

    def events_topic(self, object_id: str) -> str:
        return f"{self.topic_prefix}/{object_id}/events"

    def status_topic(self, object_id: str) -> str:
        return f"{self.topic_prefix}/{object_id}/agent_status"

    def commands_topic(self, object_id: str) -> str:
        return f"{self.topic_prefix}/{object_id}/commands"

    def command_ack_topic(self, object_id: str) -> str:
        return f"{self.topic_prefix}/{object_id}/command_ack"

    def agent_control_topic(self) -> str:
        return f"{self.topic_prefix}/agent/control"


@dataclass
class StatusCode:
    event_type: str
    label: str


@dataclass
class PollingConfig:
    interval_seconds: float = 1.0
    zones_poll_interval_seconds: float = 30.0
    objects_poll_interval_seconds: float = 60.0


@dataclass
class AgentConfig:
    """Конфигурация мульти-объектного агента.
    
    object_id, modbus и zones больше НЕ задаются здесь —
    они приходят из backend'а через HTTP API.
    """
    mqtt: MqttConfig
    polling: PollingConfig
    backend_url: str
    backend_api_token: str = ""
    modbus: ModbusConfig = field(default_factory=ModbusConfig)
    status_codes: dict[int | str, StatusCode] = field(default_factory=dict)

    def status_for(self, raw_value: int) -> StatusCode:
        if raw_value in self.status_codes:
            return self.status_codes[raw_value]
        if "default" in self.status_codes:
            return self.status_codes["default"]
        return StatusCode(event_type="unknown", label=f"Код {raw_value}")

    def validate(self) -> None:
        if not self.backend_url:
            raise ValueError(
                "backend_url is required in multi-object mode. "
                "Agent must know where to fetch objects list."
            )
        self.modbus.validate()


def load_config(path: str | Path) -> AgentConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            f"Copy config.example.yaml to config.yaml and edit it."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    mqtt = MqttConfig(**raw["mqtt"])
    polling = PollingConfig(**raw.get("polling", {}))
    backend_url = raw.get("backend_url", "")
    backend_api_token = os.getenv("TENGSL_AGENT_API_TOKEN", raw.get("backend_api_token", ""))
    
    # modbus — опционален, берём дефолты если не задан
    modbus = ModbusConfig(**raw.get("modbus", {}))

    status_codes: dict[int | str, StatusCode] = {}
    for key, value in raw.get("status_codes", {}).items():
        normalized_key: int | str = key if key == "default" else int(key)
        status_codes[normalized_key] = StatusCode(**value)

    config = AgentConfig(
        mqtt=mqtt,
        polling=polling,
        backend_url=backend_url,
        backend_api_token=backend_api_token,
        modbus=modbus,
        status_codes=status_codes,
    )
    config.validate()
    return config


def load_config_or_exit(path: str | Path) -> AgentConfig:
    try:
        return load_config(path)
    except Exception as exc:  # noqa: BLE001
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)