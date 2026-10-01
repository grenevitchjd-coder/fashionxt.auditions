import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Applicant, Designer, DesignerAssignment, ShowDay
from app.models_dayof import DayModelStatus, DesignerCheckin, NonModelAttendee
from app.timefmt import walk_label

router = APIRouter(prefix="/day-of", tags=["day-of"])


# ---------------------------------------------------------------------------
# Non-model attendees (the "Non-Model Check-In Additions" page)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# The day's roster — shared by the check-in page and the printouts
# ---------------------------------------------------------------------------
def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _pick_photo(photos) -> str | None:
    headshot = next((p for p in photos if p.tag == "headshot"), None)
    chosen = headshot or (photos[0] if photos else None)
    return chosen.url if chosen else None


async def load_day_roster(db: AsyncSession, show_day_id: int) -> dict:
    """
    Everyone expected backstage on one show day:
      models    - every model assigned to at least one designer that day, alphabetical,
                  each with their designers in show order
      designers - that day's designers in show order
      staff     - the non-model additions for that day, alphabetical
    """
    day = await db.get(ShowDay, show_day_id)
    if not day:
        raise HTTPException(status_code=404, detail="Show day not found")

    # Designers for the day, in show order
    designer_rows = (
        await db.execute(
            select(Designer)
            .where(Designer.show_day_id == show_day_id)
            .options(selectinload(Designer.assignments))
            .order_by(Designer.order_in_day, Designer.id)
        )
    ).scalars().all()
    designer_ids = [d.id for d in designer_rows]

    designer_checkins = {}
    if designer_ids:
        res = await db.execute(select(DesignerCheckin).where(DesignerCheckin.designer_id.in_(designer_ids)))
        designer_checkins = {c.designer_id: c.checked_in_at for c in res.scalars().all()}

    # Which designers each model walks for (already in show order)
    designers_by_model: dict[int, list[dict]] = {}
    for d in designer_rows:
        for a in d.assignments:
            designers_by_model.setdefault(a.applicant_id, []).append(
                {"designer_id": d.id, "name": d.name, "order_in_day": d.order_in_day,
                 "walkthrough": walk_label(d.walkthrough_time)}
            )

    models = []
    if designers_by_model:
        applicants = (
            await db.execute(
                select(Applicant)
                .where(Applicant.id.in_(list(designers_by_model.keys())))
                .options(selectinload(Applicant.photos))
            )
        ).scalars().all()

        statuses = {}
        res = await db.execute(
            select(DayModelStatus).where(
                DayModelStatus.show_day_id == show_day_id,
                DayModelStatus.applicant_id.in_(list(designers_by_model.keys())),
            )
        )
        statuses = {s.applicant_id: s for s in res.scalars().all()}

        for a in applicants:
            st = statuses.get(a.id)
            models.append({
                "applicant_id": a.id,
                "full_name": a.full_name,
                "photo_url": _pick_photo(a.photos),
                "designers": designers_by_model[a.id],
                "checked_in_at": _iso(st.checked_in_at) if st else None,
                "note": st.note if st else None,
            })
        models.sort(key=lambda m: (m["full_name"] or "").lower())

    staff_rows = (
        await db.execute(
            select(NonModelAttendee)
            .where(NonModelAttendee.show_day_id == show_day_id)
            .order_by(NonModelAttendee.name, NonModelAttendee.id)
        )
    ).scalars().all()

    return {
        "day": {
            "id": day.id,
            "name": day.name,
            "show_date": day.show_date.isoformat() if day.show_date else None,
        },
        "models": models,
        "designers": [
            {
                "id": d.id,
                "name": d.name,
                "order_in_day": d.order_in_day,
                "model_count": len(d.assignments),
                "walkthrough": walk_label(d.walkthrough_time),
                "checked_in_at": _iso(designer_checkins.get(d.id)),
            }
            for d in designer_rows
        ],
        "staff": [
            {"id": s.id, "name": s.name, "attendee_type": s.attendee_type, "checked_in_at": _iso(s.checked_in_at)}
            for s in staff_rows
        ],
    }


@router.get("/checkin-list")
async def checkin_list(show_day_id: int, db: AsyncSession = Depends(get_db)):
    return await load_day_roster(db, show_day_id)


# ---------------------------------------------------------------------------
# Check-in actions
# ---------------------------------------------------------------------------
class ModelCheckInIn(BaseModel):
    show_day_id: int
    checked_in: bool


class ModelNoteIn(BaseModel):
    show_day_id: int
    note: str | None = None


class CheckInIn(BaseModel):
    checked_in: bool


async def _get_or_create_status(db: AsyncSession, applicant_id: int, show_day_id: int) -> DayModelStatus:
    async def _find():
        res = await db.execute(
            select(DayModelStatus).where(
                DayModelStatus.applicant_id == applicant_id, DayModelStatus.show_day_id == show_day_id
            )
        )
        return res.scalar_one_or_none()

    status = await _find()
    if status:
        return status
    if await db.get(Applicant, applicant_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    if await db.get(ShowDay, show_day_id) is None:
        raise HTTPException(status_code=404, detail="Show day not found")
    status = DayModelStatus(applicant_id=applicant_id, show_day_id=show_day_id)
    db.add(status)
    try:
        await db.flush()
    except IntegrityError:
        # Another device created it a split second earlier — use theirs.
        await db.rollback()
        status = await _find()
    return status


@router.post("/models/{applicant_id}/check-in")
async def set_model_check_in(applicant_id: int, payload: ModelCheckInIn, db: AsyncSession = Depends(get_db)):
    status = await _get_or_create_status(db, applicant_id, payload.show_day_id)
    status.checked_in_at = datetime.now(timezone.utc) if payload.checked_in else None
    await db.commit()
    await db.refresh(status)
    return {"applicant_id": applicant_id, "checked_in_at": _iso(status.checked_in_at)}


@router.put("/models/{applicant_id}/note")
async def set_model_note(applicant_id: int, payload: ModelNoteIn, db: AsyncSession = Depends(get_db)):
    note = (payload.note or "").strip()
    if len(note) > 1000:
        raise HTTPException(status_code=400, detail="Note is too long")
    status = await _get_or_create_status(db, applicant_id, payload.show_day_id)
    status.note = note or None
    await db.commit()
    await db.refresh(status)
    return {"applicant_id": applicant_id, "note": status.note}


@router.post("/designers/{designer_id}/check-in")
async def set_designer_check_in(designer_id: int, payload: CheckInIn, db: AsyncSession = Depends(get_db)):
    if await db.get(Designer, designer_id) is None:
        raise HTTPException(status_code=404, detail="Designer not found")
    row = await db.get(DesignerCheckin, designer_id)
    if payload.checked_in:
        if row is None:
            row = DesignerCheckin(designer_id=designer_id, checked_in_at=datetime.now(timezone.utc))
            db.add(row)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
            row = await db.get(DesignerCheckin, designer_id)
        return {"designer_id": designer_id, "checked_in_at": _iso(row.checked_in_at) if row else None}
    if row is not None:
        await db.delete(row)
        await db.commit()
    return {"designer_id": designer_id, "checked_in_at": None}


@router.post("/attendees/{attendee_id}/check-in")
async def set_attendee_check_in(attendee_id: int, payload: CheckInIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(NonModelAttendee, attendee_id)
    if not row:
        raise HTTPException(status_code=404, detail="Attendee not found")
    row.checked_in_at = datetime.now(timezone.utc) if payload.checked_in else None
    await db.commit()
    await db.refresh(row)
    return {"attendee_id": attendee_id, "checked_in_at": _iso(row.checked_in_at)}