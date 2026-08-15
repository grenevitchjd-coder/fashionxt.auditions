import os
import uuid
import boto3
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Applicant, Photo, PhotoSource, ApplicantSource
from app.schemas import ApplicationFormPayload

router = APIRouter(prefix="/ingest", tags=["ingest"])

# R2 client — bucket/creds come from env vars set on Railway
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("R2_ENDPOINT"),
    aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
)
BUCKET = os.environ.get("R2_BUCKET", "fashionxt-photos")

INGEST_SECRET = os.environ.get("INGEST_WEBHOOK_SECRET", "")


def _check_secret(secret: str):
    if not INGEST_SECRET or secret != INGEST_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


@router.post("/application-form")
async def ingest_application_form(
    payload: ApplicationFormPayload,
    secret: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the Apps Script trigger on the application Google Form.
    One row per person — match by email, overwrite in place if they exist.
    """
    _check_secret(secret)

    result = await db.execute(select(Applicant).where(Applicant.email == payload.email))
    applicant = result.scalar_one_or_none()

    data = payload.model_dump(exclude={"photo_urls"})

    if applicant is None:
        applicant = Applicant(**data, source=ApplicantSource.form)
        db.add(applicant)
        await db.flush()
    else:
        # Reapplication: wipe old season photos, overwrite fields in place
        await db.execute(
            Photo.__table__.delete().where(Photo.applicant_id == applicant.id)
        )
        for key, value in data.items():
            setattr(applicant, key, value)

    for url in payload.photo_urls:
        db.add(Photo(applicant_id=applicant.id, url=url, source=PhotoSource.application))

    await db.commit()
    await db.refresh(applicant)
    return {"applicant_id": applicant.id, "email": applicant.email}


@router.post("/photo")
async def upload_photo(
    applicant_id: int = Form(...),
    tag: str = Form(default=""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Staff use this from the app on an iPad during check-in — tagged directly
    by applicant (audition number), no Google Form involved.
    """
    result = await db.execute(select(Applicant).where(Applicant.id == applicant_id))
    applicant = result.scalar_one_or_none()
    if applicant is None:
        raise HTTPException(status_code=404, detail="Applicant not found")

    key = f"{applicant_id}/{uuid.uuid4().hex}-{file.filename}"
    body = await file.read()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=file.content_type)
    url = f"{os.environ.get('R2_PUBLIC_BASE_URL')}/{key}"

    photo = Photo(applicant_id=applicant_id, url=url, source=PhotoSource.in_app, tag=tag or None)
    db.add(photo)
    await db.commit()
    await db.refresh(photo)
    return {"photo_id": photo.id, "url": url}