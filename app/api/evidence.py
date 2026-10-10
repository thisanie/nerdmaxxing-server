import mimetypes
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.evidence import EvidenceItem, EvidenceSubmission
from app.models.challenge import Challenge
from app.models.integration import ExternalAccountConnection
from app.models.participation import ChallengeParticipant
from app.models.progress import ChallengeProgressLog
from app.models.skill import UserSkill
from app.models.user import User
from app.models.activity import Activity
from app.schemas.evidence import EvidenceItemResponse, EvidenceResponse
from app.services.blob_storage import (
    MAX_VIDEO_UPLOAD_BYTES,
    VIDEO_CONTENT_TYPES,
    delete_blob,
    private_blob_url,
    upload_blob,
)
from app.services.verification_service import verification_config, verification_kind
from app.services.chess_com import ChessComError, fetch_rapid_rating


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


def get_submission(submission_id: str, db: Session, current_user: User) -> EvidenceSubmission:
    submission = db.get(EvidenceSubmission, submission_id)
    if submission is None or (
        submission.user_id != current_user.id and not current_user.is_admin
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence submission not found.",
        )
    return submission


def evidence_response(
    submission: EvidenceSubmission,
    db: Session,
) -> EvidenceResponse:
    items = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.submission_id == submission.id)
            .order_by(EvidenceItem.created_at.asc())
        ).all()
    )
    item = items[0] if items else None
    file_url = None
    if item:
        file_url = (
            private_blob_url(item.storage_key, settings.r2_evidence_bucket)
            if item.storage_key
            else item.external_url
        )
    return EvidenceResponse(
        id=submission.id,
        challenge_id=submission.challenge_id,
        participant_id=submission.participant_id,
        user_id=submission.user_id,
        status=submission.status,
        explanation=submission.explanation,
        submitted_at=submission.submitted_at,
        reviewed_at=submission.reviewed_at,
        items=[
            EvidenceItemResponse(
                id=item.id,
                evidence_type=item.evidence_type,
                content_type=item.content_type,
                file_size=item.file_size,
                video_url=(
                    private_blob_url(item.storage_key, settings.r2_evidence_bucket)
                    if item.storage_key
                    else None
                ),
            )
            for item in items
        ],
        verification_kind=submission.verification_kind,
        provider_id=submission.provider_id,
        provider_metric=submission.provider_metric,
        provider_value=submission.provider_value,
        provider_observed_at=submission.provider_observed_at,
        file_url=file_url,
        file_name=item.file_name if item else None,
        mime_type=item.content_type if item else None,
        review_reason=submission.review_reason,
    )


async def _file_size(file: UploadFile, maximum: int) -> int:
    if file.size is not None:
        return file.size
    content = await file.read(maximum + 1)
    await file.seek(0)
    return len(content)


async def _video_duration_seconds(file: UploadFile) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    content = await file.read()
    await file.seek(0)
    try:
        with tempfile.NamedTemporaryFile(suffix=".video") as temporary:
            temporary.write(content)
            temporary.flush()
            result = subprocess.run(
                [
                    ffprobe,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    temporary.name,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


@router.post(
    "/participation/{participant_id}",
    response_model=EvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_evidence(
    participant_id: str,
    explanation: str | None = Form(default=None, max_length=5000),
    text_content: str | None = Form(default=None, max_length=20000),
    external_url: str | None = Form(default=None, max_length=2048),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceSubmission:
    participant = get_owned_participation(participant_id, db, current_user)
    challenge = db.get(Challenge, participant.challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")
    kind = verification_kind(challenge)
    config = verification_config(challenge)
    if participant.status not in {"ACCEPTED", "IN_PROGRESS", "PAUSED"} and not (
        kind == "SELF_REPORTED" and participant.completion_status == "READY_FOR_PROOF"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence can only be submitted for an active challenge attempt.",
        )
    if db.scalar(
        select(EvidenceSubmission.id).where(
            EvidenceSubmission.participant_id == participant.id,
            EvidenceSubmission.status.in_(("PENDING", "PROCESSING", "VERIFIED")),
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evidence was already submitted.")

    evidence_config = config["evidence"]
    requires_file = evidence_config.get("requires_file", False)
    requires_explanation = evidence_config.get("requires_explanation", False)
    if requires_file and file is None:
        raise HTTPException(status_code=422, detail="A video file is required for this challenge.")
    if requires_explanation and not explanation:
        raise HTTPException(status_code=422, detail="An explanation is required for this challenge.")
    if kind == "SELF_REPORTED" and not (explanation or text_content or external_url or file):
        raise HTTPException(status_code=422, detail="Provide evidence or an explanation.")

    connection = None
    provider_id = None
    provider_account_id = None
    provider_metric = None
    provider_value = None
    provider_observed_at = None
    if kind == "EXTERNAL_ACCOUNT":
        provider_id = config["provider"]["id"]
        connection = db.scalar(
            select(ExternalAccountConnection).where(
                ExternalAccountConnection.user_id == current_user.id,
                ExternalAccountConnection.provider_id == provider_id,
            )
        )
        if connection is None:
            raise HTTPException(status_code=409, detail="Connect the required external account first.")
        provider_account_id = connection.provider_user_id
        requirements = config.get("requirements") or challenge.requirements or []
        requires_rapid_rating = any(
            requirement.get("metric_key", requirement.get("key")) == "rapid_rating"
            for requirement in requirements
        )
        if requires_rapid_rating:
            now = datetime.utcnow()
            cache_valid = (
                connection.rapid_rating is not None
                and connection.rapid_rating_observed_at is not None
                and connection.refreshed_at is not None
                and connection.refreshed_at >= now - timedelta(
                    minutes=settings.chess_com_rating_cache_minutes
                )
            )
            if cache_valid:
                provider_value = connection.rapid_rating
                provider_observed_at = connection.rapid_rating_observed_at
            else:
                try:
                    rating = fetch_rapid_rating(connection.username)
                except ChessComError as error:
                    raise HTTPException(
                        status_code=502,
                        detail="Chess.com rapid rating could not be verified.",
                    ) from error
                provider_value = rating.value
                provider_observed_at = rating.observed_at
                connection.rapid_rating = rating.value
                connection.rapid_rating_observed_at = rating.observed_at
                connection.refreshed_at = now
            provider_metric = "rapid_rating"
            for requirement in requirements:
                if requirement.get("metric_key", requirement.get("key")) != "rapid_rating":
                    continue
                expected = requirement.get("value")
                operator = requirement.get("operator", "AT_LEAST")
                passes = (
                    provider_value >= expected if operator == "AT_LEAST"
                    else provider_value <= expected if operator == "AT_MOST"
                    else provider_value == expected if operator == "EXACTLY"
                    else False
                )
                if not passes:
                    raise HTTPException(
                        status_code=409,
                        detail="The verified Chess.com rating does not satisfy this challenge.",
                    )

    storage_key = None
    file_size = None
    normalized_content_type = None
    file_name = None
    if file is not None:
        file_name = file.filename
        normalized_content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
        allowed_mime_types = set(evidence_config.get("allowed_mime_types") or [])
        allowed_types = allowed_mime_types or (VIDEO_CONTENT_TYPES if kind == "VIDEO_UPLOAD" else None)
        if allowed_types and normalized_content_type not in allowed_types:
            raise HTTPException(status_code=422, detail="The uploaded file type is not allowed for this challenge.")
        maximum = evidence_config.get("max_file_size_bytes") or MAX_VIDEO_UPLOAD_BYTES
        file_size = await _file_size(file, maximum)
        if file_size > maximum:
            raise HTTPException(status_code=422, detail="The uploaded file exceeds the challenge size limit.")
        if kind == "VIDEO_UPLOAD":
            duration = await _video_duration_seconds(file)
            max_duration = evidence_config.get("max_duration_seconds")
            if duration is not None and max_duration is not None and duration > max_duration:
                raise HTTPException(status_code=422, detail="The uploaded video exceeds the duration limit.")
        if kind == "VIDEO_UPLOAD":
            storage_key = await upload_blob(
                file,
                f"challenges/{challenge.id}/users/{current_user.id}",
                bucket=settings.r2_evidence_bucket,
                public_url="",
                return_key=True,
                max_upload_bytes=maximum,
                allowed_content_types=allowed_types or VIDEO_CONTENT_TYPES,
            )
        else:
            storage_key = await upload_blob(
                file,
                f"challenges/{challenge.id}/users/{current_user.id}",
                bucket=settings.r2_evidence_bucket,
                public_url="",
                return_key=True,
            )

    submission_status = "PROCESSING" if kind == "VIDEO_UPLOAD" else (
        "VERIFIED" if kind == "EXTERNAL_ACCOUNT" else "PENDING"
    )
    submission = EvidenceSubmission(
        challenge_id=participant.challenge_id,
        participant_id=participant.id,
        user_id=current_user.id,
        explanation=explanation,
        verification_kind=kind,
        provider_id=provider_id,
        provider_account_id=provider_account_id,
        provider_metric=provider_metric,
        provider_value=provider_value,
        provider_observed_at=provider_observed_at,
        status=submission_status,
    )
    db.add(submission)
    db.flush()
    db.add(
        EvidenceItem(
            submission_id=submission.id,
            evidence_type="VIDEO" if kind == "VIDEO_UPLOAD" else (
                "ACCOUNT_CONNECTION" if kind == "EXTERNAL_ACCOUNT" else "TEXT"
            ),
            text_content=text_content,
            external_url=external_url,
            storage_key=storage_key,
            file_name=file_name,
            content_type=normalized_content_type,
            file_size=file_size,
        )
    )
    participant.status = "SUBMITTED"
    participant.verification_status = "VERIFIED" if submission_status == "VERIFIED" else "PENDING"
    if submission_status == "VERIFIED":
        participant.status = "COMPLETED"
        participant.completion_status = "COMPLETED"
        participant.completed_at = datetime.utcnow()
        participant.verification_status = "VERIFIED"
    participant.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(submission)
    return evidence_response(submission, db)


@router.get("/{submission_id}", response_model=EvidenceResponse)
def get_evidence(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceResponse:
    submission = get_submission(submission_id, db, current_user)
    return evidence_response(submission, db)


@router.delete("/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    submission = get_submission(submission_id, db, current_user)
    items = list(db.scalars(select(EvidenceItem).where(EvidenceItem.submission_id == submission.id)).all())
    for item in items:
        if item.storage_key:
            delete_blob(item.storage_key, settings.r2_evidence_bucket)
        db.delete(item)
    participant = db.get(ChallengeParticipant, submission.participant_id)
    if participant and participant.status == "SUBMITTED":
        participant.status = "IN_PROGRESS"
        participant.verification_status = "NOT_SUBMITTED"
    db.delete(submission)
    db.commit()


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
    db.add(Activity(user_id=current_user.id, action="COMPLETED_CHALLENGE", challenge_id=challenge.id))
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
    return evidence_response(submission, db)