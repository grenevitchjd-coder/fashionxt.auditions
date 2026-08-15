from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import AuditionEvent

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def list_events(db: AsyncSession = Depends(get_db)):
    """All audition events — used by the city-hub pages to look up the right event ID by city name."""
    result = await db.execute(select(AuditionEvent).order_by(AuditionEvent.id))
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "city": e.city,
            "season_label": e.season_label,
            "start_date": e.start_date.isoformat() if e.start_date else None,
            "end_date": e.end_date.isoformat() if e.end_date else None,
        }
        for e in events
    ]