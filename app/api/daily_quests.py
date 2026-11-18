import hmac

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.daily_quest import DailyQuest, DailyQuestAttempt
from app.models.challenge import Challenge
from app.models.aura import AuraTransaction
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestAttemptResult,
    DailyQuestEvidenceResponse,
    DailyQuestQuizAnswerSubmit,
    DailyQuestQuizResult,
    DailyQuestQuizSessionResponse,
    DailyQuestQuizSubmit,
    DailyQuestTodayResponse,
    DailyQuestRelatedChallenge,
)
from app.services.blob_storage import VIDEO_CONTENT_TYPES, upload_blob
from app.services.daily_quest_service import (
    get_scheduled_quest,
    local_day_window,
    today_response,
    ensure_attempt_not_expired,
)
from app.services.daily_quest_quiz_service import answer_quiz_question, start_quiz_session
from app.services.daily_quest_notification_service import send_due_daily_quest_notifications


router = APIRouter(prefix="/api/v1/daily-quests", tags=["Daily Quests"])
IMAGE_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_QUEST_IMAGE_BYTES = 10 * 1024 * 1024
MAX_QUEST_VIDEO_BYTES = 50 * 1024 * 1024


def _timezone_for_user(timezone_name: str | None, user: User) -> str:
    return timezone_name or user.timezone_name


def _get_attempt_for_today(
    db: Session,
    user: User,
    timezone_name: str,
) -> tuple[DailyQuestAttempt, DailyQuest]:
    local_date, _, _ = local_day_window(timezone_name)
    response = today_response(db, user, timezone_name, create_attempt=True)
    attempt = db.scalar(
        select(DailyQuestAttempt)
        .where(DailyQuestAttempt.id == response.attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    quest = get_scheduled_quest(db, local_date)
    if attempt is None or quest is None:
        raise HTTPException(status_code=404, detail="Daily Quest attempt not found.")
    if attempt.local_date != local_date:
        raise HTTPException(status_code=409, detail="The attempt belongs to a different local date.")
    return attempt, quest


@router.get("/today", response_model=DailyQuestTodayResponse)
def get_today_daily_quest(
    timezone_name: str | None = Query(default=None, alias="timezone", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestTodayResponse:
    return today_response(db, current_user, _timezone_for_user(timezone_name, current_user))


@router.post("/today/start", response_model=DailyQuestTodayResponse)
def start_today_daily_quest(
    timezone_name: str | None = Query(default=None, alias="timezone", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestTodayResponse:
    selected_timezone = _timezone_for_user(timezone_name, current_user)
    response = today_response(db, current_user, selected_timezone, create_attempt=True)
    current_user.timezone_name = selected_timezone
    db.commit()
    return response


@router.post("/today/quiz", response_model=DailyQuestQuizResult)
def submit_today_quiz(
    payload: DailyQuestQuizSubmit,
    timezone_name: str | None = Query(default=None, alias="timezone", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestQuizResult:
    raise HTTPException(
        status_code=409,
        detail="Bulk quiz submission cannot enforce per-question timing. Use /today/quiz/session and submit answers sequentially.",
    )


@router.post("/today/quiz/session", response_model=DailyQuestQuizSessionResponse)
def start_today_quiz_session(
    timezone_name: str | None = Query(default=None, alias="timezone", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestQuizSessionResponse:
    selected_timezone = _timezone_for_user(timezone_name, current_user)
    attempt, quest = _get_attempt_for_today(db, current_user, selected_timezone)
    try:
        response = start_quiz_session(db, current_user, attempt, quest)
    except HTTPException as error:
        if error.status_code == status.HTTP_410_GONE:
            db.commit()
        raise
    current_user.timezone_name = selected_timezone
    db.commit()
    return response


@router.post("/today/quiz/sessions/{session_id}/answer", response_model=DailyQuestQuizSessionResponse)
def submit_today_quiz_answer(
    session_id: str,
    payload: DailyQuestQuizAnswerSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestQuizSessionResponse:
    try:
        response = answer_quiz_question(
            db,
            current_user,
            session_id,
            payload.question_index,
            payload.answer_option_index,
        )
    except HTTPException as error:
        if error.status_code == status.HTTP_410_GONE:
            db.commit()
        raise
    db.commit()
    return response


@router.get("/attempts/{attempt_id}/result", response_model=DailyQuestAttemptResult)
def get_daily_quest_attempt_result(
    attempt_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestAttemptResult:
    attempt = db.scalar(
        select(DailyQuestAttempt).where(
            DailyQuestAttempt.id == attempt_id,
            DailyQuestAttempt.user_id == current_user.id,
        )
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Daily Quest attempt result not found.")
    quest = db.get(DailyQuest, attempt.quest_id)
    challenge = db.get(Challenge, quest.related_challenge_id) if quest and quest.related_challenge_id else None
    related_challenge = None
    if challenge is not None:
        related_challenge = DailyQuestRelatedChallenge(
            id=challenge.id,
            slug=challenge.slug,
            title=challenge.title,
            deep_link=f"nerdmaxxing://challenge/{challenge.slug}",
        )
    aura_awarded = attempt.aura_awarded
    if attempt.status == "COMPLETED" and aura_awarded == 0:
        aura_awarded = db.scalar(
            select(AuraTransaction.amount).where(
                AuraTransaction.reference_type == "DAILY_QUEST_ATTEMPT",
                AuraTransaction.reference_id == attempt.id,
            )
        ) or 0
    return DailyQuestAttemptResult(
        attempt_id=attempt.id,
        status=attempt.status,
        completion_status="COMPLETED" if attempt.status == "COMPLETED" else attempt.status,
        completion_date=attempt.local_date if attempt.status == "COMPLETED" else None,
        completed_at=attempt.completed_at,
        aura_awarded=aura_awarded,
        day_streak=(
            attempt.day_streak_at_completion
            if attempt.day_streak_at_completion is not None
            else current_user.day_streak
        ),
        timezone=attempt.timezone_name,
        score_percent=attempt.quiz_score_percent,
        related_major_challenge=related_challenge,
    )


@router.get("/internal/notifications/run")
def run_daily_quest_notifications(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    expected = f"Bearer {settings.cron_secret}" if settings.cron_secret else ""
    if not expected or authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid scheduler credentials.")
    return {"notifications_created": send_due_daily_quest_notifications(db)}


@router.post("/today/evidence", response_model=DailyQuestEvidenceResponse, status_code=status.HTTP_201_CREATED)
async def submit_today_evidence(
    file: UploadFile = File(...),
    timezone_name: str | None = Query(default=None, alias="timezone", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyQuestEvidenceResponse:
    selected_timezone = _timezone_for_user(timezone_name, current_user)
    attempt, quest = _get_attempt_for_today(db, current_user, selected_timezone)
    if quest.verification_type not in {"IMAGE", "VIDEO"}:
        raise HTTPException(status_code=409, detail="This Daily Quest is not media-verified.")
    if attempt.status != "IN_PROGRESS" or attempt.evidence_storage_key is not None:
        raise HTTPException(status_code=409, detail="This quest attempt cannot accept another submission.")
    try:
        ensure_attempt_not_expired(db, attempt)
    except HTTPException as error:
        if error.status_code == status.HTTP_410_GONE:
            db.commit()
        raise

    content_type = file.content_type or ""
    if quest.verification_type == "IMAGE":
        allowed_types = IMAGE_CONTENT_TYPES
        max_bytes = MAX_QUEST_IMAGE_BYTES
    else:
        allowed_types = VIDEO_CONTENT_TYPES
        max_bytes = MAX_QUEST_VIDEO_BYTES
    if content_type not in allowed_types:
        raise HTTPException(status_code=415, detail=f"Unsupported {quest.verification_type.lower()} file type.")

    storage_key = await upload_blob(
        file,
        f"daily-quests/{current_user.id}",
        bucket=settings.r2_evidence_bucket,
        return_key=True,
        max_upload_bytes=max_bytes,
        allowed_content_types=allowed_types,
    )
    attempt.evidence_storage_key = storage_key
    attempt.evidence_content_type = content_type
    attempt.status = "PENDING_REVIEW"
    current_user.timezone_name = selected_timezone
    db.commit()
    return DailyQuestEvidenceResponse(
        attempt_id=attempt.id,
        status=attempt.status,
        message="Your submission is waiting for review.",
    )