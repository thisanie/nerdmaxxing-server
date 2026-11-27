from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_daily_quests import create_daily_quest, review_daily_quest_evidence, schedule_daily_quest
from app.api.daily_quests import (
    get_daily_quest_attempt_result,
    start_today_daily_quest,
    start_today_quiz_session,
    submit_today_quiz_answer,
)
from app.models.aura import AuraTransaction
from app.models.daily_quest import DailyQuestAttempt, DailyQuestQuizSession, DailyQuestSchedule
from app.models.invitation import Notification, PushToken
from app.models.challenge import Challenge
from app.schemas.daily_quest import DailyQuestQuizAnswerSubmit, DailyQuestReviewRequest, DailyQuestScheduleCreate
from app.services.daily_quest_service import local_day_window
from app.services.daily_quest_notification_service import send_due_daily_quest_notifications
from app.services.daily_quest_quiz_service import start_quiz_session
from tests.test_daily_quests import make_db, make_users, quest_payload


def scheduled_today(db, admin, timezone_name: str = "UTC"):
    quest = create_daily_quest(quest_payload(), db, admin)
    schedule_daily_quest(
        quest.id,
        DailyQuestScheduleCreate(local_date=local_day_window(timezone_name)[0]),
        db,
        admin,
    )
    return quest


def finish_session(db, user, session_id: str, answers: list[int], *, start_index: int = 0):
    response = None
    for offset, answer in enumerate(answers):
        response = submit_today_quiz_answer(
            session_id,
            DailyQuestQuizAnswerSubmit(question_index=start_index + offset, answer_option_index=answer),
            db,
            user,
        )
    return response


def test_server_timed_quiz_scores_late_answer_unanswered_and_awards_once():
    db = make_db()
    admin, user = make_users(db)
    scheduled_today(db, admin)
    started = start_today_daily_quest("UTC", db, user)
    session = start_today_quiz_session("UTC", db, user)
    assert session.seconds_per_question == 30
    assert session.question is not None and not hasattr(session.question, "correct_option_index")

    persisted = db.get(DailyQuestQuizSession, session.session_id)
    persisted.question_deadline_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    first = submit_today_quiz_answer(
        session.session_id,
        DailyQuestQuizAnswerSubmit(question_index=0, answer_option_index=0),
        db,
        user,
    )
    assert first.timed_out is True
    assert first.question_index == 1

    final = finish_session(db, user, session.session_id, [1, 0, 1, 0], start_index=1)
    assert final.result is not None
    assert final.result.score_percent == 80
    assert final.result.passed is True
    assert final.result.status == "COMPLETED"
    assert final.result.aura_awarded == 200
    assert final.result.completion_status == "COMPLETED"
    assert final.result.completion_date == local_day_window("UTC")[0]
    assert db.get(DailyQuestAttempt, started.attempt_id).quiz_answers[0] == -1
    assert user.aura_points == 200
    assert db.scalars(select(AuraTransaction).where(AuraTransaction.reference_id == started.attempt_id)).all().__len__() == 1

    replay = submit_today_quiz_answer(
        session.session_id,
        DailyQuestQuizAnswerSubmit(question_index=4, answer_option_index=0),
        db,
        user,
    )
    assert replay.result is not None
    assert replay.result.score_percent == final.result.score_percent
    assert replay.result.aura_awarded == final.result.aura_awarded
    assert user.aura_points == 200


def test_approval_push_targets_active_token_with_daily_quest_routing(monkeypatch):
    db = make_db()
    _, user = make_users(db)
    token = PushToken(
        user_id=user.id,
        token="device-token",
        platform="android",
        is_active=True,
    )
    notification = Notification(
        user_id=user.id,
        notification_type="DAILY_QUEST_APPROVED",
        title="Daily Quest approved",
        body="Your submission was approved.",
        daily_quest_id="quest-1",
        daily_quest_local_date=date(2026, 11, 20),
        daily_quest_attempt_id="attempt-1",
        deep_link="nerdmaxxing://daily-quest/result/attempt-1",
        submission_id="submission-1",
        aura_rewarded=150,
        day_streak=4,
    )
    db.add_all([token, notification])
    db.commit()
    sent = []
    monkeypatch.setattr("app.services.push_notification_service._get_firebase_app", lambda: object())
    monkeypatch.setattr(
        "app.services.push_notification_service.messaging.send",
        lambda message: sent.append(message) or "fcm-message-1",
    )

    from app.services.push_notification_service import send_notification_push

    send_notification_push(db, notification)

    assert len(sent) == 1
    assert sent[0].token == "device-token"
    assert sent[0].data["notification_type"] == "DAILY_QUEST_APPROVED"
    assert sent[0].data["daily_quest_id"] == "quest-1"
    assert sent[0].data["daily_quest_local_date"] == "2026-11-20"
    assert sent[0].data["daily_quest_attempt_id"] == "attempt-1"
    assert sent[0].data["deep_link"] == "nerdmaxxing://daily-quest/result/attempt-1"
    assert sent[0].data["submission_id"] == "submission-1"
    assert sent[0].data["aura_rewarded"] == "150"
    assert sent[0].data["day_streak"] == "4"


def test_single_retry_limit_and_duplicate_session_are_server_controlled():
    db = make_db()
    admin, user = make_users(db)
    scheduled_today(db, admin)
    start_today_daily_quest("UTC", db, user)

    first = start_today_quiz_session("UTC", db, user)
    duplicate_start = start_today_quiz_session("UTC", db, user)
    assert duplicate_start.session_id == first.session_id
    first_result = finish_session(db, user, first.session_id, [0, 0, 0, 0, 0]).result
    assert first_result.passed is False
    assert first_result.quiz_attempts_remaining == 1

    second = start_today_quiz_session("UTC", db, user)
    second_result = finish_session(db, user, second.session_id, [0, 0, 0, 0, 0]).result
    assert second_result.status == "FAILED"
    assert second_result.quiz_attempts_remaining == 0
    with pytest.raises(HTTPException) as exhausted:
        start_today_quiz_session("UTC", db, user)
    assert exhausted.value.status_code == 409


def test_attempt_expiry_uses_saved_local_date_and_timezone(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    quest_response = scheduled_today(db, admin, "America/Los_Angeles")
    started = start_today_daily_quest("America/Los_Angeles", db, user)
    attempt = db.get(DailyQuestAttempt, started.attempt_id)
    attempt.local_date = date(2026, 11, 1)
    attempt.timezone_name = "America/Los_Angeles"
    db.commit()
    quest = db.get(type(quest_response), quest_response.id) if False else db.get(__import__("app.models.daily_quest", fromlist=["DailyQuest"]).DailyQuest, quest_response.id)
    monkeypatch.setattr(
        "app.services.daily_quest_quiz_service.utc_now",
        lambda: datetime(2026, 11, 2, 8, 0, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "app.services.daily_quest_service.utc_now",
        lambda: datetime(2026, 11, 2, 8, 0, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(HTTPException) as expired:
        start_quiz_session(db, user, attempt, quest)
    assert expired.value.status_code == 410
    assert attempt.status == "EXPIRED"


def test_daily_live_notification_schedule_deduplicates_and_honors_local_time(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    user2 = type(user)(id="user-2", username="other", timezone_name="America/Los_Angeles")
    db.add(user2)
    db.commit()
    fixed_date = date(2026, 11, 18)
    quest = create_daily_quest(quest_payload(), db, admin)
    schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=fixed_date), db, admin)
    pushes = []
    monkeypatch.setattr("app.services.daily_quest_notification_service.send_notification_push", lambda _db, n: pushes.append(n.id))

    before = send_due_daily_quest_notifications(db, now=datetime(2026, 11, 18, 8, 59, tzinfo=timezone.utc))
    after = send_due_daily_quest_notifications(db, now=datetime(2026, 11, 18, 9, 1, tzinfo=timezone.utc))
    duplicate = send_due_daily_quest_notifications(db, now=datetime(2026, 11, 18, 9, 10, tzinfo=timezone.utc))
    assert before == 0 and after == 2 and duplicate == 0
    assert len(pushes) == 2
    notification = db.scalar(select(Notification).where(Notification.user_id == user.id))
    assert notification.deep_link == "nerdmaxxing://daily-quest/2026-11-18"
    assert notification.daily_quest_local_date == fixed_date


def test_completed_before_notification_time_is_not_notified(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    fixed_date = date(2026, 11, 18)
    quest = create_daily_quest(quest_payload(), db, admin)
    schedule = schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=fixed_date), db, admin)
    db.add(DailyQuestAttempt(
        quest_id=quest.id,
        user_id=user.id,
        schedule_id=schedule.id,
        local_date=fixed_date,
        timezone_name="UTC",
        status="COMPLETED",
    ))
    db.commit()
    monkeypatch.setattr("app.services.daily_quest_notification_service.send_notification_push", lambda *_: None)
    assert send_due_daily_quest_notifications(db, now=datetime(2026, 11, 18, 9, 1, tzinfo=timezone.utc)) == 1
    assert db.scalar(select(Notification).where(Notification.user_id == user.id)) is None


def test_media_result_is_owner_authorized_and_review_notification_does_not_leak_url(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    challenge = Challenge(
        id="major-1",
        title="Learn Engines",
        image_url="https://example.com/engine.jpg",
        slug="learn-engines",
        short_description="A related challenge.",
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
        evidence_storage_key="private/evidence.jpg",
        evidence_content_type="image/jpeg",
    )
    db.add(attempt)
    db.commit()
    sent = []
    monkeypatch.setattr("app.api.admin_daily_quests.send_notification_push", lambda _db, n: sent.append(n))
    reviewed = review_daily_quest_evidence(attempt.id, DailyQuestReviewRequest(decision="APPROVE"), db, admin)
    assert reviewed.aura_awarded == 200
    result = get_daily_quest_attempt_result(attempt.id, db, user)
    assert result.completion_date == local_day_window("UTC")[0]
    assert result.aura_awarded == 200
    assert result.day_streak == 1
    assert result.related_major_challenge.slug == "learn-engines"
    assert result.related_major_challenge.deep_link == "nerdmaxxing://challenge/learn-engines"
    notification = sent[0]
    assert notification.daily_quest_attempt_id == attempt.id
    assert notification.deep_link == f"nerdmaxxing://daily-quest/result/{attempt.id}"
    assert "private" not in (notification.body + notification.deep_link)

    stranger = type(user)(id="stranger", username="stranger", timezone_name="UTC")
    db.add(stranger)
    db.commit()
    with pytest.raises(HTTPException) as unauthorized:
        get_daily_quest_attempt_result(attempt.id, db, stranger)
    assert unauthorized.value.status_code == 404
    with pytest.raises(HTTPException) as duplicate_review:
        review_daily_quest_evidence(attempt.id, DailyQuestReviewRequest(decision="APPROVE"), db, admin)
    assert duplicate_review.value.status_code == 409
    assert user.aura_points == 200
    assert len(sent) == 1


def test_rejected_media_does_not_award_or_notify(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    quest = create_daily_quest(quest_payload("IMAGE"), db, admin)
    local_date = local_day_window("UTC")[0]
    schedule = schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=local_date), db, admin)
    attempt = DailyQuestAttempt(quest_id=quest.id, user_id=user.id, schedule_id=schedule.id, local_date=local_date, timezone_name="UTC", status="PENDING_REVIEW")
    db.add(attempt)
    db.commit()
    sent = []
    monkeypatch.setattr("app.api.admin_daily_quests.send_notification_push", lambda _db, n: sent.append(n))
    result = review_daily_quest_evidence(attempt.id, DailyQuestReviewRequest(decision="REJECT"), db, admin)
    assert result.status == "REJECTED"
    assert user.aura_points == 0
    assert sent == []


def test_late_publication_notifies_at_next_run_in_each_users_local_timezone(monkeypatch):
    db = make_db()
    admin, user = make_users(db)
    fixed_date = date(2026, 11, 1)
    user.timezone_name = "America/Los_Angeles"
    admin.timezone_name = "Pacific/Kiritimati"
    db.commit()
    monkeypatch.setattr("app.services.daily_quest_notification_service.send_notification_push", lambda *_: None)

    before_publication = send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 1, 17, 30, tzinfo=timezone.utc)
    )
    quest = create_daily_quest(quest_payload(), db, admin)
    schedule_daily_quest(quest.id, DailyQuestScheduleCreate(local_date=fixed_date), db, admin)
    late_run = send_due_daily_quest_notifications(
        db, now=datetime(2026, 11, 1, 17, 45, tzinfo=timezone.utc)
    )

    assert before_publication == 0
    assert late_run == 1
    assert db.scalar(select(Notification).where(Notification.user_id == user.id)).daily_quest_local_date == fixed_date
