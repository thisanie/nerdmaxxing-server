from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models
from app.api.participation import accept_challenge, list_my_participation
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


def test_removed_participation_can_rejoin_without_an_active_challenge_limit():
    db = make_db()
    user = User(id="user-1", username="user-1")
    creator = User(id="creator", username="creator")
    target = challenge("target")
    active_challenges = [challenge(f"active-{index}") for index in range(6)]
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


def test_submitted_participation_remains_in_my_participation():
    db = make_db()
    user = User(id="user-1", username="user-1")
    creator = User(id="creator", username="creator")
    target = challenge("target")
    db.add_all([user, creator, target])
    db.flush()
    participant = ChallengeParticipant(
        challenge_id=target.id,
        user_id=user.id,
        status="SUBMITTED",
        completion_status="NOT_COMPLETED",
        verification_status="PENDING",
    )
    db.add(participant)
    db.commit()

    listed = list_my_participation(db, user)

    assert [item.id for item in listed] == [participant.id]


def test_duplicate_accept_returns_submitted_participation():
    db = make_db()
    user = User(id="user-1", username="user-1")
    creator = User(id="creator", username="creator")
    target = challenge("target")
    db.add_all([user, creator, target])
    db.flush()
    participant = ChallengeParticipant(
        challenge_id=target.id,
        user_id=user.id,
        status="SUBMITTED",
        completion_status="NOT_COMPLETED",
        verification_status="PENDING",
    )
    db.add(participant)
    db.commit()

    accepted = accept_challenge(target.slug, db, user)

    assert accepted.id == participant.id
    assert db.scalar(
        select(func.count()).select_from(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == target.id,
            ChallengeParticipant.user_id == user.id,
        )
    ) == 1
