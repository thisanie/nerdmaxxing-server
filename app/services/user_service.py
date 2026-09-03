import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import Identity
from app.models.user import User


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,24}$")


def normalize_username(username: str) -> str:
    return username.casefold()


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.fullmatch(username))


def is_username_available(db: Session, *, username: str) -> bool:
    statement = select(User.id).where(
        User.username_normalized == normalize_username(username)
    )
    return db.scalar(statement) is None


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