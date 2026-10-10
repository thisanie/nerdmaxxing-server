import asyncio
from io import BytesIO
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.api.evidence import get_owned_participation, submit_evidence
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

    monkeypatch.setattr(evidence_api, "get_owned_participation", lambda *args: participant())
    monkeypatch.setattr(evidence_api, "private_blob_url", lambda *args: "https://private.test/video")
    db.challenge = challenge_value
    return evidence_api


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
    evidence_api = configure_submission_dependencies(monkeypatch, challenge("VIDEO_UPLOAD", video_config()), db)
    with pytest.raises(HTTPException) as error:
        asyncio.run(submit_evidence("participant-1", "proof", None, None, upload(content_type="image/png"), db, user()))
    assert error.value.status_code == 422


def test_oversized_video_returns_422(monkeypatch):
    db = FakeDb()
    evidence_api = configure_submission_dependencies(monkeypatch, challenge("VIDEO_UPLOAD", video_config()), db)
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
    configure_submission_dependencies(
        monkeypatch,
        challenge("EXTERNAL_ACCOUNT", {"kind": "EXTERNAL_ACCOUNT", "provider": {"id": "chess_com", "name": "Chess.com"}}),
        db,
    )
    result = asyncio.run(submit_evidence("participant-1", None, None, None, None, db, user()))
    assert result.verification_kind == "EXTERNAL_ACCOUNT"
    assert result.provider_id == "chess_com"
    assert result.status == "VERIFIED"


def test_self_reported_submission_still_accepts_text(monkeypatch):
    db = FakeDb()
    configure_submission_dependencies(monkeypatch, challenge("SELF_REPORTED"), db)
    result = asyncio.run(submit_evidence("participant-1", "I completed it.", "I read for 30 minutes.", None, None, db, user()))
    assert result.verification_kind == "SELF_REPORTED"
    assert result.status == "PENDING"


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
    assert verification_state("participant-1", FakeDb([processing])).status == "PROCESSING"
    assert verification_state("participant-1", FakeDb([verified])).status == "VERIFIED"


def test_participation_is_owned_by_uploader():
    db = FakeDb()
    with pytest.raises(HTTPException) as error:
        get_owned_participation("other-participant", db, user())
    assert error.value.status_code == 404
