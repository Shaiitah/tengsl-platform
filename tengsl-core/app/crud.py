"""CRUD-операции для работы с объектами и зонами."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventCodeModel, ObjectModel, ZoneModel
from app.schemas import (
    ObjectCreate,
    ObjectUpdate,
    ZoneCreate,
    ZoneUpdate,
)


# ---------- ОБЪЕКТЫ ----------

async def get_object(session: AsyncSession, object_id: str) -> ObjectModel | None:
    return await session.get(ObjectModel, object_id)


async def create_object(session: AsyncSession, obj_data: ObjectCreate) -> ObjectModel:
    obj = ObjectModel(
        object_id=obj_data.object_id,
        name=obj_data.name,
        modbus_host=obj_data.modbus_host,
        modbus_port=obj_data.modbus_port,
        modbus_unit_id=obj_data.modbus_unit_id,
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def update_object(session: AsyncSession, obj: ObjectModel, obj_data: ObjectUpdate) -> ObjectModel:
    update_data = obj_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(obj, field, value)
    await session.commit()
    await session.refresh(obj)
    return obj


async def delete_object(session: AsyncSession, object_id: str) -> None:
    obj = await session.get(ObjectModel, object_id)
    if obj:
        await session.delete(obj)
        await session.commit()


# ---------- ЗОНЫ ----------

async def get_zones_by_object(session: AsyncSession, object_id: str) -> list[ZoneModel]:
    result = await session.execute(
        select(ZoneModel)
        .where(ZoneModel.object_id == object_id)
        .order_by(ZoneModel.zone_number)
    )
    return list(result.scalars().all())


async def get_zone_by_id(session: AsyncSession, zone_id: int) -> ZoneModel | None:
    return await session.get(ZoneModel, zone_id)


async def get_zone_by_number(session: AsyncSession, object_id: str, zone_number: int) -> ZoneModel | None:
    result = await session.execute(
        select(ZoneModel)
        .where(ZoneModel.object_id == object_id, ZoneModel.zone_number == zone_number)
    )
    return result.scalar_one_or_none()


async def create_zone(session: AsyncSession, object_id: str, zone_data: ZoneCreate) -> ZoneModel:
    zone = ZoneModel(
        object_id=object_id,
        zone_number=zone_data.zone_number,
        zone_name=zone_data.zone_name,
        device_type=zone_data.device_type,
        register_type=zone_data.register_type,
        register_address=zone_data.register_address,
    )
    session.add(zone)
    await session.commit()
    await session.refresh(zone)
    return zone


async def update_zone(session: AsyncSession, zone: ZoneModel, zone_data: ZoneUpdate) -> ZoneModel:
    update_data = zone_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(zone, field, value)
    await session.commit()
    await session.refresh(zone)
    return zone


async def delete_zone(session: AsyncSession, zone: ZoneModel) -> None:
    await session.delete(zone)
    await session.commit()

# ---------- КОДЫ СОБЫТИЙ ----------

async def get_event_code(session: AsyncSession, code: int) -> EventCodeModel | None:
    return await session.get(EventCodeModel, code)


async def list_event_codes(session: AsyncSession) -> list[EventCodeModel]:
    result = await session.execute(
        select(EventCodeModel).order_by(EventCodeModel.code)
    )
    return list(result.scalars().all())


async def create_event_code(session: AsyncSession, code: EventCodeModel) -> EventCodeModel:
    session.add(code)
    await session.commit()
    await session.refresh(code)
    return code


async def update_event_code(session: AsyncSession, code: EventCodeModel, data) -> EventCodeModel:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(code, field, value)
    await session.commit()
    await session.refresh(code)
    return code


async def delete_event_code(session: AsyncSession, code: EventCodeModel) -> None:
    await session.delete(code)
    await session.commit()
