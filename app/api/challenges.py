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
from app.models.milestone import ParticipantResourceCompletion
from app.models.user import User
from app.schemas.challenge import (
    ChallengeAttemptResponse,
    ChallengeCreate,
    ChallengeDetailResponse,
    ChallengeMilestoneResponse,
    ChallengeParticipantPreviewResponse,
    ChallengeProgressSummaryResponse,
    ChallengeResourceProgressResponse,
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
    all_logs = list(db.scalars(select(ChallengeProgressLog).where(
        ChallengeProgressLog.participant_id == participant.id if participant else False
    ).order_by(ChallengeProgressLog.created_at.desc()).all())) if participant else []
    logs = all_logs[:20]
    completions = list(db.scalars(
        select(ParticipantResourceCompletion).where(
            ParticipantResourceCompletion.participant_id == participant.id
        )
    ).all()) if participant else []
    completion_by_resource = {
        (completion.milestone_id, completion.resource_id): completion
        for completion in completions
    }
    resources_by_id = {resource.id: resource for resource in challenge.resources}
    configured_metrics = challenge.metrics or []
    if not configured_metrics and challenge.target_value is not None:
        configured_metrics = [{
            "key": "value", "label": "Progress", "kind": "SCORE",
            "unit": challenge.target_unit, "target": challenge.target_value,
            "direction": "AT_LEAST", "is_primary": True, "format": "DECIMAL_2",
        }]
    metric_values = {
        metric["key"]: [log.metrics.get(metric["key"]) for log in logs if log.metrics and metric["key"] in log.metrics]
        for metric in configured_metrics
    }
    response_metrics = []
    for definition in configured_metrics:
        values = metric_values[definition["key"]]
        entry = dict(definition)
        if values:
            entry.update(current=values[0], baseline=values[-1], best=max(values), average=sum(values) / len(values))
        response_metrics.append(entry)
    requirements = challenge.requirements or []
    milestone_data = []
    previous_complete = True
    for milestone in challenge.milestones:
        attachments = milestone.resources or []
        required_attachments = [resource for resource in attachments if resource.get("required", True)]
        completed_attachments = [
            resource for resource in attachments
            if (milestone.id, resource.get("resource_id")) in completion_by_resource
        ]
        milestone_completed = all(
            (milestone.id, resource.get("resource_id")) in completion_by_resource
            for resource in required_attachments
        )
        milestone_status = "COMPLETED" if milestone_completed else ("CURRENT" if previous_complete else "LOCKED")
        previous_complete = milestone_completed
        milestone_completions = [
            completion for (milestone_id, _), completion in completion_by_resource.items()
            if milestone_id == milestone.id
        ]
        resources = []
        for attachment in attachments:
            resource_id = attachment.get("resource_id")
            resource = resources_by_id.get(resource_id)
            completion = completion_by_resource.get((milestone.id, resource_id))
            resources.append({
                "id": resource_id,
                "resource_id": resource_id,
                "title": resource.title if resource else None,
                "url": resource.url if resource else None,
                "resource_type": resource.resource_type if resource else None,
                "rationale": resource.rationale if resource else None,
                "order_index": resource.order_index if resource else None,
                "required": attachment.get("required", True),
                "completed": completion is not None,
                "completed_at": completion.completed_at if completion else None,
                "resource_minutes": completion.resource_minutes if completion else None,
                "note": completion.note if completion else None,
            })
        milestone_data.append({
            "id": milestone.id,
            "order_index": milestone.order_index,
            "title": milestone.title,
            "description": milestone.description,
            "status": milestone_status,
            "current_value": sum(
                completion.resource_minutes or 0 for completion in milestone_completions
            ),
            "target_value": milestone.target_value,
            "completed_resource_count": len(completed_attachments),
            "total_resource_count": len(attachments),
            "logged_minutes": sum(
                completion.milestone_minutes or completion.resource_minutes or 0
                for completion in milestone_completions
            ),
            "resources": resources,
        })
    milestones = [ChallengeMilestoneResponse.model_validate(item) for item in milestone_data]
    completed_milestone_count = sum(item["status"] == "COMPLETED" for item in milestone_data)
    total_resource_count = sum(item["total_resource_count"] for item in milestone_data)
    completed_resource_count = sum(item["completed_resource_count"] for item in milestone_data)
    ready_for_proof = bool(milestone_data) and completed_milestone_count == len(milestone_data)
    primary_metric = next((metric for metric in configured_metrics if metric.get("is_primary")), None)
    primary_key = primary_metric.get("key") if primary_metric else None
    primary_values = [
        log.metrics[primary_key]
        for log in all_logs
        if primary_key and log.metrics and primary_key in log.metrics
    ]
    progress = ChallengeProgressSummaryResponse(
        current_value=primary_values[0] if primary_values else 0,
        target_value=primary_metric.get("target") if primary_metric else challenge.target_value,
        unit=primary_metric.get("unit") if primary_metric else challenge.target_unit,
        logged_minutes=sum(log.minutes_spent for log in all_logs),
        completed_resource_count=completed_resource_count,
        total_resource_count=total_resource_count,
        completed_milestone_count=completed_milestone_count,
        total_milestone_count=len(milestone_data),
        challenge_status="READY_FOR_PROOF" if ready_for_proof else "IN_PROGRESS",
        ready_for_proof=ready_for_proof,
    )
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
        metrics=response_metrics,
        requirements=requirements,
        progress=progress,
        milestones=milestones,
        attempts=[ChallengeAttemptResponse(id=log.id, metrics=log.metrics or ({"value": log.value} if log.value is not None else {}), created_at=log.created_at) for log in logs],
        participants=[ChallengeParticipantPreviewResponse(user_id=user.id, username=user.username, display_name=user.display_name, avatar_url=user.avatar_url, status=entry.status, completed_at=entry.completed_at) for entry, user in participants],
        verification=ChallengeVerificationResponse(type=challenge.verification_type, requirements=requirements, required_runs=challenge.required_runs, instructions=challenge.verification_instructions),
    )