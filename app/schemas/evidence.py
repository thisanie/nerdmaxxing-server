from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItemResponse(BaseModel):
    id: str
    evidence_type: str
    content_type: str | None
    file_size: int | None
    video_url: str | None


class EvidenceSubmissionStatusResponse(BaseModel):
    has_submitted: bool


class EvidenceCreatedResponse(BaseModel):
    id: str
    status: str


class EvidenceApprovalStatusResponse(BaseModel):
    is_approved: bool
    note: str | None = None


class EvidenceVerificationRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    note: str | None = Field(default=None, max_length=5000)


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    challenge_id: str
    participant_id: str
    user_id: str
    status: str
    explanation: str | None
    submitted_at: datetime
    reviewed_at: datetime | None
    items: list[EvidenceItemResponse] = Field(default_factory=list)
    verification_kind: str = "SELF_REPORTED"
    provider_id: str | None = None
    provider_metric: str | None = None
    provider_value: int | None = None
    provider_observed_at: datetime | None = None
    file_url: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    review_reason: str | None = None


class EvidenceSubmissionListResponse(BaseModel):
    items: list[EvidenceResponse]
    total: int
    limit: int
    offset: int