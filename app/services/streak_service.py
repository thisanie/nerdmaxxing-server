from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.daily_quest import DailyQuestAttempt
from app.models.participation import ChallengeParticipant
from app.models.user import User


def refresh_day_streak(db: Session, user: User) -> int:
    try:
        user_timezone = ZoneInfo(user.timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        user_timezone = ZoneInfo("UTC")

    now = utcnow()
    today = now.replace(tzinfo=timezone.utc).astimezone(user_timezone).date()
    activity_dates: set[date] = set()
    activity_times: dict[date, list[datetime]] = {}

    quest_rows = db.execute(
        select(DailyQuestAttempt.local_date, DailyQuestAttempt.completed_at).where(
            DailyQuestAttempt.user_id == user.id,
            DailyQuestAttempt.status == "COMPLETED",
        )
    ).all()
    for local_date, completed_at in quest_rows:
        if local_date <= today:
            activity_dates.add(local_date)
            if completed_at is not None:
                timestamp = (
                    completed_at.replace(tzinfo=timezone.utc)
                    if completed_at.tzinfo is None
                    else completed_at
                )
                activity_times.setdefault(local_date, []).append(
                    timestamp.astimezone(timezone.utc).replace(tzinfo=None)
                )

    challenge_times = db.scalars(
        select(ChallengeParticipant.completed_at).where(
            ChallengeParticipant.user_id == user.id,
            ChallengeParticipant.completion_status == "COMPLETED",
            ChallengeParticipant.completed_at.is_not(None),
        )
    ).all()
    for completed_at in challenge_times:
        timestamp = completed_at.replace(tzinfo=timezone.utc) if completed_at.tzinfo is None else completed_at
        local_date = timestamp.astimezone(user_timezone).date()
        if local_date <= today:
            activity_dates.add(local_date)
            activity_times.setdefault(local_date, []).append(
                timestamp.astimezone(timezone.utc).replace(tzinfo=None)
            )

    if not activity_dates:
        user.day_streak = 0
        user.last_streak_date = None
        user.last_streak_at = None
        return user.day_streak

    latest_date = max(activity_dates)
    streak = 1
    while latest_date - timedelta(days=streak) in activity_dates:
        streak += 1

    latest_times = activity_times.get(latest_date, [])
    latest_at = max(latest_times) if latest_times else now
    user.day_streak = streak
    user.last_streak_date = latest_date
    user.last_streak_at = latest_at
    return streak