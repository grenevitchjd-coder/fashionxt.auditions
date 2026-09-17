from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
import secrets

from app.database import get_db
from app.models import ShowDay, Designer, DesignerAssignment, Applicant, Photo
from app.schemas import DesignerIn, DesignerAssignmentIn, DeckPreferenceIn

router = APIRouter(tags=["designers"])


@router.get("/show-days")
async def list_show_days(db: AsyncSession = Depends(get_db)):
    """The 3 actual FashioNXT Week runway days — Thu/Fri/Sat, not the audition days."""
    result = await db.execute(select(ShowDay).order_by(ShowDay.show_date, ShowDay.id))
    days = result.scalars().all()
    return [
        {"id": d.id, "name": d.name, "show_date": d.show_date.isoformat() if d.show_date else None}
        for d in days
    ]


@router.get("/designers")
async def list_designers(show_day_id: int, db: AsyncSession = Depends(get_db)):
    """Every designer for a show day, in lineup order, with their currently assigned models (in walk order)."""
    result = await db.execute(
        select(Designer)
        .where(Designer.show_day_id == show_day_id)
        .options(selectinload(Designer.assignments).selectinload(DesignerAssignment.applicant))
        .order_by(Designer.order_in_day)
    )
    designers = result.scalars().all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "order_in_day": d.order_in_day,
            "notes": d.notes,
            "roster_only": d.roster_only,
            "models": [
                {
                    "applicant_id": a.applicant.id,
                    "full_name": a.applicant.full_name,
                    "category": a.applicant.category,
                    "order_in_lineup": a.order_in_lineup,
                    "preference": a.preference,
                }
                for a in d.assignments
            ],
            "share_token": d.share_token,
        }
        for d in designers
    ]


@router.post("/designers")
async def add_designer(payload: DesignerIn, db: AsyncSession = Depends(get_db)):
    """Adds a designer to the end of that day's lineup automatically — no manual order entry needed."""
    result = await db.execute(
        select(func.max(Designer.order_in_day)).where(Designer.show_day_id == payload.show_day_id)
    )
    next_order = (result.scalar() or 0) + 1

    designer = Designer(
        show_day_id=payload.show_day_id, name=payload.name, notes=payload.notes, order_in_day=next_order,
        share_token=secrets.token_urlsafe(16),
    )
    db.add(designer)
    await db.commit()
    await db.refresh(designer)
    return {"id": designer.id, "name": designer.name, "order_in_day": designer.order_in_day, "share_token": designer.share_token}


@router.put("/designers/{designer_id}/move")
async def move_designer(designer_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Moves a designer to a different show day, appending them to the end of that day's lineup."""
    designer = await db.get(Designer, designer_id)
    if not designer:
        raise HTTPException(status_code=404, detail="Designer not found")

    new_show_day_id = payload.get("show_day_id")
    if not new_show_day_id:
        raise HTTPException(status_code=400, detail="show_day_id is required")

    result = await db.execute(
        select(func.max(Designer.order_in_day)).where(Designer.show_day_id == new_show_day_id)
    )
    next_order = (result.scalar() or 0) + 1

    designer.show_day_id = new_show_day_id
    designer.order_in_day = next_order

    await db.commit()
    await db.refresh(designer)
    return {"id": designer.id, "show_day_id": designer.show_day_id, "order_in_day": designer.order_in_day}


@router.put("/designers/{designer_id}/roster-only")
async def set_designer_roster_only(designer_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Toggles a designer between pick-mode (Preferred 1/2 buttons on their deck link)
    and roster-only mode (their link just lists the lineup, no picks)."""
    designer = await db.get(Designer, designer_id)
    if not designer:
        raise HTTPException(status_code=404, detail="Designer not found")
    designer.roster_only = bool(payload.get("roster_only"))
    await db.commit()
    return {"id": designer.id, "roster_only": designer.roster_only}


@router.delete("/designers/{designer_id}")
async def remove_designer(designer_id: int, db: AsyncSession = Depends(get_db)):
    designer = await db.get(Designer, designer_id)
    if not designer:
        raise HTTPException(status_code=404, detail="Designer not found")
    await db.delete(designer)
    await db.commit()
    return {"status": "deleted"}


@router.put("/designers/reorder")
async def reorder_designers(payload: dict, db: AsyncSession = Depends(get_db)):
    """Body: {"ordered_ids": [id1, id2, ...]} — resequences that day's lineup 1..N in the given order."""
    ordered_ids = payload.get("ordered_ids", [])
    for position, designer_id in enumerate(ordered_ids, start=1):
        designer = await db.get(Designer, designer_id)
        if designer:
            designer.order_in_day = position
    await db.commit()
    return {"status": "reordered"}


@router.post("/designers/{designer_id}/assignments")
async def add_assignment(designer_id: int, payload: DesignerAssignmentIn, db: AsyncSession = Depends(get_db)):
    designer = await db.get(Designer, designer_id)
    if not designer:
        raise HTTPException(status_code=404, detail="Designer not found")
    applicant = await db.get(Applicant, payload.applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    result = await db.execute(
        select(func.max(DesignerAssignment.order_in_lineup)).where(DesignerAssignment.designer_id == designer_id)
    )
    next_order = (result.scalar() or 0) + 1

    assignment = DesignerAssignment(designer_id=designer_id, applicant_id=payload.applicant_id, order_in_lineup=next_order)
    db.add(assignment)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()  # already assigned — treat as a no-op, not an error
    return {"status": "assigned"}


@router.put("/designers/{designer_id}/assignments/reorder")
async def reorder_assignments(designer_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Body: {"ordered_applicant_ids": [id1, id2, ...]} — resequences walk order within this designer's lineup."""
    ordered_ids = payload.get("ordered_applicant_ids", [])
    for position, applicant_id in enumerate(ordered_ids, start=1):
        result = await db.execute(
            select(DesignerAssignment).where(
                DesignerAssignment.designer_id == designer_id,
                DesignerAssignment.applicant_id == applicant_id,
            )
        )
        assignment = result.scalar_one_or_none()
        if assignment:
            assignment.order_in_lineup = position
    await db.commit()
    return {"status": "reordered"}


@router.delete("/designers/{designer_id}/assignments/{applicant_id}")
async def remove_assignment(designer_id: int, applicant_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DesignerAssignment).where(
            DesignerAssignment.designer_id == designer_id,
            DesignerAssignment.applicant_id == applicant_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment:
        await db.delete(assignment)
        await db.commit()
    return {"status": "removed"}


@router.get("/deck/{token}")
async def get_deck(token: str, db: AsyncSession = Depends(get_db)):
    """
    Public, unauthenticated view for a designer — accessible only with their
    unique share token. Shows exactly their assigned models with garment-fitting
    info and photos. Deliberately excludes agency info and the lingerie/swim/
    see-through fields, and never reveals which OTHER designers a model also walks for.
    """
    result = await db.execute(
        select(Designer).where(Designer.share_token == token).options(selectinload(Designer.show_day))
    )
    designer = result.scalar_one_or_none()
    if not designer:
        raise HTTPException(status_code=404, detail="Deck not found")

    result = await db.execute(
        select(DesignerAssignment)
        .where(DesignerAssignment.designer_id == designer.id)
        .options(
            selectinload(DesignerAssignment.applicant).selectinload(Applicant.measurement),
            selectinload(DesignerAssignment.applicant).selectinload(Applicant.photos),
        )
        .order_by(DesignerAssignment.order_in_lineup)
    )
    assignments = result.scalars().all()

    def photo_groups(photos):
        main_tags = ["headshot", "full_frontal", "left_side", "right_side"]
        main = [p for p in photos if p.tag in main_tags]
        main.sort(key=lambda p: main_tags.index(p.tag))
        extra = [p for p in photos if p.tag not in main_tags]
        return (
            [{"tag": p.tag, "url": p.url} for p in main],
            [{"tag": p.tag, "url": p.url} for p in extra],
        )

    models = []
    for a in assignments:
        applicant = a.applicant
        m = applicant.measurement
        main_photos, extra_photos = photo_groups(applicant.photos)
        models.append({
            "applicant_id": applicant.id,
            "full_name": applicant.full_name,
            "category": applicant.category,
            "is_minor": applicant.is_minor,
            "preference": a.preference,
            "measurement": (
                {
                    "height": m.height, "bust_chest": m.bust_chest, "waist_size": m.waist_size,
                    "hip_size": m.hip_size, "shoe_size": m.shoe_size, "dress_size": m.dress_size,
                    "jacket_size": m.jacket_size, "tattoos": m.tattoos, "piercings": m.piercings,
                }
                if m else None
            ),
            "main_photos": main_photos,
            "extra_photos": extra_photos,
        })

    return {
        "designer_name": designer.name,
        "show_day": designer.show_day.name if designer.show_day else None,
        "roster_only": designer.roster_only,
        "models": models,
    }


@router.put("/deck/{token}/models/{applicant_id}/preference")
async def set_deck_preference(token: str, applicant_id: int, payload: DeckPreferenceIn, db: AsyncSession = Depends(get_db)):
    """Public — lets a designer mark Preferred 1 / Preferred 2 (or clear it) via their own link."""
    result = await db.execute(select(Designer).where(Designer.share_token == token))
    designer = result.scalar_one_or_none()
    if not designer:
        raise HTTPException(status_code=404, detail="Deck not found")

    result = await db.execute(
        select(DesignerAssignment).where(
            DesignerAssignment.designer_id == designer.id,
            DesignerAssignment.applicant_id == applicant_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="This model isn't on your deck")
    if designer.roster_only:
        raise HTTPException(status_code=400, detail="This deck is roster-only — no picks needed")

    assignment.preference = payload.preference
    await db.commit()
    return {"status": "saved"}
@router.get("/final-roster")
async def final_roster(show_day_id: int, db: AsyncSession = Depends(get_db)):
    """
    Every model with a pool assignment (the finalists from Model Pools review),
    with full measurement data for inline editing, plus which designers they're
    currently walking for on THIS specific show day.
    """
    result = await db.execute(
        select(Applicant)
        .where(Applicant.pool_assignment.has())
        .options(
            selectinload(Applicant.measurement),
            selectinload(Applicant.photos),
            selectinload(Applicant.pool_assignment),
        )
        .order_by(Applicant.full_name)
    )
    applicants = result.scalars().all()
    ids = [a.id for a in applicants]

    assignment_map = {}
    if ids:
        a_result = await db.execute(
            select(DesignerAssignment.applicant_id, Designer.id, Designer.name, Designer.order_in_day)
            .join(Designer, Designer.id == DesignerAssignment.designer_id)
            .where(DesignerAssignment.applicant_id.in_(ids), Designer.show_day_id == show_day_id)
            .order_by(Designer.order_in_day)
        )
        for applicant_id, designer_id, designer_name, order_in_day in a_result.all():
            assignment_map.setdefault(applicant_id, []).append(
                {"designer_id": designer_id, "designer_name": designer_name, "order_in_day": order_in_day}
            )

    def pick_photo(photos):
        headshot = next((p for p in photos if p.tag == "headshot"), None)
        chosen = headshot or (photos[0] if photos else None)
        return chosen.url if chosen else None

    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "category": a.category,
            "pool": a.pool_assignment.pool if a.pool_assignment else None,
            "photo_url": pick_photo(a.photos),
            "agency_name": a.agency_name,
            "has_agency": bool(a.agency_name and a.agency_name.strip().upper() not in ("N/A", "NA", "")),
            "is_minor": a.is_minor,
            "assignments": assignment_map.get(a.id, []),
            "measurement": (
                {
                    "eye_color": a.measurement.eye_color, "hair_color": a.measurement.hair_color,
                    "height": a.measurement.height, "bust_chest": a.measurement.bust_chest,
                    "hip_size": a.measurement.hip_size, "waist_size": a.measurement.waist_size,
                    "arm_length": a.measurement.arm_length, "inseam": a.measurement.inseam,
                    "shoe_size": a.measurement.shoe_size, "dress_size": a.measurement.dress_size,
                    "jacket_size": a.measurement.jacket_size,
                    "tattoos": a.measurement.tattoos, "piercings": a.measurement.piercings,
                    "avail_thursday": a.measurement.avail_thursday, "avail_friday": a.measurement.avail_friday,
                    "avail_saturday": a.measurement.avail_saturday,
                    "swim_ok": a.measurement.swim_ok, "lingerie_ok": a.measurement.lingerie_ok,
                    "see_through_ok": a.measurement.see_through_ok,
                }
                if a.measurement else None
            ),
        }
        for a in applicants
    ]