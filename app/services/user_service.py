import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import Identity
from app.models.user import User
from app.services.aura_service import award_aura


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,24}$")


def normalize_username(username: str) -> str:
    return username.casefold()


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.fullmatch(username))


def _load_reserved_usernames() -> frozenset[str]:
    path = Path(__file__).resolve().parents[2] / "reserved_usernames.json"
    with path.open(encoding="utf-8") as reserved_file:
        usernames = json.load(reserved_file)
    if not isinstance(usernames, list) or any(not isinstance(username, str) for username in usernames):
        raise ValueError("reserved_usernames.json must contain a JSON array of strings.")
    if any(not is_valid_username(username) for username in usernames):
        raise ValueError("reserved_usernames.json contains an invalid username.")
    return frozenset(normalize_username(username) for username in usernames)


RESERVED_USERNAMES = _load_reserved_usernames()


def is_username_available(db: Session, *, username: str) -> bool:
    normalized_username = normalize_username(username)
    if normalized_username in RESERVED_USERNAMES:
        return False
    statement = select(User.id).where(
        User.username_normalized == normalized_username
    )
    return db.scalar(statement) is None


def find_google_user(db: Session, *, google_user_id: str) -> User | None:
    statement = (
        select(User)
        .join(Identity, Identity.user_id == User.id)
        .where(
            Identity.provider == "google",
            Identity.provider_user_id == google_user_id,
        )
    )
    return db.scalar(statement)


def get_or_create_google_user(
        db: Session,
        *,
        google_user_id: str,
        email: str | None,
        name: str | None,
        picture: str | None,
)->tuple[User, bool]:

    #look if exists google identity
    statement = (
        select(Identity).where(Identity.provider == "google", Identity.provider_user_id == google_user_id)
    )

    identity = db.scalar(statement)

    #if existing user

    if identity is not None:
        return  identity.user, False

    #create new user and identity
    user = User(
        username = None,
        username_normalized = None,
        display_name = name,
        avatar_url = picture,
    )

    db.add(user)
    db.flush()
    award_aura(
        db,
        user,
        100,
        "Welcome bonus",
        reference_type="ACCOUNT_CREATION",
        reference_id=user.id,
    )

    identity = Identity(
        provider="google",
        provider_user_id=google_user_id,
        user_id=user.id,
        provider_email=email,
    )
    db.add(identity)

    db.commit()

    db.refresh(user)

    return user, True