from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models
import app.api.users as users_api
from app.models.base import Base
from app.models.challenge import Challenge, ChallengeMilestone
from app.models.milestone import MetricAttempt, ParticipantResourceCompletion
from app.models.participation import ChallengeParticipant
from app.models.progress import ChallengeProgressLog
from app.models.user import User


def make_db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_stats_activity_days_use_local_dates_and_all_streak_activity_sources(monkeypatch):
    db = make_db()
    now = datetime(2026, 11, 10, 12, 0)
    monkeypatch.setattr(users_api, "utcnow", lambda: now)

    user = User(id="user-1", username="ada", aura_points=420, day_streak=3)
    challenge = Challenge(
        id="challenge-1",
        title="Challenge",
        image_url="https://example.com/image.png",
        slug="challenge",
        short_description="A challenge.",
        full_description="A challenge with useful details.",
        creator_id="creator",
    )
    participant = ChallengeParticipant(
        id="participant-1",
        challenge_id=challenge.id,
        user_id=user.id,
        status="IN_PROGRESS",
    )
    milestone = ChallengeMilestone(
        id="milestone-1",
        challenge_id=challenge.id,
        title="Milestone",
        description="Complete a milestone.",
        target_value=1,
    )
    db.add_all(
        [
            user,
            challenge,
            participant,
            milestone,
            ChallengeProgressLog(
                participant_id=participant.id,
                user_id=user.id,
                minutes_spent=10,
                created_at=datetime(2026, 11, 4, 7, 30),
            ),
            ChallengeProgressLog(
                participant_id=participant.id,
                user_id=user.id,
                minutes_spent=10,
                created_at=datetime(2026, 11, 4, 8, 30),
            ),
            MetricAttempt(
                participant_id=participant.id,
                metric_key="reps",
                value=10,
                meets_target=False,
                created_at=datetime(2026, 11, 6, 7, 30),
            ),
            ParticipantResourceCompletion(
                participant_id=participant.id,
                milestone_id=milestone.id,
                resource_id="resource-1",
                completed_at=datetime(2026, 11, 8, 8, 30),
            ),
            MetricAttempt(
                participant_id=participant.id,
                metric_key="reps",
                value=12,
                meets_target=True,
                created_at=datetime(2026, 11, 10, 12, 0),
            ),
        ]
    )
    db.commit()

    stats = users_api.get_my_stats(
        db=db,
        current_user=user,
        timezone_name="America/Los_Angeles",
    )

    assert [(day.date.isoformat(), day.active) for day in stats.activity_days] == [
        ("2026-11-04", True),
        ("2026-11-05", True),
        ("2026-11-06", False),
        ("2026-11-07", False),
        ("2026-11-08", True),
        ("2026-11-09", False),
        ("2026-11-10", True),
    ]


def test_stats_rejects_unknown_timezone():
    db = make_db()
    user = User(id="user-1", username="ada", aura_points=0, day_streak=0)

    try:
        users_api.get_my_stats(db=db, current_user=user, timezone_name="Not/A_Timezone")
    except users_api.HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("Expected an invalid timezone to be rejected")