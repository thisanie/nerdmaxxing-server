from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ParticipationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    challenge_id: str
    user_id: str
    status: str
    completion_status: str
    verification_status: str
    started_at: datetime
    last_activity_at: datetime
    completed_at: datetime | None


class ParticipationStatusUpdate(BaseModel):
    status: str


class ProgressLogCreate(BaseModel):
    hours_spent: float = Field(gt=0, le=24)
    note: str | None = Field(default=None, max_length=5000)


class ProgressLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    participant_id: str
    user_id: str
    minutes_spent: int
    note: str | None
    created_at: datetime