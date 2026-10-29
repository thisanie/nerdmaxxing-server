from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.user_service import normalize_username


_password_hasher = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _password_hasher.hash("not-a-user-password")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    stored_hash = password_hash or _DUMMY_PASSWORD_HASH
    password_matches = _password_hasher.verify(password, stored_hash)
    return password_hash is not None and password_matches


def authenticate_password_user(
    db: Session,
    *,
    username: str,
    password: str,
) -> User | None:
    user = db.scalar(
        select(User).where(User.username_normalized == normalize_username(username))
    )
    if user is None:
        verify_password(password, None)
        return None

    if not verify_password(password, user.password_hash):
        return None
    if user.is_deleted or user.is_suspended:
        return None
    return user