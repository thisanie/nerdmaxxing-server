import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.category import Category
from app.models.challenge import Challenge, ChallengeMilestone, ChallengeResource, DailyChallengeAssignment
from app.models.user import User
from app.schemas.challenge import (
    AdminChallengeCreate,
    ChallengeResponse,
    DailyChallengeScheduleCreate,
    DailyChallengeScheduleResponse,
)
from app.services.discovery_service import _responses


router = APIRouter(prefix="/api/v1/admin/challenges", tags=["Admin Challenges"])
IST = ZoneInfo("Asia/Kolkata")


def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")


@router.post("", response_model=ChallengeResponse, status_code=status.HTTP_201_CREATED)
def create_admin_challenge(
    payload: AdminChallengeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeResponse:
    _require_admin(current_user)
    if any(resource.url is None for resource in payload.resources):
        raise HTTPException(status_code=422, detail="Each resource needs a URL.")

    challenge = Challenge(
        title=payload.title,
        image_url=str(payload.image_url) if payload.image_url else settings.default_challenge_image_url,
        slug=f"{re.sub(r'[^a-z0-9]+', '-', payload.title.lower()).strip('-')}-{uuid.uuid4().hex[:8]}",
        short_description=payload.short_description,
        full_description=payload.full_description,
        creator_id=current_user.id,
        difficulty_level=payload.difficulty_level,
        status="PRIVATE",
        visibility="PRIVATE",
        estimated_effort_min_minutes=payload.estimated_effort_min_minutes,
        estimated_effort_max_minutes=payload.estimated_effort_max_minutes,
        estimated_duration_minutes=payload.estimated_duration_minutes,
        verification_type=payload.verification_type,
        verification_instructions=payload.verification_instructions,
        required_runs=payload.required_runs,
        metrics=[metric.model_dump() for metric in payload.metrics],
        requirements=[requirement.model_dump() for requirement in payload.requirements],
    )
    challenge.resources = [
        ChallengeResource(
            title=resource.title,
            url=str(resource.url),
            resource_type=resource.resource_type,
            rationale=resource.rationale,
            order_index=resource.order_index,
        )
        for resource in payload.resources
    ]
    if payload.category_ids:
        categories = list(db.scalars(select(Category).where(Category.id.in_(payload.category_ids))).all())
        if len(categories) != len(set(payload.category_ids)):
            raise HTTPException(status_code=422, detail="One or more categories do not exist.")
        challenge.categories = categories

    db.add(challenge)
    db.flush()
    challenge.milestones = [
        ChallengeMilestone(
            order_index=index,
            title=milestone.title,
            description=milestone.description,
            target_value=milestone.target_value,
            resources=[
                {
                    "resource_id": challenge.resources[resource_index].id,
                    "required": payload.resources[resource_index].required,
                }
                for resource_index in milestone.resource_order_indexes
            ],
        )
        for index, milestone in enumerate(payload.milestones, start=1)
    ]
    db.commit()
    db.refresh(challenge)
    return _responses([challenge], db)[0]


@router.post(
    "/{challenge_id}/daily",
    response_model=DailyChallengeScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
def schedule_daily_challenge(
    challenge_id: str,
    payload: DailyChallengeScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyChallengeScheduleResponse:
    _require_admin(current_user)
    challenge = db.get(Challenge, challenge_id)
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if challenge.status != "PRIVATE" or challenge.visibility != "PRIVATE":
        raise HTTPException(status_code=409, detail="Only a private challenge can be scheduled.")

    now_ist = datetime.now(IST)
    if payload.daily_date < now_ist.date():
        raise HTTPException(status_code=422, detail="Daily challenge date cannot be in the past.")
    starts_at = datetime.combine(payload.daily_date, time(5), IST)
    ends_at = starts_at + timedelta(hours=24)
    starts_at_utc = starts_at.astimezone(timezone.utc).replace(tzinfo=None)
    ends_at_utc = ends_at.astimezone(timezone.utc).replace(tzinfo=None)
    if db.scalar(
        select(DailyChallengeAssignment.id).where(
            DailyChallengeAssignment.daily_date == payload.daily_date
        )
    ) is not None:
        raise HTTPException(status_code=409, detail="A daily challenge is already scheduled for that date.")

    assignment = DailyChallengeAssignment(
        challenge_id=challenge.id,
        daily_date=payload.daily_date,
        starts_at=starts_at_utc,
        ends_at=ends_at_utc,
        bonus_multiplier=payload.bonus_multiplier,
    )
    challenge.status = "PUBLISHED"
    challenge.visibility = "PUBLIC"
    challenge.published_at = starts_at_utc
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="A daily challenge is already scheduled for that date.") from error
    db.refresh(challenge)
    return DailyChallengeScheduleResponse(
        daily_date=assignment.daily_date,
        starts_at=assignment.starts_at.replace(tzinfo=timezone.utc),
        ends_at=assignment.ends_at.replace(tzinfo=timezone.utc),
        bonus_multiplier=assignment.bonus_multiplier,
        challenge=_responses([challenge], db)[0],
    )