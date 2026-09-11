from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.progress import ChallengeProgressLog
from app.models.user import User


def record_progress_activity(user: User, now: datetime | None = None) -> None:
    now = now or utcnow()
    if user.last_progress_at is None or now - user.last_progress_at > timedelta(hours=24):
        user.day_streak = 1
    elif now.date() != user.last_progress_at.date():
        user.day_streak += 1
    user.last_progress_at = now


def record_progress(
    db: Session,
    user: User,
    participant,
    minutes_spent: int,
    note: str | None,
) -> ChallengeProgressLog:
    progress = ChallengeProgressLog(
        participant_id=participant.id,
        user_id=user.id,
        minutes_spent=minutes_spent,
        note=note,
    )
    now = utcnow()
    participant.last_activity_at = now
    record_progress_activity(user, now)
    db.add(progress)
    return progress