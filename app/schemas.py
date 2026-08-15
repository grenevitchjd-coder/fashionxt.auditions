from datetime import date, datetime
from pydantic import BaseModel, EmailStr
from app.models import Category, CastingStatus, Pool, ApplicantSource, DesignerResponse


class ApplicationFormPayload(BaseModel):
    """Shape of the row Apps Script sends when the application form fires."""
    email: EmailStr
    full_name: str
    phone: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    address_zip: str | None = None
    address_country: str | None = None
    agency_name: str | None = None
    agency_address: str | None = None
    category: Category
    height_no_shoes: str | None = None
    dress_size: str | None = None
    jacket_size: str | None = None
    sample_size_spec: str | None = None
    willing_without_lodging: bool = False
    available_show_days: str | None = None
    available_editorial: bool = False
    instagram_handle: str | None = None
    instagram_followers: int | None = None
    facebook_handle: str | None = None
    facebook_followers: int | None = None
    tiktok_handle: str | None = None
    tiktok_followers: int | None = None
    notable_achievements: str | None = None
    interested_editorial: bool = False
    interested_promo_partner: bool = False
    interested_content_services: bool = False
    interested_workshops: bool = False
    consent_given: bool = False
    talent_release_signed: bool = False
    signature_name: str | None = None
    signature_date: date | None = None
    is_minor: bool = False
    guardian_name: str | None = None
    photo_urls: list[str] = []   # Drive links from the application form


class ApplicantOut(BaseModel):
    id: int
    email: str
    full_name: str
    category: Category
    event_id: int | None
    audition_number: int | None
    casting_status: CastingStatus
    preselect: bool
    source: ApplicantSource
    updated_at: datetime
    height_no_shoes: str | None = None
    available_show_days: str | None = None

    class Config:
        from_attributes = True


class PoolGuestIn(BaseModel):
    """Adding a model directly into the pool review stage — skips audition day entirely."""
    full_name: str
    email: EmailStr
    phone: str | None = None
    category: Category
    agency_name: str | None = None
    agency_address: str | None = None


class ManualApplicantIn(BaseModel):
    """For adding a guest / walk-in model directly."""
    full_name: str
    email: EmailStr
    category: Category
    event_id: int
    audition_number: int
    phone: str | None = None
    preselect: bool = False


class CheckinIn(BaseModel):
    event_id: int
    preselect: bool | None = None


class ContactInfoUpdate(BaseModel):
    category: Category | None = None
    email: str | None = None
    phone: str | None = None
    agency_name: str | None = None
    agency_address: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_state: str | None = None


class MeasurementUpdate(BaseModel):
    tattoos: bool | None = None
    piercings: bool | None = None
    eye_color: str | None = None
    hair_color: str | None = None
    height: str | None = None
    bust_chest: str | None = None
    hip_size: str | None = None
    waist_size: str | None = None
    arm_length: str | None = None
    inseam: str | None = None
    shoe_size: str | None = None
    dress_size: str | None = None
    jacket_size: str | None = None
    avail_thursday: bool = False
    avail_friday: bool = False
    avail_saturday: bool = False
    swim_ok: bool | None = None
    lingerie_ok: bool | None = None
    see_through_ok: bool | None = None
    notes: str | None = None
    is_minor: bool | None = None


class CastingStatusUpdate(BaseModel):
    casting_status: CastingStatus
    preselect: bool | None = None


class PoolAssignmentUpdate(BaseModel):
    pool: Pool | None  # null = remove from any pool


class DesignerResponseUpdate(BaseModel):
    applicant_id: int
    designer_response: DesignerResponse | None