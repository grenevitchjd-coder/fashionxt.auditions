from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Applicant, PoolAssignment, ApplicantSource
from app.schemas import (
    ApplicantOut, ManualApplicantIn, CastingStatusUpdate, PoolAssignmentUpdate,
)

router = APIRouter(prefix="/applicants", tags=["applicants"])


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
    applicant.casting_status = payload.casting_status
    if payload.preselect is not None:
        applicant.preselect = payload.preselect
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