from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class EvidenceCreate(BaseModel):
    explanation: str | None = Field(default=None, max_length=5000)
    text_content: str | None = Field(default=None, max_length=20000)
    external_url: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def require_evidence(self) -> "EvidenceCreate":
        if not self.text_content and not self.external_url:
            raise ValueError("Provide text content or an external URL as evidence.")
        return self


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