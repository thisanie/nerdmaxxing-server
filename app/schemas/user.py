from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.challenge import ChallengeResponse


class UsernameRequest(BaseModel):
    username: str


class UsernameResponse(BaseModel):
    username: str


class UsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool


class ProfileSkillResponse(BaseModel):
    id: str
    name: str
    unlocked_at: datetime


class UserProfileResponse(BaseModel):
    id: str
    username: str | None
    name: str | None
    bio: str | None
    avatar_url: str | None
    aura_points: int
    follower_count: int
    following_count: int
    skills_count: int
    completed_challenges_count: int
    created_challenges_count: int
    skills: list[ProfileSkillResponse] = Field(default_factory=list)
    completed_challenges: list[ChallengeResponse] = Field(default_factory=list)
    is_following: bool = False


class UserProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=2048)


class UserSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str | None
    name: str | None = Field(validation_alias="display_name")
    avatar_url: str | None


class FollowStatusResponse(BaseModel):
    is_following: bool


class AuraTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    amount: int
    reason: str
    reference_type: str | None
    reference_id: str | None
    created_at: datetime
