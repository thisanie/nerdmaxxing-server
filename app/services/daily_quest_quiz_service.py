from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.daily_quest import DailyQuest, DailyQuestAttempt, DailyQuestQuizSession
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestQuestionResponse,
    DailyQuestQuizResult,
    DailyQuestQuizSessionResponse,
    DailyQuestRelatedChallenge,
)
from app.services.daily_quest_service import complete_quest, ensure_attempt_not_expired, utc_now


def _question_response(quest: DailyQuest, index: int) -> DailyQuestQuestionResponse:
    question = quest.questions[index]
    return DailyQuestQuestionResponse(
        id=question.id,
        order_index=question.order_index,
        prompt=question.prompt,
        options=question.options,
    )


def _related_challenge(quest: DailyQuest) -> DailyQuestRelatedChallenge | None:
    challenge = quest.related_challenge
    if challenge is None:
        return None
    return DailyQuestRelatedChallenge(
        id=challenge.id,
        slug=challenge.slug,
        title=challenge.title,
        deep_link=f"nerdmaxxing://challenge/{challenge.slug}",
    )


def _session_response(
    session: DailyQuestQuizSession,
    quest: DailyQuest,
    *,
    timed_out: bool = False,
    result: DailyQuestQuizResult | None = None,
) -> DailyQuestQuizSessionResponse:
    has_question = result is None and session.current_question_index < len(quest.questions)
    index = session.current_question_index if has_question else None
    return DailyQuestQuizSessionResponse(
        session_id=session.id,
        attempt_id=session.attempt_id,
        attempt_number=session.attempt_number,
        question=_question_response(quest, index) if index is not None else None,
        question_index=index,
        question_number=index + 1 if index is not None else None,
        total_questions=len(quest.questions),
        seconds_per_question=settings.daily_quest_question_seconds,
        answer_deadline_at=(
            session.question_deadline_at.replace(tzinfo=timezone.utc) if has_question else None
        ),
        timed_out=timed_out,
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
        select(DailyQuestQuizSession)
        .where(
            DailyQuestQuizSession.attempt_id == attempt.id,
            DailyQuestQuizSession.status == "ACTIVE",
        )
        .with_for_update()
    )
    if active is not None:
        return _session_response(active, quest)
    if attempt.quiz_attempt_count >= 2:
        raise HTTPException(status_code=409, detail="No quiz retries remain for today.")
    if not quest.questions:
        raise HTTPException(status_code=409, detail="This quiz has no questions.")
    now = utc_now().replace(tzinfo=None)
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
    return _session_response(session, quest)


def answer_quiz_question(
    db: Session,
    user: User,
    session_id: str,
    question_index: int,
    answer_option_index: int | None,
) -> DailyQuestQuizSessionResponse:
    session = db.scalar(
        select(DailyQuestQuizSession)
        .where(DailyQuestQuizSession.id == session_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Quiz session not found.")
    attempt = db.scalar(
        select(DailyQuestAttempt)
        .where(DailyQuestAttempt.id == session.attempt_id)
        .with_for_update()
    )
    if attempt is None or attempt.user_id != user.id:
        raise HTTPException(status_code=403, detail="This quiz session belongs to another user.")
    session = db.scalar(
        select(DailyQuestQuizSession)
        .where(DailyQuestQuizSession.id == session_id)
        .with_for_update()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Quiz session not found.")
    quest = db.scalar(
        select(DailyQuest)
        .where(DailyQuest.id == attempt.quest_id)
        .options(selectinload(DailyQuest.questions), selectinload(DailyQuest.related_challenge))
    )
    if quest is None:
        raise HTTPException(status_code=404, detail="Daily Quest not found.")
    ensure_attempt_not_expired(db, attempt)
    if session.status != "ACTIVE" or attempt.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="This quiz session is no longer active.")
    if question_index != session.current_question_index:
        raise HTTPException(status_code=409, detail="The question index is stale or has already been answered.")
    question = quest.questions[question_index]
    if answer_option_index is not None and answer_option_index >= len(question.options):
        raise HTTPException(status_code=422, detail="The answer option index is outside the available options.")

    now = utc_now().replace(tzinfo=None)
    timed_out = now >= session.question_deadline_at
    recorded_answer = -1 if timed_out or answer_option_index is None else answer_option_index
    session.answers = [*session.answers, recorded_answer]
    session.current_question_index += 1
    if session.current_question_index < len(quest.questions):
        session.question_deadline_at = now + timedelta(seconds=settings.daily_quest_question_seconds)
        db.flush()
        return _session_response(session, quest, timed_out=timed_out)

    correct = sum(
        answer == question.correct_option_index
        for answer, question in zip(session.answers, quest.questions)
    )
    score = round(correct * 100 / len(quest.questions), 2)
    passed = score >= 80
    attempt.quiz_attempt_count += 1
    attempt.quiz_score_percent = score
    attempt.quiz_answers = session.answers
    session.status = "PASSED" if passed else "FAILED"
    session.completed_at = now
    awarded = 0
    if passed:
        awarded = complete_quest(db, user, attempt, quest)
    elif attempt.quiz_attempt_count >= 2:
        attempt.status = "FAILED"
    db.flush()
    result = DailyQuestQuizResult(
        attempt_id=attempt.id,
        status=attempt.status,
        score_percent=score,
        passed=passed,
        quiz_attempt_count=attempt.quiz_attempt_count,
        quiz_attempts_remaining=max(0, 2 - attempt.quiz_attempt_count) if not passed else 0,
        aura_awarded=awarded,
        day_streak=user.day_streak,
        completion_status="COMPLETED" if passed else ("FAILED" if attempt.status == "FAILED" else "RETRY_AVAILABLE"),
        completion_date=attempt.local_date if passed else None,
        related_major_challenge=_related_challenge(quest),
    )
    return _session_response(session, quest, timed_out=timed_out, result=result)
