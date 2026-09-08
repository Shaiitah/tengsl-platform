"""SQLAlchemy-модели для PostgreSQL."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ObjectModel(Base):
    __tablename__ = "objects"

    object_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    
    # Настройки Modbus-подключения к шлюзу RS-485→Ethernet на объекте
    modbus_host: Mapped[str] = mapped_column(String(255), default="")
    modbus_port: Mapped[int] = mapped_column(Integer, default=502)
    modbus_unit_id: Mapped[int] = mapped_column(Integer, default=1)
    
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ZoneModel(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("objects.object_id", ondelete="CASCADE")
    )
    zone_number: Mapped[int] = mapped_column(Integer)
    zone_name: Mapped[str] = mapped_column(String(255), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    register_type: Mapped[str] = mapped_column(String(16), default="holding")
    register_address: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    last_raw_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_primary_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_event_type: Mapped[str] = mapped_column(String(32), default="unknown")
    last_label: Mapped[str] = mapped_column(String(255), default="")
    
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventModel(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_id: Mapped[str] = mapped_column(String(64))
    zone_number: Mapped[int] = mapped_column(Integer)
    zone_name: Mapped[str] = mapped_column(String(255), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    register_address: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), default="unknown")
    label: Mapped[str] = mapped_column(String(255), default="")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class AgentStatusModel(Base):
    __tablename__ = "agent_status"

    object_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    detail: Mapped[str] = mapped_column(String(512), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

class EventCodeModel(Base):
    __tablename__ = "event_codes"

    code: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), default="unknown")
    description: Mapped[str] = mapped_column(String(1024), default="")
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(64), default="custom")



class CommandLogModel(Base):
    __tablename__ = "command_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    zone_number: Mapped[int] = mapped_column(Integer)
    command: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    detail: Mapped[str] = mapped_column(String(1024), default="")
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)



class TestRunModel(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    overall_status: Mapped[str] = mapped_column(String(32), default="running")
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    suites_json: Mapped[str] = mapped_column(Text, default="[]")
    failures_json: Mapped[str] = mapped_column(Text, default="[]")
    output: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(128), default="")

class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class PermissionModel(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermissionModel(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(512))
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserObjectModel(Base):
    __tablename__ = "user_objects"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(64), ForeignKey("objects.object_id", ondelete="CASCADE"), primary_key=True)


class SessionModel(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserNotificationPreferenceModel(Base):
    __tablename__ = "user_notification_preferences"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(64), ForeignKey("objects.object_id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    critical: Mapped[bool] = mapped_column(Boolean, default=True)
    warning: Mapped[bool] = mapped_column(Boolean, default=True)
    info: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
