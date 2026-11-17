import asyncio
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

import app.models
from app.api.admin_daily_quests import (
    create_daily_quest,
    review_daily_quest_evidence,
    schedule_daily_quest,
)
from app.api.daily_quests import start_today_daily_quest, submit_today_evidence, submit_today_quiz
from app.core.time import utcnow
from app.models.aura import AuraTransaction
from app.models.base import Base
from app.models.daily_quest import DailyQuestAttempt
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestCreate,
    DailyQuestQuizSubmit,
    DailyQuestReviewRequest,
    DailyQuestScheduleCreate,
)
from app.services.daily_quest_service import local_day_window


def make_db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_users(db: Session) -> tuple[User, User]:
    admin = User(id="admin-1", username="admin", is_admin=True)
    user = User(id="user-1", username="user", timezone_name="UTC")
    db.add_all([admin, user])
    db.commit()
    return admin, user


def quest_payload(verification_type: str = "QUIZ") -> DailyQuestCreate:
    questions = [
        {"prompt": f"Question {index + 1}?", "options": ["A", "B"], "correct_option_index": index % 2}
        for index in range(5)
    ]
    return DailyQuestCreate.model_validate(
        {
            "title": "Learn an engine fact",
            "short_description": "Understand one useful idea.",
            "instructions": "Read the resource and complete the activity.",
            "category": "KNOWLEDGE",
            "estimated_minutes": 10,
            "verification_type": verification_type,
            "resource_url": "https://example.com/engine",
            "aura_reward": 2,
            "questions": questions if verification_type == "QUIZ" else [],
        }
    )


def create_scheduled_quest(db: Session, admin: User, verification_type: str = "QUIZ"):
    quest = create_daily_quest(quest_payload(verification_type), db, admin)
    schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=local_day_window("UTC")[0]), db, admin)
    return quest


def test_daily_quest_quiz_hides_answers_and_allows_one_retry():
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)

    started = start_today_daily_quest("UTC", db, user)

    assert started.quiz_attempts_remaining == 2
    assert not hasattr(started.quest.questions[0], "correct_option_index")
    first = submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 0, 0, 0, 0]), "UTC", db, user)
    assert first.passed is False
    assert first.score_percent == 60
    assert first.quiz_attempts_remaining == 1
    second = submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 1, 0, 1, 0]), "UTC", db, user)
    assert second.passed is True
    assert second.score_percent == 100
    assert second.status == "COMPLETED"
    assert second.aura_awarded == 2
    assert second.day_streak == 1
    assert user.aura_points == 2
    assert db.scalar(select(AuraTransaction).where(AuraTransaction.reference_type == "DAILY_QUEST_ATTEMPT"))

    with pytest.raises(HTTPException) as error:
        submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 1, 0, 1, 0]), "UTC", db, user)
    assert error.value.status_code == 409


def test_quiz_second_failure_uses_only_retry_and_does_not_award():
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)

    first = submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 0, 0, 0, 0]), "UTC", db, user)
    second = submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 0, 0, 0, 0]), "UTC", db, user)

    assert first.status == "IN_PROGRESS"
    assert second.status == "FAILED"
    assert second.quiz_attempts_remaining == 0
    assert user.aura_points == 0
    with pytest.raises(HTTPException) as error:
        submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 1, 0, 1, 0]), "UTC", db, user)
    assert error.value.status_code == 409


def test_media_evidence_is_private_pending_review_until_admin_approval(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    quest = create_scheduled_quest(db, admin, verification_type="IMAGE")
    started = start_today_daily_quest("UTC", db, user)

    async def fake_upload(*args, **kwargs):
        assert kwargs["return_key"] is True
        assert kwargs["bucket"]
        return "daily-quests/user-1/proof.jpg"

    monkeypatch.setattr("app.api.daily_quests.upload_blob", fake_upload)
    file = UploadFile(
        BytesIO(b"image"),
        filename="proof.jpg",
        headers=Headers({"content-type": "image/jpeg"}),
    )
    result = asyncio.run(submit_today_evidence(file, "UTC", db, user))

    attempt = db.get(DailyQuestAttempt, started.attempt_id)
    assert result.status == "PENDING_REVIEW"
    assert attempt.evidence_storage_key == "daily-quests/user-1/proof.jpg"
    assert user.aura_points == 0
    assert user.day_streak == 0

    reviewed = review_daily_quest_evidence(
        attempt.id,
        DailyQuestReviewRequest(decision="APPROVE"),
        db,
        admin,
    )
    assert reviewed.status == "COMPLETED"
    assert reviewed.aura_awarded == quest.aura_reward
    assert reviewed.day_streak == 1
    assert user.aura_points == quest.aura_reward


def test_only_one_quest_can_be_scheduled_for_a_local_date():
    db = make_db()
    admin, _ = make_users(db)
    first = create_daily_quest(quest_payload(), db, admin)
    second = create_daily_quest(quest_payload().model_copy(update={"title": "Another quest"}), db, admin)
    local_date = date.today()
    schedule_daily_quest(first.id, DailyQuestScheduleCreate(local_date=local_date), db, admin)

    with pytest.raises(HTTPException) as error:
        schedule_daily_quest(second.id, DailyQuestScheduleCreate(local_date=local_date), db, admin)
    assert error.value.status_code == 409


def test_local_day_expiry_uses_user_timezone_and_dst_boundary():
    now = datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
    local_date, expires_at, user_timezone = local_day_window("America/Los_Angeles", now)
    expected = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)

    assert user_timezone == ZoneInfo("America/Los_Angeles")
    assert local_date == date(2026, 11, 1)
    assert expires_at.replace(tzinfo=timezone.utc) == expected


def test_major_challenge_progress_does_not_increment_daily_streak():
    from app.services.progress_service import record_progress_activity

    user = User(id="user-1", username="user", day_streak=4)
    record_progress_activity(user, utcnow())

    assert user.day_streak == 4
    assert user.last_progress_at is not None