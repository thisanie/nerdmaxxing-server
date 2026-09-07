from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, limiter
from app.models.user import User
from app.models.aura import AuraTransaction
from app.models.challenge import Challenge
from app.models.follow import UserFollow
from app.models.participation import ChallengeParticipant
from app.models.skill import UserSkill
from app.schemas.challenge import ChallengeResponse
from app.schemas.user import (
    AuraTransactionResponse,
    UsernameAvailabilityResponse,
    UsernameRequest,
    UsernameResponse,
    UserProfileResponse,
    UserProfileUpdate,
    UserSummaryResponse,
)
from app.services.discovery_service import _responses
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


def profile_response(
    db: Session,
    user: User,
    viewer_id: str | None = None,
    public_only: bool = False,
) -> UserProfileResponse:
    follower_count = db.scalar(
        select(func.count()).select_from(UserFollow).where(UserFollow.following_id == user.id)
    ) or 0
    following_count = db.scalar(
        select(func.count()).select_from(UserFollow).where(UserFollow.follower_id == user.id)
    ) or 0
    skills = list(
        db.scalars(
            select(UserSkill).where(UserSkill.user_id == user.id).order_by(UserSkill.earned_at.desc())
        ).all()
    )
    completed_conditions = [
        ChallengeParticipant.user_id == user.id,
        ChallengeParticipant.completion_status == "COMPLETED",
    ]
    completed_statement = select(Challenge).join(ChallengeParticipant).where(*completed_conditions)
    if public_only:
        completed_statement = completed_statement.where(
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    completed_challenges = db.scalars(
        completed_statement
        .options(selectinload(Challenge.categories), selectinload(Challenge.resources))
        .order_by(ChallengeParticipant.completed_at.desc(), Challenge.created_at.desc())
    ).unique().all()
    completed_count = len(completed_challenges)
    created_count = db.scalar(
        select(func.count())
        .select_from(Challenge)
        .where(
            Challenge.creator_id == user.id,
            *(
                [Challenge.status == "PUBLISHED", Challenge.visibility == "PUBLIC"]
                if public_only
                else []
            ),
        )
    ) or 0
    is_following = False
    if viewer_id is not None:
        is_following = db.scalar(
            select(UserFollow).where(
                UserFollow.follower_id == viewer_id,
                UserFollow.following_id == user.id,
            )
        ) is not None
    return UserProfileResponse(
        id=user.id,
        username=user.username,
        name=user.display_name,
        bio=user.bio,
        avatar_url=user.avatar_url,
        aura_points=user.aura_points,
        follower_count=follower_count,
        following_count=following_count,
        skills_count=len(skills),
        completed_challenges_count=completed_count,
        created_challenges_count=created_count,
        skills=[
            {"id": skill.id, "name": skill.skill_name, "unlocked_at": skill.earned_at}
            for skill in skills
        ],
        completed_challenges=_responses(list(completed_challenges), db),
        is_following=is_following,
    )


@router.get("/me/profile", response_model=UserProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    return profile_response(db, current_user, current_user.id)


@router.patch("/me/profile", response_model=UserProfileResponse)
def update_my_profile(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    current_user.display_name = payload.name
    current_user.bio = payload.bio
    current_user.avatar_url = payload.avatar_url
    db.commit()
    db.refresh(current_user)
    return profile_response(db, current_user, current_user.id)


def list_user_challenges(
    db: Session,
    current_user: User,
    mode: str,
    limit: int,
    offset: int,
) -> list:
    if mode == "active":
        conditions = [
            ChallengeParticipant.user_id == current_user.id,
            ChallengeParticipant.status.in_(("ACCEPTED", "IN_PROGRESS")),
        ]
        statement = select(Challenge).join(ChallengeParticipant)
    elif mode == "completed":
        conditions = [
            ChallengeParticipant.user_id == current_user.id,
            ChallengeParticipant.completion_status == "COMPLETED",
        ]
        statement = select(Challenge).join(ChallengeParticipant)
    else:
        conditions = [Challenge.creator_id == current_user.id]
        statement = select(Challenge)
    statement = (
        statement.where(*conditions)
        .options(selectinload(Challenge.categories), selectinload(Challenge.resources))
        .order_by(Challenge.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return _responses(list(db.scalars(statement).unique().all()), db)


@router.get("/me/challenges/active", response_model=list[ChallengeResponse])
def get_my_active_challenges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list:
    return list_user_challenges(db, current_user, "active", limit, offset)


@router.get("/me/challenges/completed", response_model=list[ChallengeResponse])
def get_my_completed_challenges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list:
    return list_user_challenges(db, current_user, "completed", limit, offset)


@router.get("/me/challenges/created", response_model=list[ChallengeResponse])
def get_my_created_challenges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list:
    return list_user_challenges(db, current_user, "created", limit, offset)


@router.get("/{username}", response_model=UserProfileResponse)
def get_profile(
    username: str,
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    user = db.scalar(select(User).where(User.username_normalized == username.lower()))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return profile_response(db, user, public_only=True)


@router.get("/me/aura/transactions", response_model=list[AuraTransactionResponse])
def list_my_aura_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AuraTransaction]:
    return list(
        db.scalars(
            select(AuraTransaction)
            .where(AuraTransaction.user_id == current_user.id)
            .order_by(AuraTransaction.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )


@router.post("/{user_id}/follow", status_code=status.HTTP_204_NO_CONTENT)
def follow_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot follow yourself.")
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if db.get(UserFollow, {"follower_id": current_user.id, "following_id": user_id}) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already following this user.")
    db.add(UserFollow(follower_id=current_user.id, following_id=user_id))
    db.commit()


@router.delete("/{user_id}/follow", status_code=status.HTTP_204_NO_CONTENT)
def unfollow_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    follow = db.get(UserFollow, {"follower_id": current_user.id, "following_id": user_id})
    if follow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow not found.")
    db.delete(follow)
    db.commit()


def list_user_connections(
    db: Session, user_id: str, following: bool, limit: int, offset: int
) -> list[User]:
    join_column = UserFollow.following_id if following else UserFollow.follower_id
    user_column = UserFollow.follower_id if following else UserFollow.following_id
    return list(
        db.scalars(
            select(User)
            .join(UserFollow, User.id == user_column)
            .where(join_column == user_id)
            .order_by(UserFollow.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )


@router.get("/{user_id}/followers", response_model=list[UserSummaryResponse])
def list_followers(
    user_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[User]:
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return list_user_connections(db, user_id, following=False, limit=limit, offset=offset)


@router.get("/{user_id}/following", response_model=list[UserSummaryResponse])
def list_following(
    user_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[User]:
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return list_user_connections(db, user_id, following=True, limit=limit, offset=offset)
