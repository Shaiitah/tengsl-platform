"""ntfy provider for TENGSL event notifications."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import json
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from app.config import settings
from app.database import async_session_factory
from app.models import UserModel, RoleModel, UserNotificationPreferenceModel
from sqlalchemy import select

logger = logging.getLogger(__name__)


def user_topic(user_id: int) -> str:
    digest = hmac.new(settings.ntfy_topic_secret.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()[:24]
    return f"tengsl-user-{user_id}-{digest}"


def subscribe_url(user_id: int) -> str:
    return f"{settings.ntfy_public_url.rstrip('/')}/{user_topic(user_id)}"


def _publish_sync(topic: str, title: str, body: str, priority: str, tags: str) -> None:
    url = f"{settings.ntfy_base_url.rstrip('/')}/{topic}"
    # Use ntfy's JSON API instead of HTTP metadata headers. urllib encodes headers
    # as latin-1, so Cyrillic/Unicode event titles would otherwise crash delivery.
    payload = {
        "topic": topic,
        "title": title,
        "message": body,
        "priority": priority,
        "tags": [tag for tag in tags.split(",") if tag],
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
    }
    if settings.ntfy_publisher_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_publisher_token}"
    req = urllib_request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    with urllib_request.urlopen(req, timeout=settings.ntfy_timeout_seconds) as response:
        if response.status >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


async def publish(user_id: int, title: str, body: str, priority: str = "default", tags: str = "tengsl") -> None:
    if not settings.ntfy_enabled:
        return
    topic = user_topic(user_id)
    try:
        await asyncio.to_thread(_publish_sync, topic, title, body, priority, tags)
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
        logger.warning("ntfy delivery failed for user %s: %s", user_id, exc)


def _notification_severity(event_type: str) -> tuple[str, str]:
    if event_type == "alarm":
        return "max", "rotating_light"
    if event_type == "fault":
        return "high", "warning"
    if event_type in {"attention", "disabled"}:
        return "default", "warning"
    return "low", "information_source"


async def notify_event_users(object_id: str, zone_number: int, zone_name: str, event_type: str, label: str, timestamp) -> None:
    if not settings.ntfy_enabled:
        return
    severity = "critical" if event_type == "alarm" else "warning" if event_type in {"fault", "attention", "disabled"} else "info"
    priority, tag = _notification_severity(event_type)
    async with async_session_factory() as session:
        result = await session.execute(select(UserModel, RoleModel).join(RoleModel, RoleModel.id == UserModel.role_id).where(UserModel.is_active == True))
        for user, role in result.all():
            if role.name != "admin":
                allowed = await session.scalar(select(UserNotificationPreferenceModel.user_id).where(UserNotificationPreferenceModel.user_id == user.id, UserNotificationPreferenceModel.object_id == object_id))
                if allowed is None:
                    from app.models import UserObjectModel
                    allowed = await session.scalar(select(UserObjectModel.user_id).where(UserObjectModel.user_id == user.id, UserObjectModel.object_id == object_id))
                if allowed is None:
                    continue
            pref = await session.get(UserNotificationPreferenceModel, {"user_id": user.id, "object_id": object_id})
            if pref is not None and (not pref.enabled or not getattr(pref, severity)):
                continue
            title = f"TENGSL — {label or event_type.upper()}"
            body = f"Объект: {object_id}\nЗона: {zone_number} — {zone_name or 'без названия'}\nСобытие: {event_type}\nВремя: {timestamp.astimezone().strftime('%d.%m.%Y %H:%M:%S')}"
            await publish(user.id, title, body, priority, f"tengsl,{tag}")
