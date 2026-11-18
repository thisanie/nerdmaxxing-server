from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.aura import AuraTransaction
from app.models.daily_quest import DailyQuest, DailyQuestAttempt, DailyQuestSchedule
from app.models.challenge import Challenge
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestQuestionResponse,
    DailyQuestResponse,
    DailyQuestTodayResponse,
    DailyQuestRelatedChallenge,
)
from app.services.aura_service import award_aura


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_day_window(timezone_name: str, now: datetime | None = None) -> tuple[date, datetime, ZoneInfo]:
    try:
        user_timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(status_code=422, detail="timezone must be a valid IANA timezone.") from error
    current_time = now or utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    local_date = current_time.astimezone(user_timezone).date()
    next_midnight = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=user_timezone)
    expires_at = next_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    return local_date, expires_at, user_timezone


def get_scheduled_quest(db: Session, local_date: date) -> DailyQuest | None:
    return db.scalar(
        select(DailyQuest)
        .join(DailyQuestSchedule, DailyQuestSchedule.quest_id == DailyQuest.id)
        .where(
            DailyQuestSchedule.local_date == local_date,
            DailyQuest.status == "PUBLISHED",
        )
        .options(selectinload(DailyQuest.questions), selectinload(DailyQuest.related_challenge))
    )


def attempt_expires_at(attempt: DailyQuestAttempt) -> datetime:
    try:
        user_timezone = ZoneInfo(attempt.timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(status_code=422, detail="The attempt timezone is not a valid IANA timezone.") from error
    local_midnight = datetime.combine(attempt.local_date + timedelta(days=1), time.min, tzinfo=user_timezone)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def ensure_attempt_not_expired(db: Session, attempt: DailyQuestAttempt) -> None:
    if utc_now().replace(tzinfo=None) >= attempt_expires_at(attempt):
        if attempt.status == "IN_PROGRESS":
            attempt.status = "EXPIRED"
            db.flush()
        raise HTTPException(status_code=410, detail="The Daily Quest window has expired.")


def public_quest_response(quest: DailyQuest) -> DailyQuestResponse:
    return DailyQuestResponse(
        id=quest.id,
        title=quest.title,
        short_description=quest.short_description,
        instructions=quest.instructions,
        category=quest.category,
        estimated_minutes=quest.estimated_minutes,
        verification_type=quest.verification_type,
        resource_url=quest.resource_url,
        related_challenge_id=quest.related_challenge_id,
        aura_reward=quest.aura_reward,
        related_major_challenge=(
            DailyQuestRelatedChallenge(
                id=quest.related_challenge.id,
                slug=quest.related_challenge.slug,
                title=quest.related_challenge.title,
                deep_link=f"nerdmaxxing://challenge/{quest.related_challenge.slug}",
            )
            if getattr(quest, "related_challenge", None) is not None
            else None
        ),
        questions=[
            DailyQuestQuestionResponse(
                id=question.id,
                order_index=question.order_index,
                prompt=question.prompt,
                options=question.options,
            )
            for question in quest.questions
        ] if quest.verification_type != "QUIZ" else [],
    )


def today_response(
    db: Session,
    user: User,
    timezone_name: str,
    *,
    create_attempt: bool = False,
) -> DailyQuestTodayResponse:
    local_date, expires_at, _ = local_day_window(timezone_name)
    quest = get_scheduled_quest(db, local_date)
    if quest is None:
        raise HTTPException(status_code=404, detail="No Daily Quest is scheduled for your local date.")

    schedule = db.scalar(
        select(DailyQuestSchedule).where(
            DailyQuestSchedule.quest_id == quest.id,
            DailyQuestSchedule.local_date == local_date,
        )
    )
    attempt = db.scalar(
        select(DailyQuestAttempt).where(
            DailyQuestAttempt.user_id == user.id,
            DailyQuestAttempt.local_date == local_date,
        )
    )
    if attempt is None and create_attempt:
        attempt = DailyQuestAttempt(
            quest_id=quest.id,
            user_id=user.id,
            schedule_id=schedule.id,
            local_date=local_date,
            timezone_name=timezone_name,
            status="IN_PROGRESS",
        )
        db.add(attempt)
        db.flush()
    if attempt is not None and attempt.quest_id != quest.id:
        raise HTTPException(status_code=409, detail="A different quest attempt already exists for your local date.")
    if attempt is not None and attempt.timezone_name != timezone_name:
        raise HTTPException(status_code=409, detail="This local-date attempt uses a different saved timezone.")

    remaining = (
        max(0, 2 - attempt.quiz_attempt_count)
        if attempt is not None and attempt.status == "IN_PROGRESS"
        else 2 if attempt is None and quest.verification_type == "QUIZ" else 0
    )
    return DailyQuestTodayResponse(
        local_date=local_date,
        timezone=timezone_name,
        expires_at=expires_at.replace(tzinfo=timezone.utc),
        quest=public_quest_response(quest),
        attempt_id=attempt.id if attempt else None,
        status=attempt.status if attempt else None,
        quiz_attempt_count=attempt.quiz_attempt_count if attempt else 0,
        quiz_attempts_remaining=remaining if quest.verification_type == "QUIZ" else 0,
        quiz_score_percent=attempt.quiz_score_percent if attempt else None,
    )


def complete_quest(db: Session, user: User, attempt: DailyQuestAttempt, quest: DailyQuest) -> int:
    if attempt.status == "COMPLETED":
        return 0
    existing_reward = db.scalar(
        select(AuraTransaction.id).where(
            AuraTransaction.reference_type == "DAILY_QUEST_ATTEMPT",
            AuraTransaction.reference_id == attempt.id,
        )
    )
    now = utc_now().replace(tzinfo=None)
    if existing_reward is None:
        award_aura(
            db,
            user,
            quest.aura_reward,
            "Completed Daily Quest",
            reference_type="DAILY_QUEST_ATTEMPT",
            reference_id=attempt.id,
        )
    attempt.status = "COMPLETED"
    attempt.completed_at = now
    db.flush()
    completed_dates = set(db.scalars(
        select(DailyQuestAttempt.local_date).where(
            DailyQuestAttempt.user_id == user.id,
            DailyQuestAttempt.status == "COMPLETED",
        )
    ).all())
    if completed_dates:
        latest_date = max(completed_dates)
        streak = 1
        while latest_date - timedelta(days=streak) in completed_dates:
            streak += 1
        user.last_daily_quest_date = latest_date
        user.day_streak = streak
        user.last_daily_quest_at = now
    attempt.aura_awarded = quest.aura_reward if existing_reward is not None else quest.aura_reward
    attempt.day_streak_at_completion = user.day_streak
    return quest.aura_reward if existing_reward is None else 0


def submit_quiz_answers(
    db: Session,
    user: User,
    attempt: DailyQuestAttempt,
    quest: DailyQuest,
    answers: list[int],
) -> tuple[float, bool, int]:
    """Deprecated: retained only for callers that need an explicit migration error."""
    raise HTTPException(
        status_code=409,
        detail="Bulk quiz scoring is disabled; use the server-timed quiz session flow.",
    )