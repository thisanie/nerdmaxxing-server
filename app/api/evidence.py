import mimetypes
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.evidence import EvidenceItem, EvidenceSubmission
from app.models.challenge import Challenge
from app.models.integration import ExternalAccountConnection
from app.models.participation import ChallengeParticipant
from app.models.aura import AuraTransaction
from app.models.skill import UserSkill
from app.models.user import User
from app.models.activity import Activity
from app.schemas.evidence import (
    EvidenceApprovalStatusResponse,
    EvidenceCreatedResponse,
    EvidenceItemResponse,
    EvidenceResponse,
    EvidenceSubmissionStatusResponse,
    EvidenceVerificationRequest,
)
from app.services.blob_storage import (
    MAX_VIDEO_UPLOAD_BYTES,
    VIDEO_CONTENT_TYPES,
    blob_metadata,
    create_upload_url,
    delete_blob,
    private_blob_url,
    upload_blob,
)
from app.services.verification_service import verification_config, verification_kind
from app.services.chess_com import ChessComError, fetch_rapid_rating
from app.services.aura_service import award_aura, calculate_aura


router = APIRouter(prefix="/api/v1/evidence", tags=["Evidence"])


def _video_upload_details(challenge: Challenge) -> tuple[int, set[str]]:
    config = verification_config(challenge)["evidence"]
    maximum = config.get("max_file_size_bytes") or MAX_VIDEO_UPLOAD_BYTES
    allowed_types = set(config.get("allowed_mime_types") or VIDEO_CONTENT_TYPES)
    return maximum, allowed_types


@router.post("/participation/{participant_id}/upload-url")
def create_evidence_upload_url(
    participant_id: str,
    file_name: str,
    content_type: str,
    file_size: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str | int]:
    participant = get_owned_participation(participant_id, db, current_user)
    challenge = db.get(Challenge, participant.challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")
    if verification_kind(challenge) != "VIDEO_UPLOAD":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This challenge does not accept video uploads.")
    if participant.status not in {"ACCEPTED", "IN_PROGRESS", "PAUSED"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evidence can only be submitted for an active challenge attempt.")
    if db.scalar(
        select(EvidenceSubmission.id).where(
            EvidenceSubmission.participant_id == participant.id,
            EvidenceSubmission.status.in_(("PENDING", "PROCESSING", "VERIFIED")),
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evidence was already submitted.")
    maximum, allowed_types = _video_upload_details(challenge)
    if file_size <= 0 or file_size > maximum:
        raise HTTPException(status_code=422, detail="The uploaded file exceeds the challenge size limit.")
    if content_type not in allowed_types:
        raise HTTPException(status_code=422, detail="The uploaded file type is not allowed for this challenge.")
    extension = PurePosixPath(file_name).suffix.lower()
    key = f"challenges/{challenge.id}/users/{current_user.id}/{uuid.uuid4().hex}{extension}"
    return {
        "upload_url": create_upload_url(
            key,
            settings.r2_evidence_bucket,
            content_type,
        ),
        "storage_key": key,
        "expires_in": 900,
        "max_file_size_bytes": maximum,
    }


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
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    submission = db.get(EvidenceSubmission, submission_id)
    if submission is None:
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
    response_model=EvidenceCreatedResponse,
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
    uploaded_key: str | None = Form(default=None),
    uploaded_file_name: str | None = Form(default=None),
    uploaded_content_type: str | None = Form(default=None),
    uploaded_file_size: int | None = Form(default=None),
) -> EvidenceCreatedResponse:
    if not isinstance(uploaded_key, str):
        uploaded_key = None
    if not isinstance(uploaded_file_name, str):
        uploaded_file_name = None
    if not isinstance(uploaded_content_type, str):
        uploaded_content_type = None
    if not isinstance(uploaded_file_size, int):
        uploaded_file_size = None
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
    if requires_file and file is None and uploaded_key is None:
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
    if uploaded_key is not None:
        expected_prefix = f"challenges/{challenge.id}/users/{current_user.id}/"
        if not uploaded_key.startswith(expected_prefix):
            raise HTTPException(status_code=422, detail="The uploaded file is invalid.")
        if kind != "VIDEO_UPLOAD" or not uploaded_file_name or not uploaded_content_type:
            raise HTTPException(status_code=422, detail="Video upload metadata is required.")
        maximum = evidence_config.get("max_file_size_bytes") or MAX_VIDEO_UPLOAD_BYTES
        allowed_mime_types = set(evidence_config.get("allowed_mime_types") or VIDEO_CONTENT_TYPES)
        if uploaded_content_type not in allowed_mime_types:
            raise HTTPException(status_code=422, detail="The uploaded file type is not allowed for this challenge.")
        actual_size, actual_content_type = blob_metadata(uploaded_key, settings.r2_evidence_bucket)
        if actual_size <= 0 or actual_size > maximum or (
            uploaded_file_size is not None and actual_size != uploaded_file_size
        ):
            raise HTTPException(status_code=422, detail="The uploaded file exceeds the challenge size limit.")
        if actual_content_type and actual_content_type != uploaded_content_type:
            raise HTTPException(status_code=422, detail="The uploaded file type is not allowed for this challenge.")
        storage_key = uploaded_key
        file_name = uploaded_file_name
        file_size = actual_size
        normalized_content_type = uploaded_content_type
    elif file is not None:
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

    submission_status = "PENDING"
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
    participant.completion_status = "NOT_COMPLETED"
    participant.verification_status = "PENDING"
    participant.completed_at = None
    participant.last_activity_at = datetime.utcnow()
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    db.refresh(submission)
    return EvidenceCreatedResponse(id=submission.id, status=submission.status)


@router.get(
    "/status/{challenge_id}",
    response_model=EvidenceSubmissionStatusResponse,
)
def get_evidence_submission_status(
    challenge_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceSubmissionStatusResponse:
    submission_exists = db.scalar(
        select(EvidenceSubmission.id)
        .join(
            ChallengeParticipant,
            ChallengeParticipant.id == EvidenceSubmission.participant_id,
        )
        .where(
            ChallengeParticipant.user_id == current_user.id,
            ChallengeParticipant.challenge_id == challenge_id,
            EvidenceSubmission.user_id == current_user.id,
            EvidenceSubmission.status.in_(("PENDING", "PROCESSING", "VERIFIED")),
        )
    ) is not None
    return EvidenceSubmissionStatusResponse(has_submitted=submission_exists)


@router.get("/{submission_id}", response_model=EvidenceResponse)
def get_evidence(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceResponse:
    submission = get_submission(submission_id, db, current_user)
    return evidence_response(submission, db)


@router.get(
    "/{submission_id}/is-approved",
    response_model=EvidenceApprovalStatusResponse,
)
def get_evidence_approval_status(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceApprovalStatusResponse:
    statement = select(EvidenceSubmission).where(EvidenceSubmission.id == submission_id)
    if not current_user.is_admin:
        statement = statement.where(EvidenceSubmission.user_id == current_user.id)
    submission = db.scalar(statement)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission not found.")
    return EvidenceApprovalStatusResponse(
        is_approved=submission.status == "VERIFIED",
        note=submission.review_reason,
    )


@router.post("/{submission_id}/verify", response_model=EvidenceResponse)
def verify_evidence(
    submission_id: str,
    request: EvidenceVerificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceResponse:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    submission = db.scalar(
        select(EvidenceSubmission)
        .where(EvidenceSubmission.id == submission_id)
        .with_for_update()
    )
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission not found.")
    if submission.status not in {"PENDING", "PROCESSING"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evidence is no longer awaiting review.")
    participant = db.get(ChallengeParticipant, submission.participant_id)
    challenge = db.get(Challenge, submission.challenge_id)
    if participant is None or challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission context not found.")

    submission.reviewed_at = datetime.utcnow()
    submission.review_reason = request.note
    if request.decision == "APPROVE":
        submission.status = "VERIFIED"
        participant.status = "COMPLETED"
        participant.completion_status = "COMPLETED"
        participant.verification_status = "VERIFIED"
        participant.completed_at = datetime.utcnow()
        participant.last_activity_at = datetime.utcnow()
        db.add(Activity(user_id=submission.user_id, action="COMPLETED_CHALLENGE", challenge_id=challenge.id))
        if db.scalar(
            select(UserSkill).where(
                UserSkill.user_id == submission.user_id,
                UserSkill.source_challenge_id == challenge.id,
            )
        ) is None:
            db.add(
                UserSkill(
                    user_id=submission.user_id,
                    source_challenge_id=challenge.id,
                    skill_name=challenge.title,
                    verification_status="VERIFIED",
                )
            )
        if db.scalar(
            select(AuraTransaction.id).where(
                AuraTransaction.reference_type == "EVIDENCE_SUBMISSION",
                AuraTransaction.reference_id == submission.id,
            )
        ) is None:
            recipient = db.get(User, submission.user_id)
            if recipient is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence owner not found.")
            amount = calculate_aura(challenge)
            award_aura(
                db,
                recipient,
                amount,
                "Approved challenge evidence",
                reference_type="EVIDENCE_SUBMISSION",
                reference_id=submission.id,
            )
            db.add(Activity(user_id=submission.user_id, action="EARNED_AURA", challenge_id=challenge.id))
    else:
        submission.status = "REJECTED"
        participant.status = "IN_PROGRESS"
        participant.completion_status = "INCOMPLETE"
        participant.verification_status = "REJECTED"
        participant.completed_at = None
        participant.last_activity_at = datetime.utcnow()

    db.commit()
    db.refresh(submission)
    return evidence_response(submission, db)


@router.delete("/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    submission = db.get(EvidenceSubmission, submission_id)
    if submission is None or (submission.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence submission not found.")
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