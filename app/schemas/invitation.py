from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserSummaryResponse


class ChallengeInvitationCreate(BaseModel):
    invitee_id: str = Field(min_length=1)


class ChallengeInvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    challenge_id: str
    inviter_id: str
    invitee_id: str | None
    status: str
    created_at: datetime
    expires_at: datetime | None
    accepted_at: datetime | None
    challenge_title: str
    inviter: UserSummaryResponse


class ChallengeInviteLinkResponse(BaseModel):
    url: str
    inviter_id: str
    inviter_username: str | None
    challenge_id: str
    expires_at: datetime | None


class ChallengeInviteLinkPreviewResponse(BaseModel):
    challenge_id: str
    challenge_title: str
    inviter_id: str
    inviter_username: str | None
    status: str
    expires_at: datetime | None


class NotificationResponse(BaseModel):
    id: str
    notification_type: str
    title: str
    body: str
    invitation_id: str | None
    invitation_status: str | None
    is_read: bool
    created_at: datetime


class PushTokenCreate(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
    platform: str = Field(pattern="^(ios|android|web)$")


class PushTokenResponse(BaseModel):
    id: str
    platform: str
    is_active: bool
    last_seen_at: datetime
