import asyncio
from datetime import date, datetime, time, timedelta, timezone
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
from app.api.daily_quests import (
    get_daily_quest_attempt_result,
    start_today_daily_quest,
    start_today_quiz_session,
    submit_today_evidence,
    submit_today_quiz,
    submit_today_quiz_answer,
)
from app.api.invitations import list_my_notifications
from app.core.time import utcnow
from app.models.aura import AuraTransaction
from app.models.base import Base
from app.models.challenge import Challenge
from app.models.daily_quest import DailyQuestAttempt, DailyQuestQuizSession
from app.models.invitation import Notification
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestCreate,
    DailyQuestQuizAnswerSubmit,
    DailyQuestQuizAnswerSubmit,
    DailyQuestQuizSubmit,
    DailyQuestReviewRequest,
    DailyQuestScheduleCreate,
)
from app.services.daily_quest_service import local_day_window
from app.services.daily_quest_notification_service import send_due_daily_quest_notifications


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


def finish_quiz_session(db: Session, user: User, session_id: str, answers: list[int]):
    response = None
    for question_index, answer in enumerate(answers):
        response = submit_today_quiz_answer(
            session_id,
            DailyQuestQuizAnswerSubmit(
                question_index=question_index,
                answer_option_index=answer,
            ),
            db,
            user,
        )
    return response.result


def test_daily_quest_quiz_hides_answers_and_allows_one_retry():
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)

    started = start_today_daily_quest("UTC", db, user)

    assert started.quiz_attempts_remaining == 2
    assert started.quest.questions == []
    first_session = start_today_quiz_session("UTC", db, user)
    assert first_session.seconds_per_question == 30
    assert first_session.question is not None
    assert not hasattr(first_session.question, "correct_option_index")
    first = finish_quiz_session(db, user, first_session.session_id, [0, 0, 0, 0, 0])
    assert first.passed is False
    assert first.score_percent == 60
    assert first.quiz_attempts_remaining == 1
    second_session = start_today_quiz_session("UTC", db, user)
    second = finish_quiz_session(db, user, second_session.session_id, [0, 1, 0, 1, 0])
    assert second.passed is True
    assert second.score_percent == 100
    assert second.status == "COMPLETED"
    assert second.aura_awarded == 2
    assert second.day_streak == 1
    assert user.aura_points == 2
    assert db.scalar(select(AuraTransaction).where(AuraTransaction.reference_type == "DAILY_QUEST_ATTEMPT"))

    with pytest.raises(HTTPException) as error:
        submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 0, 0, 0, 0]), "UTC", db, user)
    assert error.value.status_code == 409
    assert user.aura_points == 0


def test_quiz_second_failure_uses_only_retry_and_does_not_award():
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)

    first_session = start_today_quiz_session("UTC", db, user)
    first = finish_quiz_session(db, user, first_session.session_id, [0, 0, 0, 0, 0])
    second_session = start_today_quiz_session("UTC", db, user)
    second = finish_quiz_session(db, user, second_session.session_id, [0, 0, 0, 0, 0])

    assert first.status == "IN_PROGRESS"
    assert second.status == "FAILED"
    assert second.quiz_attempts_remaining == 0
    assert user.aura_points == 0
    with pytest.raises(HTTPException) as error:
        start_today_quiz_session("UTC", db, user)
    assert error.value.status_code == 409


def test_legacy_bulk_quiz_route_is_rejected():
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)

    with pytest.raises(HTTPException) as error:
        submit_today_quiz(DailyQuestQuizSubmit(answers=[0, 1, 0, 1, 0]), "UTC", db, user)
    assert error.value.status_code == 409
    assert user.aura_points == 0


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
    with pytest.raises(HTTPException) as duplicate_submission:
        asyncio.run(submit_today_evidence(file, "UTC", db, user))
    assert duplicate_submission.value.status_code == 409

    with pytest.raises(HTTPException) as duplicate:
        asyncio.run(submit_today_evidence(file, "UTC", db, user))
    assert duplicate.value.status_code == 409

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


def test_timed_quiz_marks_late_answers_wrong_and_replays_final_result_once():
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)
    started = start_today_quiz_session("UTC", db, user)
    session = db.get(DailyQuestQuizSession, started.session_id)
    session.question_deadline_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()

    timed_out = submit_today_quiz_answer(
        session.id,
        DailyQuestQuizAnswerSubmit(question_index=0, answer_option_index=0),
        db,
        user,
    )
    assert timed_out.timed_out is True
    assert timed_out.question_index == 1
    assert session.answers == [-1]

    result = None
    for question_index, answer in enumerate([1, 0, 1, 0], start=1):
        result = submit_today_quiz_answer(
            session.id,
            DailyQuestQuizAnswerSubmit(question_index=question_index, answer_option_index=answer),
            db,
            user,
        )
    assert result.result.passed is True
    assert result.result.score_percent == 80
    assert result.result.aura_awarded == 2
    assert result.result.completion_status == "COMPLETED"
    assert result.result.completion_date == db.get(DailyQuestAttempt, started.attempt_id).local_date

    replay = submit_today_quiz_answer(
        session.id,
        DailyQuestQuizAnswerSubmit(question_index=4, answer_option_index=0),
        db,
        user,
    )
    assert replay.result == result.result
    assert user.aura_points == 2
    rewards = db.scalars(
        select(AuraTransaction).where(
            AuraTransaction.reference_type == "DAILY_QUEST_ATTEMPT",
            AuraTransaction.reference_id == started.attempt_id,
        )
    ).all()
    assert len(rewards) == 1


def test_attempt_expires_at_saved_local_midnight(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    create_scheduled_quest(db, admin)
    started = start_today_quiz_session("America/Los_Angeles", db, user)
    attempt = db.get(DailyQuestAttempt, started.attempt_id)
    expires_at = datetime.combine(attempt.local_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(attempt.timezone_name))
    expired_at_utc = expires_at.astimezone(timezone.utc)
    monkeypatch.setattr("app.services.daily_quest_service.utc_now", lambda: expired_at_utc)

    with pytest.raises(HTTPException) as error:
        submit_today_quiz_answer(
            started.session_id,
            DailyQuestQuizAnswerSubmit(question_index=0, answer_option_index=0),
            db,
            user,
        )
    assert error.value.status_code == 410
    assert attempt.status == "EXPIRED"


def test_live_notifications_use_local_time_and_are_deduplicated(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    admin.timezone_name = "America/Los_Angeles"
    fixed_date = date(2026, 11, 18)
    quest = create_daily_quest(quest_payload(), db, admin)
    schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=fixed_date), db, admin)
    pushed = []
    monkeypatch.setattr(
        "app.services.daily_quest_notification_service.send_notification_push",
        lambda _db, notification: pushed.append(notification.id),
    )

    assert send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 18, 8, 59, tzinfo=timezone.utc)
    ) == 0
    assert send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 18, 9, 1, tzinfo=timezone.utc)
    ) == 1
    assert send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 18, 9, 16, tzinfo=timezone.utc)
    ) == 0
    notification = db.scalar(select(Notification).where(Notification.user_id == user.id))
    assert notification.deep_link == "nerdmaxxing://daily-quest/2026-11-18"
    assert notification.daily_quest_id == quest.id
    assert notification.daily_quest_local_date == fixed_date
    assert len(pushed) == 1


def test_late_publication_and_dst_use_the_users_local_date(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    admin.is_deleted = True
    user.timezone_name = "America/Los_Angeles"
    fixed_date = date(2026, 11, 1)
    pushed = []
    monkeypatch.setattr(
        "app.services.daily_quest_notification_service.send_notification_push",
        lambda _db, notification: pushed.append(notification.id),
    )
    assert send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 1, 17, 5, tzinfo=timezone.utc)
    ) == 0

    quest = create_daily_quest(quest_payload(), db, admin)
    schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=fixed_date), db, admin)
    assert send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 1, 17, 10, tzinfo=timezone.utc)
    ) == 1
    assert len(pushed) == 1


def test_completed_attempt_skips_live_notification(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    admin.is_deleted = True
    local_date = date(2026, 11, 18)
    quest = create_daily_quest(quest_payload(), db, admin)
    schedule = schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=local_date), db, admin)
    db.add(DailyQuestAttempt(
        quest_id=quest.id,
        user_id=user.id,
        schedule_id=schedule.id,
        local_date=local_date,
        timezone_name="UTC",
        status="COMPLETED",
    ))
    db.commit()
    monkeypatch.setattr("app.services.daily_quest_notification_service.send_notification_push", lambda *_: None)

    assert send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 18, 9, 5, tzinfo=timezone.utc)
    ) == 0
    assert db.scalar(select(Notification).where(Notification.user_id == user.id)) is None


def test_approval_notification_result_and_owner_authorization(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    challenge = Challenge(
        id="major-1",
        title="Learn Engines",
        image_url="https://example.com/engine.jpg",
        slug="learn-engines",
        short_description="Related challenge.",
        full_description="Complete the larger learning journey.",
        creator_id=admin.id,
    )
    db.add(challenge)
    db.commit()
    payload = quest_payload("IMAGE").model_copy(update={"related_challenge_id": challenge.id})
    quest = create_daily_quest(payload, db, admin)
    local_date = local_day_window("UTC")[0]
    schedule = schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=local_date), db, admin)
    attempt = DailyQuestAttempt(
        quest_id=quest.id,
        user_id=user.id,
        schedule_id=schedule.id,
        local_date=local_date,
        timezone_name="UTC",
        status="PENDING_REVIEW",
        evidence_storage_key="private/proof.jpg",
        evidence_content_type="image/jpeg",
    )
    db.add(attempt)
    db.commit()
    sent = []
    monkeypatch.setattr("app.api.admin_daily_quests.send_notification_push", lambda _db, item: sent.append(item))

    review = review_daily_quest_evidence(
        attempt.id,
        DailyQuestReviewRequest(decision="APPROVE"),
        db,
        admin,
    )
    result = get_daily_quest_attempt_result(attempt.id, db, user)
    assert review.aura_awarded == result.aura_awarded == 2
    assert result.completion_date == local_date
    assert result.day_streak == 1
    assert result.related_major_challenge.deep_link == "nerdmaxxing://challenge/learn-engines"
    assert sent[0].daily_quest_attempt_id == attempt.id
    assert sent[0].deep_link == f"nerdmaxxing://daily-quest/result/{attempt.id}"
    assert "private" not in sent[0].body + sent[0].deep_link
    in_app_notification = list_my_notifications(db, user, False, 20, 0)[0]
    assert in_app_notification.deep_link == sent[0].deep_link
    assert in_app_notification.daily_quest_attempt_id == attempt.id

    stranger = User(id="user-2", username="stranger")
    db.add(stranger)
    db.commit()
    with pytest.raises(HTTPException) as unauthorized:
        get_daily_quest_attempt_result(attempt.id, db, stranger)
    assert unauthorized.value.status_code == 404
    with pytest.raises(HTTPException) as duplicate_review:
        review_daily_quest_evidence(
            attempt.id,
            DailyQuestReviewRequest(decision="APPROVE"),
            db,
            admin,
        )
    assert duplicate_review.value.status_code == 409
    assert len(sent) == 1


def test_rejection_does_not_award_or_notify(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    quest = create_daily_quest(quest_payload("VIDEO"), db, admin)
    local_date = local_day_window("UTC")[0]
    schedule = schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=local_date), db, admin)
    attempt = DailyQuestAttempt(
        quest_id=quest.id,
        user_id=user.id,
        schedule_id=schedule.id,
        local_date=local_date,
        timezone_name="UTC",
        status="PENDING_REVIEW",
    )
    db.add(attempt)
    db.commit()
    sent = []
    monkeypatch.setattr("app.api.admin_daily_quests.send_notification_push", lambda *_: sent.append(True))

    result = review_daily_quest_evidence(
        attempt.id,
        DailyQuestReviewRequest(decision="REJECT"),
        db,
        admin,
    )
    assert result.status == "REJECTED"
    assert user.aura_points == 0
    assert sent == []