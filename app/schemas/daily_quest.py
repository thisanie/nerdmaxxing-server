from datetime import date, datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


QuestCategory = Literal["KNOWLEDGE", "PHYSICAL", "CREATIVE", "PRACTICAL", "MENTAL"]
QuestVerification = Literal["QUIZ", "IMAGE", "VIDEO"]


class DailyQuestQuestionCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_option_index: int = Field(ge=0)

    @model_validator(mode="after")
    def correct_answer_is_an_option(self) -> "DailyQuestQuestionCreate":
        if self.correct_option_index >= len(self.options):
            raise ValueError("correct_option_index must reference an option.")
        return self


class DailyQuestCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    short_description: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1)
    category: QuestCategory
    estimated_minutes: int = Field(ge=5, le=15)
    verification_type: QuestVerification
    resource_url: AnyHttpUrl | None = None
    related_challenge_id: str | None = None
    aura_reward: Literal[200] = 200
    questions: list[DailyQuestQuestionCreate] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def questions_match_verification(self) -> "DailyQuestCreate":
        if self.verification_type == "QUIZ" and not self.questions:
            raise ValueError("Quiz quests require at least one question.")
        if self.verification_type != "QUIZ" and self.questions:
            raise ValueError("Only quiz quests can contain quiz questions.")
        return self


class DailyQuestScheduleCreate(BaseModel):
    local_date: date


class DailyQuestScheduleResponse(BaseModel):
    id: str
    quest_id: str
    local_date: date


class DailyQuestQuestionAdminResponse(BaseModel):
    id: str
    order_index: int
    prompt: str
    options: list[str]
    correct_option_index: int


class DailyQuestAdminResponse(BaseModel):
    id: str
    title: str
    short_description: str
    instructions: str
    category: str
    estimated_minutes: int
    verification_type: str
    resource_url: str | None
    related_challenge_id: str | None
    aura_reward: int
    status: str
    questions: list[DailyQuestQuestionAdminResponse]


class DailyQuestQuestionResponse(BaseModel):
    id: str
    order_index: int
    prompt: str
    options: list[str]


class DailyQuestRelatedChallenge(BaseModel):
    id: str
    slug: str
    title: str
    deep_link: str


class DailyQuestResponse(BaseModel):
    id: str
    title: str
    short_description: str
    instructions: str
    category: str
    estimated_minutes: int
    verification_type: str
    resource_url: str | None
    related_challenge_id: str | None
    aura_reward: int
    questions: list[DailyQuestQuestionResponse] = Field(default_factory=list)
    related_major_challenge: DailyQuestRelatedChallenge | None = None


class DailyQuestTodayResponse(BaseModel):
    local_date: date
    timezone: str
    expires_at: datetime
    quest: DailyQuestResponse
    attempt_id: str | None = None
    status: str | None = None
    quiz_attempt_count: int = 0
    quiz_attempts_remaining: int = 0
    quiz_score_percent: float | None = None


class DailyQuestQuizSubmit(BaseModel):
    answers: list[int] = Field(min_length=1, max_length=20)


class DailyQuestQuizAnswerSubmit(BaseModel):
    question_index: int = Field(ge=0)
    answer_option_index: int | None = Field(default=None, ge=0)


class DailyQuestQuizResult(BaseModel):
    attempt_id: str
    status: str
    score_percent: float
    passed: bool
    quiz_attempt_count: int
    quiz_attempts_remaining: int
    aura_awarded: int = 0
    day_streak: int
    completion_status: str
    completion_date: date | None = None
    related_major_challenge: DailyQuestRelatedChallenge | None = None


class DailyQuestQuizSessionResponse(BaseModel):
    session_id: str
    attempt_id: str
    attempt_number: int
    question: DailyQuestQuestionResponse | None
    question_index: int | None
    question_number: int | None
    total_questions: int
    seconds_per_question: int
    answer_deadline_at: datetime | None
    timed_out: bool = False
    result: DailyQuestQuizResult | None = None


class DailyQuestAttemptResult(BaseModel):
    attempt_id: str
    status: str
    completion_status: str
    completion_date: date | None = None
    completed_at: datetime | None = None
    aura_awarded: int = 0
    day_streak: int
    timezone: str
    score_percent: float | None = None
    related_major_challenge: DailyQuestRelatedChallenge | None = None


class DailyQuestEvidenceResponse(BaseModel):
    attempt_id: str
    status: str
    message: str


class DailyQuestReviewRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    note: str | None = Field(default=None, max_length=1000)


class DailyQuestReviewResponse(BaseModel):
    attempt_id: str
    status: str
    aura_awarded: int = 0
    day_streak: int


class DailyQuestAttemptAdminResponse(BaseModel):
    id: str
    quest_id: str
    user_id: str
    local_date: date
    timezone: str
    status: str
    evidence_content_type: str | None
    review_note: str | None
    created_at: datetime
    reviewed_at: datetime | None