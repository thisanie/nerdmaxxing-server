from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItemResponse(BaseModel):
    id: str
    evidence_type: str
    content_type: str | None
    file_size: int | None
    video_url: str | None


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
    file_url: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    review_reason: str | None = None