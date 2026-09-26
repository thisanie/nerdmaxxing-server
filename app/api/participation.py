from datetime import datetime
import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.challenge import Challenge, ChallengeResource
from app.models.challenge import ChallengeMilestone
from app.models.milestone import MetricAttempt, ParticipantResourceCompletion
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.participation import (
    ParticipationResponse,
    ParticipationStatusUpdate,
    ProgressLogCreate,
    ProgressLogResponse,
    ResourceCompletionCreate,
    ResourceCompletionResponse,
    ResourceCompletionStatusResponse,
    MetricAttemptCreate,
    MetricAttemptResponse,
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


def get_active_participant(participant_id: str, db: Session, current_user: User) -> ChallengeParticipant:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.id == participant_id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")
    if participant.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Participation is not active.")
    return participant


def milestone_progress(
    participant: ChallengeParticipant,
    milestones: list[ChallengeMilestone],
    completions: list[ParticipantResourceCompletion],
) -> tuple[dict[str, str], set[str]]:
    completed_resources = {
        (completion.milestone_id, completion.resource_id)
        for completion in completions
    }
    statuses: dict[str, str] = {}
    completed_milestones: set[str] = set()
    previous_complete = True
    for milestone in milestones:
        required_ids = {
            resource["resource_id"]
            for resource in milestone.resources or []
            if resource.get("required", True)
        }
        complete = all(
            (milestone.id, resource_id) in completed_resources
            for resource_id in required_ids
        )
        if complete:
            statuses[milestone.id] = "COMPLETED"
            completed_milestones.add(milestone.id)
        elif previous_complete:
            statuses[milestone.id] = "CURRENT"
        else:
            statuses[milestone.id] = "LOCKED"
        previous_complete = complete
    return statuses, completed_milestones


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


@router.get(
    "/{participant_id}/milestones/{milestone_id}/resources/{resource_id}",
    response_model=ResourceCompletionStatusResponse,
)
def get_milestone_resource_completion(
    participant_id: str,
    milestone_id: str,
    resource_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceCompletionStatusResponse:
    participant = get_active_participant(participant_id, db, current_user)
    challenge = db.get(Challenge, participant.challenge_id)
    milestone = db.get(ChallengeMilestone, milestone_id)
    if challenge is None or milestone is None or milestone.challenge_id != challenge.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found.")

    attachment = next(
        (resource for resource in milestone.resources or [] if resource.get("resource_id") == resource_id),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource is not attached to this milestone.")
    if db.scalar(
        select(ChallengeResource.id).where(
            ChallengeResource.id == resource_id,
            ChallengeResource.challenge_id == challenge.id,
        )
    ) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found for this challenge.")

    completion = db.scalar(
        select(ParticipantResourceCompletion).where(
            ParticipantResourceCompletion.participant_id == participant.id,
            ParticipantResourceCompletion.milestone_id == milestone.id,
            ParticipantResourceCompletion.resource_id == resource_id,
        )
    )
    milestones = list(db.scalars(
        select(ChallengeMilestone)
        .where(ChallengeMilestone.challenge_id == challenge.id)
        .order_by(ChallengeMilestone.order_index)
    ).all())
    completions = list(db.scalars(
        select(ParticipantResourceCompletion).where(
            ParticipantResourceCompletion.participant_id == participant.id
        )
    ).all())
    statuses, completed_milestones = milestone_progress(participant, milestones, completions)
    ready_for_proof = bool(milestones) and len(completed_milestones) == len(milestones)
    return ResourceCompletionStatusResponse(
        resource_id=resource_id,
        milestone_id=milestone.id,
        completed=completion is not None,
        completed_at=completion.completed_at if completion else None,
        resource_minutes=completion.resource_minutes if completion else None,
        milestone_minutes=completion.milestone_minutes if completion else None,
        note=completion.note if completion else None,
        milestone_status=statuses[milestone.id],
        milestone_completed=milestone.id in completed_milestones,
        challenge_status="READY_FOR_PROOF" if ready_for_proof else "IN_PROGRESS",
        ready_for_proof=ready_for_proof,
    )


@router.post(
    "/{participant_id}/milestones/{milestone_id}/resources/{resource_id}/complete",
    response_model=ResourceCompletionResponse,
)
def complete_milestone_resource(
    participant_id: str,
    milestone_id: str,
    resource_id: str,
    payload: ResourceCompletionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceCompletionResponse:
    participant = get_active_participant(participant_id, db, current_user)
    challenge = db.get(Challenge, participant.challenge_id)
    milestone = db.get(ChallengeMilestone, milestone_id)
    if challenge is None or milestone is None or milestone.challenge_id != challenge.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found.")

    attachment = next(
        (resource for resource in milestone.resources or [] if resource.get("resource_id") == resource_id),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource is not attached to this milestone.")
    challenge_resource_ids = {
        resource.id for resource in db.scalars(
            select(ChallengeResource).where(ChallengeResource.challenge_id == challenge.id)
        ).all()
    }
    if resource_id not in challenge_resource_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found for this challenge.")

    completion = db.scalar(
        select(ParticipantResourceCompletion).where(
            ParticipantResourceCompletion.participant_id == participant.id,
            ParticipantResourceCompletion.milestone_id == milestone.id,
            ParticipantResourceCompletion.resource_id == resource_id,
        )
    )
    is_new_completion = completion is None
    if completion is None:
        completion = ParticipantResourceCompletion(
            participant_id=participant.id,
            milestone_id=milestone.id,
            resource_id=resource_id,
            resource_minutes=payload.resource_minutes,
            milestone_minutes=payload.milestone_minutes,
            note=payload.note,
        )
        db.add(completion)
    elif payload.resource_minutes is not None or payload.milestone_minutes is not None or payload.note is not None:
        completion.resource_minutes = payload.resource_minutes
        completion.milestone_minutes = payload.milestone_minutes
        completion.note = payload.note

    if is_new_completion and payload.log_progress and (payload.resource_minutes or payload.milestone_minutes):
        record_progress(
            db,
            current_user,
            participant,
            minutes_spent=payload.milestone_minutes or payload.resource_minutes or 0,
            note=payload.note,
        )
    participant.last_activity_at = datetime.utcnow()
    db.flush()
    milestones = list(db.scalars(
        select(ChallengeMilestone)
        .where(ChallengeMilestone.challenge_id == challenge.id)
        .order_by(ChallengeMilestone.order_index)
    ).all())
    completions = list(db.scalars(
        select(ParticipantResourceCompletion).where(
            ParticipantResourceCompletion.participant_id == participant.id
        )
    ).all())
    statuses, completed_milestones = milestone_progress(participant, milestones, completions)
    ready_for_proof = len(completed_milestones) == len(milestones)
    participant.completion_status = "READY_FOR_PROOF" if ready_for_proof else "INCOMPLETE"
    db.commit()
    db.refresh(completion)
    return ResourceCompletionResponse(
        resource_id=resource_id,
        milestone_id=milestone.id,
        completed=True,
        completed_at=completion.completed_at,
        resource_minutes=completion.resource_minutes,
        milestone_minutes=completion.milestone_minutes,
        note=completion.note,
        milestone_status=statuses[milestone.id],
        milestone_completed=milestone.id in completed_milestones,
        challenge_status="READY_FOR_PROOF" if ready_for_proof else "IN_PROGRESS",
        ready_for_proof=ready_for_proof,
    )


@router.post(
    "/{participant_id}/metric-attempts",
    response_model=MetricAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_metric_attempt(
    participant_id: str,
    payload: MetricAttemptCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MetricAttempt:
    participant = get_active_participant(participant_id, db, current_user)
    challenge = db.get(Challenge, participant.challenge_id)
    definitions = {
        metric["key"]: metric for metric in (challenge.metrics or [])
    } if challenge else {}
    definition = definitions.get(payload.metric_key)
    if definition is None:
        raise HTTPException(status_code=422, detail="Unknown challenge metric.")
    if definition.get("unit") != payload.unit:
        raise HTTPException(status_code=422, detail="Metric unit does not match the challenge definition.")
    if isinstance(payload.value, float) and not math.isfinite(payload.value):
        raise HTTPException(status_code=422, detail="Metric value must be finite.")
    if isinstance(payload.value, bool) and definition.get("kind") != "BOOLEAN":
        raise HTTPException(status_code=422, detail="Metric value must be numeric.")
    if isinstance(payload.value, (int, float)) and payload.value < 0:
        raise HTTPException(status_code=422, detail="Metric value cannot be negative.")
    target = definition.get("target")
    direction = definition.get("direction", "AT_LEAST")
    meets_target = False
    if target is not None:
        if direction == "AT_MOST":
            meets_target = payload.value <= target
        elif direction == "EXACTLY":
            meets_target = payload.value == target
        elif direction == "BOOLEAN":
            meets_target = payload.value is target
        else:
            meets_target = payload.value >= target
    attempt = MetricAttempt(
        participant_id=participant.id,
        metric_key=payload.metric_key,
        value=payload.value,
        unit=payload.unit,
        note=payload.note,
        meets_target=meets_target,
    )
    db.add(attempt)
    participant.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(attempt)
    return attempt


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