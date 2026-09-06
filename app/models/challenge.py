import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Table, Text, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


challenge_categories = Table(
    "challenge_categories",
    Base.metadata,
    Column("challenge_id", String(36), ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", String(36), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


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
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    featured: Mapped[bool] = mapped_column(nullable=False, default=False)
    legendary: Mapped[bool] = mapped_column(nullable=False, default=False)
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

    categories: Mapped[list["Category"]] = relationship(
        secondary=challenge_categories,
        back_populates="challenges",
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