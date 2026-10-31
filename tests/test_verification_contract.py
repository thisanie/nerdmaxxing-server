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
    get_active_evidence_submissions,
    get_evidence,
    get_evidence_approval_status,
    get_evidence_submission_status,
    get_owned_participation,
    submit_evidence,
    verify_evidence,
)
from app.api.users import get_profile
from app.models.base import Base
from app.models.aura import AuraTransaction
from app.models.challenge import Challenge
from app.models.evidence import EvidenceSubmission
from app.models.follow import UserFollow
from app.models.invitation import Notification
from app.models.participation import ChallengeParticipant
from app.models.skill import UserSkill
from app.models.user import User
from app.schemas.evidence import EvidenceVerificationRequest
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


def admin(identifier="admin-1"):
    return SimpleNamespace(id=identifier, is_admin=True)


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
    assert result.id == "submission-id"
    assert result.status == "PENDING"
    assert participant_value.status == "SUBMITTED"
    assert participant_value.completion_status == "NOT_COMPLETED"
    assert participant_value.verification_status == "PENDING"


def test_self_reported_submission_still_accepts_text(monkeypatch):
    db = FakeDb()
    evidence_api, participant_value = configure_submission_dependencies(monkeypatch, challenge("SELF_REPORTED"), db)
    result = asyncio.run(submit_evidence("participant-1", "I completed it.", "I read for 30 minutes.", None, None, db, user()))
    assert result.id == "submission-id"
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


def create_review_database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    current_user = User(
        id="user-1",
        username="user-1",
        username_normalized="user-1",
    )
    reviewer = User(id="admin-1", username="admin-1", is_admin=True)
    challenge_value = Challenge(
        id="challenge-1",
        title="Learn Python",
        slug="learn-python",
        image_url="https://example.com/image.png",
        short_description="Learn Python.",
        full_description="Learn Python.",
        creator_id="creator",
        difficulty_level="MEDIUM",
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
    db.add_all([current_user, reviewer, challenge_value, participant_value, submission])
    db.commit()
    return db, current_user, reviewer, challenge_value, participant_value, submission


def test_admin_approval_completes_and_awards_aura_once(monkeypatch):
    db, current_user, reviewer, challenge_value, participant_value, submission = create_review_database()
    pushed_notifications = []
    monkeypatch.setattr(
        "app.api.evidence.send_notification_push",
        lambda db, notification: pushed_notifications.append(notification),
    )
    followers = [
        User(id="follower-1", username="follower-1"),
        User(id="follower-2", username="follower-2"),
    ]
    db.add_all(followers)
    db.add_all(
        UserFollow(follower_id=follower.id, following_id=current_user.id)
        for follower in followers
    )
    db.commit()
    with pytest.raises(HTTPException) as error:
        get_evidence(submission.id, db, current_user)
    assert error.value.status_code == 403

    result = verify_evidence(
        submission.id,
        EvidenceVerificationRequest(decision="APPROVE", note="Looks good."),
        db,
        reviewer,
    )

    assert result.status == "VERIFIED"
    assert result.review_reason == "Looks good."
    assert challenge_value.status == "PUBLISHED"
    assert challenge_value.visibility == "PUBLIC"
    assert challenge_value.published_at is not None
    assert participant_value.status == "COMPLETED"
    assert participant_value.completion_status == "COMPLETED"
    assert participant_value.verification_status == "VERIFIED"
    assert participant_value.completed_at is not None
    assert current_user.aura_points == 40
    assert db.scalar(
        select(func.count()).select_from(AuraTransaction).where(
            AuraTransaction.user_id == current_user.id,
            AuraTransaction.reference_type == "EVIDENCE_SUBMISSION",
            AuraTransaction.reference_id == submission.id,
        )
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(UserSkill).where(
            UserSkill.user_id == current_user.id,
            UserSkill.source_challenge_id == challenge_value.id,
        )
    ) == 1
    public_profile = get_profile("user-1", db)
    assert [item.id for item in public_profile.completed_challenges] == [challenge_value.id]
    notifications = list(db.scalars(select(Notification)).all())
    assert {(item.user_id, item.notification_type) for item in notifications} == {
        (current_user.id, "EVIDENCE_APPROVED"),
        ("follower-1", "FOLLOWING_CHALLENGE_COMPLETED"),
        ("follower-2", "FOLLOWING_CHALLENGE_COMPLETED"),
    }
    assert all(item.challenge_id == challenge_value.id for item in notifications)
    assert len(pushed_notifications) == 3
    assert get_evidence_approval_status(submission.id, db, current_user).is_approved is True
    with pytest.raises(HTTPException) as error:
        verify_evidence(
            submission.id,
            EvidenceVerificationRequest(decision="APPROVE"),
            db,
            reviewer,
        )
    assert error.value.status_code == 409


def test_rejection_note_is_visible_as_unapproved_status_and_can_retry(monkeypatch):
    db, current_user, reviewer, challenge_value, participant_value, submission = create_review_database()
    pushed_notifications = []
    monkeypatch.setattr(
        "app.api.evidence.send_notification_push",
        lambda db, notification: pushed_notifications.append(notification),
    )
    result = verify_evidence(
        submission.id,
        EvidenceVerificationRequest(decision="REJECT", note="Please provide clearer proof."),
        db,
        reviewer,
    )

    assert result.status == "REJECTED"
    assert result.review_reason == "Please provide clearer proof."
    assert participant_value.status == "IN_PROGRESS"
    assert participant_value.completion_status == "INCOMPLETE"
    assert challenge_value.status == "PRIVATE"
    assert challenge_value.visibility == "PRIVATE"
    status_result = get_evidence_approval_status(submission.id, db, current_user)
    assert status_result.is_approved is False
    assert status_result.note == "Please provide clearer proof."
    assert current_user.aura_points == 0
    notifications = list(db.scalars(select(Notification)).all())
    assert len(notifications) == 1
    assert notifications[0].user_id == current_user.id
    assert notifications[0].notification_type == "EVIDENCE_REJECTED"
    assert pushed_notifications == notifications


def test_non_admin_cannot_review_or_view_other_users_evidence():
    db, current_user, _, _, _, submission = create_review_database()
    other_user = user("user-2")
    with pytest.raises(HTTPException) as error:
        verify_evidence(
            submission.id,
            EvidenceVerificationRequest(decision="APPROVE"),
            db,
            other_user,
        )
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        get_evidence_approval_status(submission.id, db, other_user)
    assert error.value.status_code == 404


def test_active_evidence_queue_is_admin_only_and_paginated():
    db, _, reviewer, challenge_value, participant_value, submission = create_review_database()
    submission.submitted_at = datetime(2026, 1, 2)
    processing = EvidenceSubmission(
        id="submission-processing",
        challenge_id=challenge_value.id,
        participant_id=participant_value.id,
        user_id=submission.user_id,
        status="PROCESSING",
        submitted_at=datetime(2026, 1, 1),
    )
    verified = EvidenceSubmission(
        id="submission-verified",
        challenge_id=challenge_value.id,
        participant_id=participant_value.id,
        user_id=submission.user_id,
        status="VERIFIED",
    )
    rejected = EvidenceSubmission(
        id="submission-rejected",
        challenge_id=challenge_value.id,
        participant_id=participant_value.id,
        user_id=submission.user_id,
        status="REJECTED",
    )
    db.add_all([processing, verified, rejected])
    db.commit()

    first_page = get_active_evidence_submissions(1, 0, db, reviewer)
    second_page = get_active_evidence_submissions(1, 1, db, reviewer)
    assert first_page.total == 2
    assert [item.id for item in first_page.items] == [processing.id]
    assert [item.id for item in second_page.items] == [submission.id]

    with pytest.raises(HTTPException) as error:
        get_active_evidence_submissions(20, 0, db, user())
    assert error.value.status_code == 403
