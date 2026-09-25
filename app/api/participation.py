from datetime import datetime
import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.participation import (
    ParticipationResponse,
    ParticipationStatusUpdate,
    ProgressLogCreate,
    ProgressLogResponse,
)
from app.models.progress import ChallengeProgressLog
from app.services.progress_service import record_progress


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


@router.post("/{participant_id}/progress", response_model=ProgressLogResponse, status_code=status.HTTP_201_CREATED)
def log_progress(
    participant_id: str,
    payload: ProgressLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeProgressLog:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.id == participant_id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")
    if participant.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Progress can only be logged for an active challenge.",
        )

    challenge = db.get(Challenge, participant.challenge_id)
    configured_metrics = challenge.metrics if challenge and challenge.metrics else []
    definitions = {metric["key"]: metric for metric in configured_metrics}
    if not definitions and challenge and challenge.target_value is not None:
        definitions = {"value": {"unit": challenge.target_unit}}
    if not payload.metrics and payload.hours_spent is None:
        raise HTTPException(status_code=422, detail="Provide metrics or hours_spent.")
    unknown_keys = set(payload.metrics) - set(definitions)
    if unknown_keys:
        raise HTTPException(status_code=422, detail=f"Unknown metric keys: {sorted(unknown_keys)}")
    for key, value in payload.metrics.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise HTTPException(status_code=422, detail=f"Metric '{key}' must be finite.")
        if isinstance(value, bool):
            if definitions[key].get("kind") != "BOOLEAN" and definitions[key].get("direction") != "BOOLEAN":
                raise HTTPException(status_code=422, detail=f"Metric '{key}' must be numeric.")
        elif not isinstance(value, (int, float)):
            raise HTTPException(status_code=422, detail=f"Metric '{key}' must be numeric or boolean.")
        if definitions[key].get("kind") == "PERCENTAGE" and not 0 <= value <= 100:
            raise HTTPException(status_code=422, detail=f"Metric '{key}' must be between 0 and 100.")
        if isinstance(value, (int, float)) and value < 0:
            raise HTTPException(status_code=422, detail=f"Metric '{key}' cannot be negative.")

    progress = record_progress(
        db,
        current_user,
        participant,
        minutes_spent=round((payload.hours_spent or 0) * 60),
        note=payload.note,
        value=payload.metrics.get("value"),
        unit=definitions.get("value", {}).get("unit"),
        accuracy_percent=payload.metrics.get("accuracy"),
        metrics=payload.metrics,
    )
    db.commit()
    db.refresh(progress)
    return progress


@router.get("/{participant_id}/progress", response_model=list[ProgressLogResponse])
def list_progress(
    participant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ChallengeProgressLog]:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.id == participant_id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")
    return list(
        db.scalars(
            select(ChallengeProgressLog)
            .where(ChallengeProgressLog.participant_id == participant_id)
            .order_by(ChallengeProgressLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )


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


@router.delete("/{participant_id}", status_code=status.HTTP_204_NO_CONTENT)
def unenroll_from_challenge(
    participant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.id == participant_id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")

    db.delete(participant)
    db.commit()