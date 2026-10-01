"""Day-of-show tables (check-in, non-model attendees, hair/make-up status).
Kept in its own module so models.py stays untouched. The tables themselves are
created by app/migrations.py."""
from datetime import datetime

from sqlalchemy import ForeignKey, Text, DateTime, func
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