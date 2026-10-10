from datetime import datetime

from pydantic import BaseModel, Field


class IntegrationAccountResponse(BaseModel):
    provider_user_id: str
    username: str
    avatar_url: str | None = None
    verified_at: datetime


class IntegrationStatusResponse(BaseModel):
    provider_id: str
    connected: bool
    account: IntegrationAccountResponse | None = None


class ChessComStartRequest(BaseModel):
    username: str = Field(min_length=3, max_length=25)


class ChessComStartResponse(BaseModel):
    provider_id: str = "chess_com"
    challenge_id: str
    username: str
    verification_code: str
    instructions: str
    expires_at: datetime


class ChessComConfirmRequest(BaseModel):
    challenge_id: str
    verification_code: str = Field(min_length=8, max_length=20)


class ChessComRatingResponse(BaseModel):
    provider_id: str = "chess_com"
    metric: str = "rapid_rating"
    value: int
    source: str
    observed_at: datetime
