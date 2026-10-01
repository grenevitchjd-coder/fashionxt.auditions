"""
Model Tracking page (day of show).

  GET  /day-of/tracking?show_day_id=   everything the three views need, per designer in walk order
  POST /day-of/tracking/rehearsal      tick / untick one model at one designer's walk-through

Rehearsal is tracked per model per designer and does NOT require the model to be checked in.
Each model's "step" for a designer is worked out here so every view agrees:
  not_checked_in -> waiting (checked in, nothing started) -> hair / makeup (in progress) -> done
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Designer
from app.models_dayof import RehearsalAttendance
from app.routers.teams import load_team_board

router = APIRouter(prefix="/day-of/tracking", tags=["day-of-tracking"])


def _step(checked_in: bool, hair: str, makeup: str) -> str:
    if not checked_in:
        return "not_checked_in"
    if hair == "done" and makeup == "done":
        return "done"
    if hair == "in_progress":
        return "hair"
    if makeup == "in_progress":
        return "makeup"
    return "waiting"


@router.get("")
async def tracking_board(show_day_id: int, db: AsyncSession = Depends(get_db)):
    hair = await load_team_board(db, "hair", show_day_id)
    makeup = await load_team_board(db, "makeup", show_day_id)
    makeup_by = {
        (d["id"], m["applicant_id"]): m["status"] for d in makeup["designers"] for m in d["models"]
    }

    designer_ids = [d["id"] for d in hair["designers"]]
    attended: set[tuple[int, int]] = set()
    if designer_ids:
        rows = (
            await db.execute(select(RehearsalAttendance).where(RehearsalAttendance.designer_id.in_(designer_ids)))
        ).scalars().all()
        attended = {(r.designer_id, r.applicant_id) for r in rows}

    designers = []
    for d in hair["designers"]:
        models = []
        for m in d["models"]:
            h = m["status"]
            mk = makeup_by.get((d["id"], m["applicant_id"]), "todo")
            checked = bool(m["checked_in_at"])
            models.append({
                "applicant_id": m["applicant_id"],
                "full_name": m["full_name"],
                "photo_url": m["photo_url"],
                "checked_in_at": m["checked_in_at"],
                "note": m["note"],
                "rehearsal": (d["id"], m["applicant_id"]) in attended,
                "hair": h,
                "makeup": mk,
                "step": _step(checked, h, mk),
            })
        designers.append({
            "id": d["id"],
            "name": d["name"],
            "order_in_day": d["order_in_day"],
            "walkthrough": d["walkthrough"],
            "models": models,
            "totals": {
                "models": len(models),
                "checked_in": sum(1 for m in models if m["checked_in_at"]),
                "hair_done": sum(1 for m in models if m["hair"] == "done"),
                "makeup_done": sum(1 for m in models if m["makeup"] == "done"),
                "rehearsal": sum(1 for m in models if m["rehearsal"]),
            },
        })
    return {"day": hair["day"], "designers": designers}


class RehearsalIn(BaseModel):
    applicant_id: int
    designer_id: int
    attended: bool


@router.post("/rehearsal")
async def set_rehearsal(payload: RehearsalIn, db: AsyncSession = Depends(get_db)):
    designer = await db.get(Designer, payload.designer_id)
    if designer is None:
        raise HTTPException(status_code=404, detail="Designer not found")

    async def _find():
        return (
            await db.execute(
                select(RehearsalAttendance).where(
                    RehearsalAttendance.applicant_id == payload.applicant_id,
                    RehearsalAttendance.designer_id == payload.designer_id,
                )
            )
        ).scalar_one_or_none()

    row = await _find()
    if payload.attended:
        if row is None:
            db.add(RehearsalAttendance(applicant_id=payload.applicant_id, designer_id=payload.designer_id))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()  # another device ticked them a split second earlier
    elif row is not None:
        await db.delete(row)
        await db.commit()
    return {"status": "ok"}