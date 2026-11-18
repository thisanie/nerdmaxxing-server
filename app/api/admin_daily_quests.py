from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.challenge import Challenge
from app.models.daily_quest import DailyQuest, DailyQuestAttempt, DailyQuestQuestion, DailyQuestSchedule
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestAdminResponse,
    DailyQuestAttemptAdminResponse,
    DailyQuestCreate,
    DailyQuestReviewRequest,
    DailyQuestReviewResponse,
    DailyQuestScheduleCreate,
    DailyQuestScheduleResponse,
)
from app.services.daily_quest_service import complete_quest
from app.services.daily_quest_notification_service import create_approval_notification
from app.services.blob_storage import private_blob_url
from app.services.push_notification_service import send_notification_push


router = APIRouter(prefix="/api/v1/admin/daily-quests", tags=["Admin Daily Quests"])


def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")


def _admin_quest_response(quest: DailyQuest) -> DailyQuestAdminResponse:
    return DailyQuestAdminResponse(
        id=quest.id,
        title=quest.title,
        short_description=quest.short_description,
        instructions=quest.instructions,
        category=quest.category,
        estimated_minutes=quest.estimated_minutes,
        verification_type=quest.verification_type,
        resource_url=quest.resource_url,
        related_challenge_id=quest.related_challenge_id,
        aura_reward=quest.aura_reward,
        status=quest.status,
        questions=[
            {
                "id": question.id,
                "order_index": question.order_index,
                "prompt": question.prompt,
                "options": question.options,
                "correct_option_index": question.correct_option_index,
            }
            for question in quest.questions
        ],
    )


@router.post("", response_model=DailyQuestAdminResponse, status_code=status.HTTP_201_CREATED)
def create_daily_quest(
    payload: DailyQuestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestAdminResponse:
    _require_admin(current_user)
    if payload.related_challenge_id and db.get(Challenge, payload.related_challenge_id) is None:
        raise HTTPException(status_code=422, detail="Related Major Challenge does not exist.")

    quest = DailyQuest(
        title=payload.title,
        short_description=payload.short_description,
        instructions=payload.instructions,
        category=payload.category,
        estimated_minutes=payload.estimated_minutes,
        verification_type=payload.verification_type,
        resource_url=str(payload.resource_url) if payload.resource_url else None,
        related_challenge_id=payload.related_challenge_id,
        aura_reward=payload.aura_reward,
        status="DRAFT",
        created_by=current_user.id,
        questions=[
            DailyQuestQuestion(
                order_index=index,
                prompt=question.prompt,
                options=question.options,
                correct_option_index=question.correct_option_index,
            )
            for index, question in enumerate(payload.questions)
        ],
    )
    db.add(quest)
    db.commit()
    db.refresh(quest)
    return _admin_quest_response(quest)


@router.get("", response_model=list[DailyQuestAdminResponse])
def list_daily_quests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DailyQuestAdminResponse]:
    _require_admin(current_user)
    quests = db.scalars(select(DailyQuest).order_by(DailyQuest.created_at.desc())).all()
    return [_admin_quest_response(quest) for quest in quests]


@router.post("/{quest_id}/schedule", response_model=DailyQuestScheduleResponse, status_code=status.HTTP_201_CREATED)
def schedule_daily_quest(
    quest_id: str,
    payload: DailyQuestScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestScheduleResponse:
    _require_admin(current_user)
    quest = db.get(DailyQuest, quest_id)
    if quest is None:
        raise HTTPException(status_code=404, detail="Daily Quest not found.")
    if quest.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only a draft Daily Quest can be scheduled.")

    schedule = DailyQuestSchedule(
        quest_id=quest.id,
        local_date=payload.local_date,
        created_by=current_user.id,
    )
    quest.status = "PUBLISHED"
    db.add(schedule)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="A Daily Quest is already scheduled for that date.") from error
    return DailyQuestScheduleResponse(id=schedule.id, quest_id=quest.id, local_date=schedule.local_date)


@router.get("/attempts/{attempt_id}", response_model=DailyQuestAttemptAdminResponse)
def get_daily_quest_attempt(
    attempt_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestAttemptAdminResponse:
    _require_admin(current_user)
    attempt = db.get(DailyQuestAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Daily Quest attempt not found.")
    return DailyQuestAttemptAdminResponse(
        id=attempt.id,
        quest_id=attempt.quest_id,
        user_id=attempt.user_id,
        local_date=attempt.local_date,
        timezone=attempt.timezone_name,
        status=attempt.status,
        evidence_content_type=attempt.evidence_content_type,
        review_note=attempt.review_note,
        created_at=attempt.created_at,
        reviewed_at=attempt.reviewed_at,
    )


@router.get("/attempts", response_model=list[DailyQuestAttemptAdminResponse])
def list_daily_quest_attempts(
    status_filter: str = Query(default="PENDING_REVIEW", alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DailyQuestAttemptAdminResponse]:
    _require_admin(current_user)
    attempts = db.scalars(
        select(DailyQuestAttempt)
        .where(DailyQuestAttempt.status == status_filter)
        .order_by(DailyQuestAttempt.created_at.asc())
        .limit(100)
    ).all()
    return [
        DailyQuestAttemptAdminResponse(
            id=attempt.id,
            quest_id=attempt.quest_id,
            user_id=attempt.user_id,
            local_date=attempt.local_date,
            timezone=attempt.timezone_name,
            status=attempt.status,
            evidence_content_type=attempt.evidence_content_type,
            review_note=attempt.review_note,
            created_at=attempt.created_at,
            reviewed_at=attempt.reviewed_at,
        )
        for attempt in attempts
    ]


@router.get("/attempts/{attempt_id}/evidence-url")
def get_daily_quest_evidence_url(
    attempt_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    _require_admin(current_user)
    attempt = db.get(DailyQuestAttempt, attempt_id)
    if attempt is None or attempt.evidence_storage_key is None:
        raise HTTPException(status_code=404, detail="Daily Quest evidence not found.")
    return {"url": private_blob_url(attempt.evidence_storage_key, settings.r2_evidence_bucket)}


@router.post("/attempts/{attempt_id}/review", response_model=DailyQuestReviewResponse)
def review_daily_quest_evidence(
    attempt_id: str,
    payload: DailyQuestReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestReviewResponse:
    _require_admin(current_user)
    attempt = db.scalar(
        select(DailyQuestAttempt)
        .where(DailyQuestAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Daily Quest attempt not found.")
    if attempt.status != "PENDING_REVIEW":
        raise HTTPException(status_code=409, detail="This submission is no longer awaiting review.")
    quest = db.get(DailyQuest, attempt.quest_id)
    user = db.get(User, attempt.user_id)
    if quest is None or user is None:
        raise HTTPException(status_code=404, detail="Daily Quest attempt context not found.")

    attempt.reviewed_by = current_user.id
    attempt.reviewed_at = datetime.utcnow()
    attempt.review_note = payload.note
    aura_awarded = 0
    approval_notification = None
    if payload.decision == "APPROVE":
        aura_awarded = complete_quest(db, user, attempt, quest)
        approval_notification = create_approval_notification(db, attempt, quest.title)
    else:
        attempt.status = "REJECTED"
    db.commit()
    if approval_notification is not None:
        send_notification_push(db, approval_notification)
    return DailyQuestReviewResponse(
        attempt_id=attempt.id,
        status=attempt.status,
        aura_awarded=aura_awarded,
        day_streak=user.day_streak,
    )