"""
Run nightly (Railway cron or APScheduler) to delete applicants who haven't
reapplied or had any activity in RETENTION_MONTHS. Cascades to their photos,
pool assignment, and deck entries automatically via FK constraints.

Usage: python -m app.cleanup
"""
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete

from app.database import AsyncSessionLocal
from app.models import Applicant

RETENTION_MONTHS = 9


async def run_cleanup():
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_MONTHS * 30)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Applicant).where(Applicant.updated_at < cutoff))
        stale = result.scalars().all()
        count = len(stale)
        if count:
            await db.execute(delete(Applicant).where(Applicant.updated_at < cutoff))
            await db.commit()
        print(f"Cleanup: removed {count} applicant(s) inactive since before {cutoff.date()}")


if __name__ == "__main__":
    asyncio.run(run_cleanup())