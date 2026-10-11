from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models
from app.api.participation import accept_challenge
from app.models.base import Base
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.user import User


def make_db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def challenge(identifier: str) -> Challenge:
    return Challenge(
        id=identifier,
        title=identifier.title(),
        slug=identifier,
        image_url="https://example.com/image.png",
        short_description="A public challenge.",
        full_description="A public challenge with useful details.",
        creator_id="creator",
        status="PUBLISHED",
        visibility="PUBLIC",
        published_at=datetime.utcnow(),
    )


def test_removed_participation_can_rejoin_without_counting_as_active():
    db = make_db()
    user = User(id="user-1", username="user-1")
    creator = User(id="creator", username="creator")
    target = challenge("target")
    active_challenges = [challenge(f"active-{index}") for index in range(4)]
    db.add_all([user, creator, target, *active_challenges])
    db.flush()
    db.add(
        ChallengeParticipant(
            challenge_id=target.id,
            user_id=user.id,
            status="REMOVED",
        )
    )
    db.add_all(
        ChallengeParticipant(
            challenge_id=item.id,
            user_id=user.id,
            status="IN_PROGRESS",
        )
        for item in active_challenges
    )
    db.commit()

    rejoined = accept_challenge(target.slug, db, user)

    assert rejoined.challenge_id == target.id
    assert rejoined.user_id == user.id
    assert rejoined.status == "ACCEPTED"
    assert db.scalar(
        select(func.count()).select_from(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == target.id,
            ChallengeParticipant.user_id == user.id,
        )
    ) == 1
