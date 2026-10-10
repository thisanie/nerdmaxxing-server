from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.challenge import Challenge
from app.models.evidence import EvidenceSubmission
from app.models.integration import ExternalAccountConnection
from app.models.user import User
from app.schemas.challenge import (
    ChallengeVerificationResponse,
    VerificationAccountResponse,
    VerificationCompletionResponse,
    VerificationEvidenceResponse,
    VerificationProviderResponse,
    VerificationRequirementResponse,
    VerificationStateResponse,
)


VERIFICATION_KINDS = {"SELF_REPORTED", "VIDEO_UPLOAD", "EXTERNAL_ACCOUNT"}


def verification_kind(challenge: Challenge) -> str:
    configured = (challenge.verification_type or "SELF_REPORTED").upper()
    aliases = {
        "VIDEO_VERIFIED": "VIDEO_UPLOAD",
        "ACCOUNT_VERIFIED": "EXTERNAL_ACCOUNT",
    }
    return aliases.get(configured, configured if configured in VERIFICATION_KINDS else "SELF_REPORTED")


def verification_config(challenge: Challenge) -> dict:
    config = dict(challenge.verification_config or {})
    kind = verification_kind(challenge)
    config["kind"] = kind
    config.setdefault("instructions", challenge.verification_instructions or "")
    config.setdefault("required_runs", challenge.required_runs or 1)
    config.setdefault("requirements", challenge.requirements or [])

    if kind == "VIDEO_UPLOAD":
        config.setdefault(
            "evidence",
            {
                "allowed_types": ["VIDEO"],
                "requires_file": True,
                "requires_explanation": True,
                "max_file_size_bytes": 50 * 1024 * 1024,
                "max_duration_seconds": 120,
                "allowed_mime_types": ["video/mp4", "video/quicktime"],
            },
        )
        config.setdefault("completion", {"mode": "REVIEW", "requires_review": True})
    elif kind == "EXTERNAL_ACCOUNT":
        config.setdefault(
            "provider",
            {
                "id": "chess_com",
                "name": "Chess.com",
                "connect_url": "/api/v1/integrations/chess_com/start",
            },
        )
        config.setdefault(
            "evidence",
            {
                "allowed_types": ["ACCOUNT_CONNECTION"],
                "requires_file": False,
                "requires_explanation": False,
                "max_file_size_bytes": None,
                "max_duration_seconds": None,
                "allowed_mime_types": [],
            },
        )
        config.setdefault("completion", {"mode": "AUTOMATIC", "requires_review": False})
    else:
        config.setdefault(
            "evidence",
            {
                "allowed_types": ["TEXT", "FILE"],
                "requires_file": False,
                "requires_explanation": False,
                "max_file_size_bytes": None,
                "max_duration_seconds": None,
                "allowed_mime_types": [],
            },
        )
        config.setdefault("completion", {"mode": "SELF_CONFIRMATION", "requires_review": False})
    return config


def _requirements(challenge: Challenge, config: dict) -> list[VerificationRequirementResponse]:
    requirements = config.get("requirements") or []
    result = []
    for requirement in requirements:
        key = requirement.get("key") or requirement.get("metric_key")
        if not key:
            continue
        result.append(
            VerificationRequirementResponse(
                key=key,
                label=requirement.get("label") or key.replace("_", " ").title(),
                operator=requirement.get("operator", "AT_LEAST"),
                value=requirement.get("value", 0),
                unit=requirement.get("unit"),
            )
        )
    return result


def challenge_verification(
    challenge: Challenge,
    db: Session,
    current_user: User | None = None,
) -> ChallengeVerificationResponse:
    config = verification_config(challenge)
    kind = config["kind"]
    provider = None
    provider_config = config.get("provider")
    if kind == "EXTERNAL_ACCOUNT" and provider_config:
        connection = None
        if current_user:
            connection = db.scalar(
                select(ExternalAccountConnection).where(
                    ExternalAccountConnection.user_id == current_user.id,
                    ExternalAccountConnection.provider_id == provider_config["id"],
                )
            )
        account = (
            VerificationAccountResponse(
                provider_user_id=connection.provider_user_id,
                username=connection.username,
                avatar_url=connection.avatar_url,
                verified_at=connection.verified_at,
            )
            if connection
            else None
        )
        provider = VerificationProviderResponse(
            id=provider_config["id"],
            name=provider_config["name"],
            connect_url=provider_config.get(
                "connect_url",
                f"/api/v1/integrations/{provider_config['id']}/connect",
            ),
            connected=connection is not None,
            account=account,
        )
    evidence = config["evidence"]
    completion = config["completion"]
    return ChallengeVerificationResponse(
        type=challenge.verification_type,
        kind=kind,
        instructions=config.get("instructions", ""),
        required_runs=config.get("required_runs", 1),
        provider=provider,
        requirements=_requirements(challenge, config),
        evidence=VerificationEvidenceResponse(**evidence),
        completion=VerificationCompletionResponse(**completion),
    )


def verification_state(
    participant_id: str | None,
    db: Session,
) -> VerificationStateResponse:
    if participant_id is None:
        return VerificationStateResponse(status="NOT_STARTED", can_retry=True)
    submission = db.scalar(
        select(EvidenceSubmission)
        .where(EvidenceSubmission.participant_id == participant_id)
        .order_by(EvidenceSubmission.submitted_at.desc())
    )
    if submission is None:
        return VerificationStateResponse(status="NOT_STARTED", can_retry=True)
    status = submission.status if submission.status in {
        "PENDING", "PROCESSING", "VERIFIED", "REJECTED"
    } else "PENDING"
    return VerificationStateResponse(
        status=status,
        submission_id=submission.id,
        rejection_reason=submission.review_reason,
        can_retry=status == "REJECTED",
    )
