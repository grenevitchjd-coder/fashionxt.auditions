import enum
from datetime import datetime, date
from sqlalchemy import (
    String, Text, Boolean, Integer, ForeignKey, DateTime, Date,
    Enum, UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Category(str, enum.Enum):
    female = "female"
    male = "male"
    non_binary = "non_binary"


class CastingStatus(str, enum.Enum):
    pending = "pending"
    yes = "yes"
    maybe = "maybe"
    no = "no"


class Pool(str, enum.Enum):
    pool_a = "pool_a"
    pool_b = "pool_b"
    backup = "backup"


class ApplicantSource(str, enum.Enum):
    form = "form"
    manual = "manual"


class DesignerResponse(str, enum.Enum):
    one = "one"
    two = "two"


class PhotoSource(str, enum.Enum):
    application = "application"
    in_app = "in_app"


class AuditionEvent(Base):
    __tablename__ = "audition_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    city: Mapped[str] = mapped_column(Text)
    season_label: Mapped[str] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    applicants: Mapped[list["Applicant"]] = relationship(back_populates="event")
    decks: Mapped[list["Deck"]] = relationship(back_populates="event")


class Applicant(Base):
    __tablename__ = "applicant"
    __table_args__ = (UniqueConstraint("event_id", "audition_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)

    address_street: Mapped[str | None] = mapped_column(Text)
    address_city: Mapped[str | None] = mapped_column(Text)
    address_state: Mapped[str | None] = mapped_column(Text)
    address_zip: Mapped[str | None] = mapped_column(Text)
    address_country: Mapped[str | None] = mapped_column(Text)

    agency_name: Mapped[str | None] = mapped_column(Text)
    agency_address: Mapped[str | None] = mapped_column(Text)

    category: Mapped[Category] = mapped_column(Enum(Category, name="category"), nullable=False)
    height_no_shoes: Mapped[str | None] = mapped_column(Text)
    dress_size: Mapped[str | None] = mapped_column(Text)
    jacket_size: Mapped[str | None] = mapped_column(Text)
    sample_size_spec: Mapped[str | None] = mapped_column(Text)

    willing_without_lodging: Mapped[bool] = mapped_column(Boolean, default=False)
    available_show_days: Mapped[str | None] = mapped_column(Text)
    available_editorial: Mapped[bool] = mapped_column(Boolean, default=False)

    instagram_handle: Mapped[str | None] = mapped_column(Text)
    instagram_followers: Mapped[int | None] = mapped_column(Integer)
    facebook_handle: Mapped[str | None] = mapped_column(Text)
    facebook_followers: Mapped[int | None] = mapped_column(Integer)
    tiktok_handle: Mapped[str | None] = mapped_column(Text)
    tiktok_followers: Mapped[int | None] = mapped_column(Integer)
    notable_achievements: Mapped[str | None] = mapped_column(Text)

    interested_editorial: Mapped[bool] = mapped_column(Boolean, default=False)
    interested_promo_partner: Mapped[bool] = mapped_column(Boolean, default=False)
    interested_content_services: Mapped[bool] = mapped_column(Boolean, default=False)
    interested_workshops: Mapped[bool] = mapped_column(Boolean, default=False)

    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    talent_release_signed: Mapped[bool] = mapped_column(Boolean, default=False)
    signature_name: Mapped[str | None] = mapped_column(Text)
    signature_date: Mapped[date | None] = mapped_column(Date)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    guardian_name: Mapped[str | None] = mapped_column(Text)

    event_id: Mapped[int | None] = mapped_column(ForeignKey("audition_event.id", ondelete="SET NULL"))
    audition_number: Mapped[int | None] = mapped_column(Integer)
    casting_status: Mapped[CastingStatus] = mapped_column(
        Enum(CastingStatus, name="casting_status"), default=CastingStatus.pending
    )
    preselect: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[ApplicantSource] = mapped_column(
        Enum(ApplicantSource, name="applicant_source"), default=ApplicantSource.form
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped["AuditionEvent | None"] = relationship(back_populates="applicants")
    measurement: Mapped["Measurement | None"] = relationship(back_populates="applicant", uselist=False, cascade="all, delete-orphan")
    photos: Mapped[list["Photo"]] = relationship(back_populates="applicant", cascade="all, delete-orphan")
    pool_assignment: Mapped["PoolAssignment | None"] = relationship(back_populates="applicant", uselist=False, cascade="all, delete-orphan")


class Measurement(Base):
    __tablename__ = "measurement"

    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"), primary_key=True)
    tattoos: Mapped[bool | None] = mapped_column(Boolean)
    piercings: Mapped[bool | None] = mapped_column(Boolean)
    eye_color: Mapped[str | None] = mapped_column(Text)
    hair_color: Mapped[str | None] = mapped_column(Text)
    height: Mapped[str | None] = mapped_column(Text)
    bust_chest: Mapped[str | None] = mapped_column(Text)
    hip_size: Mapped[str | None] = mapped_column(Text)
    waist_size: Mapped[str | None] = mapped_column(Text)
    arm_length: Mapped[str | None] = mapped_column(Text)
    inseam: Mapped[str | None] = mapped_column(Text)
    shoe_size: Mapped[str | None] = mapped_column(Text)
    dress_size: Mapped[str | None] = mapped_column(Text)
    jacket_size: Mapped[str | None] = mapped_column(Text)
    avail_thursday: Mapped[bool] = mapped_column(Boolean, default=False)
    avail_friday: Mapped[bool] = mapped_column(Boolean, default=False)
    avail_saturday: Mapped[bool] = mapped_column(Boolean, default=False)
    swim_ok: Mapped[bool | None] = mapped_column(Boolean)
    lingerie_ok: Mapped[bool | None] = mapped_column(Boolean)
    see_through_ok: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    applicant: Mapped["Applicant"] = relationship(back_populates="measurement")


class Photo(Base):
    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[PhotoSource] = mapped_column(Enum(PhotoSource, name="photo_source"), nullable=False)
    tag: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    applicant: Mapped["Applicant"] = relationship(back_populates="photos")


class PoolAssignment(Base):
    __tablename__ = "pool_assignment"

    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"), primary_key=True)
    pool: Mapped[Pool] = mapped_column(Enum(Pool, name="pool"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    applicant: Mapped["Applicant"] = relationship(back_populates="pool_assignment")


class ShowDay(Base):
    """The actual FashioNXT Week runway days (Thu/Fri/Sat) — distinct from
    audition_event, which tracks the Portland/Seattle audition days."""
    __tablename__ = "show_day"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    show_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    designers: Mapped[list["Designer"]] = relationship(back_populates="show_day", cascade="all, delete-orphan")


class Designer(Base):
    __tablename__ = "designer"

    id: Mapped[int] = mapped_column(primary_key=True)
    show_day_id: Mapped[int] = mapped_column(ForeignKey("show_day.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    order_in_day: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    share_token: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    show_day: Mapped["ShowDay"] = relationship(back_populates="designers")
    assignments: Mapped[list["DesignerAssignment"]] = relationship(
        back_populates="designer", cascade="all, delete-orphan", order_by="DesignerAssignment.order_in_lineup"
    )


class DesignerAssignment(Base):
    """Which model is walking for which designer — many-to-many by design,
    since reusing the same model across multiple designers in one day is the goal.
    order_in_lineup tracks walk order within THIS designer's segment specifically.
    preference holds the designer's own pick ("one" / "two" / None) from their deck link."""
    __tablename__ = "designer_assignment"
    __table_args__ = (UniqueConstraint("designer_id", "applicant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    designer_id: Mapped[int] = mapped_column(ForeignKey("designer.id", ondelete="CASCADE"))
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"))
    order_in_lineup: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    designer: Mapped["Designer"] = relationship(back_populates="assignments")
    applicant: Mapped["Applicant"] = relationship()

class Deck(Base):
    __tablename__ = "deck"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("audition_event.id", ondelete="CASCADE"))
    designer_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["AuditionEvent"] = relationship(back_populates="decks")
    models: Mapped[list["DeckModel"]] = relationship(back_populates="deck", cascade="all, delete-orphan")
    link: Mapped["DeckLink | None"] = relationship(back_populates="deck", uselist=False, cascade="all, delete-orphan")


class DeckModel(Base):
    __tablename__ = "deck_model"
    __table_args__ = (UniqueConstraint("deck_id", "applicant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("deck.id", ondelete="CASCADE"))
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"))
    designer_response: Mapped[DesignerResponse | None] = mapped_column(
        Enum(DesignerResponse, name="designer_response")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    deck: Mapped["Deck"] = relationship(back_populates="models")
    applicant: Mapped["Applicant"] = relationship()


class DeckLink(Base):
    __tablename__ = "deck_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("deck.id", ondelete="CASCADE"), unique=True)
    access_token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    deck: Mapped["Deck"] = relationship(back_populates="link")