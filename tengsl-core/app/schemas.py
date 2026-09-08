"""Pydantic-схемы для валидации API-запросов и ответов."""
from __future__ import annotations

import datetime as dt
from pydantic import BaseModel, Field, field_validator


# ---------- ВХОДЯЩИЕ MQTT-СООБЩЕНИЯ ----------

class IncomingEvent(BaseModel):
    object_id: str
    zone_number: int
    zone_name: str = ""
    device_type: str = ""
    register_address: int | None = None
    raw_value: int | None = None
    primary_code: int | None = None
    secondary_code: int | None = 0
    event_type: str
    label: str
    timestamp: str


class IncomingAgentStatus(BaseModel):
    object_id: str
    status: str
    detail: str = ""
    timestamp: str


# ---------- ОБЪЕКТЫ ----------

class ObjectCreate(BaseModel):
    object_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="", max_length=255)
    modbus_host: str = Field(default="", max_length=255)
    modbus_port: int = Field(default=502, ge=1, le=65535)
    modbus_unit_id: int = Field(default=1, ge=1, le=247)


class ObjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    modbus_host: str | None = Field(default=None, max_length=255)
    modbus_port: int | None = Field(default=None, ge=1, le=65535)
    modbus_unit_id: int | None = Field(default=None, ge=1, le=247)


class ObjectOut(BaseModel):
    object_id: str
    name: str
    modbus_host: str
    modbus_port: int
    modbus_unit_id: int
    created_at: dt.datetime
    model_config = {"from_attributes": True}


# ---------- ЗОНЫ ----------

class ZoneCreate(BaseModel):
    zone_number: int = Field(..., ge=1)
    zone_name: str = Field(default="", max_length=255)
    device_type: str = Field(default="", max_length=64)
    register_address: int | None = Field(default=None)
    register_type: str = Field(default="holding")


class ZoneUpdate(BaseModel):
    zone_name: str | None = Field(default=None, max_length=255)
    device_type: str | None = Field(default=None, max_length=64)
    register_address: int | None = None
    register_type: str | None = None


class ZoneResponse(BaseModel):
    id: int
    object_id: str
    zone_number: int
    zone_name: str
    device_type: str
    register_type: str
    register_address: int | None
    last_raw_value: int | None
    last_primary_code: int | None
    last_event_type: str
    last_label: str
    updated_at: dt.datetime
    model_config = {"from_attributes": True}


ZoneOut = ZoneResponse


class EventOut(BaseModel):
    id: int
    object_id: str
    zone_number: int
    zone_name: str
    device_type: str
    register_address: int | None
    raw_value: int | None
    primary_code: int | None
    event_type: str
    label: str
    timestamp: dt.datetime
    model_config = {"from_attributes": True}


# ---------- КОДЫ СОБЫТИЙ ----------

class EventCodeCreate(BaseModel):
    code: int = Field(..., ge=0, le=255)
    label: str = Field(..., min_length=1, max_length=255)
    event_type: str = Field(default="unknown", max_length=32)
    description: str = Field(default="", max_length=1024)
    priority: int | None = Field(default=None, ge=0, le=255)
    enabled: bool = True

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        allowed = {"normal", "alarm", "attention", "fault", "disabled", "info", "unknown"}
        if value not in allowed:
            raise ValueError(f"event_type must be one of: {', '.join(sorted(allowed))}")
        return value


class EventCodeUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    event_type: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=1024)
    priority: int | None = Field(default=None, ge=0, le=255)
    enabled: bool | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"normal", "alarm", "attention", "fault", "disabled", "info", "unknown"}
        if value not in allowed:
            raise ValueError(f"event_type must be one of: {', '.join(sorted(allowed))}")
        return value


class EventCodeOut(BaseModel):
    code: int
    label: str
    event_type: str
    description: str
    priority: int | None
    enabled: bool
    source: str
    model_config = {"from_attributes": True}


class AgentStatusOut(BaseModel):
    object_id: str
    status: str
    detail: str
    updated_at: dt.datetime
    model_config = {"from_attributes": True}

class ZoneImport(BaseModel):
    zone_number: int = Field(..., ge=1)
    zone_name: str = Field(default="", max_length=255)
    device_type: str = Field(default="", max_length=64)
    register_type: str = Field(default="holding", max_length=16)
    register_address: int | None = Field(default=None)


class ObjectImport(BaseModel):
    object_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="", max_length=255)
    modbus_host: str = Field(default="", max_length=255)
    modbus_port: int = Field(default=502, ge=1, le=65535)
    modbus_unit_id: int = Field(default=1, ge=1, le=247)
    zones: list[ZoneImport] = Field(default_factory=list)


class ObjectsImportPayload(BaseModel):
    version: int = Field(default=1, ge=1)
    objects: list[ObjectImport] = Field(default_factory=list)


class ObjectsImportResult(BaseModel):
    objects_created: int
    objects_updated: int
    zones_created: int
    zones_updated: int


class CommandLogOut(BaseModel):
    id: int
    command_id: str
    object_id: str
    zone_number: int
    command: str
    status: str
    detail: str
    requested_at: dt.datetime
    completed_at: dt.datetime | None
    model_config = {"from_attributes": True}


class TestSuiteResult(BaseModel):
    suite: str
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    status: str


class TestResultsOut(BaseModel):
    generated_at: dt.datetime
    overall_status: str
    total: int
    passed: int
    failed: int
    skipped: int
    suites: list[TestSuiteResult]
    failures: list[str]

# ---------- AUTH / USERS ----------

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class RoleOut(BaseModel):
    id: int
    name: str
    description: str
    permissions: list[str] = Field(default_factory=list)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    object_ids: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(default="", max_length=255)
    password: str = Field(..., min_length=8, max_length=256)
    role: str = Field(default="operator", max_length=64)
    object_ids: list[str] = Field(default_factory=list)
    is_active: bool = True


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: str | None = Field(default=None, max_length=64)
    object_ids: list[str] | None = None
    is_active: bool | None = None


class UserImport(BaseModel):
    username: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: str = Field(default="operator", max_length=64)
    object_ids: list[str] = Field(default_factory=list)
    is_active: bool = True


class UsersImportPayload(BaseModel):
    version: int = Field(default=1, ge=1)
    users: list[UserImport] = Field(default_factory=list)


class UsersImportResult(BaseModel):
    users_created: int
    users_updated: int


class MeOut(UserOut):
    pass


# ---------- УВЕДОМЛЕНИЯ ----------

class NotificationPreferenceOut(BaseModel):
    object_id: str
    object_name: str = ""
    enabled: bool = True
    critical: bool = True
    warning: bool = True
    info: bool = False
    topic: str
    subscribe_url: str

class NotificationPreferenceUpdate(BaseModel):
    enabled: bool = True
    critical: bool = True
    warning: bool = True
    info: bool = False

class NotificationTestRequest(BaseModel):
    object_id: str | None = None
