import hashlib
import hmac
import secrets
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.integration import (
    ExternalAccountConnection,
    ExternalAccountVerificationChallenge,
)
from app.models.user import User
from app.schemas.integration import (
    ChessComConfirmRequest,
    ChessComRatingResponse,
    ChessComStartRequest,
    ChessComStartResponse,
    IntegrationAccountResponse,
    IntegrationStatusResponse,
)
from app.services.chess_com import (
    ChessComError,
    ChessComNotFound,
    ChessComRateLimited,
    ChessComUnavailable,
    fetch_profile,
    fetch_rapid_rating,
)


router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])
CHESS_COM = "chess_com"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,24}$")


def _status_response(connection: ExternalAccountConnection | None) -> IntegrationStatusResponse:
    return IntegrationStatusResponse(
        provider_id=CHESS_COM,
        connected=connection is not None,
        account=(
            IntegrationAccountResponse(
                provider_user_id=connection.provider_user_id,
                username=connection.username,
                avatar_url=connection.avatar_url,
                verified_at=connection.verified_at,
            )
            if connection
            else None
        ),
    )


def _chess_error(error: ChessComError) -> HTTPException:
    if isinstance(error, ChessComNotFound):
        return HTTPException(status_code=404, detail="Chess.com player was not found.")
    if isinstance(error, ChessComRateLimited):
        return HTTPException(status_code=429, detail="Chess.com is rate limiting requests. Try again later.")
    if isinstance(error, ChessComUnavailable):
        return HTTPException(status_code=503, detail="Chess.com is temporarily unavailable.")
    return HTTPException(status_code=502, detail="Chess.com returned invalid player data.")


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _connection(db: Session, user_id: str) -> ExternalAccountConnection | None:
    return db.scalar(
        select(ExternalAccountConnection).where(
            ExternalAccountConnection.user_id == user_id,
            ExternalAccountConnection.provider_id == CHESS_COM,
        )
    )


@router.post("/chess_com/start", response_model=ChessComStartResponse)
def start_chess_com_verification(
    payload: ChessComStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChessComStartResponse:
    if not USERNAME_PATTERN.fullmatch(payload.username):
        raise HTTPException(status_code=422, detail="Invalid Chess.com username.")
    try:
        profile = fetch_profile(payload.username)
    except ChessComError as error:
        raise _chess_error(error) from error

    existing_owner = db.scalar(
        select(ExternalAccountConnection).where(
            ExternalAccountConnection.provider_id == CHESS_COM,
            ExternalAccountConnection.provider_user_id == profile.player_id,
            ExternalAccountConnection.user_id != current_user.id,
        )
    )
    if existing_owner is not None:
        raise HTTPException(status_code=409, detail="This Chess.com account is already linked.")

    now = datetime.utcnow()
    for pending in db.scalars(
        select(ExternalAccountVerificationChallenge).where(
            ExternalAccountVerificationChallenge.user_id == current_user.id,
            ExternalAccountVerificationChallenge.provider_id == CHESS_COM,
            ExternalAccountVerificationChallenge.consumed_at.is_(None),
        )
    ).all():
        pending.consumed_at = now

    code = f"NMX-{secrets.token_hex(3).upper()}"
    challenge = ExternalAccountVerificationChallenge(
        user_id=current_user.id,
        provider_id=CHESS_COM,
        provider_user_id=profile.player_id,
        username=profile.username,
        secret_hash=_hash_code(code),
        created_at=now,
        expires_at=now + timedelta(minutes=settings.chess_com_challenge_ttl_minutes),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return ChessComStartResponse(
        challenge_id=challenge.id,
        username=profile.username,
        verification_code=code,
        instructions=(
            "Temporarily set your Chess.com public profile Location to this exact code, "
            "then confirm here. Remove it after verification."
        ),
        expires_at=challenge.expires_at,
    )


@router.post("/chess_com/confirm", response_model=IntegrationStatusResponse)
def confirm_chess_com_verification(
    payload: ChessComConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IntegrationStatusResponse:
    challenge = db.scalar(
        select(ExternalAccountVerificationChallenge)
        .where(
            ExternalAccountVerificationChallenge.id == payload.challenge_id,
            ExternalAccountVerificationChallenge.user_id == current_user.id,
            ExternalAccountVerificationChallenge.provider_id == CHESS_COM,
        )
        .with_for_update()
    )
    now = datetime.utcnow()
    if challenge is None or challenge.consumed_at is not None or challenge.expires_at <= now:
        raise HTTPException(status_code=410, detail="Verification challenge is expired or already used.")
    if challenge.attempt_count >= settings.chess_com_max_confirmation_attempts:
        raise HTTPException(status_code=429, detail="Too many verification attempts. Start a new challenge.")
    challenge.attempt_count += 1
    if not hmac.compare_digest(challenge.secret_hash, _hash_code(payload.verification_code.strip().upper())):
        db.commit()
        raise HTTPException(status_code=400, detail="Verification code is incorrect.")

    try:
        profile = fetch_profile(challenge.username)
    except ChessComError as error:
        db.rollback()
        raise _chess_error(error) from error
    if profile.player_id != challenge.provider_user_id or profile.location != payload.verification_code.strip().upper():
        db.commit()
        raise HTTPException(status_code=400, detail="Chess.com profile does not contain the verification code.")

    owner = db.scalar(
        select(ExternalAccountConnection).where(
            ExternalAccountConnection.provider_id == CHESS_COM,
            ExternalAccountConnection.provider_user_id == profile.player_id,
            ExternalAccountConnection.user_id != current_user.id,
        )
    )
    if owner is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail="This Chess.com account is already linked.")

    connection = _connection(db, current_user.id)
    if connection is not None and connection.provider_user_id != profile.player_id:
        db.delete(connection)
        db.flush()
        connection = None
    if connection is None:
        connection = ExternalAccountConnection(
            user_id=current_user.id,
            provider_id=CHESS_COM,
            provider_user_id=profile.player_id,
            username=profile.username,
            avatar_url=profile.avatar_url,
            verified_at=now,
        )
        db.add(connection)
    else:
        connection.username = profile.username
        connection.avatar_url = profile.avatar_url
        connection.verified_at = now
    challenge.consumed_at = now
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="This Chess.com account is already linked.") from error
    db.refresh(connection)
    return _status_response(connection)


@router.get("/chess_com/status", response_model=IntegrationStatusResponse)
def chess_com_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IntegrationStatusResponse:
    return _status_response(_connection(db, current_user.id))


@router.delete("/chess_com/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_chess_com(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    connection = _connection(db, current_user.id)
    if connection is not None:
        db.delete(connection)
    for challenge in db.scalars(
        select(ExternalAccountVerificationChallenge).where(
            ExternalAccountVerificationChallenge.user_id == current_user.id,
            ExternalAccountVerificationChallenge.provider_id == CHESS_COM,
            ExternalAccountVerificationChallenge.consumed_at.is_(None),
        )
    ).all():
        challenge.consumed_at = datetime.utcnow()
    db.commit()


@router.get("/chess_com/rating/rapid", response_model=ChessComRatingResponse)
def chess_com_rapid_rating(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChessComRatingResponse:
    connection = _connection(db, current_user.id)
    if connection is None:
        raise HTTPException(status_code=409, detail="Connect a verified Chess.com account first.")
    now = datetime.utcnow()
    cache_valid = (
        connection.rapid_rating is not None
        and connection.rapid_rating_observed_at is not None
        and connection.refreshed_at is not None
        and connection.refreshed_at >= now - timedelta(minutes=settings.chess_com_rating_cache_minutes)
    )
    if not cache_valid:
        try:
            rating = fetch_rapid_rating(connection.username)
        except ChessComError as error:
            raise _chess_error(error) from error
        connection.rapid_rating = rating.value
        connection.rapid_rating_observed_at = rating.observed_at
        connection.refreshed_at = now
        db.commit()
    return ChessComRatingResponse(
        value=connection.rapid_rating,
        source="chess_com_public_api",
        observed_at=connection.rapid_rating_observed_at,
    )


@router.post("/{provider_id}/connect", response_model=IntegrationStatusResponse)
def deprecated_connect_provider(provider_id: str) -> IntegrationStatusResponse:
    if provider_id == CHESS_COM:
        raise HTTPException(
            status_code=410,
            detail="Chess.com accounts must be verified using /chess_com/start and /chess_com/confirm.",
        )
    raise HTTPException(status_code=404, detail="Integration provider not found.")
