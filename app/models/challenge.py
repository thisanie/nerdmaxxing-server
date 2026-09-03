import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    short_description: Mapped[str] = mapped_column(String(300), nullable=False)
    full_description: Mapped[str] = mapped_column(Text, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    difficulty_level: Mapped[str] = mapped_column(String(30), nullable=False, default="BEGINNER")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PRIVATE")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="PRIVATE")
    estimated_effort_min_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_effort_max_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="SELF_REPORTED"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    resources: Mapped[list["ChallengeResource"]] = relationship(
        back_populates="challenge",
        cascade="all, delete-orphan",
        order_by="ChallengeResource.order_index",
    )


class ChallengeResource(Base):
    __tablename__ = "challenge_resources"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    challenge_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False, default="LINK")
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    challenge: Mapped[Challenge] = relationship(back_populates="resources")