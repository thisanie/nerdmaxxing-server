from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, limiter
from app.models.user import User
from app.schemas.user import (
    UsernameAvailabilityResponse,
    UsernameRequest,
    UsernameResponse,
)
from app.services.user_service import (
    is_username_available,
    is_valid_username,
    normalize_username,
)


router = APIRouter(
    prefix="/api/v1/users",
    tags=["Users"],
)


def validate_username(username: str) -> None:
    if not is_valid_username(username):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Username must be 3-24 characters using letters, numbers, or underscores.",
        )


def save_username(db: Session, user: User, username: str) -> UsernameResponse:
    validate_username(username)

    normalized_username = normalize_username(username)
    if user.username_normalized == normalized_username:
        return UsernameResponse(username=user.username or username)

    if not is_username_available(db, username=username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is unavailable.",
        )

    user.username = username
    user.username_normalized = normalized_username

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is unavailable.",
        )

    return UsernameResponse(username=user.username)


@router.get("/username-availability", response_model=UsernameAvailabilityResponse)
@limiter.limit(settings.username_availability_rate_limit)
def username_availability(
    request: Request,
    username: str,
    db: Session = Depends(get_db),
) -> UsernameAvailabilityResponse:
    validate_username(username)
    return UsernameAvailabilityResponse(
        username=username,
        available=is_username_available(db, username=username),
    )


@router.post("/me/username", response_model=UsernameResponse, status_code=status.HTTP_201_CREATED)
def create_username(
    payload: UsernameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UsernameResponse:
    if current_user.username is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A username already exists. Use PATCH to update it.",
        )
    return save_username(db, current_user, payload.username)


@router.patch("/me/username", response_model=UsernameResponse)
def update_username(
    payload: UsernameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UsernameResponse:
    return save_username(db, current_user, payload.username)
