from datetime import datetime, timedelta

from sqlalchemy import case, cast, func, Integer, select
from sqlalchemy.orm import Session

from app.models.aura import AuraTransaction
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.leaderboard import (
    LeaderboardEntryResponse,
    LeaderboardResponse,
    LeaderboardViewerResponse,
)
from app.services.rank_service import RANK_THRESHOLDS


PUBLIC_CHALLENGE = (Challenge.status == "PUBLISHED", Challenge.visibility == "PUBLIC")
PUBLIC_USER = (User.is_deleted.is_(False), User.is_suspended.is_(False), User.is_private.is_(False))


def period_start(period: str, now: datetime) -> datetime | None:
    if period == "all_time":
        return None
    beginning = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return beginning - timedelta(days=now.weekday())
    return beginning.replace(day=1)


def _player_rank_expression():
    return case(
        *[(User.aura_points >= minimum, rank) for rank, minimum, _ in RANK_THRESHOLDS[::-1]],
        else_="E",
    )


def _ranking_query(db: Session, period: str, metric: str, player_rank: str | None, now: datetime):
    start = period_start(period, now)
    aura_filters = [AuraTransaction.created_at >= start] if start else []
    completion_filters = [*PUBLIC_CHALLENGE, ChallengeParticipant.completion_status == "COMPLETED"]
    if start:
        completion_filters.append(ChallengeParticipant.completed_at >= start)

    aura_subquery = (
        select(
            AuraTransaction.user_id,
            func.coalesce(func.sum(AuraTransaction.amount), 0).label("period_aura"),
            func.max(AuraTransaction.created_at).label("aura_achieved_at"),
        )
        .where(*aura_filters)
        .group_by(AuraTransaction.user_id)
        .subquery()
    )
    completion_subquery = (
        select(
            ChallengeParticipant.user_id,
            func.count(ChallengeParticipant.id).label("period_completed"),
            func.max(ChallengeParticipant.completed_at).label("completed_achieved_at"),
        )
        .join(Challenge, Challenge.id == ChallengeParticipant.challenge_id)
        .where(*completion_filters)
        .group_by(ChallengeParticipant.user_id)
        .subquery()
    )
    lifetime_completion_subquery = (
        select(
            ChallengeParticipant.user_id,
            func.count(ChallengeParticipant.id).label("lifetime_completed"),
        )
        .join(Challenge, Challenge.id == ChallengeParticipant.challenge_id)
        .where(*PUBLIC_CHALLENGE, ChallengeParticipant.completion_status == "COMPLETED")
        .group_by(ChallengeParticipant.user_id)
        .subquery()
    )
    period_aura = func.coalesce(aura_subquery.c.period_aura, 0)
    period_completed = func.coalesce(completion_subquery.c.period_completed, 0)
    if metric == "aura":
        metric_value = User.aura_points if period == "all_time" else period_aura
    elif metric == "completed":
        metric_value = period_completed
    else:
        metric_value = User.day_streak if period == "all_time" else case(
            (User.last_progress_at >= start, User.day_streak), else_=0
        )
    if metric == "aura":
        achievement_at = func.coalesce(aura_subquery.c.aura_achieved_at, User.created_at)
    elif metric == "completed":
        achievement_at = func.coalesce(completion_subquery.c.completed_achieved_at, User.created_at)
    else:
        achievement_at = func.coalesce(User.last_progress_at, User.created_at)
    statement = (
        select(
            User,
            cast(metric_value, Integer).label("metric_value"),
            cast(period_aura if period != "all_time" else User.aura_points, Integer).label("period_aura"),
            cast(period_completed, Integer).label("period_completed"),
            cast(func.coalesce(lifetime_completion_subquery.c.lifetime_completed, 0), Integer).label("lifetime_completed"),
            achievement_at.label("achievement_at"),
            _player_rank_expression().label("player_rank"),
        )
        .outerjoin(aura_subquery, aura_subquery.c.user_id == User.id)
        .outerjoin(completion_subquery, completion_subquery.c.user_id == User.id)
        .outerjoin(lifetime_completion_subquery, lifetime_completion_subquery.c.user_id == User.id)
        .where(*PUBLIC_USER)
    )
    if player_rank:
        minimum = dict((rank, minimum) for rank, minimum, _ in RANK_THRESHOLDS)[player_rank]
        maximum = next(maximum for rank, _, maximum in RANK_THRESHOLDS if rank == player_rank)
        statement = statement.where(User.aura_points >= minimum)
        if maximum is not None:
            statement = statement.where(User.aura_points < maximum)
    return statement, metric_value, achievement_at


def get_leaderboard(
    db: Session,
    period: str,
    metric: str,
    limit: int,
    offset: int,
    current_user: User | None = None,
    player_rank: str | None = None,
) -> LeaderboardResponse:
    statement, metric_value, achievement_at = _ranking_query(
        db, period, metric, player_rank, datetime.utcnow()
    )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    competition_rank = func.rank().over(order_by=metric_value.desc()).label("competition_rank")
    rows = db.execute(
        statement.add_columns(competition_rank)
        .order_by(metric_value.desc(), User.aura_points.desc(), achievement_at.asc(), User.id.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    viewer = None
    if current_user is not None:
        viewer_rows = db.execute(statement.where(User.id == current_user.id)).all()
        if viewer_rows:
            viewer_metric = viewer_rows[0].metric_value
            ranked_values = statement.with_only_columns(metric_value.label("metric_value")).subquery()
            rank = (db.scalar(select(func.count()).select_from(ranked_values).where(ranked_values.c.metric_value > viewer_metric)) or 0) + 1
            viewer = LeaderboardViewerResponse(rank=rank, metric_value=viewer_metric, user_id=current_user.id)
    entries = []
    for user, value, _, _, lifetime_completed, _, player_rank_value, rank in rows:
        entries.append(LeaderboardEntryResponse(
            rank=rank,
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            player_rank=player_rank_value,
            aura_points=user.aura_points,
            completed_challenge_count=lifetime_completed,
            day_streak=user.day_streak,
            metric_value=value,
            is_current_user=current_user is not None and user.id == current_user.id,
        ))
    return LeaderboardResponse(
        period=period, metric=metric, entries=entries, viewer=viewer, total=total
    )
