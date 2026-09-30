from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_optional_current_user
from app.models.user import User
from app.schemas.leaderboard import LeaderboardResponse
from app.services.leaderboard_service import get_leaderboard


router = APIRouter(prefix="/api/v1", tags=["Leaderboard"])


@router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(
    period: str = Query(default="week", pattern="^(week|month|all_time)$"),
    metric: str = Query(default="aura", pattern="^(aura|completed|streak)$"),
    player_rank: str | None = Query(default=None, pattern="^(E|D|C|B|A|S)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
) -> LeaderboardResponse:
    return get_leaderboard(db, period, metric, limit, offset, current_user, player_rank)
