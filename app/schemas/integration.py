from datetime import datetime

from pydantic import BaseModel


class IntegrationAccountResponse(BaseModel):
    provider_user_id: str
    username: str
    avatar_url: str | None = None
    verified_at: datetime


class IntegrationStatusResponse(BaseModel):
    provider_id: str
    connected: bool
    account: IntegrationAccountResponse | None = None
