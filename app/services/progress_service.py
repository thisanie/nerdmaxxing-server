from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.progress import ChallengeProgressLog
from app.models.user import User


def record_progress_activity(user: User, now: datetime | None = None) -> None:
    now = now or utcnow()
    user.last_progress_at = now


def record_progress(
    db: Session,
    user: User,
    participant,
    minutes_spent: int,
    note: str | None,
    value: float | None = None,
    unit: str | None = None,
    accuracy_percent: int | None = None,
    metrics: dict[str, float | bool] | None = None,
) -> ChallengeProgressLog:
    progress = ChallengeProgressLog(
        participant_id=participant.id,
        user_id=user.id,
        minutes_spent=minutes_spent,
        note=note,
        value=value,
        unit=unit,
        accuracy_percent=accuracy_percent,
        metrics=metrics or {},
    )
    now = utcnow()
    participant.last_activity_at = now
    record_progress_activity(user, now)
    db.add(progress)
    return progress