from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.models.session import Session as UserSession
from app.core.time import utcnow
from app.services.token_service import create_refresh_token, hash_refresh_token



def create_session(
        db:Session,
        *,
        user_id:str,
        )-> tuple[UserSession, str]:

    refresh_token = create_refresh_token()

    refresh_token_hash = hash_refresh_token(refresh_token=refresh_token)

    expires_at = (
        utcnow() + timedelta(days = settings.refresh_token_expire_days)

    )

    session = UserSession(
        user_id=user_id,
        refresh_token_hash = refresh_token_hash,
        expires_at = expires_at 
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    return session, refresh_token

def rotate_refresh_token(
        db:Session,
        *,
        refresh_token:str,
)-> tuple[UserSession, str] | None:

    token_hash = hash_refresh_token(refresh_token=refresh_token)

    statement = select(UserSession).where(UserSession.refresh_token_hash == token_hash)

    session = db.scalar(statement=statement)

    if session is None:
        return None

    now = utcnow()

    if session.revoked_at is not None:
        return None

    if session.expires_at<= now:
        return None

    #generate new
    new_refresh_token = create_refresh_token()
    new_refresh_token_hash = hash_refresh_token(new_refresh_token)

    session.refresh_token_hash=(new_refresh_token_hash)

    session.last_used_at = now
    db.commit()
    db.refresh(session)

    return session, new_refresh_token


def revoke_refresh_token(
        db: Session,
        *,
        refresh_token: str,
) -> None:
    token_hash = hash_refresh_token(refresh_token=refresh_token)
    statement = select(UserSession).where(UserSession.refresh_token_hash == token_hash)
    session = db.scalar(statement=statement)

    if session is not None and session.revoked_at is None:
        session.revoked_at = utcnow()
        db.commit()