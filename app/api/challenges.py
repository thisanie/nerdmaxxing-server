import re
import uuid

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_db, get_optional_current_user
from app.core.config import settings
from app.models.category import Category
from app.models.challenge import Challenge, ChallengeResource
from app.models.participation import ChallengeParticipant
from app.models.progress import ChallengeProgressLog
from app.models.user import User
from app.schemas.challenge import (
    ChallengeAttemptResponse,
    ChallengeCreate,
    ChallengeDetailResponse,
    ChallengeMilestoneResponse,
    ChallengeParticipantPreviewResponse,
    ChallengeProgressResponse,
    ChallengeResponse,
    ChallengeStatsResponse,
    ChallengeVerificationResponse,
)
from app.services.discovery_service import _responses
from app.services.blob_storage import upload_blob


router = APIRouter(prefix="/api/v1/challenges", tags=["Challenges"])


def make_slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "challenge"
    return f"{base}-{uuid.uuid4().hex[:8]}"


@router.get("", response_model=list[ChallengeResponse])
def list_public_challenges(
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    min_duration_minutes: int | None = Query(default=None, ge=1),
    max_duration_minutes: int | None = Query(default=None, ge=1),
    sort: str = Query(default="newest", pattern="^(newest|popular|trending)$"),
) -> list[ChallengeResponse]:
    statement = (
        select(Challenge)
        .where(Challenge.status == "PUBLISHED", Challenge.visibility == "PUBLIC")
        .options(selectinload(Challenge.categories), selectinload(Challenge.resources))
    )
    if category:
        statement = statement.join(Challenge.categories).where(Category.slug == category)
    if difficulty:
        statement = statement.where(Challenge.difficulty_level == difficulty.upper())
    if min_duration_minutes is not None:
        statement = statement.where(Challenge.estimated_duration_minutes >= min_duration_minutes)
    if max_duration_minutes is not None:
        statement = statement.where(Challenge.estimated_duration_minutes <= max_duration_minutes)
    if sort == "newest":
        statement = statement.order_by(Challenge.published_at.desc(), Challenge.created_at.desc())
    else:
        statement = statement.order_by(Challenge.created_at.desc())
    statement = (
        statement
        .offset(offset)
        .limit(limit)
    )
    return _responses(list(db.scalars(statement).all()), db)


@router.get("/private", response_model=list[ChallengeResponse])
def list_private_challenges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ChallengeResponse]:
    statement = (
        select(Challenge)
        .where(
            Challenge.creator_id == current_user.id,
            Challenge.status == "PRIVATE",
            Challenge.visibility == "PRIVATE",
        )
        .options(selectinload(Challenge.categories), selectinload(Challenge.resources))
        .order_by(Challenge.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return _responses(list(db.scalars(statement).all()), db)


@router.get("/{slug}", response_model=ChallengeResponse)
def get_public_challenge(slug: str, db: Session = Depends(get_db)) -> ChallengeResponse:
    challenge = db.scalar(
        select(Challenge).where(
            Challenge.slug == slug,
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")
    return _responses([challenge], db)[0]


@router.post("", response_model=ChallengeResponse, status_code=status.HTTP_201_CREATED)
async def create_private_challenge(
    payload: str = Form(...),
    image: UploadFile | None = File(default=None),
    resource_files: list[UploadFile] = File(
        default=[],
        description="Upload one file for each resource, in the same order as payload.resources.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeResponse:
    try:
        challenge_payload = ChallengeCreate.model_validate(json.loads(payload))
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="payload must be valid challenge JSON.") from error
    image_url = (
        await upload_blob(image, f"users/{current_user.id}/challenges")
        if image
        else settings.default_challenge_image_url
    )
    uploaded_resources = resource_files
    if len(uploaded_resources) > len(challenge_payload.resources):
        raise HTTPException(status_code=422, detail="Too many resource files were uploaded.")
    challenge = Challenge(
        **challenge_payload.model_dump(exclude={"image_url", "category_ids", "resources"}),
        image_url=image_url,
        slug=make_slug(challenge_payload.title),
        creator_id=current_user.id,
        status="PRIVATE",
        visibility="PRIVATE",
        published_at=None,
    )
    challenge.resources = [
        ChallengeResource(
            title=resource.title,
            url=str(resource.url) if resource.url else "",
            resource_type=resource.resource_type,
            rationale=resource.rationale,
            order_index=resource.order_index,
        )
        for resource in challenge_payload.resources
    ]
    for index, file in enumerate(uploaded_resources):
        challenge.resources[index].url = await upload_blob(
            file, f"users/{current_user.id}/resources"
        )
    if any(not resource.url for resource in challenge.resources):
        raise HTTPException(
            status_code=422,
            detail="Each resource needs a URL or a matching resource file.",
        )
    if challenge_payload.category_ids:
        categories = list(db.scalars(select(Category).where(Category.id.in_(challenge_payload.category_ids))).all())
        if len(categories) != len(set(challenge_payload.category_ids)):
            raise HTTPException(status_code=422, detail="One or more categories do not exist.")
        challenge.categories = categories
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return _responses([challenge], db)[0]


@router.get("/{slug}/detail", response_model=ChallengeDetailResponse)
def get_public_challenge_detail(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
) -> ChallengeDetailResponse:
    challenge = db.scalar(
        select(Challenge)
        .where(Challenge.slug == slug, Challenge.status == "PUBLISHED", Challenge.visibility == "PUBLIC")
        .options(selectinload(Challenge.resources), selectinload(Challenge.categories), selectinload(Challenge.milestones))
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")

    participant_count = db.scalar(
        select(func.count()).select_from(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.status != "REMOVED",
        )
    ) or 0
    completed_count = db.scalar(
        select(func.count()).select_from(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.status != "REMOVED",
            ChallengeParticipant.completion_status == "COMPLETED",
        )
    ) or 0
    participant = None
    if current_user:
        participant = db.scalar(select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.user_id == current_user.id,
            ChallengeParticipant.status != "REMOVED",
        ))
    logs = list(db.scalars(select(ChallengeProgressLog).where(
        ChallengeProgressLog.participant_id == participant.id if participant else False
    ).order_by(ChallengeProgressLog.created_at.desc()).limit(20)).all()) if participant else []
    values = [log.value for log in logs if log.value is not None]
    current_value = values[0] if values else 0
    progress = ChallengeProgressResponse(
        current_value=current_value,
        target_value=challenge.target_value,
        unit=challenge.target_unit,
        baseline_value=values[-1] if values else 0,
        best_value=max(values) if values else 0,
        average_value=sum(values) / len(values) if values else 0,
        accuracy_percent=logs[0].accuracy_percent if logs and logs[0].accuracy_percent is not None else 0,
        attempt_count=len(logs),
        logged_minutes=sum(log.minutes_spent for log in logs),
    )
    milestones = [
        ChallengeMilestoneResponse(
            id=milestone.id,
            order_index=milestone.order_index,
            title=milestone.title,
            description=milestone.description,
            status="COMPLETED" if current_value >= milestone.target_value else ("CURRENT" if milestone.order_index == 1 else "LOCKED"),
            current_value=current_value,
            target_value=milestone.target_value,
        )
        for milestone in challenge.milestones
    ]
    participants = db.execute(
        select(ChallengeParticipant, User)
        .join(User, User.id == ChallengeParticipant.user_id)
        .where(ChallengeParticipant.challenge_id == challenge.id, ChallengeParticipant.status != "REMOVED")
        .order_by(ChallengeParticipant.last_activity_at.desc()).limit(5)
    ).all()
    challenge_response = _responses([challenge], db)[0]
    return ChallengeDetailResponse(
        challenge=challenge_response,
        stats=ChallengeStatsResponse(participant_count=participant_count, completed_participant_count=completed_count),
        progress=progress,
        milestones=milestones,
        attempts=[ChallengeAttemptResponse(id=log.id, value=log.value, unit=log.unit, accuracy_percent=log.accuracy_percent, created_at=log.created_at) for log in logs],
        participants=[ChallengeParticipantPreviewResponse(user_id=user.id, username=user.username, display_name=user.display_name, avatar_url=user.avatar_url, status=entry.status, completed_at=entry.completed_at) for entry, user in participants],
        verification=ChallengeVerificationResponse(type=challenge.verification_type, target_value=challenge.target_value, target_unit=challenge.target_unit, min_accuracy_percent=challenge.min_accuracy_percent, required_runs=challenge.required_runs, instructions=challenge.verification_instructions),
    )