from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models
from app.models.base import Base
from app.models.user import User
from app.services.password_service import (
    authenticate_password_user,
    hash_password,
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