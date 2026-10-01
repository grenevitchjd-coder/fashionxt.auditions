import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ShowDay
from app.models_dayof import NonModelAttendee

router = APIRouter(prefix="/day-of", tags=["day-of"])


class NonModelAttendeeIn(BaseModel):
    name: str
    attendee_type: str
    show_day_id: int | None = None   # one specific day...
    all_days: bool = False           # ...or every show day


def _out(a: NonModelAttendee) -> dict:
    return {
        "id": a.id,
        "show_day_id": a.show_day_id,
        "name": a.name,
        "attendee_type": a.attendee_type,
        "group_key": a.group_key,
        "checked_in_at": a.checked_in_at.isoformat() if a.checked_in_at else None,
    }


@router.get("/attendees")
async def list_non_model_attendees(show_day_id: int, db: AsyncSession = Depends(get_db)):
    """Non-model backstage people for one show day, alphabetical."""
    result = await db.execute(
        select(NonModelAttendee)
        .where(NonModelAttendee.show_day_id == show_day_id)
        .order_by(NonModelAttendee.name, NonModelAttendee.id)
    )
    return [_out(a) for a in result.scalars().all()]


@router.post("/attendees")
async def add_non_model_attendee(payload: NonModelAttendeeIn, db: AsyncSession = Depends(get_db)):
    """Adds a person to one show day, or to every show day when all_days is true."""
    name = payload.name.strip()
    attendee_type = payload.attendee_type.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not attendee_type:
        raise HTTPException(status_code=400, detail="Type is required")
    if len(name) > 200 or len(attendee_type) > 100:
        raise HTTPException(status_code=400, detail="Name or type is too long")

    if payload.all_days:
        days = (await db.execute(select(ShowDay).order_by(ShowDay.show_date, ShowDay.id))).scalars().all()
        if not days:
            raise HTTPException(status_code=400, detail="No show days exist yet")
        day_ids = [d.id for d in days]
        group_key = uuid.uuid4().hex
    else:
        if payload.show_day_id is None or await db.get(ShowDay, payload.show_day_id) is None:
            raise HTTPException(status_code=400, detail="Pick a valid show day")
        day_ids = [payload.show_day_id]
        group_key = None

    created = []
    for day_id in day_ids:
        row = NonModelAttendee(show_day_id=day_id, name=name, attendee_type=attendee_type, group_key=group_key)
        db.add(row)
        created.append(row)
    await db.commit()
    for row in created:
        await db.refresh(row)
    return [_out(r) for r in created]


@router.delete("/attendees/{attendee_id}")
async def remove_non_model_attendee(attendee_id: int, all_days: bool = False, db: AsyncSession = Depends(get_db)):
    """Removes one day's entry, or (all_days=true) every entry that was added together."""
    row = await db.get(NonModelAttendee, attendee_id)
    if not row:
        raise HTTPException(status_code=404, detail="Attendee not found")
    if all_days and row.group_key:
        await db.execute(delete(NonModelAttendee).where(NonModelAttendee.group_key == row.group_key))
    else:
        await db.delete(row)
    await db.commit()
    return {"status": "deleted"}