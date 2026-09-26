import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ParticipantResourceCompletion(Base):
    __tablename__ = "participant_resource_completions"
    __table_args__ = (UniqueConstraint("participant_id", "milestone_id", "resource_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("challenge_participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    milestone_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("challenge_milestones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    resource_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    milestone_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MetricAttempt(Base):
    __tablename__ = "metric_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("challenge_participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_key: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float | bool] = mapped_column(JSON, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    meets_target: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)