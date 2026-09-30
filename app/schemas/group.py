from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


GroupVisibility = Literal["PUBLIC", "PRIVATE"]
MembershipStatus = Literal["PENDING", "ACTIVE"]


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: GroupVisibility = "PUBLIC"


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    visibility: GroupVisibility
    creator_id: str
    member_count: int
    membership_status: MembershipStatus | None = None
    created_at: datetime


class GroupMembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    user_id: str
    status: MembershipStatus
    created_at: datetime


class GroupJoinRequestResponse(GroupMembershipResponse):
    username: str | None
    display_name: str | None


class GroupMemberResponse(BaseModel):
    id: str
    group_id: str
    user_id: str
    status: MembershipStatus
    created_at: datetime
    username: str | None
    display_name: str | None
    avatar_url: str | None


class GroupMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class GroupChallengeInvitationCreate(BaseModel):
    group_id: str = Field(min_length=1)


class GroupInvitationResponseRequest(BaseModel):
    response: Literal["ACCEPTED", "DECLINED"]


class GroupInvitationCreatedResponse(BaseModel):
    invitation_id: str
    invitation: "GroupChallengeInvitationSummary"
    message: "GroupMessageResponse"


class GroupChallengeInvitationSummary(BaseModel):
    id: str
    group_id: str
    challenge_id: str
    invited_by: str
    status: str
    created_at: datetime
    expires_at: datetime | None


class GroupMessageAuthorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str | None
    name: str | None = Field(validation_alias="display_name")
    avatar_url: str | None


class GroupMessageResponse(BaseModel):
    id: str
    group_id: str
    author: GroupMessageAuthorResponse
    type: str
    body: str
    created_at: datetime
    challenge_invitation: "GroupChallengeInvitationPayload | None" = None


class GroupInvitationResponseCounts(BaseModel):
    pending: int
    accepted: int
    declined: int


class GroupChallengeInvitationPayload(BaseModel):
    id: str
    challenge_id: str
    challenge_slug: str
    challenge_title: str
    status: str
    my_response: str
    response_counts: GroupInvitationResponseCounts