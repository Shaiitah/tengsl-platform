"""FastAPI-приложение: REST API + WebSocket для дашборда, MQTT-воркер в фоне."""
from __future__ import annotations

import asyncio
import json
import uuid
import logging
import ssl
import os
import signal
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import asyncio.subprocess

import aiomqtt
from fastapi import Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth import default_admin_credentials, hash_password, new_session_token, session_hash, session_expiry, verify_password
from app.crud import (
    create_event_code,
    delete_event_code,
    get_event_code,
    list_event_codes,
    update_event_code,
    create_object,
    create_zone,
    delete_object,
    delete_zone,
    get_object,
    get_zone_by_id,
    get_zone_by_number,
    get_zones_by_object,
    update_object,
    update_zone,
)
from app.database import async_session_factory, engine, get_session
from app.event_codes import DEFAULT_EVENT_CODES
from app.models import (AgentStatusModel, Base, CommandLogModel, EventCodeModel, EventModel, ObjectModel, ZoneModel, RoleModel, PermissionModel, RolePermissionModel, UserModel, UserObjectModel, SessionModel, UserNotificationPreferenceModel, TestRunModel)
from app.mqtt_worker import run_mqtt_worker
from app.notifications import publish as publish_ntfy, subscribe_url, user_topic
from app.schemas import (
    AgentStatusOut,
    CommandLogOut,
    EventOut,
    ObjectCreate,
    ObjectImport,
    ObjectOut,
    ObjectUpdate,
    ObjectsImportPayload,
    ObjectsImportResult,
    TestResultsOut,
    EventCodeCreate,
    EventCodeOut,
    EventCodeUpdate,
    ZoneCreate,
    ZoneOut,
    ZoneResponse,
    ZoneUpdate,
    LoginRequest, RoleOut, UserOut, UserCreate, UserUpdate, UsersImportPayload, UsersImportResult, MeOut, NotificationPreferenceOut, NotificationPreferenceUpdate, NotificationTestRequest,
)
from app.websocket_manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("backend")

_mqtt_stop_event = asyncio.Event()
_mqtt_task: asyncio.Task | None = None
_mqtt_client: aiomqtt.Client | None = None
_mqtt_client_lock = asyncio.Lock()
_diagnostics_task: asyncio.Task | None = None


async def _ensure_event_code_schema() -> None:
    """Добавляет колонки справочника в существующую БД без удаления данных.

    create_all() не изменяет уже существующие таблицы, поэтому после обновления
    с версии без event_codes/source старая БД могла оставаться несовместимой.
    """
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS event_codes (
                code INTEGER PRIMARY KEY,
                label VARCHAR(255) NOT NULL,
                event_type VARCHAR(32) DEFAULT 'unknown',
                description VARCHAR(1024) DEFAULT '',
                priority INTEGER NULL,
                enabled BOOLEAN DEFAULT TRUE,
                source VARCHAR(64) DEFAULT 'custom'
            )
        """))
        await conn.execute(text("ALTER TABLE event_codes ADD COLUMN IF NOT EXISTS description VARCHAR(1024) DEFAULT ''"))
        await conn.execute(text("ALTER TABLE event_codes ADD COLUMN IF NOT EXISTS priority INTEGER NULL"))
        await conn.execute(text("ALTER TABLE event_codes ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT TRUE"))
        await conn.execute(text("ALTER TABLE event_codes ADD COLUMN IF NOT EXISTS source VARCHAR(64) DEFAULT 'custom'"))
        await conn.execute(text("UPDATE event_codes SET event_type = 'unknown' WHERE event_type IS NULL"))
        await conn.execute(text("UPDATE event_codes SET description = '' WHERE description IS NULL"))
        await conn.execute(text("UPDATE event_codes SET enabled = TRUE WHERE enabled IS NULL"))
        await conn.execute(text("UPDATE event_codes SET source = 'custom' WHERE source IS NULL"))


async def _seed_event_codes() -> None:
    async with async_session_factory() as session:
        async with session.begin():
            for code, defaults in DEFAULT_EVENT_CODES.items():
                existing = await session.get(EventCodeModel, code)
                if existing is None:
                    session.add(EventCodeModel(
                        code=code,
                        label=defaults.label,
                        event_type=defaults.event_type,
                        priority=defaults.priority,
                        source="bolid-re-guide",
                        enabled=True,
                    ))



def _mqtt_tls_params() -> aiomqtt.TLSParameters | None:
    if not settings.mqtt_use_tls:
        return None
    return aiomqtt.TLSParameters(cert_reqs=ssl.CERT_REQUIRED)


async def _get_mqtt_client() -> aiomqtt.Client:
    global _mqtt_client

    async with _mqtt_client_lock:
        if _mqtt_client is None:
            client = aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                username=settings.mqtt_username or None,
                password=settings.mqtt_password or None,
                tls_params=_mqtt_tls_params(),
            )
            await client.__aenter__()
            _mqtt_client = client

        return _mqtt_client


async def _reset_mqtt_client(expected: aiomqtt.Client | None = None) -> None:
    global _mqtt_client

    async with _mqtt_client_lock:
        if expected is not None and _mqtt_client is not expected:
            return

        client = _mqtt_client
        _mqtt_client = None

        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to close MQTT client: %s", exc)


async def _publish_command(topic: str, payload: str) -> None:
    last_exc: Exception | None = None

    for _ in range(2):
        client = await _get_mqtt_client()
        try:
            await client.publish(topic, payload, qos=1)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            await _reset_mqtt_client(client)

    raise last_exc or RuntimeError("Failed to publish MQTT command")


async def _close_mqtt_client() -> None:
    await _reset_mqtt_client()


async def _ensure_command_log_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS command_logs (
                id SERIAL PRIMARY KEY,
                command_id VARCHAR(64) UNIQUE NOT NULL,
                object_id VARCHAR(64) NOT NULL,
                zone_number INTEGER NOT NULL,
                command VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                detail VARCHAR(1024) NOT NULL DEFAULT '',
                requested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ NULL
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_command_logs_object_id ON command_logs (object_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_command_logs_command_id ON command_logs (command_id)"))



PERMISSIONS = {
    "objects.view": "Просмотр объектов",
    "objects.manage": "Управление объектами",
    "zones.view": "Просмотр зон",
    "zones.control": "Управление зонами",
    "events.view": "Просмотр событий",
    "commands.view": "Просмотр журнала команд",
    "commands.execute": "Выполнение команд",
    "journal.view": "Просмотр журнала",
    "users.view": "Просмотр пользователей",
    "users.manage": "Управление пользователями",
    "system.manage": "Системные настройки",
    "data.import": "Импорт данных",
    "data.export": "Экспорт данных",
    "notifications.manage": "Настройка уведомлений",
}
ROLE_PERMISSIONS = {
    "admin": set(PERMISSIONS),
    "dispatcher": {"objects.view", "zones.view", "zones.control", "events.view", "commands.view", "commands.execute", "journal.view", "data.export", "notifications.manage"},
    "operator": {"objects.view", "zones.view", "events.view", "commands.view", "journal.view", "data.export"},
    "viewer": {"objects.view", "zones.view", "events.view", "commands.view", "journal.view"},
}

async def _get_current_user(session: AsyncSession, token: str | None) -> tuple[UserModel | None, set[str], list[str]]:
    if not token:
        return None, set(), []
    row = await session.execute(select(SessionModel, UserModel, RoleModel).join(UserModel, UserModel.id == SessionModel.user_id).join(RoleModel, RoleModel.id == UserModel.role_id).where(SessionModel.token_hash == session_hash(token)))
    found = row.first()
    if not found:
        return None, set(), []
    auth_session, user, role = found
    now = datetime.now(timezone.utc)
    if auth_session.expires_at <= now or not user.is_active:
        await session.delete(auth_session)
        await session.commit()
        return None, set(), []
    perm_rows = await session.execute(select(PermissionModel.code).join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id).where(RolePermissionModel.role_id == role.id))
    permissions = {r[0] for r in perm_rows.all()}
    obj_rows = await session.execute(select(UserObjectModel.object_id).where(UserObjectModel.user_id == user.id))
    return user, permissions, [r[0] for r in obj_rows.all()]

async def _has_object_access(session: AsyncSession, user: UserModel, object_id: str) -> bool:
    role = await session.get(RoleModel, user.role_id)
    if role and role.name == "admin":
        return True
    return await session.scalar(select(UserObjectModel.user_id).where(UserObjectModel.user_id == user.id, UserObjectModel.object_id == object_id)) is not None

async def _is_admin(session: AsyncSession, user: UserModel) -> bool:
    role = await session.get(RoleModel, user.role_id)
    return bool(role and role.name == "admin")

async def _visible_object_ids(session: AsyncSession, user: UserModel) -> list[str] | None:
    if await _is_admin(session, user):
        return None
    rows = await session.execute(select(UserObjectModel.object_id).where(UserObjectModel.user_id == user.id))
    return [r[0] for r in rows.all()]


async def _seed_auth() -> None:
    async with async_session_factory() as session:
        async with session.begin():
            permissions = {}
            for code, description in PERMISSIONS.items():
                item = await session.scalar(select(PermissionModel).where(PermissionModel.code == code))
                if item is None:
                    item = PermissionModel(code=code, description=description)
                    session.add(item)
                    await session.flush()
                permissions[code] = item
            roles = {}
            for name, codes in ROLE_PERMISSIONS.items():
                role = await session.scalar(select(RoleModel).where(RoleModel.name == name))
                if role is None:
                    role = RoleModel(name=name, description={"admin":"Администратор","dispatcher":"Диспетчер","operator":"Оператор","viewer":"Наблюдатель"}[name])
                    session.add(role)
                    await session.flush()
                roles[name] = role
                existing = await session.execute(select(RolePermissionModel.permission_id).where(RolePermissionModel.role_id == role.id))
                existing_ids = {r[0] for r in existing.all()}
                for code in codes:
                    if permissions[code].id not in existing_ids:
                        session.add(RolePermissionModel(role_id=role.id, permission_id=permissions[code].id))
            username, password = default_admin_credentials()
            admin = await session.scalar(select(UserModel).where(UserModel.username == username))
            if admin is None:
                session.add(UserModel(username=username, display_name="Administrator", password_hash=hash_password(password), role_id=roles["admin"].id, is_active=True))
                logger.warning("Initial administrator created: username=%s. Change TENGSL_ADMIN_PASSWORD before production use.", username)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_task
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_event_code_schema()
    await _ensure_command_log_schema()
    await _seed_event_codes()
    await _seed_auth()
    diagnostics_path = _diagnostics_path()
    try:
        if diagnostics_path.exists():
            diagnostics_state = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            if diagnostics_state.get("status") == "running":
                diagnostics_state.update({
                    "status": "completed",
                    "overall_status": "failed",
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "suites": [{"suite": "Backend pytest", "status": "failed", "passed": 0, "failed": 1, "skipped": 0, "duration_seconds": 0}],
                    "failures": ["Диагностика была прервана перезапуском Backend."],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })
                _write_diagnostics_state(diagnostics_state)
    except Exception:
        logger.exception("Failed to recover diagnostics state")
    _mqtt_stop_event.clear()
    _mqtt_task = asyncio.create_task(run_mqtt_worker(_mqtt_stop_event))
    logger.info("Backend started, MQTT worker launched")
    try:
        yield
    finally:
        _mqtt_stop_event.set()
        if _mqtt_task is not None:
            _mqtt_task.cancel()
            try:
                await _mqtt_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("MQTT worker failed during shutdown")
            finally:
                _mqtt_task = None
        await _close_mqtt_client()
        await engine.dispose()


app = FastAPI(title="TENGSL Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if "*" in settings.cors_origins_list else settings.cors_origins_list,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if "*" in settings.cors_origins_list else None,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


PUBLIC_API_PATHS = {"/api/health", "/api/health/detailed", "/api/auth/login"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or not request.url.path.startswith("/api/") or request.url.path in PUBLIC_API_PATHS:
        return await call_next(request)
    path = request.url.path
    method = request.method

    # Edge Agent uses a dedicated shared secret for internal read-only API access.
    # It is intentionally limited to object/zone reads and cannot access admin/control APIs.
    agent_token = request.headers.get("X-TENGSL-Agent-Token")
    agent_authenticated = bool(
        settings.agent_api_token
        and agent_token
        and agent_token == settings.agent_api_token
        and method == "GET"
        and (
            path == "/api/objects"
            or (path.startswith("/api/objects/") and path.endswith("/zones"))
        )
    )
    async with async_session_factory() as session:
        token = request.cookies.get("tengsl_session")
        user, permissions, object_ids = await _get_current_user(session, token)
        if user is None and not agent_authenticated:
            return Response(content=json.dumps({"detail": "Authentication required"}), status_code=401, media_type="application/json")
        method = request.method
        required = "objects.view"
        if path == "/api/auth/password": required = None
        elif path == "/api/stats": required = "objects.view"
        elif path == "/api/objects" and method == "POST": required = "objects.manage"
        elif path == "/api/objects" and method == "GET": required = "objects.view"
        elif path.startswith("/api/objects/") and "/zones/" in path and path.endswith("/command"): required = "commands.execute"
        elif path.startswith("/api/objects/") and "/events" in path: required = "events.view"
        elif path.startswith("/api/objects/") and path.endswith("/status"): required = "zones.view"
        elif path.startswith("/api/objects/") and "/zones" in path: required = "zones.view" if method == "GET" else "objects.manage"
        elif path.startswith("/api/objects/") and path.endswith("/export"): required = "data.export"
        elif path.startswith("/api/objects/") and path.endswith("/import"): required = "data.import"
        elif path == "/api/objects/export": required = "data.export"
        elif path == "/api/objects/import": required = "data.import"
        elif path.startswith("/api/event-codes"): required = "system.manage"
        elif path.startswith("/api/commands"): required = "commands.view"
        elif path.startswith("/api/admin/"): required = "system.manage"
        elif path.startswith("/api/test-results"): required = "system.manage"
        elif path == "/api/users/export" or path == "/api/users/import": required = "users.manage"
        elif path.startswith("/api/users"): required = "users.view" if method == "GET" else "users.manage"
        elif path.startswith("/api/roles"): required = "users.view"
        elif path.startswith("/api/notifications/test"): required = "notifications.manage"
        elif path.startswith("/api/notifications") and method in {"GET", "PUT"}: required = "objects.view"
        if required is not None and required not in permissions:
            return Response(content=json.dumps({"detail": "Forbidden"}), status_code=403, media_type="application/json")
        request.state.user = user
        request.state.permissions = permissions
        request.state.object_ids = object_ids
        # Object-level authorization for all object-specific endpoints.
        parts = path.split("/")
        if (not agent_authenticated) and len(parts) >= 4 and parts[2] == "objects" and parts[3] and parts[3] != "export" and parts[3] != "import":
            if not await _has_object_access(session, user, parts[3]):
                return Response(content=json.dumps({"detail": "Object access denied"}), status_code=403, media_type="application/json")
        response = await call_next(request)
        return response


# ---------- HEALTH ----------

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/admin/status")
async def admin_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Сводка для вкладки «Администрирование»."""
    try:
        await session.execute(text("SELECT 1"))
        database = {"status": "ok", "detail": "PostgreSQL connected"}
    except Exception as exc:
        database = {"status": "error", "detail": str(exc)}
    mqtt_ok = _mqtt_task is not None and not _mqtt_task.done()
    mode_file = Path(os.getenv("TENGSL_MODE_FILE", "/runtime/mode"))
    runtime_mode = "production"
    try:
        if mode_file.exists():
            candidate = mode_file.read_text(encoding="utf-8").strip().lower()
            if candidate in {"development", "production"}:
                runtime_mode = candidate
    except OSError:
        pass
    return {
        "environment": runtime_mode,
        "backend": {"status": "ok", "detail": "FastAPI running"},
        "database": database,
        "mqtt": {"status": "ok" if mqtt_ok else "error", "detail": "MQTT worker running" if mqtt_ok else "MQTT worker not running"},
        "websocket": {"status": "ok", "detail": f"{len(manager._connections)} active connections"},
        "development": {
            "hot_reload": runtime_mode == "development",
            "source_mounts": True,
            "database_persistent": True,
            "migration_tool": "Alembic",
        },
    }


class AdminModeRequest(BaseModel):
    mode: str


def _write_runtime_mode(mode: str) -> str:
    mode = mode.strip().lower()
    if mode not in {"development", "production"}:
        raise HTTPException(status_code=422, detail="mode must be development or production")
    mode_file = Path(os.getenv("TENGSL_MODE_FILE", "/runtime/mode"))
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = mode_file.with_suffix(".tmp")
    tmp_file.write_text(mode + "\n", encoding="utf-8")
    tmp_file.replace(mode_file)
    return mode


@app.post("/api/admin/mode")
async def admin_set_mode(payload: AdminModeRequest) -> dict:
    """Безопасно меняет runtime-режим без доступа контейнера к Docker socket.

    Backend и Edge Agent читают общий runtime-файл при следующем запуске.
    После смены режима оба сервиса штатно перезапускаются через уже существующие
    механизмы: SIGTERM для Backend и MQTT control для Edge Agent.
    """
    mode = _write_runtime_mode(payload.mode)
    topic = f"{settings.mqtt_topic_prefix}/agent/control"
    agent_payload = json.dumps({
        "action": "restart",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": f"mode-switch:{mode}",
    })
    await _publish_command(topic, agent_payload)
    asyncio.create_task(_delayed_backend_restart())
    return {
        "status": "accepted",
        "mode": mode,
        "detail": f"Runtime mode changed to {mode}; services are restarting",
    }


async def _delayed_backend_restart() -> None:
    await asyncio.sleep(0.5)
    logger.warning("Administrative backend restart requested")
    # The shell supervisor owns this uvicorn process. Exit code 75 asks it to
    # restart the child and re-read /runtime/mode, without relying on Docker
    # restart-policy semantics for a graceful SIGTERM.
    os.kill(os.getpid(), signal.SIGTERM)


@app.post("/api/admin/backend/restart")
async def admin_restart_backend() -> dict:
    """Перезапускает контейнер backend без пересборки image. Docker restart policy поднимет его снова."""
    asyncio.create_task(_delayed_backend_restart())
    return {"status": "accepted", "detail": "Backend is restarting"}


@app.post("/api/admin/agent/restart")
async def admin_restart_agent() -> dict:
    """Просит Edge Agent штатно завершиться; restart policy Docker запустит его снова."""
    topic = f"{settings.mqtt_topic_prefix}/agent/control"
    payload = json.dumps({"action": "restart", "requested_at": datetime.now(timezone.utc).isoformat()})
    await _publish_command(topic, payload)
    return {"status": "accepted", "detail": "Restart command sent to Edge Agent"}


def _diagnostics_path() -> Path:
    return Path(settings.test_results_path) if settings.test_results_path else Path(os.getenv("TENGSL_DIAGNOSTICS_PATH", "/runtime/diagnostics.json"))

def _write_diagnostics_state(data: dict) -> None:
    path = _diagnostics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _diagnostics_text(result: dict, output: str, started: datetime, requested_by: str = "") -> str:
    finished = datetime.fromisoformat(result["generated_at"])
    lines = [
        "TENGSL — отчёт диагностики",
        f"Дата запуска: {started.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Дата завершения: {finished.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Результат: {result.get('overall_status', 'unknown')}",
        f"Всего: {result.get('total', 0)}",
        f"Пройдено: {result.get('passed', 0)}",
        f"Ошибок: {result.get('failed', 0)}",
        f"Пропущено: {result.get('skipped', 0)}",
        f"Длительность: {sum(float(x.get('duration_seconds', 0) or 0) for x in result.get('suites', [])):.2f} с",
        f"Запустил: {requested_by or '—'}",
        "", "Наборы тестов:",
    ]
    for suite in result.get("suites", []):
        lines.append(f"- {suite.get('suite')}: {suite.get('status')} | passed={suite.get('passed',0)}, failed={suite.get('failed',0)}, skipped={suite.get('skipped',0)}, duration={float(suite.get('duration_seconds',0)):.2f}s")
    failures = result.get("failures", [])
    if failures:
        lines += ["", "Ошибки:"] + [f"- {x}" for x in failures]
    lines += ["", "Полный вывод pytest:", output or "(нет вывода)"]
    return "\n".join(lines) + "\n"

def _write_diagnostics_text_file(result: dict, output: str, started: datetime, requested_by: str = "") -> None:
    root = Path(os.getenv("TENGSL_DIAGNOSTICS_DIR", "/runtime/diagnostics"))
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(result["generated_at"]).astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    path = root / f"tengsl-diagnostics-{stamp}-{result.get('job_id','run')[:8]}.txt"
    path.write_text(_diagnostics_text(result, output, started, requested_by), encoding="utf-8")


async def _save_test_run(run_id: str, started: datetime, result: dict, output: str, requested_by: str) -> None:
    async with async_session_factory() as session:
        run = await session.get(TestRunModel, run_id)
        if run is None:
            run = TestRunModel(id=run_id, started_at=started, requested_by=requested_by)
            session.add(run)
        run.completed_at = datetime.fromisoformat(result["generated_at"])
        run.status = result.get("status", "completed")
        run.overall_status = result.get("overall_status", "failed")
        run.total = int(result.get("total", 0) or 0)
        run.passed = int(result.get("passed", 0) or 0)
        run.failed = int(result.get("failed", 0) or 0)
        run.skipped = int(result.get("skipped", 0) or 0)
        run.duration_seconds = sum(float(x.get("duration_seconds", 0) or 0) for x in result.get("suites", []))
        run.suites_json = json.dumps(result.get("suites", []), ensure_ascii=False)
        run.failures_json = json.dumps(result.get("failures", []), ensure_ascii=False)
        run.output = output
        await session.commit()


async def _run_diagnostics_job(job_id: str, requested_by: str = "") -> None:
    tests_dir = Path(__file__).resolve().parents[1] / "tests"
    started = datetime.now(timezone.utc)
    cmd = [sys.executable, "-m", "pytest", "-q", str(tests_dir), "--disable-warnings"]
    output = ""
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace")
        passed = failed = skipped = 0
        import re
        m = re.search(r"(?:(\d+) passed)?(?:,?\s*(\d+) failed)?(?:,?\s*(\d+) skipped)?", output)
        if m:
            passed = int(m.group(1) or 0)
            failed = int(m.group(2) or 0)
            skipped = int(m.group(3) or 0)
        total = passed + failed + skipped
        if proc.returncode != 0 and total == 0:
            failed = 1
            total = 1
        finished = datetime.now(timezone.utc)
        result = {
            "job_id": job_id, "status": "completed", "generated_at": finished.isoformat(),
            "overall_status": "passed" if proc.returncode == 0 else "failed",
            "total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "suites": [{"suite": "Backend pytest", "status": "passed" if proc.returncode == 0 else "failed", "passed": passed, "failed": failed, "skipped": skipped, "duration_seconds": (finished-started).total_seconds()}],
            "failures": [] if proc.returncode == 0 else [output[-4000:]],
        }
        _write_diagnostics_state(result)
        _write_diagnostics_text_file(result, output, started, requested_by)
        await _save_test_run(job_id, started, result, output, requested_by)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        output = output or "Диагностика превысила лимит 120 секунд"
        result = {"job_id": job_id, "status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(), "overall_status": "failed", "total": 1, "passed": 0, "failed": 1, "skipped": 0, "suites": [{"suite": "Backend pytest", "status": "failed", "passed": 0, "failed": 1, "skipped": 0, "duration_seconds": (datetime.now(timezone.utc)-started).total_seconds()}], "failures": [output]}
        _write_diagnostics_state(result)
        _write_diagnostics_text_file(result, output, started, requested_by)
        await _save_test_run(job_id, started, result, output, requested_by)
    except FileNotFoundError:
        output = "pytest не установлен в контейнере backend"
        result = {"job_id": job_id, "status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(), "overall_status": "failed", "total": 1, "passed": 0, "failed": 1, "skipped": 0, "suites": [{"suite": "Backend pytest", "status": "failed", "passed": 0, "failed": 1, "skipped": 0, "duration_seconds": 0}], "failures": [output]}
        _write_diagnostics_state(result)
        _write_diagnostics_text_file(result, output, started, requested_by)
        await _save_test_run(job_id, started, result, output, requested_by)
    except Exception as exc:
        logger.exception("Diagnostics job %s failed", job_id)
        output = str(exc)
        result = {"job_id": job_id, "status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(), "overall_status": "failed", "total": 1, "passed": 0, "failed": 1, "skipped": 0, "suites": [{"suite": "Backend pytest", "status": "failed", "passed": 0, "failed": 1, "skipped": 0, "duration_seconds": (datetime.now(timezone.utc)-started).total_seconds()}], "failures": [output]}
        _write_diagnostics_state(result)
        _write_diagnostics_text_file(result, output, started, requested_by)
        await _save_test_run(job_id, started, result, output, requested_by)

@app.post("/api/admin/diagnostics/run", status_code=202)
async def admin_run_diagnostics(request: Request) -> dict:
    """Запускает диагностику в фоне и сохраняет каждый запуск в PostgreSQL."""
    global _diagnostics_task
    if _diagnostics_task is not None and not _diagnostics_task.done():
        state = _diagnostics_path()
        current = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
        return {"status": "running", "job_id": current.get("job_id"), "detail": "Диагностика уже выполняется"}
    job_id = uuid.uuid4().hex
    requested_by = getattr(getattr(request, "state", None), "user", None)
    requested_by = getattr(requested_by, "username", "") or ""
    started = datetime.now(timezone.utc)
    _write_diagnostics_state({"job_id": job_id, "status": "running", "generated_at": started.isoformat(), "overall_status": "running", "total": 0, "passed": 0, "failed": 0, "skipped": 0, "suites": [], "failures": []})
    async with async_session_factory() as session:
        session.add(TestRunModel(id=job_id, started_at=started, status="running", overall_status="running", requested_by=requested_by))
        await session.commit()
    _diagnostics_task = asyncio.create_task(_run_diagnostics_job(job_id, requested_by))
    return {"status": "running", "job_id": job_id, "detail": "Диагностика запущена в фоне"}

@app.get("/api/admin/test-runs")
async def list_test_runs(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    limit = max(1, min(limit, 200))
    rows = (await session.execute(select(TestRunModel).order_by(desc(TestRunModel.started_at)).limit(limit))).scalars().all()
    return [{"id": r.id, "started_at": r.started_at, "completed_at": r.completed_at, "status": r.status, "overall_status": r.overall_status, "total": r.total, "passed": r.passed, "failed": r.failed, "skipped": r.skipped, "duration_seconds": r.duration_seconds, "requested_by": r.requested_by} for r in rows]

@app.get("/api/admin/test-runs/{run_id}")
async def get_test_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    r = await session.get(TestRunModel, run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    return {"id": r.id, "started_at": r.started_at, "completed_at": r.completed_at, "status": r.status, "overall_status": r.overall_status, "total": r.total, "passed": r.passed, "failed": r.failed, "skipped": r.skipped, "duration_seconds": r.duration_seconds, "requested_by": r.requested_by, "suites": json.loads(r.suites_json or "[]"), "failures": json.loads(r.failures_json or "[]"), "output": r.output}

@app.get("/api/admin/test-runs/latest/export")
async def export_latest_test_run(session: AsyncSession = Depends(get_session)) -> Response:
    r = await session.scalar(select(TestRunModel).order_by(desc(TestRunModel.started_at)).limit(1))
    if r is None:
        raise HTTPException(status_code=404, detail="No test runs found")
    return await _export_test_run_record(r)


async def _export_test_run_record(r: TestRunModel) -> Response:
    date = (r.completed_at or r.started_at).astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    lines = ["TENGSL — отчёт диагностики", f"Дата запуска: {r.started_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", f"Дата завершения: {(r.completed_at or r.started_at).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", f"Результат: {r.overall_status}", f"Всего: {r.total}", f"Пройдено: {r.passed}", f"Ошибок: {r.failed}", f"Пропущено: {r.skipped}", f"Длительность: {r.duration_seconds:.2f} с", f"Запустил: {r.requested_by or '—'}", "", "Наборы тестов:"]
    for suite in json.loads(r.suites_json or "[]"):
        lines.append(f"- {suite.get('suite')}: {suite.get('status')} | passed={suite.get('passed',0)}, failed={suite.get('failed',0)}, skipped={suite.get('skipped',0)}, duration={float(suite.get('duration_seconds',0)):.2f}s")
    failures = json.loads(r.failures_json or "[]")
    if failures:
        lines += ["", "Ошибки:"] + [f"- {x}" for x in failures]
    lines += ["", "Полный вывод pytest:", r.output or "(нет вывода)"]
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="tengsl-diagnostics-{date}.txt"'})


@app.get("/api/admin/test-runs/{run_id}/export")
async def export_test_run(run_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    r = await session.get(TestRunModel, run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    return await _export_test_run_record(r)

# ---------- STATS ----------

@app.get("/api/stats")
async def get_stats(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    visible = await _visible_object_ids(session, request.state.user)
    object_filter = None if visible is None else ObjectModel.object_id.in_(visible)
    event_filter = None if visible is None else EventModel.object_id.in_(visible)
    objects_count_query = select(func.count(ObjectModel.object_id))
    zones_count_query = select(func.count(ZoneModel.id))
    if object_filter is not None:
        objects_count_query = objects_count_query.where(object_filter)
        zones_count_query = zones_count_query.where(ZoneModel.object_id.in_(visible))
    objects_count = await session.scalar(objects_count_query)
    zones_count = await session.scalar(zones_count_query)

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)
    events_24h = await session.scalar(
        select(func.count(EventModel.id)).where(EventModel.timestamp >= yesterday).where(event_filter) if event_filter is not None else select(func.count(EventModel.id)).where(EventModel.timestamp >= yesterday)
    )
    events_total = await session.scalar(select(func.count(EventModel.id)).where(event_filter) if event_filter is not None else select(func.count(EventModel.id)))
    event_type_query = select(EventModel.event_type, func.count(EventModel.id)).where(EventModel.timestamp >= yesterday).group_by(EventModel.event_type)
    if event_filter is not None:
        event_type_query = event_type_query.where(event_filter)
    event_type_rows = await session.execute(event_type_query)
    events_by_type = {event_type or "unknown": count for event_type, count in event_type_rows.all()}

    zone_counts = (
        select(
            ZoneModel.object_id.label("object_id"),
            func.count(ZoneModel.id).label("zones_count"),
        )
        .group_by(ZoneModel.object_id)
        .subquery()
    )
    event_counts = (
        select(
            EventModel.object_id.label("object_id"),
            func.count(EventModel.id).label("events_count"),
        )
        .group_by(EventModel.object_id)
        .subquery()
    )
    ranked_events = (
        select(
            EventModel.object_id.label("object_id"),
            EventModel.timestamp.label("last_event_time"),
            EventModel.event_type.label("last_event_type"),
            EventModel.label.label("last_event_label"),
            func.row_number().over(
                partition_by=EventModel.object_id,
                order_by=EventModel.timestamp.desc(),
            ).label("row_number"),
        )
        .subquery()
    )

    objects_query = (
        select(
            ObjectModel,
            zone_counts.c.zones_count,
            event_counts.c.events_count,
            AgentStatusModel.status.label("agent_status"),
            AgentStatusModel.detail.label("agent_detail"),
            ranked_events.c.last_event_time,
            ranked_events.c.last_event_type,
            ranked_events.c.last_event_label,
        )
        .outerjoin(zone_counts, zone_counts.c.object_id == ObjectModel.object_id)
        .outerjoin(event_counts, event_counts.c.object_id == ObjectModel.object_id)
        .outerjoin(AgentStatusModel, AgentStatusModel.object_id == ObjectModel.object_id)
        .outerjoin(
            ranked_events,
            (ranked_events.c.object_id == ObjectModel.object_id)
            & (ranked_events.c.row_number == 1),
        )
    )
    if object_filter is not None:
        objects_query = objects_query.where(object_filter)
    objects_result = await session.execute(objects_query.order_by(ObjectModel.object_id))

    objects_stats = []
    for (
        obj,
        zones_count_obj,
        events_count_obj,
        agent_status,
        agent_detail,
        last_event_time,
        last_event_type,
        last_event_label,
    ) in objects_result.all():
        objects_stats.append({
            "object_id": obj.object_id,
            "name": obj.name,
            "modbus_host": obj.modbus_host,
            "modbus_port": obj.modbus_port,
            "zones_count": zones_count_obj or 0,
            "events_count": events_count_obj or 0,
            "agent_status": agent_status or "unknown",
            "agent_detail": agent_detail or "",
            "last_event_time": last_event_time.isoformat() if last_event_time else None,
            "last_event_type": last_event_type,
            "last_event_label": last_event_label,
        })

    return {
        "totals": {
            "objects": objects_count or 0,
            "zones": zones_count or 0,
            "events_24h": events_24h or 0,
            "events_total": events_total or 0,
            "events_by_type": events_by_type,
            "unknown_events_24h": events_by_type.get("unknown", 0),
            "alarm_events_24h": events_by_type.get("alarm", 0),
            "fault_events_24h": events_by_type.get("fault", 0),
            "attention_events_24h": events_by_type.get("attention", 0),
        },
        "objects": objects_stats,
    }



# ---------- AUTHENTICATION / USERS ----------

@app.post("/api/auth/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    async with async_session_factory() as session:
        row = await session.execute(select(UserModel, RoleModel).join(RoleModel, RoleModel.id == UserModel.role_id).where(UserModel.username == payload.username))
        found = row.first()
        if not found or not found[0].is_active or not verify_password(payload.password, found[0].password_hash):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        user, role = found
        token = new_session_token()
        session.add(SessionModel(token_hash=session_hash(token), user_id=user.id, expires_at=session_expiry()))
        await session.commit()
        response.set_cookie("tengsl_session", token, httponly=True, secure=settings.auth_cookie_secure, samesite="lax", max_age=settings.auth_session_ttl_hours * 3600, path="/")
        return {"user": {"id": user.id, "username": user.username, "display_name": user.display_name, "role": role.name}}

@app.post("/api/auth/logout", status_code=204)
async def logout(request: Request, response: Response) -> Response:
    token = request.cookies.get("tengsl_session")
    if token:
        async with async_session_factory() as session:
            item = await session.scalar(select(SessionModel).where(SessionModel.token_hash == session_hash(token)))
            if item:
                await session.delete(item)
                await session.commit()
    response.delete_cookie("tengsl_session", path="/")
    return Response(status_code=204)

async def _user_out(session: AsyncSession, user: UserModel) -> UserOut:
    role = await session.get(RoleModel, user.role_id)
    perm_rows = await session.execute(select(PermissionModel.code).join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id).where(RolePermissionModel.role_id == user.role_id))
    obj_rows = await session.execute(select(UserObjectModel.object_id).where(UserObjectModel.user_id == user.id))
    return UserOut(id=user.id, username=user.username, display_name=user.display_name, role=role.name if role else "", is_active=user.is_active, object_ids=[r[0] for r in obj_rows.all()], permissions=[r[0] for r in perm_rows.all()])

@app.post("/api/auth/password", status_code=204)
async def change_my_password(payload: PasswordChange, request: Request) -> Response:
    if not verify_password(payload.current_password, request.state.user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from current password")
    try:
        new_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with async_session_factory() as session:
        user = await session.get(UserModel, request.state.user.id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.password_hash = new_hash
        await session.commit()
    return Response(status_code=204)

@app.get("/api/auth/me", response_model=MeOut)
async def auth_me(request: Request) -> UserOut:
    async with async_session_factory() as session:
        return await _user_out(session, request.state.user)

@app.get("/api/roles", response_model=list[RoleOut])
async def list_roles() -> list[dict]:
    async with async_session_factory() as session:
        rows = await session.execute(select(RoleModel).order_by(RoleModel.id))
        result = []
        for role in rows.scalars().all():
            perms = await session.execute(select(PermissionModel.code).join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id).where(RolePermissionModel.role_id == role.id))
            result.append({"id": role.id, "name": role.name, "description": role.description, "permissions": [r[0] for r in perms.all()]})
        return result

@app.get("/api/users", response_model=list[UserOut])
async def list_users() -> list[UserOut]:
    async with async_session_factory() as session:
        rows = await session.execute(select(UserModel).order_by(UserModel.username))
        return [await _user_out(session, user) for user in rows.scalars().all()]

@app.post("/api/users", response_model=UserOut, status_code=201)
async def create_user(payload: UserCreate) -> UserOut:
    async with async_session_factory() as session:
        if await session.scalar(select(UserModel).where(UserModel.username == payload.username)):
            raise HTTPException(status_code=409, detail="Username already exists")
        role = await session.scalar(select(RoleModel).where(RoleModel.name == payload.role))
        if not role:
            raise HTTPException(status_code=400, detail="Unknown role")
        user = UserModel(username=payload.username, display_name=payload.display_name, password_hash=hash_password(payload.password), role_id=role.id, is_active=payload.is_active)
        session.add(user); await session.flush()
        for object_id in payload.object_ids:
            if await session.get(ObjectModel, object_id) is None:
                raise HTTPException(status_code=400, detail=f"Object '{object_id}' not found")
            session.add(UserObjectModel(user_id=user.id, object_id=object_id))
        await session.commit(); await session.refresh(user)
        return await _user_out(session, user)

@app.get("/api/users/export")
async def export_users() -> dict:
    async with async_session_factory() as session:
        rows = await session.execute(select(UserModel).order_by(UserModel.username))
        users = []
        for user in rows.scalars().all():
            role = await session.get(RoleModel, user.role_id)
            objs = await session.execute(select(UserObjectModel.object_id).where(UserObjectModel.user_id == user.id))
            users.append({"username": user.username, "display_name": user.display_name, "role": role.name, "object_ids": [r[0] for r in objs.all()], "is_active": user.is_active})
        return {"version": 1, "users": users}

@app.post("/api/users/import", response_model=UsersImportResult)
async def import_users(payload: UsersImportPayload) -> UsersImportResult:
    created = updated = 0
    async with async_session_factory() as session:
        for item in payload.users:
            role = await session.scalar(select(RoleModel).where(RoleModel.name == item.role))
            if not role: raise HTTPException(status_code=400, detail=f"Unknown role: {item.role}")
            for object_id in item.object_ids:
                if await session.get(ObjectModel, object_id) is None: raise HTTPException(status_code=400, detail=f"Object '{object_id}' not found")
            user = await session.scalar(select(UserModel).where(UserModel.username == item.username))
            if user is None:
                if not item.password:
                    raise HTTPException(status_code=400, detail=f"Password required for new user: {item.username}")
                user = UserModel(username=item.username, display_name=item.display_name, password_hash=hash_password(item.password), role_id=role.id, is_active=item.is_active)
                session.add(user); await session.flush(); created += 1
            else:
                user.display_name=item.display_name; user.role_id=role.id; user.is_active=item.is_active
                if item.password: user.password_hash=hash_password(item.password)
                updated += 1
            await session.execute(text("DELETE FROM user_objects WHERE user_id = :uid"), {"uid": user.id})
            for object_id in item.object_ids: session.add(UserObjectModel(user_id=user.id, object_id=object_id))
        await session.commit()
    return UsersImportResult(users_created=created, users_updated=updated)


@app.put("/api/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, payload: UserUpdate) -> UserOut:
    async with async_session_factory() as session:
        user = await session.get(UserModel, user_id)
        if not user: raise HTTPException(status_code=404, detail="User not found")
        if payload.role is not None:
            role = await session.scalar(select(RoleModel).where(RoleModel.name == payload.role))
            if not role: raise HTTPException(status_code=400, detail="Unknown role")
            user.role_id = role.id
        if payload.display_name is not None: user.display_name = payload.display_name
        if payload.password is not None: user.password_hash = hash_password(payload.password)
        if payload.is_active is not None: user.is_active = payload.is_active
        if payload.object_ids is not None:
            await session.execute(text("DELETE FROM user_objects WHERE user_id = :uid"), {"uid": user.id})
            for object_id in payload.object_ids:
                if await session.get(ObjectModel, object_id) is None: raise HTTPException(status_code=400, detail=f"Object '{object_id}' not found")
                session.add(UserObjectModel(user_id=user.id, object_id=object_id))
        await session.commit(); await session.refresh(user)
        return await _user_out(session, user)

@app.delete("/api/users/{user_id}", status_code=204)
async def delete_user(user_id: int, request: Request) -> Response:
    async with async_session_factory() as session:
        user = await session.get(UserModel, user_id)
        if not user: raise HTTPException(status_code=404, detail="User not found")
        role = await session.get(RoleModel, user.role_id)
        if role and role.name == "admin":
            count = await session.scalar(select(func.count(UserModel.id)).join(RoleModel, RoleModel.id == UserModel.role_id).where(RoleModel.name == "admin", UserModel.is_active == True))
            if count <= 1: raise HTTPException(status_code=400, detail="Cannot remove the last administrator")
        if user.id == request.state.user.id: raise HTTPException(status_code=400, detail="Cannot delete current user")
        await session.delete(user); await session.commit()
    return Response(status_code=204)

# ---------- NOTIFICATIONS ----------

@app.get("/api/notifications", response_model=list[NotificationPreferenceOut])
async def list_notification_preferences(request: Request) -> list[dict]:
    async with async_session_factory() as session:
        objects = await _visible_object_ids(session, request.state.user)
        query = select(ObjectModel).order_by(ObjectModel.object_id)
        if objects is not None:
            query = query.where(ObjectModel.object_id.in_(objects))
        rows = (await session.execute(query)).scalars().all()
        prefs = await session.execute(select(UserNotificationPreferenceModel, TestRunModel).where(UserNotificationPreferenceModel.user_id == request.state.user.id))
        by_object = {p.object_id: p for p in prefs.scalars().all()}
        return [{
            "object_id": obj.object_id, "object_name": obj.name,
            "enabled": by_object.get(obj.object_id).enabled if obj.object_id in by_object else True,
            "critical": by_object.get(obj.object_id).critical if obj.object_id in by_object else True,
            "warning": by_object.get(obj.object_id).warning if obj.object_id in by_object else True,
            "info": by_object.get(obj.object_id).info if obj.object_id in by_object else False,
            "topic": user_topic(request.state.user.id),
            "subscribe_url": subscribe_url(request.state.user.id),
        } for obj in rows]


@app.put("/api/notifications/{object_id}", response_model=NotificationPreferenceOut)
async def update_notification_preferences(object_id: str, payload: NotificationPreferenceUpdate, request: Request) -> dict:
    async with async_session_factory() as session:
        obj = await session.get(ObjectModel, object_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Object not found")
        if not await _has_object_access(session, request.state.user, object_id):
            raise HTTPException(status_code=403, detail="Object access denied")
        pref = await session.get(UserNotificationPreferenceModel, {"user_id": request.state.user.id, "object_id": object_id})
        if pref is None:
            pref = UserNotificationPreferenceModel(user_id=request.state.user.id, object_id=object_id)
            session.add(pref)
        pref.enabled, pref.critical, pref.warning, pref.info = payload.enabled, payload.critical, payload.warning, payload.info
        await session.commit()
        return {"object_id": obj.object_id, "object_name": obj.name, "enabled": pref.enabled, "critical": pref.critical, "warning": pref.warning, "info": pref.info, "topic": user_topic(request.state.user.id), "subscribe_url": subscribe_url(request.state.user.id)}


@app.post("/api/notifications/test")
async def test_notification(payload: NotificationTestRequest, request: Request) -> dict:
    async with async_session_factory() as session:
        if payload.object_id and not await _has_object_access(session, request.state.user, payload.object_id):
            raise HTTPException(status_code=403, detail="Object access denied")
    await publish_ntfy(request.state.user.id, "TENGSL - test notification", "Канал уведомлений ntfy работает.", "default", "tengsl,white_check_mark")
    return {"status": "sent"}



# ---------- OBJECTS (CRUD) ----------

@app.get("/api/objects", response_model=list[ObjectOut])
async def list_objects(request: Request, session: AsyncSession = Depends(get_session)) -> list[ObjectModel]:
    visible = await _visible_object_ids(session, request.state.user)
    query = select(ObjectModel).order_by(ObjectModel.object_id)
    if visible is not None:
        query = query.where(ObjectModel.object_id.in_(visible))
    result = await session.execute(query)
    return list(result.scalars().all())


@app.post("/api/objects", response_model=ObjectOut, status_code=201)
async def create_new_object(
    obj_data: ObjectCreate,
    session: AsyncSession = Depends(get_session),
) -> ObjectModel:
    existing = await get_object(session, obj_data.object_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Object '{obj_data.object_id}' already exists")
    return await create_object(session, obj_data)


@app.put("/api/objects/{object_id}", response_model=ObjectOut)
async def update_existing_object(
    object_id: str,
    obj_data: ObjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> ObjectModel:
    obj = await get_object(session, object_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    return await update_object(session, obj, obj_data)


@app.delete("/api/objects/{object_id}", status_code=204, response_class=Response)
async def delete_existing_object(
    object_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    obj = await get_object(session, object_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    await delete_object(session, object_id)
    return Response(status_code=204)


# ---------- EVENT CODES ----------

@app.get("/api/event-codes", response_model=list[EventCodeOut])
async def list_event_codes_api(session: AsyncSession = Depends(get_session)) -> list[EventCodeModel]:
    return await list_event_codes(session)


@app.post("/api/event-codes", response_model=EventCodeOut, status_code=201)
async def create_event_code_api(
    code_data: EventCodeCreate,
    session: AsyncSession = Depends(get_session),
) -> EventCodeModel:
    existing = await get_event_code(session, code_data.code)
    if existing:
        raise HTTPException(status_code=409, detail=f"Event code {code_data.code} already exists")
    return await create_event_code(session, EventCodeModel(
        code=code_data.code,
        label=code_data.label,
        event_type=code_data.event_type,
        description=code_data.description,
        priority=code_data.priority,
        enabled=code_data.enabled,
        source="custom",
    ))


@app.put("/api/event-codes/{code}", response_model=EventCodeOut)
async def update_event_code_api(
    code: int,
    code_data: EventCodeUpdate,
    session: AsyncSession = Depends(get_session),
) -> EventCodeModel:
    event_code = await get_event_code(session, code)
    if not event_code:
        raise HTTPException(status_code=404, detail="Event code not found")
    return await update_event_code(session, event_code, code_data)


@app.delete("/api/event-codes/{code}", status_code=204, response_class=Response)
async def delete_event_code_api(code: int, session: AsyncSession = Depends(get_session)) -> Response:
    event_code = await get_event_code(session, code)
    if not event_code:
        raise HTTPException(status_code=404, detail="Event code not found")
    await delete_event_code(session, event_code)
    return Response(status_code=204)


# ---------- ZONES (CRUD) ----------

@app.get("/api/objects/{object_id}/zones", response_model=list[ZoneOut])
async def list_zones(
    object_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[ZoneModel]:
    obj = await get_object(session, object_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    return await get_zones_by_object(session, object_id)


@app.post("/api/objects/{object_id}/zones", response_model=ZoneResponse, status_code=201)
async def create_new_zone(
    object_id: str,
    zone_data: ZoneCreate,
    session: AsyncSession = Depends(get_session),
) -> ZoneModel:
    existing = await get_zone_by_number(session, object_id, zone_data.zone_number)
    if existing:
        raise HTTPException(status_code=409, detail=f"Zone {zone_data.zone_number} already exists")
    
    obj = await session.get(ObjectModel, object_id)
    if not obj:
        obj = ObjectModel(object_id=object_id, name=f"Object {object_id}")
        session.add(obj)
        await session.flush()
    
    return await create_zone(session, object_id, zone_data)


@app.put("/api/objects/{object_id}/zones/{zone_id}", response_model=ZoneResponse)
async def update_existing_zone(
    object_id: str,
    zone_id: int,
    zone_data: ZoneUpdate,
    session: AsyncSession = Depends(get_session),
) -> ZoneModel:
    zone = await get_zone_by_id(session, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if zone.object_id != object_id:
        raise HTTPException(status_code=400, detail="Zone does not belong to this object")
    return await update_zone(session, zone, zone_data)


@app.delete("/api/objects/{object_id}/zones/{zone_id}", status_code=204, response_class=Response)
async def delete_existing_zone(
    object_id: str,
    zone_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    zone = await get_zone_by_id(session, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if zone.object_id != object_id:
        raise HTTPException(status_code=400, detail="Zone does not belong to this object")
    await delete_zone(session, zone)
    return Response(status_code=204)


# ---------- УПРАВЛЕНИЕ ЗОНАМИ (НОВОЕ) ----------

class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class ZoneCommand(BaseModel):
    """Команда управления зоной."""
    command: str  # "arm", "disarm", "enable", "disable"


@app.post("/api/objects/{object_id}/zones/{zone_number}/command")
async def send_zone_command(
    object_id: str,
    zone_number: int,
    cmd: ZoneCommand,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Отправляет команду в MQTT и создаёт отслеживаемую запись журнала."""
    allowed_commands = {"arm", "disarm", "enable", "disable"}
    if cmd.command not in allowed_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command '{cmd.command}'. Allowed: {', '.join(sorted(allowed_commands))}",
        )

    zone = await get_zone_by_number(session, object_id, zone_number)
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone {zone_number} not found")

    command_id = uuid.uuid4().hex
    command_log = CommandLogModel(
        command_id=command_id,
        object_id=object_id,
        zone_number=zone_number,
        command=cmd.command,
        status="pending",
    )
    session.add(command_log)
    await session.commit()

    command = {
        "command_id": command_id,
        "object_id": object_id,
        "zone_number": zone_number,
        "command": cmd.command,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        topic_prefix = settings.mqtt_topic_prefix.strip("/") or "tengsl"
        await _publish_command(
            f"{topic_prefix}/{object_id}/commands",
            json.dumps(command),
        )
    except Exception as exc:
        command_log.status = "failed"
        command_log.detail = str(exc)[:1024]
        command_log.completed_at = datetime.now(timezone.utc)
        await session.commit()
        logger.error("Failed to publish command %s to MQTT: %s", command_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to send command: {exc}")

    command_log.status = "sent"
    command_log.detail = "Команда опубликована в MQTT; ожидание подтверждения от агента"
    await session.commit()

    await manager.broadcast({
        "type": "command_status",
        "data": {
            "command_id": command_id,
            "object_id": object_id,
            "zone_number": zone_number,
            "command": cmd.command,
            "status": "sent",
            "detail": command_log.detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }, object_id=object_id)

    return {
        "status": "sent",
        "command_id": command_id,
        "command": cmd.command,
        "zone_number": zone_number,
    }


# ---------- AGENT STATUS ----------

@app.get("/api/objects/{object_id}/status", response_model=AgentStatusOut)
async def get_agent_status(object_id: str, session: AsyncSession = Depends(get_session)) -> AgentStatusModel:
    status = await session.get(AgentStatusModel, object_id)
    if status is None:
        raise HTTPException(status_code=404, detail="No status reported for this object yet")
    return status


# ---------- EVENTS ----------

@app.get("/api/objects/{object_id}/events", response_model=list[EventOut])
async def list_events(
    object_id: str,
    limit: int = 100,
    event_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[EventModel]:
    limit = max(1, min(limit, 1000))
    query = select(EventModel).where(EventModel.object_id == object_id)
    if event_type:
        query = query.where(EventModel.event_type == event_type)
    query = query.order_by(EventModel.timestamp.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


# ---------- COMMAND JOURNAL ----------

@app.get("/api/commands", response_model=list[CommandLogOut])
async def list_commands(
    request: Request,
    object_id: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[CommandLogModel]:
    limit = max(1, min(limit, 1000))
    query = select(CommandLogModel)
    visible = await _visible_object_ids(session, request.state.user)
    if visible is not None:
        query = query.where(CommandLogModel.object_id.in_(visible))
    if object_id:
        query = query.where(CommandLogModel.object_id == object_id)
    query = query.order_by(CommandLogModel.requested_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


# ---------- IMPORT / EXPORT OBJECTS ----------

@app.get("/api/objects/export")
async def export_objects(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    visible = await _visible_object_ids(session, request.state.user)
    query = select(ObjectModel).order_by(ObjectModel.object_id)
    if visible is not None:
        query = query.where(ObjectModel.object_id.in_(visible))
    objects_result = await session.execute(query)
    payload_objects = []
    for obj in objects_result.scalars().all():
        zones = await get_zones_by_object(session, obj.object_id)
        payload_objects.append({
            "object_id": obj.object_id,
            "name": obj.name,
            "modbus_host": obj.modbus_host,
            "modbus_port": obj.modbus_port,
            "modbus_unit_id": obj.modbus_unit_id,
            "zones": [
                {
                    "zone_number": zone.zone_number,
                    "zone_name": zone.zone_name,
                    "device_type": zone.device_type,
                    "register_type": zone.register_type,
                    "register_address": zone.register_address,
                } for zone in zones
            ],
        })
    return {"version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "objects": payload_objects}


@app.get("/api/objects/{object_id}/export")
async def export_object(object_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    obj = await get_object(session, object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Object not found")
    zones = await get_zones_by_object(session, object_id)
    return {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "object": {
            "object_id": obj.object_id,
            "name": obj.name,
            "modbus_host": obj.modbus_host,
            "modbus_port": obj.modbus_port,
            "modbus_unit_id": obj.modbus_unit_id,
            "zones": [
                {
                    "zone_number": zone.zone_number,
                    "zone_name": zone.zone_name,
                    "device_type": zone.device_type,
                    "register_type": zone.register_type,
                    "register_address": zone.register_address,
                } for zone in zones
            ],
        },
    }


@app.post("/api/objects/{object_id}/import", response_model=ObjectsImportResult)
async def import_object(
    object_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> ObjectsImportResult:
    # Поддерживаем формат {version, object} для индивидуального экспорта.
    raw_object = payload.get("object") if isinstance(payload, dict) else None
    if raw_object is None:
        raise HTTPException(status_code=400, detail="В файле отсутствует object")
    try:
        item = ObjectImport.model_validate(raw_object)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Некорректная конфигурация объекта: {exc}") from exc

    if item.object_id != object_id:
        raise HTTPException(status_code=400, detail="Идентификатор объекта в файле не совпадает с целевым объектом")

    zones_created = zones_updated = 0
    async with session.begin():
        obj = await session.get(ObjectModel, object_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="Object not found")
        obj.name = item.name
        obj.modbus_host = item.modbus_host
        obj.modbus_port = item.modbus_port
        obj.modbus_unit_id = item.modbus_unit_id

        for zone_data in item.zones:
            result = await session.execute(
                select(ZoneModel).where(
                    ZoneModel.object_id == object_id,
                    ZoneModel.zone_number == zone_data.zone_number,
                )
            )
            zone = result.scalar_one_or_none()
            if zone is None:
                session.add(ZoneModel(
                    object_id=object_id,
                    zone_number=zone_data.zone_number,
                    zone_name=zone_data.zone_name,
                    device_type=zone_data.device_type,
                    register_type=zone_data.register_type,
                    register_address=zone_data.register_address,
                ))
                zones_created += 1
            else:
                zone.zone_name = zone_data.zone_name
                zone.device_type = zone_data.device_type
                zone.register_type = zone_data.register_type
                zone.register_address = zone_data.register_address
                zones_updated += 1

    return ObjectsImportResult(
        objects_created=0,
        objects_updated=1,
        zones_created=zones_created,
        zones_updated=zones_updated,
    )


@app.post("/api/objects/import", response_model=ObjectsImportResult)
async def import_objects(
    payload: ObjectsImportPayload,
    session: AsyncSession = Depends(get_session),
) -> ObjectsImportResult:
    objects_created = objects_updated = zones_created = zones_updated = 0

    async with session.begin():
        for item in payload.objects:
            obj = await session.get(ObjectModel, item.object_id)
            if obj is None:
                obj = ObjectModel(
                    object_id=item.object_id,
                    name=item.name,
                    modbus_host=item.modbus_host,
                    modbus_port=item.modbus_port,
                    modbus_unit_id=item.modbus_unit_id,
                )
                session.add(obj)
                await session.flush()
                objects_created += 1
            else:
                obj.name = item.name
                obj.modbus_host = item.modbus_host
                obj.modbus_port = item.modbus_port
                obj.modbus_unit_id = item.modbus_unit_id
                objects_updated += 1

            for zone_data in item.zones:
                result = await session.execute(
                    select(ZoneModel).where(
                        ZoneModel.object_id == item.object_id,
                        ZoneModel.zone_number == zone_data.zone_number,
                    )
                )
                zone = result.scalar_one_or_none()
                if zone is None:
                    session.add(ZoneModel(
                        object_id=item.object_id,
                        zone_number=zone_data.zone_number,
                        zone_name=zone_data.zone_name,
                        device_type=zone_data.device_type,
                        register_type=zone_data.register_type,
                        register_address=zone_data.register_address,
                    ))
                    zones_created += 1
                else:
                    zone.zone_name = zone_data.zone_name
                    zone.device_type = zone_data.device_type
                    zone.register_type = zone_data.register_type
                    zone.register_address = zone_data.register_address
                    zones_updated += 1

    return ObjectsImportResult(
        objects_created=objects_created,
        objects_updated=objects_updated,
        zones_created=zones_created,
        zones_updated=zones_updated,
    )


# ---------- TEST RESULTS ----------

@app.get("/api/test-results", response_model=TestResultsOut)
async def get_test_results() -> dict:
    path = _diagnostics_path()
    if not path.exists():
        return {
            "generated_at": datetime.now(timezone.utc),
            "overall_status": "not_run",
            "total": 0, "passed": 0, "failed": 0, "skipped": 0,
            "suites": [], "failures": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Do not trust a stale/inconsistent overall_status value from an external
        # test runner. The dashboard status is derived from the actual counters.
        failed = int(data.get("failed", 0) or 0)
        total = int(data.get("total", 0) or 0)
        if total <= 0:
            data["overall_status"] = "not_run"
        elif failed > 0:
            data["overall_status"] = "failed"
        else:
            data["overall_status"] = "passed"
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read test results: {exc}")


# ---------- WEBSOCKET ----------

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    token = websocket.cookies.get("tengsl_session")
    async with async_session_factory() as session:
        user, permissions, object_ids = await _get_current_user(session, token)
        if user is None or "events.view" not in permissions:
            await websocket.close(code=1008, reason="Authentication required")
            return
        object_filter = websocket.query_params.get("object_id")
        if object_filter and not await _has_object_access(session, user, object_filter):
            await websocket.close(code=1008, reason="Object access denied")
            return
        await manager.connect(websocket, object_id_filter=object_filter)
    try:
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket connection closed with error: %s", exc)
    finally:
        await manager.disconnect(websocket)

