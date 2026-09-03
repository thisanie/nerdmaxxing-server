from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.participation import ParticipationResponse, ParticipationStatusUpdate


router = APIRouter(prefix="/api/v1/participation", tags=["Participation"])
ACTIVE_STATUSES = ("ACCEPTED", "IN_PROGRESS")
ALLOWED_TRANSITIONS = {
    "ACCEPTED": {"IN_PROGRESS", "PAUSED", "REMOVED"},
    "IN_PROGRESS": {"PAUSED", "REMOVED"},
    "PAUSED": {"IN_PROGRESS", "REMOVED"},
}


@router.get("/me", response_model=list[ParticipationResponse])
def list_my_participation(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChallengeParticipant]:
    statement = (
        select(ChallengeParticipant)
        .where(ChallengeParticipant.user_id == current_user.id)
        .order_by(ChallengeParticipant.last_activity_at.desc())
    )
    return list(db.scalars(statement).all())


@router.post(
    "/challenges/{slug}/accept",
    response_model=ParticipationResponse,
    status_code=status.HTTP_201_CREATED,
)
def accept_challenge(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeParticipant:
    challenge = db.scalar(
        select(Challenge).where(
            Challenge.slug == slug,
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")

    existing = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already accepted this challenge.",
        )

    active_count = db.scalar(
        select(func.count())
        .select_from(ChallengeParticipant)
        .where(
            ChallengeParticipant.user_id == current_user.id,
            ChallengeParticipant.status.in_(ACTIVE_STATUSES),
        )
    )
    if active_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have reached the limit of five active challenges.",
        )

    participant = ChallengeParticipant(challenge_id=challenge.id, user_id=current_user.id)
    db.add(participant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already accepted this challenge.",
        )
    db.refresh(participant)
    return participant


@router.patch("/{participant_id}", response_model=ParticipationResponse)
def update_participation_status(
    participant_id: str,
    payload: ParticipationStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeParticipant:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.id == participant_id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")
    if payload.status not in ALLOWED_TRANSITIONS.get(participant.status, set()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invalid participation transition.")

    participant.status = payload.status
    participant.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(participant)
    return participant