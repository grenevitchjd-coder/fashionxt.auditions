from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from datetime import datetime
import csv
import io
import re

from app.database import get_db
from app.models import Applicant, PoolAssignment, ApplicantSource, Photo, CastingStatus, Measurement, AuditionEvent, Category, PhotoSource
from app.schemas import (
    ApplicantOut, ManualApplicantIn, CastingStatusUpdate, PoolAssignmentUpdate, CheckinIn, MeasurementUpdate, PoolGuestIn, ContactInfoUpdate,
)

router = APIRouter(prefix="/applicants", tags=["applicants"])


def _csv_bool(val):
    return bool(val) and val.strip().lower().startswith("yes")


def _csv_category(val):
    if not val:
        return Category.female
    v = val.strip().lower()
    if "female" in v:
        return Category.female
    if "male" in v:
        return Category.male
    return Category.non_binary


def _csv_date(val):
    """
    Real form data has dates typed every which way — ordinal suffixes,
    weekday prefixes, dot separators, day-first order. Handles the common
    ones; genuinely ambiguous or garbage values (e.g. missing year, or a
    stray letter) fall through to None rather than guessing wrong.
    """
    if not val:
        return None
    s = val.strip()
    s = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    if re.match(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$", s):
        s = s.replace(".", "/")

    formats = [
        "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%b.%d.%Y", "%B.%d.%Y",
        "%d %B %Y", "%d %b %Y", "%B %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        pass
    return None


def _csv_get(row_values: list, index_map: dict, *candidates: str):
    for c in candidates:
        idx = index_map.get(c.strip())
        if idx is not None and idx < len(row_values):
            val = row_values[idx]
            return val.strip() if val else None
    return None


def _find_all_indices(header_row: list, name: str):
    return [idx for idx, h in enumerate(header_row) if (h or "").strip() == name]


def _csv_email(row_values: list, email_indices: list):
    """
    This form has two 'Email Address' columns — Google's own auto-captured
    account email, and a separate question asking the person to type it in.
    Prefers the typed-in one (more likely to be the address they actually
    check), but falls back to Google's captured email if that field was
    left blank, so a blank typed-email cell doesn't drop a real applicant.
    """
    for idx in reversed(email_indices):
        if idx < len(row_values):
            val = row_values[idx].strip() if row_values[idx] else ""
            if val:
                return val
    return None


def _build_index_map(header_row: list):
    """Maps each stripped header name to its FIRST column index — the CSV export
    has genuine duplicate headers (Google's own captured email vs. a form question
    also asking for email), so 'first occurrence wins' resolves it predictably."""
    index_map = {}
    for idx, name in enumerate(header_row):
        key = (name or "").strip()
        if key not in index_map:
            index_map[key] = idx
    return index_map


@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    Bulk-import applicants from a CSV export of the application Google Form.
    Upserts by email — same semantics as the live webhook — and skips any
    blank/padding rows (a Google Sheets export quirk). Re-runnable safely.
    """
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    try:
        header_row = next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="CSV appears to be empty")

    index_map = _build_index_map(header_row)
    email_indices = _find_all_indices(header_row, "Email Address")

    added = 0
    updated = 0
    skipped = 0
    errors = []
    imported_emails = []

    for i, row_values in enumerate(reader, start=2):
        email = _csv_email(row_values, email_indices)
        full_name = _csv_get(row_values, index_map, "Model's Full Name")
        if not email or not full_name:
            skipped += 1
            continue

        try:
            guardian = _csv_get(
                row_values, index_map,
                "Must be Filled up by Parent or Legal Guardian: Write your Full Name and relationship with the model if model's age is under 18. \nIt will be Considered as the Signature of the Parent or Legal Guardian.",
            )
            data = {
                "email": email,
                "full_name": full_name,
                "phone": _csv_get(row_values, index_map, "Mobile Phone Number"),
                "address_street": _csv_get(row_values, index_map, "Address (Street)"),
                "address_city": _csv_get(row_values, index_map, "Address (City)"),
                "address_state": _csv_get(row_values, index_map, "Address (State, Zip Country)"),
                "agency_name": _csv_get(row_values, index_map, "Agency Name (if currently signed. Write N/A if not signed)"),
                "agency_address": _csv_get(row_values, index_map, "Agency Address (Street, City, State, Zip, Country)"),
                "category": _csv_category(_csv_get(row_values, index_map, "Auditioning to Model As")),
                "height_no_shoes": _csv_get(row_values, index_map, "Height without shoes"),
                "dress_size": _csv_get(row_values, index_map, "Dress Size (Modeling as a Female)"),
                "jacket_size": _csv_get(row_values, index_map, "Jacket Size (Modeling as a Male)"),
                "sample_size_spec": _csv_get(row_values, index_map, "Based on Industry Spec, are you .."),
                "willing_without_lodging": _csv_bool(_csv_get(row_values, index_map, "Willing to Participate even if FashioNXT can't Offer Lodging in Portland")),
                "available_show_days": _csv_get(row_values, index_map, "Available for FashioNXT Week Oct 8,9, 10, 2026"),
                "available_editorial": _csv_bool(_csv_get(row_values, index_map, "Available for Editorial or Look-book shoot for FashioNXT other times of the year if schedule permits? The rates will be the same as FashioNXT Week day rate")),
                "instagram_handle": _csv_get(row_values, index_map, "Instagram Handle and Number of followers"),
                "facebook_handle": _csv_get(row_values, index_map, "Facebook Handle and  Number of followers"),
                "tiktok_handle": _csv_get(row_values, index_map, "TikTok Handle and Number of followers"),
                "notable_achievements": _csv_get(row_values, index_map, "Mention any of your Significant Media Placements, or Highlight Achievements"),
                "interested_editorial": _csv_bool(_csv_get(row_values, index_map, "Available for Editorial or Look-book shoot for FashioNXT other times of the year if schedule permits? The rates will be the same as FashioNXT Week day rate")),
                "interested_promo_partner": _csv_bool(_csv_get(row_values, index_map, "Would you like to be a promotional partner of FashioNXT?")),
                "interested_content_services": _csv_bool(_csv_get(row_values, index_map, "Would you like to purchase Content services from FashioNXT")),
                "interested_workshops": _csv_bool(_csv_get(row_values, index_map, "Would you like to learn about any workshop or development sessions FashioNXT organizes for models' development?")),
                "consent_given": _csv_get(row_values, index_map, "CONSENT") == "I CONSENT",
                "talent_release_signed": _csv_get(row_values, index_map, "TALENT RELEASE FORM") == "I Consent",
                "signature_name": _csv_get(row_values, index_map, "Writing Your Full Name Below Will be Considered as Your Signature Consenting to the Model Release Form Above"),
                "signature_date": _csv_date(_csv_get(row_values, index_map, "Date")),
                "is_minor": bool(guardian),
                "guardian_name": guardian,
            }

            result = await db.execute(select(Applicant).where(Applicant.email == email))
            applicant = result.scalar_one_or_none()

            if applicant is None:
                applicant = Applicant(**data, source=ApplicantSource.form)
                db.add(applicant)
                await db.flush()
                added += 1
            else:
                for key, value in data.items():
                    setattr(applicant, key, value)
                await db.execute(Photo.__table__.delete().where(Photo.applicant_id == applicant.id))
                updated += 1

            for photo_col in [
                "Modeling Image Upload 1 (Gives FashioNXT Usage Rights)",
                "Modeling Image Upload 2 (Gives FashioNXT Usage Rights)",
                "Modeling Image Upload 3 (Gives FashioNXT Usage Rights)",
            ]:
                url = _csv_get(row_values, index_map, photo_col)
                if url:
                    db.add(Photo(applicant_id=applicant.id, url=url, source=PhotoSource.application))

            imported_emails.append({"email": email, "full_name": full_name})

        except Exception as e:
            errors.append(f"Row {i} ({email}): {str(e)}")
            skipped += 1
            continue

    await db.commit()
    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:10],
        "imported": imported_emails,
    }


@router.post("/{applicant_id}/reset")
async def reset_applicant(applicant_id: int, db: AsyncSession = Depends(get_db)):
    """
    Testing helper — clears this applicant's check-in, casting decision,
    preselect, pool assignment, measurements, and photos. Keeps their base
    contact/agency record intact (name, email, phone, agency, address).
    """
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    applicant.event_id = None
    applicant.audition_number = None
    applicant.casting_status = CastingStatus.pending
    applicant.preselect = False

    existing_pool = await db.get(PoolAssignment, applicant_id)
    if existing_pool:
        await db.delete(existing_pool)

    existing_measurement = await db.get(Measurement, applicant_id)
    if existing_measurement:
        await db.delete(existing_measurement)

    await db.execute(Photo.__table__.delete().where(Photo.applicant_id == applicant_id))

    await db.commit()
    await db.refresh(applicant)
    return applicant


@router.post("/reset-all")
async def reset_all_applicants(db: AsyncSession = Depends(get_db)):
    """
    System-wide testing reset — clears check-ins, casting decisions, preselect
    flags, pool assignments, measurements, and photos for EVERY applicant.
    Keeps base applicant records (name, email, agency, address) and audition
    events (Portland/Seattle) intact — no re-import needed after this.
    """
    result = await db.execute(select(Applicant))
    applicants = result.scalars().all()

    for a in applicants:
        a.event_id = None
        a.audition_number = None
        a.casting_status = CastingStatus.pending
        a.preselect = False

    await db.execute(PoolAssignment.__table__.delete())
    await db.execute(Measurement.__table__.delete())
    await db.execute(Photo.__table__.delete())

    await db.commit()
    return {"reset_count": len(applicants)}


@router.get("/directory")
async def applicants_directory(db: AsyncSession = Depends(get_db)):
    """
    Lightweight bulk fetch for the Roster search/confirm directory — every
    applicant regardless of event, loaded once for instant client-side search.
    """
    result = await db.execute(
        select(Applicant).order_by(Applicant.full_name)
    )
    applicants = result.scalars().all()
    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "email": a.email,
            "phone": a.phone,
            "category": a.category,
            "has_agency": bool(a.agency_name and a.agency_name.strip().upper() not in ("N/A", "NA", "")),
            "casting_status": a.casting_status,
            "audition_number": a.audition_number,
        }
        for a in applicants
    ]


@router.put("/{applicant_id}/checkin")
async def checkin_applicant(applicant_id: int, payload: CheckinIn, db: AsyncSession = Depends(get_db)):
    """
    Check an applicant into today's event — audition number is auto-assigned
    as the next sequential number for this event (no manual entry needed).
    Preselecting here auto-approves them for the show, same as elsewhere.
    """
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    event = await db.get(AuditionEvent, payload.event_id)
    if not event:
        raise HTTPException(status_code=400, detail=f"Event ID {payload.event_id} doesn't exist yet.")

    # Serializes concurrent check-ins for THIS event only (Portland and Seattle
    # don't block each other) — a simultaneous tap from two staff members just
    # queues briefly instead of ever risking a duplicate number or an error.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": payload.event_id})

    result = await db.execute(
        select(func.max(Applicant.audition_number)).where(Applicant.event_id == payload.event_id)
    )
    next_number = (result.scalar() or 0) + 1

    applicant.event_id = payload.event_id
    applicant.audition_number = next_number
    if payload.preselect is not None:
        applicant.preselect = payload.preselect
    if payload.preselect:
        # Preselects skip judging entirely — automatically approved for the show.
        applicant.casting_status = CastingStatus.yes

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Number assignment conflict — try again")

    await db.refresh(applicant)
    return applicant


@router.post("/pool-guest")
async def add_pool_guest(payload: PoolGuestIn, db: AsyncSession = Depends(get_db)):
    """
    Add a model directly into the pool review stage — no audition event,
    no audition number, automatically marked Yes. For designer requests,
    late additions, or anyone brought in after auditions have finished.
    """
    applicant = Applicant(
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        category=payload.category,
        agency_name=payload.agency_name,
        agency_address=payload.agency_address,
        source=ApplicantSource.manual,
        casting_status=CastingStatus.yes,
        event_id=None,
        audition_number=None,
    )
    db.add(applicant)
    await db.commit()
    await db.refresh(applicant)
    return {"id": applicant.id, "full_name": applicant.full_name}


@router.get("/pools-list")
async def pools_list(db: AsyncSession = Depends(get_db)):
    """
    Every Yes/Maybe/preselect applicant across ALL events — pooling happens
    after both cities finish auditioning, so this isn't scoped to one event.
    Includes the fields needed for the review grid without per-card fetches.
    """
    result = await db.execute(
        select(Applicant)
        .where(or_(Applicant.casting_status.in_([CastingStatus.yes, CastingStatus.maybe]), Applicant.preselect == True))
        .options(
            selectinload(Applicant.photos),
            selectinload(Applicant.measurement),
            selectinload(Applicant.pool_assignment),
        )
        .order_by(Applicant.audition_number)
    )
    applicants = result.scalars().all()

    def pick_photo(photos):
        headshot = next((p for p in photos if p.tag == "headshot"), None)
        chosen = headshot or (photos[0] if photos else None)
        return chosen.url if chosen else None

    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "audition_number": a.audition_number,
            "category": a.category,
            "casting_status": a.casting_status,
            "preselect": a.preselect,
            "pool": a.pool_assignment.pool if a.pool_assignment else None,
            "photo_url": pick_photo(a.photos),
            "has_agency": bool(a.agency_name and a.agency_name.strip().upper() not in ("N/A", "NA", "")),
            "measurement": (
                {
                    "height": a.measurement.height,
                    "bust_chest": a.measurement.bust_chest,
                    "waist_size": a.measurement.waist_size,
                    "hip_size": a.measurement.hip_size,
                    "dress_size": a.measurement.dress_size,
                    "jacket_size": a.measurement.jacket_size,
                    "lingerie_ok": a.measurement.lingerie_ok,
                    "see_through_ok": a.measurement.see_through_ok,
                    "avail_thursday": a.measurement.avail_thursday,
                    "avail_friday": a.measurement.avail_friday,
                    "avail_saturday": a.measurement.avail_saturday,
                }
                if a.measurement else None
            ),
        }
        for a in applicants
    ]


@router.get("/measurements-list")
async def measurements_list(event_id: int, db: AsyncSession = Depends(get_db)):
    """
    Lightweight bulk fetch for the measurements queue — loaded ONCE per event.
    Flags who already has a measurement record so the default queue can show
    only Yes/Maybe applicants still missing one.
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

    measured_ids = set()
    if ids:
        m_result = await db.execute(select(Measurement.applicant_id).where(Measurement.applicant_id.in_(ids)))
        measured_ids = {row[0] for row in m_result.all()}

    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "audition_number": a.audition_number,
            "category": a.category,
            "casting_status": a.casting_status,
            "preselect": a.preselect,
            "has_measurement": a.id in measured_ids,
        }
        for a in applicants
    ]


@router.get("/{applicant_id}/measurement")
async def get_measurement(applicant_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch existing measurement data to pre-fill the entry form, or empty defaults if none yet."""
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    m = await db.get(Measurement, applicant_id)
    data = {
        "tattoos": None, "piercings": None, "eye_color": None, "hair_color": None,
        "height": None, "bust_chest": None, "hip_size": None, "waist_size": None,
        "arm_length": None, "inseam": None, "shoe_size": None, "dress_size": None,
        "jacket_size": None, "avail_thursday": False, "avail_friday": False,
        "avail_saturday": False, "swim_ok": None, "lingerie_ok": None,
        "see_through_ok": None, "notes": None,
    }
    if m:
        data.update({
            "tattoos": m.tattoos, "piercings": m.piercings, "eye_color": m.eye_color, "hair_color": m.hair_color,
            "height": m.height, "bust_chest": m.bust_chest, "hip_size": m.hip_size, "waist_size": m.waist_size,
            "arm_length": m.arm_length, "inseam": m.inseam, "shoe_size": m.shoe_size, "dress_size": m.dress_size,
            "jacket_size": m.jacket_size, "avail_thursday": m.avail_thursday, "avail_friday": m.avail_friday,
            "avail_saturday": m.avail_saturday, "swim_ok": m.swim_ok, "lingerie_ok": m.lingerie_ok,
            "see_through_ok": m.see_through_ok, "notes": m.notes,
        })
    data["is_minor"] = applicant.is_minor
    return data


@router.put("/{applicant_id}/measurement")
async def save_measurement(applicant_id: int, payload: MeasurementUpdate, db: AsyncSession = Depends(get_db)):
    """Create or update this applicant's measurement record (1:1, upsert). Minor status lives on the applicant itself."""
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    data = payload.model_dump(exclude_unset=True)
    is_minor = data.pop("is_minor", None)
    if is_minor is not None:
        applicant.is_minor = is_minor

    m = await db.get(Measurement, applicant_id)
    if m is None:
        m = Measurement(applicant_id=applicant_id, **data)
        db.add(m)
    else:
        for key, value in data.items():
            setattr(m, key, value)

    await db.commit()
    return {"status": "saved"}


@router.get("/photo-station-list")
async def photo_station_list(event_id: int, db: AsyncSession = Depends(get_db)):
    """
    Lightweight bulk fetch for the photo station — loaded ONCE per event.
    Includes each person's actual photo records (not just tags) so the whole
    queue can render fully-inline capture buttons with zero per-row fetches.
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

    photos_by_applicant = {}
    if ids:
        photo_result = await db.execute(
            select(Photo.id, Photo.applicant_id, Photo.url, Photo.tag).where(Photo.applicant_id.in_(ids))
        )
        for pid, aid, url, tag in photo_result.all():
            photos_by_applicant.setdefault(aid, []).append({"id": pid, "url": url, "tag": tag})

    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "audition_number": a.audition_number,
            "category": a.category,
            "casting_status": a.casting_status,
            "preselect": a.preselect,
            "photos": photos_by_applicant.get(a.id, []),
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
            Applicant.category, Applicant.event_id, Applicant.audition_number, Applicant.preselect,
        ).order_by(Applicant.full_name)
    )
    rows = result.all()
    return [
        {
            "id": r.id, "full_name": r.full_name, "phone": r.phone, "email": r.email,
            "category": r.category, "event_id": r.event_id, "audition_number": r.audition_number,
            "preselect": r.preselect,
        }
        for r in rows
    ]


@router.put("/{applicant_id}/checkin")
async def checkin_applicant(applicant_id: int, payload: CheckinIn, db: AsyncSession = Depends(get_db)):
    """Assign an audition number for today's event to an already-existing applicant."""
    applicant = await db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    event = await db.get(AuditionEvent, payload.event_id)
    if not event:
        raise HTTPException(status_code=400, detail=f"Event ID {payload.event_id} doesn't exist yet.")

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
        "agency_address": applicant.agency_address,
        "address_street": applicant.address_street,
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