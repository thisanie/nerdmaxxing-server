import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class DailyQuest(Base):
    __tablename__ = "daily_quests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    short_description: Mapped[str] = mapped_column(String(300), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    related_challenge_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("challenges.id", ondelete="SET NULL"), nullable=True
    )
    aura_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", index=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    questions: Mapped[list["DailyQuestQuestion"]] = relationship(
        back_populates="quest", cascade="all, delete-orphan", order_by="DailyQuestQuestion.order_index"
    )


class DailyQuestQuestion(Base):
    __tablename__ = "daily_quest_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    quest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_quests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    correct_option_index: Mapped[int] = mapped_column(Integer, nullable=False)

    quest: Mapped[DailyQuest] = relationship(back_populates="questions")


class DailyQuestSchedule(Base):
    __tablename__ = "daily_quest_schedules"
    __table_args__ = (UniqueConstraint("local_date", name="uq_daily_quest_schedules_local_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    quest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_quests.id", ondelete="CASCADE"), nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    quest: Mapped[DailyQuest] = relationship()


class DailyQuestAttempt(Base):
    __tablename__ = "daily_quest_attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", name="uq_daily_quest_attempts_user_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    quest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_quests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    schedule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_quest_schedules.id", ondelete="CASCADE"), nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="IN_PROGRESS")
    quiz_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quiz_score_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    quiz_answers: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    aura_awarded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day_streak_at_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    quest: Mapped[DailyQuest] = relationship()


class DailyQuestQuizSession(Base):
    __tablename__ = "daily_quest_quiz_sessions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "attempt_number", name="uq_daily_quest_quiz_session_attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_quest_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    current_question_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answers: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    question_deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_question_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_timed_out: Mapped[bool] = mapped_column(nullable=False, default=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    attempt: Mapped[DailyQuestAttempt] = relationship()