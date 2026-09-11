import re
import uuid

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user, get_db
from app.core.config import settings
from app.models.category import Category
from app.models.challenge import Challenge, ChallengeResource
from app.models.user import User
from app.schemas.challenge import ChallengeCreate, ChallengeResponse
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