"""Day-of-show tables (check-in, non-model attendees, hair/make-up status).
Kept in its own module so models.py stays untouched. The tables themselves are
created by app/migrations.py."""
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NonModelAttendee(Base):
    """Anyone who needs backstage access who is not a model or a designer
    (volunteers, designer staff, hair/make-up team, security, ...), added for one show day."""
    __tablename__ = "non_model_attendee"

    id: Mapped[int] = mapped_column(primary_key=True)
    show_day_id: Mapped[int] = mapped_column(ForeignKey("show_day.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    attendee_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Rows created together by "all days" share a group_key so they can be removed together.
    group_key: Mapped[str | None] = mapped_column(Text)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DayModelStatus(Base):
    """One model's status on one show day: checked in, a late/arrival note, and
    (used by the Hair and Make Up pages) whether hair / make-up is done."""
    __tablename__ = "day_model_status"
    __table_args__ = (UniqueConstraint("applicant_id", "show_day_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"))
    show_day_id: Mapped[int] = mapped_column(ForeignKey("show_day.id", ondelete="CASCADE"))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    hair_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    makeup_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DesignerCheckin(Base):
    """A row exists only while the designer is checked in (each designer belongs to one show day)."""
    __tablename__ = "designer_checkin"

    designer_id: Mapped[int] = mapped_column(ForeignKey("designer.id", ondelete="CASCADE"), primary_key=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LookStatus(Base):
    """Whether the Hair / Make Up team has finished one model's look for one designer."""
    __tablename__ = "look_status"
    __table_args__ = (UniqueConstraint("applicant_id", "designer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"))
    designer_id: Mapped[int] = mapped_column(ForeignKey("designer.id", ondelete="CASCADE"))
    hair_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    makeup_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TeamOrder(Base):
    """A team's own model order inside one designer (the designer's real lineup is never touched)."""
    __tablename__ = "team_order"
    __table_args__ = (UniqueConstraint("team", "designer_id", "applicant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team: Mapped[str] = mapped_column(Text, nullable=False)
    designer_id: Mapped[int] = mapped_column(ForeignKey("designer.id", ondelete="CASCADE"))
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicant.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(nullable=False)