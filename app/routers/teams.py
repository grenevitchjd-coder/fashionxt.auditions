"""
Hair Team and Make Up Team pages (one shared implementation, team = "hair" | "makeup").

Per designer, in walk order: that designer's models with headshot, check-in status and late
note. Each model/designer pair has its own done flag per team. A team can reorder a designer's
models for its own view without touching the designer's real lineup.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Applicant, Designer, ShowDay
from app.models_dayof import DayModelStatus, LookStatus, TeamOrder
from app.routers.dayof import _iso, _pick_photo

router = APIRouter(prefix="/day-of/teams", tags=["day-of-teams"])

TEAMS = {"hair": "hair_done", "makeup": "makeup_done"}
PROGRESS = {"hair": "hair_in_progress", "makeup": "makeup_in_progress"}
TEAM_LABELS = {"hair": "Hair Team", "makeup": "Make Up Team"}


def _check_team(team: str) -> str:
    if team not in TEAMS:
        raise HTTPException(status_code=404, detail="Unknown team")
    return TEAMS[team]


async def load_team_board(db: AsyncSession, team: str, show_day_id: int) -> dict:
    """Everything one team page (and its PDF) needs for one show day."""
    done_col = _check_team(team)
    prog_col = PROGRESS[team]
    day = await db.get(ShowDay, show_day_id)
    if not day:
        raise HTTPException(status_code=404, detail="Show day not found")

    designers = (
        await db.execute(
            select(Designer)
            .where(Designer.show_day_id == show_day_id)
            .options(selectinload(Designer.assignments))
            .order_by(Designer.order_in_day, Designer.id)
        )
    ).scalars().all()
    designer_ids = [d.id for d in designers]

    applicant_ids = {a.applicant_id for d in designers for a in d.assignments}
    applicants = {}
    if applicant_ids:
        rows = (
            await db.execute(
                select(Applicant).where(Applicant.id.in_(applicant_ids)).options(selectinload(Applicant.photos))
            )
        ).scalars().all()
        applicants = {a.id: a for a in rows}

    statuses = {}
    looks = {}
    orders: dict[tuple[int, int], int] = {}
    if applicant_ids:
        res = await db.execute(
            select(DayModelStatus).where(
                DayModelStatus.show_day_id == show_day_id, DayModelStatus.applicant_id.in_(applicant_ids)
            )
        )
        statuses = {s.applicant_id: s for s in res.scalars().all()}
        res = await db.execute(select(LookStatus).where(LookStatus.designer_id.in_(designer_ids)))
        looks = {
            (l.applicant_id, l.designer_id): (bool(getattr(l, done_col)), bool(getattr(l, prog_col)))
            for l in res.scalars().all()
        }
        res = await db.execute(
            select(TeamOrder).where(TeamOrder.team == team, TeamOrder.designer_id.in_(designer_ids))
        )
        orders = {(o.designer_id, o.applicant_id): o.position for o in res.scalars().all()}

    # all designers each model walks for that day, in show order
    designers_by_model: dict[int, list[dict]] = {}
    for d in designers:
        for a in d.assignments:
            designers_by_model.setdefault(a.applicant_id, []).append(
                {"designer_id": d.id, "name": d.name, "order_in_day": d.order_in_day}
            )

    out_designers = []
    for d in designers:
        assignments = sorted(d.assignments, key=lambda a: (a.order_in_lineup, a.id))
        # team order wins when it exists for this designer; unseen models go last in show order
        if any((d.id, a.applicant_id) in orders for a in assignments):
            assignments.sort(key=lambda a: orders.get((d.id, a.applicant_id), 10_000 + a.order_in_lineup))
        models = []
        for a in assignments:
            ap = applicants.get(a.applicant_id)
            if ap is None:
                continue
            st = statuses.get(ap.id)
            mine = designers_by_model[ap.id]
            done, in_progress = looks.get((ap.id, d.id), (False, False))
            models.append({
                "applicant_id": ap.id,
                "full_name": ap.full_name,
                "photo_url": _pick_photo(ap.photos),
                "checked_in_at": _iso(st.checked_in_at) if st else None,
                "note": st.note if st else None,
                "done": done,
                "status": "done" if done else ("in_progress" if in_progress else "todo"),
                "looks_total": len(mine),
                "all_done": all(looks.get((ap.id, x["designer_id"]), (False, False))[0] for x in mine),
                "other_designers": [x for x in mine if x["designer_id"] != d.id],
            })
        out_designers.append({
            "id": d.id,
            "name": d.name,
            "order_in_day": d.order_in_day,
            "custom_order": any((d.id, a.applicant_id) in orders for a in assignments),
            "models": models,
        })

    return {
        "team": team,
        "team_label": TEAM_LABELS[team],
        "day": {"id": day.id, "name": day.name, "show_date": day.show_date.isoformat() if day.show_date else None},
        "designers": out_designers,
    }


@router.get("/{team}")
async def team_board(team: str, show_day_id: int, db: AsyncSession = Depends(get_db)):
    return await load_team_board(db, team, show_day_id)


# ---------------------------------------------------------------------------
# Done flags
# ---------------------------------------------------------------------------
class LookDoneIn(BaseModel):
    applicant_id: int
    designer_id: int
    done: bool


class LookStatusIn(BaseModel):
    applicant_id: int
    designer_id: int
    status: str  # "todo" | "in_progress" | "done"


class AllDoneIn(BaseModel):
    applicant_id: int
    show_day_id: int
    done: bool


async def _is_checked_in(db: AsyncSession, applicant_id: int, show_day_id: int) -> bool:
    st = (
        await db.execute(
            select(DayModelStatus).where(
                DayModelStatus.applicant_id == applicant_id, DayModelStatus.show_day_id == show_day_id
            )
        )
    ).scalar_one_or_none()
    return bool(st and st.checked_in_at)


async def _set_look(db: AsyncSession, team: str, applicant_id: int, designer_id: int, status: str) -> None:
    async def _find():
        return (
            await db.execute(
                select(LookStatus).where(LookStatus.applicant_id == applicant_id, LookStatus.designer_id == designer_id)
            )
        ).scalar_one_or_none()

    row = await _find()
    if row is None:
        row = LookStatus(applicant_id=applicant_id, designer_id=designer_id)
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            row = await _find()
    setattr(row, TEAMS[team], status == "done")
    setattr(row, PROGRESS[team], status == "in_progress")


@router.post("/{team}/look-status")
async def set_look_status(team: str, payload: LookStatusIn, db: AsyncSession = Depends(get_db)):
    """Sets one model's look for one designer to todo / in_progress / done."""
    _check_team(team)
    if payload.status not in ("todo", "in_progress", "done"):
        raise HTTPException(status_code=400, detail="Status must be todo, in_progress or done")
    designer = await db.get(Designer, payload.designer_id)
    if designer is None:
        raise HTTPException(status_code=404, detail="Designer not found")
    if payload.status != "todo" and not await _is_checked_in(db, payload.applicant_id, designer.show_day_id):
        raise HTTPException(status_code=409, detail="This model isn't checked in yet")
    await _set_look(db, team, payload.applicant_id, payload.designer_id, payload.status)
    await db.commit()
    return {"status": "ok"}


@router.post("/{team}/look-done")
async def set_look_done(team: str, payload: LookDoneIn, db: AsyncSession = Depends(get_db)):
    _check_team(team)
    designer = await db.get(Designer, payload.designer_id)
    if designer is None:
        raise HTTPException(status_code=404, detail="Designer not found")
    if payload.done and not await _is_checked_in(db, payload.applicant_id, designer.show_day_id):
        raise HTTPException(status_code=409, detail="This model isn't checked in yet")
    await _set_look(db, team, payload.applicant_id, payload.designer_id, "done" if payload.done else "todo")
    await db.commit()
    return {"status": "ok"}


@router.post("/{team}/all-done")
async def set_all_done(team: str, payload: AllDoneIn, db: AsyncSession = Depends(get_db)):
    """Marks (or clears) this model's look for EVERY designer they walk for that day."""
    _check_team(team)
    if payload.done and not await _is_checked_in(db, payload.applicant_id, payload.show_day_id):
        raise HTTPException(status_code=409, detail="This model isn't checked in yet")
    designers = (
        await db.execute(
            select(Designer)
            .where(Designer.show_day_id == payload.show_day_id)
            .options(selectinload(Designer.assignments))
        )
    ).scalars().all()
    ids = [d.id for d in designers if any(a.applicant_id == payload.applicant_id for a in d.assignments)]
    if not ids:
        raise HTTPException(status_code=404, detail="Model has no designers that day")
    for did in ids:
        await _set_look(db, team, payload.applicant_id, did, "done" if payload.done else "todo")
    await db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Team's own order
# ---------------------------------------------------------------------------
class OrderIn(BaseModel):
    designer_id: int
    applicant_ids: list[int]


@router.put("/{team}/order")
async def set_team_order(team: str, payload: OrderIn, db: AsyncSession = Depends(get_db)):
    _check_team(team)
    designer = (
        await db.execute(
            select(Designer).where(Designer.id == payload.designer_id).options(selectinload(Designer.assignments))
        )
    ).scalar_one_or_none()
    if designer is None:
        raise HTTPException(status_code=404, detail="Designer not found")
    assigned = {a.applicant_id for a in designer.assignments}
    if set(payload.applicant_ids) != assigned or len(payload.applicant_ids) != len(assigned):
        raise HTTPException(status_code=400, detail="The list doesn't match this designer's models — refresh and try again")
    await db.execute(delete(TeamOrder).where(TeamOrder.team == team, TeamOrder.designer_id == payload.designer_id))
    for pos, aid in enumerate(payload.applicant_ids, start=1):
        db.add(TeamOrder(team=team, designer_id=payload.designer_id, applicant_id=aid, position=pos))
    await db.commit()
    return {"status": "ok"}


@router.delete("/{team}/order")
async def reset_team_order(team: str, designer_id: int, db: AsyncSession = Depends(get_db)):
    _check_team(team)
    await db.execute(delete(TeamOrder).where(TeamOrder.team == team, TeamOrder.designer_id == designer_id))
    await db.commit()
    return {"status": "ok"}