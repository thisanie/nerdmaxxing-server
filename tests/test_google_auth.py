from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import app.api.auth as auth_api
import app.models
from app.models.aura import AuraTransaction
from app.models.base import Base
from app.services.user_service import get_or_create_google_user


def make_client() -> TestClient:
    app = FastAPI()
    app.state.limiter = auth_api.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth_api.router)
    app.dependency_overrides[auth_api.get_db] = lambda: None
    return TestClient(app)


def test_google_login_only_returns_not_found_without_creating_user(monkeypatch):
    monkeypatch.setattr(
        auth_api,
        "verify_google_id_token",
        lambda token: {"sub": "google-user-1", "email_verified": True},
    )
    monkeypatch.setattr(auth_api, "find_google_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth_api,
        "get_or_create_google_user",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not create a user")),
    )

    response = make_client().post(
        "/api/v1/auth/google/login-only",
        json={"id_token": "google-id-token"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No account is linked to this Google account."


def test_google_login_only_issues_session_for_existing_user(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        username="ada",
        password_hash=None,
        display_name="Ada",
        avatar_url=None,
    )
    monkeypatch.setattr(
        auth_api,
        "verify_google_id_token",
        lambda token: {"sub": "google-user-1", "email_verified": True},
    )
    monkeypatch.setattr(auth_api, "find_google_user", lambda *args, **kwargs: user)
    monkeypatch.setattr(
        auth_api,
        "create_session",
        lambda **kwargs: (SimpleNamespace(id="session-1"), "refresh-token"),
    )
    monkeypatch.setattr(auth_api, "create_access_token", lambda **kwargs: "access-token")

    response = make_client().post(
        "/api/v1/auth/google/login-only",
        json={"id_token": "google-id-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "token_type": "bearer",
        "user_id": "user-1",
        "username": "ada",
        "needs_username": False,
        "needs_password": True,
        "is_new_user": False,
        "display_name": "Ada",
        "avatar_url": None,
    }


def test_regular_google_auth_keeps_account_creation_flow(monkeypatch):
    user = SimpleNamespace(
        id="user-2",
        username=None,
        password_hash=None,
        display_name="Grace",
        avatar_url=None,
    )
    monkeypatch.setattr(
        auth_api,
        "verify_google_id_token",
        lambda token: {"sub": "google-user-2", "email_verified": True},
    )
    monkeypatch.setattr(
        auth_api,
        "get_or_create_google_user",
        lambda **kwargs: (user, True),
    )
    monkeypatch.setattr(
        auth_api,
        "create_session",
        lambda **kwargs: (SimpleNamespace(id="session-2"), "refresh-token"),
    )
    monkeypatch.setattr(auth_api, "create_access_token", lambda **kwargs: "access-token")

    response = make_client().post(
        "/api/v1/auth/google",
        json={"id_token": "google-id-token"},
    )

    assert response.status_code == 200
    assert response.json()["is_new_user"] is True
    assert response.json()["user_id"] == "user-2"


def test_new_google_account_receives_one_time_welcome_aura():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)

    user, is_new_user = get_or_create_google_user(
        db,
        google_user_id="google-user-3",
        email="new@example.com",
        name="New User",
        picture=None,
    )
    existing_user, is_existing_user = get_or_create_google_user(
        db,
        google_user_id="google-user-3",
        email="new@example.com",
        name="New User",
        picture=None,
    )
    welcome_transactions = db.scalars(
        select(AuraTransaction).where(
            AuraTransaction.user_id == user.id,
            AuraTransaction.reference_type == "ACCOUNT_CREATION",
        )
    ).all()

    assert is_new_user is True
    assert is_existing_user is False
    assert existing_user.id == user.id
    assert user.aura_points == 100
    assert len(welcome_transactions) == 1
    assert welcome_transactions[0].amount == 100


def test_bulk_welcome_bonus_is_dry_run_safe_and_idempotent():
    from app.models.aura import AuraTransaction
    from app.models.base import Base
    from app.models.user import User
    from scripts.grant_welcome_bonus import grant_welcome_bonus

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    old_user = User(id="old-user", username="old-user")
    signed_up_user = User(id="signed-up-user", username="signed-up-user", aura_points=100)
    deleted_user = User(id="deleted-user", username="deleted-user", is_deleted=True)
    db.add_all([old_user, signed_up_user, deleted_user])
    db.add(
        AuraTransaction(
            user_id=signed_up_user.id,
            amount=100,
            reason="Welcome bonus",
            reference_type="ACCOUNT_CREATION",
            reference_id=signed_up_user.id,
        )
    )
    db.commit()

    eligible_count, skipped_count = grant_welcome_bonus(db, apply=False)
    assert (eligible_count, skipped_count) == (1, 1)
    assert old_user.aura_points == 0

    eligible_count, skipped_count = grant_welcome_bonus(db, apply=True)
    assert (eligible_count, skipped_count) == (1, 1)
    assert old_user.aura_points == 100

    eligible_count, skipped_count = grant_welcome_bonus(db, apply=True)
    assert (eligible_count, skipped_count) == (0, 2)
    assert old_user.aura_points == 100