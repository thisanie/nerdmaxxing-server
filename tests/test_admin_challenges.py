from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models
from app.api.admin_challenges import create_admin_challenge, schedule_daily_challenge
from app.api.challenges import get_daily_challenge, list_public_challenges
from app.models.base import Base
from app.models.challenge import Challenge, DailyChallengeAssignment
from app.models.user import User
from app.schemas.challenge import AdminChallengeCreate, DailyChallengeScheduleCreate
from app.services.challenge_visibility import public_challenge_conditions


IST = ZoneInfo("Asia/Kolkata")


def make_db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def challenge_payload(title: str = "Daily focus challenge") -> AdminChallengeCreate:
    return AdminChallengeCreate.model_validate(
        {
            "title": title,
            "short_description": "A short challenge.",
            "full_description": "A detailed challenge description.",
            "verification_instructions": "Submit evidence showing the completed target.",
            "resources": [
                {
                    "title": "Reference guide",
                    "url": "https://example.com/guide",
                    "rationale": "Use this guide to prepare.",
                    "required": False,
                }
            ],
            "milestones": [
                {
                    "title": "Reach the target",
                    "description": "Complete the challenge target.",
                    "target_value": 1,
                    "resource_order_indexes": [0],
                }
            ],
        }
    )


def test_admin_creation_keeps_challenge_private_and_saves_milestones():
    db = make_db()
    admin = User(id="admin-1", username="admin", is_admin=True)
    db.add(admin)
    db.commit()

    response = create_admin_challenge(challenge_payload(), db, admin)

    challenge = db.get(Challenge, response.id)
    assert challenge is not None
    assert challenge.status == "PRIVATE"
    assert challenge.visibility == "PRIVATE"
    assert challenge.verification_instructions == "Submit evidence showing the completed target."
    assert len(challenge.milestones) == 1
    resource_id = challenge.resources[0].id
    assert challenge.milestones[0].resources == [{"resource_id": resource_id, "required": False}]


def test_non_admin_cannot_create_admin_challenge():
    db = make_db()
    user = User(id="user-1", username="user", is_admin=False)
    db.add(user)
    db.commit()

    with pytest.raises(HTTPException) as error:
        create_admin_challenge(challenge_payload(), db, user)

    assert error.value.status_code == 403


def test_admin_challenge_rejects_negative_milestone_resource_indexes():
    payload = challenge_payload().model_dump(mode="json")
    payload["milestones"][0]["resource_order_indexes"] = [-1]

    with pytest.raises(ValidationError):
        AdminChallengeCreate.model_validate(payload)


def test_daily_schedule_starts_at_five_ist_and_is_not_public_early():
    db = make_db()
    admin = User(id="admin-1", username="admin", is_admin=True)
    db.add(admin)
    db.commit()
    created = create_admin_challenge(challenge_payload(), db, admin)
    daily_date = datetime.now(IST).date() + timedelta(days=1)

    response = schedule_daily_challenge(
        created.id,
        DailyChallengeScheduleCreate(daily_date=daily_date),
        db,
        admin,
    )

    expected_start = datetime.combine(daily_date, datetime.min.time(), IST).replace(hour=5)
    assert response.starts_at == expected_start.astimezone(timezone.utc)
    assert response.ends_at - response.starts_at == timedelta(hours=24)
    assert response.bonus_multiplier == 2
    assert db.scalar(
        select(Challenge).where(Challenge.id == created.id, *public_challenge_conditions())
    ) is None
    assert db.scalar(select(DailyChallengeAssignment).where(DailyChallengeAssignment.daily_date == daily_date))


def test_only_one_challenge_can_be_scheduled_per_day():
    db = make_db()
    admin = User(id="admin-1", username="admin", is_admin=True)
    db.add(admin)
    db.commit()
    first = create_admin_challenge(challenge_payload("First challenge"), db, admin)
    second = create_admin_challenge(challenge_payload("Second challenge"), db, admin)
    daily_date = datetime.now(IST).date() + timedelta(days=1)
    payload = DailyChallengeScheduleCreate(daily_date=daily_date)
    schedule_daily_challenge(first.id, payload, db, admin)

    with pytest.raises(HTTPException) as error:
        schedule_daily_challenge(second.id, payload, db, admin)

    assert error.value.status_code == 409


def test_active_daily_challenge_is_marked_in_daily_and_list_payloads():
    db = make_db()
    admin = User(id="admin-1", username="admin", is_admin=True)
    db.add(admin)
    db.commit()
    created = create_admin_challenge(challenge_payload(), db, admin)
    daily_date = datetime.now(IST).date() + timedelta(days=1)
    schedule_daily_challenge(
        created.id,
        DailyChallengeScheduleCreate(daily_date=daily_date),
        db,
        admin,
    )
    assignment = db.scalar(select(DailyChallengeAssignment))
    now = datetime.utcnow()
    assignment.starts_at = now - timedelta(hours=1)
    assignment.ends_at = now + timedelta(hours=23)
    db.get(Challenge, created.id).published_at = assignment.starts_at
    db.commit()

    daily = get_daily_challenge(db)
    public_list = list_public_challenges(
        db,
        limit=20,
        offset=0,
        category=None,
        difficulty=None,
        min_duration_minutes=None,
        max_duration_minutes=None,
        sort="newest",
    )

    assert daily is not None
    assert daily.is_daily_challenge
    assert daily.daily_bonus_multiplier == 2
    listed_daily = next(challenge for challenge in public_list if challenge.id == created.id)
    assert listed_daily.is_daily_challenge
    assert listed_daily.daily_challenge_date == daily_date