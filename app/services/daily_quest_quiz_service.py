from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.challenge import Challenge
from app.models.daily_quest import DailyQuest, DailyQuestAttempt, DailyQuestQuizSession
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestQuestionResponse,
    DailyQuestQuizResult,
    DailyQuestQuizSessionResponse,
    DailyQuestRelatedChallenge,
)
from app.services.daily_quest_service import (
    attempt_expires_at,
    complete_quest,
    ensure_attempt_not_expired,
    utc_now,
)


def _related_challenge(db: Session, quest: DailyQuest) -> DailyQuestRelatedChallenge | None:
    if quest.related_challenge_id is None:
        return None
    challenge = db.get(Challenge, quest.related_challenge_id)
    if challenge is None:
        return None
    return DailyQuestRelatedChallenge(
        id=challenge.id,
        slug=challenge.slug,
        title=challenge.title,
        deep_link=f"nerdmaxxing://challenge/{challenge.slug}",
    )


def _result_from_session(session: DailyQuestQuizSession) -> DailyQuestQuizResult | None:
    if session.result is None:
        return None
    return DailyQuestQuizResult.model_validate(session.result)


def _response(
    session: DailyQuestQuizSession,
    quest: DailyQuest,
    db: Session,
    *,
    timed_out: bool | None = None,
) -> DailyQuestQuizSessionResponse:
    result = _result_from_session(session)
    active_question = session.status == "ACTIVE" and result is None
    question_index = session.current_question_index if active_question else None
    question = None
    if question_index is not None:
        source = quest.questions[question_index]
        question = DailyQuestQuestionResponse(
            id=source.id,
            order_index=source.order_index,
            prompt=source.prompt,
            options=source.options,
        )
    return DailyQuestQuizSessionResponse(
        session_id=session.id,
        attempt_id=session.attempt_id,
        attempt_number=session.attempt_number,
        question=question,
        question_index=question_index,
        question_number=question_index + 1 if question_index is not None else None,
        total_questions=len(quest.questions),
        seconds_per_question=settings.daily_quest_question_seconds,
        answer_deadline_at=(
            session.question_deadline_at.replace(tzinfo=timezone.utc) if active_question else None
        ),
        timed_out=session.last_timed_out if timed_out is None else timed_out,
        result=result,
    )


def start_quiz_session(
    db: Session,
    user: User,
    attempt: DailyQuestAttempt,
    quest: DailyQuest,
) -> DailyQuestQuizSessionResponse:
    if quest.verification_type != "QUIZ":
        raise HTTPException(status_code=409, detail="This Daily Quest is not quiz-verified.")
    ensure_attempt_not_expired(db, attempt)
    if attempt.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="This quest attempt is no longer active.")
    active = db.scalar(
        select(DailyQuestQuizSession).where(
            DailyQuestQuizSession.attempt_id == attempt.id,
            DailyQuestQuizSession.status == "ACTIVE",
        )
    )
    if active is not None:
        return _response(active, quest, db)
    if attempt.quiz_attempt_count >= 2:
        raise HTTPException(status_code=409, detail="No quiz retries remain for today.")
    if not quest.questions:
        raise HTTPException(status_code=409, detail="This quiz has no questions.")

    now = utc_now().astimezone(timezone.utc).replace(tzinfo=None)
    session = DailyQuestQuizSession(
        attempt_id=attempt.id,
        attempt_number=attempt.quiz_attempt_count + 1,
        current_question_index=0,
        answers=[],
        status="ACTIVE",
        question_deadline_at=now + timedelta(seconds=settings.daily_quest_question_seconds),
    )
    db.add(session)
    db.flush()
    return _response(session, quest, db, timed_out=False)


def _finish_quiz(
    db: Session,
    user: User,
    attempt: DailyQuestAttempt,
    quest: DailyQuest,
    session: DailyQuestQuizSession,
    now: datetime,
) -> DailyQuestQuizResult:
    correct = sum(
        answer == question.correct_option_index
        for answer, question in zip(session.answers, quest.questions)
    )
    score = round(correct * 100 / len(quest.questions), 2)
    passed = score >= 80
    attempt.quiz_attempt_count += 1
    attempt.quiz_score_percent = score
    attempt.quiz_answers = list(session.answers)
    aura_awarded = complete_quest(db, user, attempt, quest) if passed else 0
    if not passed and attempt.quiz_attempt_count >= 2:
        attempt.status = "FAILED"
    session.status = "PASSED" if passed else "FAILED"
    session.completed_at = now
    result = DailyQuestQuizResult(
        attempt_id=attempt.id,
        status=attempt.status,
        score_percent=score,
        passed=passed,
        quiz_attempt_count=attempt.quiz_attempt_count,
        quiz_attempts_remaining=max(0, 2 - attempt.quiz_attempt_count) if not passed else 0,
        aura_awarded=aura_awarded,
        day_streak=user.day_streak,
        completion_status=("COMPLETED" if passed else "FAILED" if attempt.status == "FAILED" else "RETRY_AVAILABLE"),
        completion_date=attempt.local_date if passed else None,
        related_major_challenge=_related_challenge(db, quest),
    )
    session.result = result.model_dump(mode="json")
    db.flush()
    return result


def answer_quiz_question(
    db: Session,
    user: User,
    session_id: str,
    question_index: int,
    answer_option_index: int | None,
) -> DailyQuestQuizSessionResponse:
    session = db.get(DailyQuestQuizSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Quiz session not found.")
    attempt = db.scalar(
        select(DailyQuestAttempt)
        .where(
            DailyQuestAttempt.id == session.attempt_id,
            DailyQuestAttempt.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Quiz session not found.")
    session = db.scalar(
        select(DailyQuestQuizSession)
        .where(DailyQuestQuizSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Quiz session not found.")
    quest = db.scalar(
        select(DailyQuest)
        .where(DailyQuest.id == attempt.quest_id)
        .options(selectinload(DailyQuest.questions))
    )
    if quest is None:
        raise HTTPException(status_code=404, detail="Daily Quest not found.")

    if (
        session.status in {"PASSED", "FAILED"}
        and session.result is not None
        and question_index == session.last_question_index
    ):
        return _response(session, quest, db)
    if session.status != "ACTIVE" or attempt.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="This quiz session is no longer active.")
    ensure_attempt_not_expired(db, attempt)
    if question_index == session.last_question_index:
        return _response(session, quest, db)
    if question_index != session.current_question_index:
        raise HTTPException(status_code=409, detail="The question index is stale or out of sequence.")
    if answer_option_index is not None and answer_option_index >= len(quest.questions[question_index].options):
        raise HTTPException(status_code=422, detail="The answer option index is outside the available options.")

    now = utc_now().astimezone(timezone.utc).replace(tzinfo=None)
    is_late = now >= session.question_deadline_at
    session.last_question_index = question_index
    session.last_timed_out = is_late
    session.answers = [*session.answers, -1 if is_late or answer_option_index is None else answer_option_index]
    session.current_question_index += 1
    if session.current_question_index == len(quest.questions):
        _finish_quiz(db, user, attempt, quest, session, now)
    else:
        session.question_deadline_at = min(
            now + timedelta(seconds=settings.daily_quest_question_seconds),
            attempt_expires_at(attempt),
        )
        db.flush()
    return _response(session, quest, db)