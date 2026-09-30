from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models
from app.models.aura import AuraTransaction
from app.models.base import Base
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.services.leaderboard_service import get_leaderboard


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def public_challenge(identifier: str) -> Challenge:
    return Challenge(
        id=identifier,
        title=identifier,
        slug=identifier,
        image_url="https://example.com/image.png",
        short_description="Public challenge",
        full_description="Public challenge",
        creator_id="creator",
        status="PUBLISHED",
        visibility="PUBLIC",
    )


def test_all_time_aura_pagination_viewer_and_competition_ties():
    db = make_db()
    users = [
        User(id="one", username="one", aura_points=100),
        User(id="two", username="two", aura_points=100),
        User(id="three", username="three", aura_points=0),
    ]
    db.add_all(users)
    db.add_all([
        AuraTransaction(id="a1", user_id="one", amount=100, reason="test"),
        AuraTransaction(id="a2", user_id="two", amount=100, reason="test"),
    ])
    db.commit()

    response = get_leaderboard(db, "all_time", "aura", 1, 1, users[0])

    assert response.total == 3
    assert response.entries[0].rank == 1
    assert response.entries[0].player_rank == "D"
    assert response.viewer.rank == 1
    assert response.entries[0].is_current_user is False


def test_period_metrics_rank_filter_and_public_privacy():
    db = make_db()
    now = datetime.utcnow()
    challenge = public_challenge("public-challenge")
    private_challenge = public_challenge("private-challenge")
    private_challenge.visibility = "PRIVATE"
    active = User(id="active", username="active", aura_points=500, day_streak=4, last_progress_at=now)
    zero = User(id="zero", username="zero")
    deleted = User(id="deleted", username="deleted", is_deleted=True, aura_points=900)
    db.add_all([challenge, private_challenge, active, zero, deleted])
    db.flush()
    db.add_all([
        AuraTransaction(id="current", user_id="active", amount=40, reason="test", created_at=now),
        ChallengeParticipant(id="public-completion", challenge_id=challenge.id, user_id=active.id, completion_status="COMPLETED", completed_at=now),
        ChallengeParticipant(id="private-completion", challenge_id=private_challenge.id, user_id=active.id, completion_status="COMPLETED", completed_at=now),
        ChallengeParticipant(id="old-completion", challenge_id=challenge.id, user_id=zero.id, completion_status="COMPLETED", completed_at=now - timedelta(days=40)),
    ])
    db.commit()

    completed = get_leaderboard(db, "month", "completed", 20, 0, player_rank="B")
    streak = get_leaderboard(db, "week", "streak", 20, 0)

    assert [entry.user_id for entry in completed.entries] == ["active"]
    assert completed.entries[0].completed_challenge_count == 1
    assert completed.entries[0].metric_value == 1
    assert [entry.user_id for entry in streak.entries] == ["active", "zero"]
    assert streak.entries[0].metric_value == 4
    assert deleted.id not in [entry.user_id for entry in streak.entries]
