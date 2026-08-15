from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Applicant, PoolAssignment, ApplicantSource, Photo, CastingStatus
from app.schemas import (
    ApplicantOut, ManualApplicantIn, CastingStatusUpdate, PoolAssignmentUpdate, CheckinIn,
)

router = APIRouter(prefix="/applicants", tags=["applicants"])


@router.get("/photo-station-list")
async def photo_station_list(event_id: int, db: AsyncSession = Depends(get_db)):
    """
    Lightweight bulk fetch for the photo station — loaded ONCE per event.
    Returns each person's captured photo tags (not just a count) so the
    frontend can tell exactly which required shots are still missing.
    """
    result = await db.execute(
        select(
            Applicant.id, Applicant.full_name, Applicant.audition_number,
            Applicant.category, Applicant.casting_status, Applicant.preselect,
        )
        .where(Applicant.event_id == event_id, Applicant.audition_number.isnot(None))
        .order_by(Applicant.audition_number)
    )
    applicants = result.all()
    ids = [a.id for a in applicants]

    tags_by_applicant = {}
    if ids:
        photo_result = await db.execute(select(Photo.applicant_id, Photo.tag).where(Photo.applicant_id.in_(ids)))
        for pid, tag in photo_result.all():
            tags_by_applicant.setdefault(pid, []).append(tag)

    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "audition_number": a.audition_number,
            "category": a.category,
            "casting_status": a.casting_status,
            "preselect": a.preselect,
            "tags": tags_by_applicant.get(a.id, []),
            "photo_count": len(tags_by_applicant.get(a.id, [])),
        }
        for a in applicants
    ]


@router.get("/checkin-list")
async def checkin_list(db: AsyncSession = Depends(get_db)):
    """
    Lightweight bulk fetch for the check-in station — loaded ONCE when staff
    open the screen, then filtered entirely client-side (no per-keystroke
    API calls). Keeps venue wifi out of the critical path for search speed.
    """
    result = await db.execute(
        select(
            Applicant.id, Applicant.full_name, Applicant.phone, Applicant.email,
            Applicant.category, Applicant.event_id, Applicant.audition_number,
        ).order_by(Applicant.full_name)
    )
    rows = result.all()
    return [
        {
            "id": r.id, "full_name": r.full_name, "phone": r.phone, "email": r.email,
            "category": r.category, "event_id": r.event_id, "audition_number": r.audition_number,
        }
        for r in rows
    ]


@router.put("/{applicant_id}/checkin")
async def checkin_applicant(applicant_id: int, payload: CheckinIn, db: AsyncSession = Depends(get_db)):
    """Assign an audition number for today's event to an already-existing applicant."""
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    applicant.event_id = payload.event_id
    applicant.audition_number = payload.audition_number
    if payload.preselect is not None:
        applicant.preselect = payload.preselect

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That audition number is already taken for this event")

    await db.refresh(applicant)
    return applicant


@router.get("/{applicant_id}/detail")
async def get_applicant_detail(applicant_id: int, db: AsyncSession = Depends(get_db)):
    """Full record for the detail screen — photos, measurements, pool included."""
    result = await db.execute(
        select(Applicant)
        .where(Applicant.id == applicant_id)
        .options(
            selectinload(Applicant.photos),
            selectinload(Applicant.measurement),
            selectinload(Applicant.pool_assignment),
        )
    )
    applicant = result.scalar_one_or_none()
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    return {
        "id": applicant.id,
        "full_name": applicant.full_name,
        "email": applicant.email,
        "phone": applicant.phone,
        "category": applicant.category,
        "audition_number": applicant.audition_number,
        "casting_status": applicant.casting_status,
        "preselect": applicant.preselect,
        "source": applicant.source,
        "agency_name": applicant.agency_name,
        "address_city": applicant.address_city,
        "address_state": applicant.address_state,
        "willing_without_lodging": applicant.willing_without_lodging,
        "pool": applicant.pool_assignment.pool if applicant.pool_assignment else None,
        "photos": [{"id": p.id, "url": p.url, "tag": p.tag, "source": p.source} for p in applicant.photos],
        "measurement": (
            {
                "height": applicant.measurement.height,
                "bust_chest": applicant.measurement.bust_chest,
                "waist_size": applicant.measurement.waist_size,
                "hip_size": applicant.measurement.hip_size,
                "shoe_size": applicant.measurement.shoe_size,
                "notes": applicant.measurement.notes,
            }
            if applicant.measurement else None
        ),
    }


@router.get("/search", response_model=list[ApplicantOut])
async def search_applicants(
    q: str = Query(..., min_length=1),
    event_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Search by name or audition number — used to pull up a model during check-in."""
    stmt = select(Applicant).where(
        or_(
            Applicant.full_name.ilike(f"%{q}%"),
            Applicant.audition_number == (int(q) if q.isdigit() else -1),
        )
    )
    if event_id:
        stmt = stmt.where(Applicant.event_id == event_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/roster", response_model=list[ApplicantOut])
async def roster(event_id: int, db: AsyncSession = Depends(get_db)):
    """Full roster for a given city/event — the audition-day working list."""
    result = await db.execute(
        select(Applicant).where(Applicant.event_id == event_id).order_by(Applicant.audition_number)
    )
    return result.scalars().all()


@router.post("/manual", response_model=ApplicantOut)
async def add_manual_applicant(payload: ManualApplicantIn, db: AsyncSession = Depends(get_db)):
    """Add a guest/walk-in model directly, bypassing the application form entirely."""
    applicant = Applicant(**payload.model_dump(), source=ApplicantSource.manual)
    db.add(applicant)
    await db.commit()
    await db.refresh(applicant)
    return applicant


@router.patch("/{applicant_id}/casting-status", response_model=ApplicantOut)
async def update_casting_status(
    applicant_id: int, payload: CastingStatusUpdate, db: AsyncSession = Depends(get_db)
):
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    if payload.preselect is not None:
        applicant.preselect = payload.preselect

    if payload.preselect:
        # Preselects skip judging entirely — automatically approved for the show.
        applicant.casting_status = CastingStatus.yes
    else:
        applicant.casting_status = payload.casting_status

    await db.commit()
    await db.refresh(applicant)
    return applicant


@router.put("/{applicant_id}/pool")
async def set_pool(applicant_id: int, payload: PoolAssignmentUpdate, db: AsyncSession = Depends(get_db)):
    """
    Independently editable at any time — a model can move between pools,
    or be pulled out entirely (pool=null), regardless of prior stage.
    """
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    existing = await db.get(PoolAssignment, applicant_id)
    if payload.pool is None:
        if existing:
            await db.delete(existing)
    elif existing:
        existing.pool = payload.pool
    else:
        db.add(PoolAssignment(applicant_id=applicant_id, pool=payload.pool))

    await db.commit()
    return {"applicant_id": applicant_id, "pool": payload.pool}