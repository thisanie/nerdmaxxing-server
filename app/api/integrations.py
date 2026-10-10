from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.integration import ExternalAccountConnection
from app.models.user import User
from app.schemas.integration import IntegrationAccountResponse, IntegrationStatusResponse


router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])
SUPPORTED_PROVIDERS = {
    "chess_com": {
        "name": "Chess.com",
        "provider_user_id": "seed-chess-user-1",
        "username": "demo_player",
    }
}


def _provider(provider_id: str) -> dict:
    provider = SUPPORTED_PROVIDERS.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration provider not found.")
    return provider


def _status_response(
    provider_id: str,
    connection: ExternalAccountConnection | None,
) -> IntegrationStatusResponse:
    return IntegrationStatusResponse(
        provider_id=provider_id,
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


@router.post("/{provider_id}/connect", response_model=IntegrationStatusResponse)
def connect_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IntegrationStatusResponse:
    provider = _provider(provider_id)
    connection = db.scalar(
        select(ExternalAccountConnection).where(
            ExternalAccountConnection.user_id == current_user.id,
            ExternalAccountConnection.provider_id == provider_id,
        )
    )
    if connection is None:
        connection = ExternalAccountConnection(
            user_id=current_user.id,
            provider_id=provider_id,
            provider_user_id=provider["provider_user_id"],
            username=provider["username"],
            avatar_url=None,
            verified_at=datetime.utcnow(),
        )
        db.add(connection)
    else:
        connection.verified_at = datetime.utcnow()
    db.commit()
    db.refresh(connection)
    return _status_response(provider_id, connection)


@router.get("/{provider_id}/status", response_model=IntegrationStatusResponse)
def provider_status(
    provider_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IntegrationStatusResponse:
    _provider(provider_id)
    connection = db.scalar(
        select(ExternalAccountConnection).where(
            ExternalAccountConnection.user_id == current_user.id,
            ExternalAccountConnection.provider_id == provider_id,
        )
    )
    return _status_response(provider_id, connection)
