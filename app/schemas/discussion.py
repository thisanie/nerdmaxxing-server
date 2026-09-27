from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.user import UserSummaryResponse


class DiscussionCreate(BaseModel):
    type: str = Field(default="COMMENT", pattern="^(COMMENT|QUESTION)$")
    body: str = Field(min_length=1, max_length=2200)


class DiscussionUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=2200)


class ReplyCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2200)


class ReplyUpdate(ReplyCreate):
    pass


class ReportCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=30)


class DiscussionResponse(BaseModel):
    id: str
    challenge_id: str
    author: UserSummaryResponse
    type: str
    body: str
    reply_count: int
    is_resolved: bool
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class ReplyResponse(BaseModel):
    id: str
    discussion_id: str
    author: UserSummaryResponse
    body: str
    created_at: datetime
    updated_at: datetime | None
    is_deleted: bool


class DiscussionDetailResponse(DiscussionResponse):
    replies: list[ReplyResponse]