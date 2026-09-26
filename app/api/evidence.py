from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.evidence import EvidenceItem, EvidenceSubmission
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.progress import ChallengeProgressLog
from app.models.skill import UserSkill
from app.models.user import User
from app.schemas.evidence import EvidenceCreate, EvidenceResponse
from app.services.blob_storage import upload_blob


router = APIRouter(prefix="/api/v1/evidence", tags=["Evidence"])


def get_owned_participation(
    participant_id: str, db: Session, current_user: User
) -> ChallengeParticipant:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.id == participant_id,
            ChallengeParticipant.user_id == current_user.id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")
    return participant


@router.post(
    "/participation/{participant_id}",
    response_model=EvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_evidence(
    participant_id: str,
    explanation: str | None = Form(default=None, max_length=5000),
    text_content: str | None = Form(default=None, max_length=20000),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceSubmission:
    participant = get_owned_participation(participant_id, db, current_user)
    if participant.completion_status != "READY_FOR_PROOF":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete all required milestones before submitting evidence.",
        )

    if not text_content and file is None:
        raise HTTPException(status_code=422, detail="Provide text content or an uploaded file as evidence.")
    file_url = await upload_blob(file, f"users/{current_user.id}/evidence") if file else None
    evidence_type = "TEXT" if text_content else "FILE"
    submission = EvidenceSubmission(
        challenge_id=participant.challenge_id,
        participant_id=participant.id,
        user_id=current_user.id,
        explanation=explanation,
    )
    db.add(submission)
    db.flush()
    db.add(
        EvidenceItem(
            submission_id=submission.id,
            evidence_type=evidence_type,
            text_content=text_content,
            external_url=file_url,
        )
    )
    participant.status = "SUBMITTED"
    participant.verification_status = "PENDING"
    participant.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(submission)
    return submission


@router.post("/{submission_id}/self-verify", response_model=EvidenceResponse)
def self_verify_evidence(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceSubmission:
    submission = db.scalar(
        select(EvidenceSubmission).where(
            EvidenceSubmission.id == submission_id,
            EvidenceSubmission.user_id == current_user.id,
        )
    )
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission not found.")
    if submission.status != "PENDING":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evidence is no longer pending.")

    participant = db.get(ChallengeParticipant, submission.participant_id)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participation not found.")
    challenge = db.get(Challenge, submission.challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")

    requirements = challenge.requirements or []
    if not requirements and challenge.target_value is not None:
        requirements = [{
            "metric_key": "value", "operator": "AT_LEAST",
            "value": challenge.target_value,
        }]
    logs = list(db.scalars(
        select(ChallengeProgressLog)
        .where(ChallengeProgressLog.participant_id == participant.id)
        .order_by(ChallengeProgressLog.created_at.desc())
    ).all())
    def requirement_met(requirement: dict, metrics: dict) -> bool:
        if requirement["metric_key"] not in metrics:
            return False
        actual = metrics[requirement["metric_key"]]
        expected = requirement["value"]
        operator = requirement["operator"]
        if operator == "AT_LEAST":
            return actual >= expected
        if operator == "AT_MOST":
            return actual <= expected
        if operator == "EXACTLY":
            return actual == expected
        return actual is True if expected is True else actual is False

    qualifying_runs = [
        log for log in logs
        if all(
            requirement_met(
                requirement,
                log.metrics or ({"value": log.value} if log.value is not None else {}),
            )
            for requirement in requirements
        )
    ] if requirements else logs
    if requirements and len(qualifying_runs) < challenge.required_runs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Progress does not satisfy the challenge requirements.",
        )

    submission.status = "VERIFIED"
    submission.reviewed_at = datetime.utcnow()
    participant.status = "COMPLETED"
    participant.completion_status = "COMPLETED"
    participant.verification_status = "VERIFIED"
    participant.completed_at = datetime.utcnow()
    participant.last_activity_at = datetime.utcnow()
    if db.scalar(
        select(UserSkill).where(
            UserSkill.user_id == current_user.id,
            UserSkill.source_challenge_id == challenge.id,
        )
    ) is None:
        db.add(
            UserSkill(
                user_id=current_user.id,
                source_challenge_id=challenge.id,
                skill_name=challenge.title,
                verification_status="VERIFIED",
            )
        )
    db.commit()
    db.refresh(submission)
    return submission