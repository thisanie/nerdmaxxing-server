from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.api.users as users_api
import app.models
from app.models.base import Base
from app.models.user import User
from app.schemas.auth import PasswordStatusResponse
from app.schemas.user import UserProfileResponse
from app.services.password_service import (
    authenticate_password_user,
    hash_password,
    verify_password,
)


def make_db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_password_hash_is_not_plaintext_and_authenticates_normalized_username():
    db = make_db()
    password = "a-long-user-chosen-password"
    password_hash = hash_password(password)
    user = User(
        id="user-1",
        username="Ada_Lovelace",
        username_normalized="ada_lovelace",
        password_hash=password_hash,
    )
    db.add(user)
    db.commit()

    authenticated_user = authenticate_password_user(
        db,
        username="ADA_LOVELACE",
        password=password,
    )

    assert password_hash != password
    assert password_hash.startswith("$argon2id$")
    assert authenticated_user is user


def test_password_authentication_rejects_unknown_user_and_wrong_password():
    db = make_db()
    password_hash = hash_password("a-long-user-chosen-password")
    db.add(
        User(
            id="user-1",
            username="ada",
            username_normalized="ada",
            password_hash=password_hash,
        )
    )
    db.commit()

    assert authenticate_password_user(
        db,
        username="ada",
        password="incorrect-password",
    ) is None
    assert authenticate_password_user(
        db,
        username="unknown",
        password="incorrect-password",
    ) is None


def test_password_status_reflects_stored_hash_and_updated_password_hashes():
    user = User(id="user-1", username="ada", username_normalized="ada")
    assert PasswordStatusResponse(has_password=user.password_hash is not None).has_password is False

    current_password = "a-long-user-chosen-password"
    user.password_hash = hash_password(current_password)
    assert PasswordStatusResponse(has_password=user.password_hash is not None).has_password is True

    new_password = "a-different-long-password"
    user.password_hash = hash_password(new_password)
    assert verify_password(new_password, user.password_hash)
    assert not verify_password(current_password, user.password_hash)


def test_self_profile_includes_admin_flag_without_changing_public_profile(monkeypatch):
    profile = UserProfileResponse(
        id="user-1",
        username="ada",
        name="Ada",
        bio=None,
        avatar_url=None,
        aura_points=0,
        rank="E",
        follower_count=0,
        following_count=0,
        skills_count=0,
        completed_challenges_count=0,
        created_challenges_count=0,
    )
    monkeypatch.setattr(users_api, "profile_response", lambda *args: profile)

    result = users_api.get_my_profile(
        db=None,
        current_user=SimpleNamespace(id="user-1", is_admin=True),
    )

    assert result.is_admin is True
    assert "is_admin" not in profile.model_dump()