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
    body: str
    created_at: datetime