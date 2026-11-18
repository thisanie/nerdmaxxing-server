from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.daily_quest import DailyQuestAttempt, DailyQuestSchedule
from app.models.invitation import Notification
from app.models.user import User
from app.services.push_notification_service import send_notification_push


def daily_quest_link(local_date: str) -> str:
    return f"nerdmaxxing://daily-quest/{local_date}"


def daily_quest_result_link(attempt_id: str) -> str:
    return f"nerdmaxxing://daily-quest/result/{attempt_id}"


def create_approval_notification(db: Session, attempt: DailyQuestAttempt, quest_title: str) -> Notification | None:
    dedupe_key = f"DAILY_QUEST_APPROVED:{attempt.id}"
    existing = db.scalar(select(Notification).where(Notification.dedupe_key == dedupe_key))
    if existing is not None:
        return None
    notification = Notification(
        user_id=attempt.user_id,
        notification_type="DAILY_QUEST_APPROVED",
        title="Daily Quest approved",
        body=f"Your submission for {quest_title} was approved. Your reward is ready.",
        daily_quest_id=attempt.quest_id,
        daily_quest_local_date=attempt.local_date,
        daily_quest_attempt_id=attempt.id,
        deep_link=daily_quest_result_link(attempt.id),
        dedupe_key=dedupe_key,
    )
    db.add(notification)
    db.flush()
    return notification


def send_due_daily_quest_notifications(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Create and push each user's due live-quest notification.

    Call from an external scheduler at least every 15 minutes. A row keyed by
    user/quest/local date makes overlapping scheduler runs idempotent.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    hour, minute = _notification_time()
    sent = 0
    for user in db.scalars(select(User).where(User.is_deleted.is_(False), User.is_suspended.is_(False))).all():
        try:
            user_tz = ZoneInfo(user.timezone_name)
        except (ValueError, ZoneInfoNotFoundError):
            continue
        local_now = current.astimezone(user_tz)
        if local_now.time().replace(second=0, microsecond=0) < (datetime.min.replace(hour=hour, minute=minute).time()):
            continue
        local_date = local_now.date()
        schedule = db.scalar(
            select(DailyQuestSchedule).where(DailyQuestSchedule.local_date == local_date)
        )
        if schedule is None:
            continue
        completed = db.scalar(
            select(DailyQuestAttempt.id).where(
                DailyQuestAttempt.user_id == user.id,
                DailyQuestAttempt.local_date == local_date,
                DailyQuestAttempt.status == "COMPLETED",
            )
        )
        if completed is not None:
            continue
        dedupe_key = f"DAILY_QUEST_LIVE:{user.id}:{schedule.quest_id}:{local_date.isoformat()}"
        if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key)) is not None:
            continue
        notification = Notification(
            user_id=user.id,
            notification_type="DAILY_QUEST_LIVE",
            title="Today's Daily Quest is live",
            body="A new Daily Quest is ready. You can complete it before your local day ends.",
            daily_quest_id=schedule.quest_id,
            daily_quest_local_date=local_date,
            deep_link=daily_quest_link(local_date.isoformat()),
            dedupe_key=dedupe_key,
        )
        db.add(notification)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        send_notification_push(db, notification)
        sent += 1
    return sent


def _notification_time() -> tuple[int, int]:
    value = settings.daily_quest_notification_time
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour, minute
    except ValueError as error:
        raise RuntimeError("DAILY_QUEST_NOTIFICATION_TIME must use HH:MM (24-hour time).") from error
