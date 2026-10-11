import asyncio
from io import BytesIO
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.evidence import (
    get_evidence_submission_status,
    get_owned_participation,
    self_verify_evidence,
    submit_evidence,
)
from app.models.base import Base
from app.models.challenge import Challenge
from app.models.evidence import EvidenceSubmission
from app.models.participation import ChallengeParticipant
from app.models.skill import UserSkill
from app.models.user import User
from app.services.verification_service import challenge_verification, verification_state


class EmptyRows:
    def all(self):
        return []


class FakeDb:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []

    def scalar(self, statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def get(self, model, identifier):
        return self.challenge

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = "submission-id"
            if hasattr(value, "submitted_at") and value.submitted_at is None:
                value.submitted_at = datetime.utcnow()

    def commit(self):
        return None

    def refresh(self, value):
        return None

    def scalars(self, statement):
        return EmptyRows()


def challenge(kind, config=None):
    return SimpleNamespace(
        verification_type=kind,
        required_runs=1,
        verification_instructions="Follow the challenge instructions.",
        requirements=[],
        verification_config=config,
    )


def user(identifier="user-1"):
    return SimpleNamespace(id=identifier, is_admin=False)


def video_config():
    return {
        "kind": "VIDEO_UPLOAD",
        "evidence": {
            "allowed_types": ["VIDEO"],
            "requires_file": True,
            "requires_explanation": True,
            "max_file_size_bytes": 50,
            "max_duration_seconds": 120,
            "allowed_mime_types": ["video/mp4"],
        },
        "completion": {"mode": "REVIEW", "requires_review": True},
    }


def upload(name="proof.mp4", content_type="video/mp4", content=b"video"):
    return UploadFile(
        BytesIO(content),
        filename=name,
        headers=Headers({"content-type": content_type}),
    )


def participant():
    return SimpleNamespace(
        id="participant-1",
        challenge_id="challenge-1",
        user_id="user-1",
        status="IN_PROGRESS",
        completion_status="INCOMPLETE",
        verification_status="NOT_SUBMITTED",
        last_activity_at=None,
        completed_at=None,
    )


def configure_submission_dependencies(monkeypatch, challenge_value, db):
    import app.api.evidence as evidence_api

    participant_value = participant()
    monkeypatch.setattr(evidence_api, "get_owned_participation", lambda *args: participant_value)
    monkeypatch.setattr(evidence_api, "private_blob_url", lambda *args: "https://private.test/video")
    db.challenge = challenge_value
    return evidence_api, participant_value


def test_detail_verification_payloads_have_canonical_kinds():
    db = FakeDb()
    self_reported = challenge("SELF_REPORTED")
    video = challenge("VIDEO_UPLOAD", video_config())
    external = challenge(
        "EXTERNAL_ACCOUNT",
        {
            "kind": "EXTERNAL_ACCOUNT",
            "provider": {"id": "chess_com", "name": "Chess.com"},
        },
    )

    assert challenge_verification(self_reported, db).kind == "SELF_REPORTED"
    video_response = challenge_verification(video, db)
    assert video_response.kind == "VIDEO_UPLOAD"
    assert video_response.evidence.requires_file is True
    assert video_response.completion.mode == "REVIEW"
    external_response = challenge_verification(external, db)
    assert external_response.kind == "EXTERNAL_ACCOUNT"
    assert external_response.provider.connected is False


def test_missing_video_file_returns_422(monkeypatch):
    import app.api.evidence as evidence_api

    db = FakeDb()
    configure_submission_dependencies(monkeypatch, challenge("VIDEO_UPLOAD", video_config()), db)
    with pytest.raises(HTTPException) as error:
        asyncio.run(submit_evidence("participant-1", "proof", None, None, None, db, user()))
    assert error.value.status_code == 422


def test_invalid_video_mime_returns_422(monkeypatch):
    db = FakeDb()
    evidence_api, _ = configure_submission_dependencies(monkeypatch, challenge("VIDEO_UPLOAD", video_config()), db)
    with pytest.raises(HTTPException) as error:
        asyncio.run(submit_evidence("participant-1", "proof", None, None, upload(content_type="image/png"), db, user()))
    assert error.value.status_code == 422


def test_oversized_video_returns_422(monkeypatch):
    db = FakeDb()
    evidence_api, _ = configure_submission_dependencies(monkeypatch, challenge("VIDEO_UPLOAD", video_config()), db)
    file = upload(content=b"x" * 51)
    with pytest.raises(HTTPException) as error:
        asyncio.run(submit_evidence("participant-1", "proof", None, None, file, db, user()))
    assert error.value.status_code == 422


def test_unconnected_external_account_returns_409(monkeypatch):
    db = FakeDb()
    configure_submission_dependencies(
        monkeypatch,
        challenge("EXTERNAL_ACCOUNT", {"kind": "EXTERNAL_ACCOUNT", "provider": {"id": "chess_com", "name": "Chess.com"}}),
        db,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(submit_evidence("participant-1", None, None, None, None, db, user()))
    assert error.value.status_code == 409


def test_connected_external_account_submission_succeeds(monkeypatch):
    connection = SimpleNamespace(provider_user_id="seed-chess-user-1")
    db = FakeDb([None, connection])
    evidence_api, participant_value = configure_submission_dependencies(
        monkeypatch,
        challenge("EXTERNAL_ACCOUNT", {"kind": "EXTERNAL_ACCOUNT", "provider": {"id": "chess_com", "name": "Chess.com"}}),
        db,
    )
    result = asyncio.run(submit_evidence("participant-1", None, None, None, None, db, user()))
    assert result.verification_kind == "EXTERNAL_ACCOUNT"
    assert result.provider_id == "chess_com"
    assert result.status == "PENDING"
    assert participant_value.status == "SUBMITTED"
    assert participant_value.completion_status == "NOT_COMPLETED"
    assert participant_value.verification_status == "PENDING"


def test_self_reported_submission_still_accepts_text(monkeypatch):
    db = FakeDb()
    evidence_api, participant_value = configure_submission_dependencies(monkeypatch, challenge("SELF_REPORTED"), db)
    result = asyncio.run(submit_evidence("participant-1", "I completed it.", "I read for 30 minutes.", None, None, db, user()))
    assert result.verification_kind == "SELF_REPORTED"
    assert result.status == "PENDING"
    assert participant_value.status == "SUBMITTED"
    assert participant_value.completion_status == "NOT_COMPLETED"
    assert participant_value.verification_status == "PENDING"


def test_verification_state_exposes_submission_lifecycle():
    assert verification_state(None, FakeDb()).status == "NOT_STARTED"
    processing = SimpleNamespace(
        id="submission-1",
        status="PROCESSING",
        review_reason=None,
    )
    verified = SimpleNamespace(
        id="submission-2",
        status="VERIFIED",
        review_reason=None,
    )
    processing_state = verification_state("participant-1", FakeDb([processing]))
    verified_state = verification_state("participant-1", FakeDb([verified]))
    assert processing_state.status == "PROCESSING"
    assert processing_state.submission_id == "submission-1"
    assert verified_state.status == "VERIFIED"
    assert verified_state.submission_id == "submission-2"


def test_participation_is_owned_by_uploader():
    db = FakeDb()
    with pytest.raises(HTTPException) as error:
        get_owned_participation("other-participant", db, user())
    assert error.value.status_code == 404


def test_evidence_submission_status_checks_user_and_challenge():
    submission = SimpleNamespace(id="submission-1")
    assert get_evidence_submission_status(
        "challenge-1", FakeDb([submission]), user()
    ).has_submitted is True
    assert get_evidence_submission_status(
        "challenge-1", FakeDb(), user()
    ).has_submitted is False


def test_self_verification_completes_participation_and_awards_skill_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    current_user = User(id="user-1", username="user-1")
    challenge_value = Challenge(
        id="challenge-1",
        title="Learn Python",
        slug="learn-python",
        image_url="https://example.com/image.png",
        short_description="Learn Python.",
        full_description="Learn Python.",
        creator_id="creator",
        requirements=[],
        required_runs=1,
    )
    participant_value = ChallengeParticipant(
        id="participant-1",
        challenge_id=challenge_value.id,
        user_id=current_user.id,
        status="SUBMITTED",
        completion_status="NOT_COMPLETED",
        verification_status="PENDING",
    )
    submission = EvidenceSubmission(
        id="submission-1",
        challenge_id=challenge_value.id,
        participant_id=participant_value.id,
        user_id=current_user.id,
        status="PENDING",
    )
    db.add_all([current_user, challenge_value, participant_value, submission])
    db.commit()

    result = self_verify_evidence(submission.id, db, current_user)

    assert result.status == "VERIFIED"
    assert participant_value.status == "COMPLETED"
    assert participant_value.completion_status == "COMPLETED"
    assert participant_value.verification_status == "VERIFIED"
    assert participant_value.completed_at is not None
    assert db.scalar(
        select(func.count()).select_from(UserSkill).where(
            UserSkill.user_id == current_user.id,
            UserSkill.source_challenge_id == challenge_value.id,
        )
    ) == 1
    with pytest.raises(HTTPException) as error:
        self_verify_evidence(submission.id, db, current_user)
    assert error.value.status_code == 409
