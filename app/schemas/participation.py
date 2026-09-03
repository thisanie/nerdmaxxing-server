from datetime import datetime

from pydantic import BaseModel, ConfigDict


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