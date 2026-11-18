from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.daily_quest import DailyQuest, DailyQuestAttempt, DailyQuestSchedule
from app.models.invitation import Notification
from app.models.user import User
from app.services.push_notification_service import send_notification_push


def live_quest_link(local_date: str) -> str:
    return f"nerdmaxxing://daily-quest/{local_date}"


def attempt_result_link(attempt_id: str) -> str:
    return f"nerdmaxxing://daily-quest/result/{attempt_id}"


def create_approval_notification(
    db: Session,
    attempt: DailyQuestAttempt,
    quest_title: str,
) -> Notification | None:
    dedupe_key = f"DAILY_QUEST_APPROVED:{attempt.id}"
    if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key)) is not None:
        return None
    notification = Notification(
        user_id=attempt.user_id,
        notification_type="DAILY_QUEST_APPROVED",
        title="Daily Quest approved",
        body=f"Your submission for {quest_title} was approved. Your reward is ready.",
        daily_quest_id=attempt.quest_id,
        daily_quest_local_date=attempt.local_date,
        daily_quest_attempt_id=attempt.id,
        deep_link=attempt_result_link(attempt.id),
        dedupe_key=dedupe_key,
    )
    db.add(notification)
    db.flush()
    return notification


def _configured_local_time() -> time:
    try:
        hour_text, minute_text = settings.daily_quest_notification_time.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return time(hour, minute)
    except (AttributeError, ValueError) as error:
        raise RuntimeError("DAILY_QUEST_NOTIFICATION_TIME must be HH:MM in 24-hour time.") from error


def send_due_daily_quest_notifications(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Create and push due notifications; invoke periodically from a scheduler."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    notification_time = _configured_local_time()
    created = 0

    users = db.scalars(
        select(User).where(User.is_deleted.is_(False), User.is_suspended.is_(False))
    ).all()
    for user in users:
        try:
            local_now = current.astimezone(ZoneInfo(user.timezone_name))
        except (ValueError, ZoneInfoNotFoundError):
            continue
        if local_now.timetz().replace(tzinfo=None) < notification_time:
            continue

        local_date = local_now.date()
        schedule_and_quest = db.execute(
            select(DailyQuestSchedule, DailyQuest)
            .join(DailyQuest, DailyQuest.id == DailyQuestSchedule.quest_id)
            .where(
                DailyQuestSchedule.local_date == local_date,
                DailyQuest.status == "PUBLISHED",
            )
        ).first()
        if schedule_and_quest is None:
            continue
        schedule, quest = schedule_and_quest
        completed = db.scalar(
            select(DailyQuestAttempt.id).where(
                DailyQuestAttempt.user_id == user.id,
                DailyQuestAttempt.local_date == local_date,
                DailyQuestAttempt.status == "COMPLETED",
            )
        )
        if completed is not None:
            continue

        dedupe_key = f"DAILY_QUEST_LIVE:{user.id}:{quest.id}:{local_date.isoformat()}"
        notification = Notification(
            user_id=user.id,
            notification_type="DAILY_QUEST_LIVE",
            title="Today's Daily Quest is live",
            body=f"{quest.title} is ready for you.",
            daily_quest_id=quest.id,
            daily_quest_local_date=local_date,
            deep_link=live_quest_link(local_date.isoformat()),
            dedupe_key=dedupe_key,
        )
        db.add(notification)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        send_notification_push(db, notification)
        created += 1
    return created
