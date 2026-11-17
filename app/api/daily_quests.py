from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.daily_quest import DailyQuest, DailyQuestAttempt
from app.models.user import User
from app.schemas.daily_quest import (
    DailyQuestEvidenceResponse,
    DailyQuestQuizResult,
    DailyQuestQuizSubmit,
    DailyQuestTodayResponse,
)
from app.services.blob_storage import VIDEO_CONTENT_TYPES, upload_blob
from app.services.daily_quest_service import (
    get_scheduled_quest,
    local_day_window,
    submit_quiz_answers,
    today_response,
)


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
    )
    quest = get_scheduled_quest(db, local_date)
    if attempt is None or quest is None:
        raise HTTPException(status_code=404, detail="Daily Quest attempt not found.")
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
    selected_timezone = _timezone_for_user(timezone_name, current_user)
    attempt, quest = _get_attempt_for_today(db, current_user, selected_timezone)
    score, passed, aura_awarded = submit_quiz_answers(
        db, current_user, attempt, quest, payload.answers
    )
    current_user.timezone_name = selected_timezone
    db.commit()
    return DailyQuestQuizResult(
        attempt_id=attempt.id,
        status=attempt.status,
        score_percent=score,
        passed=passed,
        quiz_attempt_count=attempt.quiz_attempt_count,
        quiz_attempts_remaining=max(0, 2 - attempt.quiz_attempt_count),
        aura_awarded=aura_awarded,
        day_streak=current_user.day_streak,
    )


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