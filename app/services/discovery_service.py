import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.activity import Activity
from app.models.category import Category
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.saved_challenge import SavedChallenge
from app.models.user import User
from app.schemas.challenge import CategoryResponse, ChallengeResponse
from app.schemas.discover import RecentActivityResponse, TopNerdResponse
from app.services.aura_service import calculate_aura

PUBLIC_CHALLENGE = (Challenge.status == "PUBLISHED", Challenge.visibility == "PUBLIC")
PUBLIC_USER = (User.is_deleted.is_(False), User.is_suspended.is_(False), User.is_private.is_(False))
VALID_ACTIVITY_ACTIONS = ("JOINED_CHALLENGE", "COMPLETED_CHALLENGE", "STARTED_CHALLENGE", "EARNED_AURA", "REACHED_STREAK")


def _public_query():
    return select(Challenge).where(*PUBLIC_CHALLENGE).options(
        selectinload(Challenge.categories), selectinload(Challenge.resources)
    )


def _challenge_response(challenge: Challenge, enrollment_count=0, completion_count=0) -> ChallengeResponse:
    response = ChallengeResponse.model_validate(challenge)
    response.image_key = challenge.image_key or settings.default_challenge_image_key
    response.aura_points = calculate_aura(challenge)
    response.enrollment_count = enrollment_count
    response.completion_count = completion_count
    return response


def _responses(challenges: list[Challenge], db: Session) -> list[ChallengeResponse]:
    ids = [challenge.id for challenge in challenges]
    if not ids:
        return []
    enrollment_counts = dict(db.execute(select(ChallengeParticipant.challenge_id, func.count()).where(
        ChallengeParticipant.challenge_id.in_(ids), ChallengeParticipant.status != "REMOVED"
    ).group_by(ChallengeParticipant.challenge_id)).all())
    completion_counts = dict(db.execute(select(ChallengeParticipant.challenge_id, func.count()).where(
        ChallengeParticipant.challenge_id.in_(ids), ChallengeParticipant.completion_status == "COMPLETED"
    ).group_by(ChallengeParticipant.challenge_id)).all())
    return [_challenge_response(challenge, enrollment_counts.get(challenge.id, 0), completion_counts.get(challenge.id, 0)) for challenge in challenges]


def get_categories(db: Session) -> list[CategoryResponse]:
    rows = db.execute(select(Category, func.count(distinct(Challenge.id)).label("challenge_count"))
        .join(Category.challenges).where(*PUBLIC_CHALLENGE, Category.is_active.is_(True))
        .group_by(Category.id).order_by(Category.display_order, Category.name)).all()
    return [CategoryResponse.model_validate(category).model_copy(update={"challenge_count": count}) for category, count in rows]


def _recent_signal_maps(db: Session, cutoff: datetime):
    participants = db.scalars(select(ChallengeParticipant).where(ChallengeParticipant.started_at >= cutoff)).all()
    scores: dict[str, float] = {}
    counts: dict[str, int] = {}
    now = datetime.utcnow()
    for participant in participants:
        event_time = participant.last_activity_at or participant.started_at
        weight = math.exp(-max((now - event_time).total_seconds(), 0) / 604800)
        scores[participant.challenge_id] = scores.get(participant.challenge_id, 0) + weight
        counts[participant.challenge_id] = counts.get(participant.challenge_id, 0) + 1
        if participant.completion_status == "COMPLETED":
            scores[participant.challenge_id] += 2 * weight
    return scores, counts


def get_trending(db: Session, limit: int = 20) -> list[ChallengeResponse]:
    challenges = list(db.scalars(_public_query()).all())
    if not challenges:
        return []
    scores, counts = _recent_signal_maps(db, datetime.utcnow() - timedelta(days=30))
    ranked = sorted(challenges, key=lambda c: (scores.get(c.id, 0), counts.get(c.id, 0), c.published_at or c.created_at, c.id), reverse=True)
    return _responses(ranked[:limit], db)


def get_featured(db: Session) -> ChallengeResponse | None:
    trending = get_trending(db, 1)
    return trending[0] if trending else None


def get_new(db: Session, limit: int = 20) -> list[ChallengeResponse]:
    challenges = db.scalars(_public_query().where(Challenge.published_at.is_not(None))
        .order_by(Challenge.published_at.desc(), Challenge.id).limit(limit)).all()
    return _responses(list(challenges), db)


def get_legendary(db: Session, limit: int = 20) -> list[ChallengeResponse]:
    challenges = db.scalars(_public_query().where(Challenge.legendary.is_(True))
        .order_by(Challenge.published_at.desc(), Challenge.id).limit(limit)).all()
    return _responses(list(challenges), db)


def get_unexpected(db: Session, limit: int = 20, exclude_id: str | None = None) -> list[ChallengeResponse]:
    statement = _public_query()
    if exclude_id:
        statement = statement.where(Challenge.id != exclude_id)
    return _responses(list(db.scalars(statement.order_by(func.random()).limit(limit)).all()), db)


def get_recommended(db: Session, user: User, limit: int = 20) -> list[ChallengeResponse]:
    completed_ids = set(db.scalars(select(ChallengeParticipant.challenge_id).where(
        ChallengeParticipant.user_id == user.id, ChallengeParticipant.completion_status == "COMPLETED")).all())
    active_ids = set(db.scalars(select(ChallengeParticipant.challenge_id).where(
        ChallengeParticipant.user_id == user.id, ChallengeParticipant.status.in_(("ACCEPTED", "IN_PROGRESS", "PAUSED")))).all())
    saved_ids = set(db.scalars(select(SavedChallenge.challenge_id).where(SavedChallenge.user_id == user.id)).all())
    history_ids = completed_ids | active_ids
    if not history_ids and not saved_ids:
        return []
    history = list(db.scalars(select(Challenge).where(Challenge.id.in_(history_ids))).all()) if history_ids else []
    category_ids = {category.id for challenge in history for category in challenge.categories}
    difficulties = {challenge.difficulty_level for challenge in history}
    scored = []
    for challenge in db.scalars(_public_query()).all():
        if challenge.id in history_ids:
            continue
        score = (4 if challenge.id in saved_ids else 0) + (3 if category_ids.intersection(c.id for c in challenge.categories) else 0) + (2 if challenge.difficulty_level in difficulties else 0)
        if score:
            scored.append((score, challenge.published_at or challenge.created_at, challenge.id, challenge))
    scored.sort(key=lambda item: item[:3], reverse=True)
    return _responses([item[3] for item in scored[:limit]], db)


def get_top_nerds(db: Session, limit: int = 10) -> list[TopNerdResponse]:
    today = datetime.now(timezone.utc)
    monday = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    rows = db.execute(select(User, func.count(ChallengeParticipant.id), func.max(ChallengeParticipant.completed_at))
        .join(ChallengeParticipant, ChallengeParticipant.user_id == User.id).join(Challenge, Challenge.id == ChallengeParticipant.challenge_id)
        .where(*PUBLIC_USER, *PUBLIC_CHALLENGE, ChallengeParticipant.completion_status == "COMPLETED", ChallengeParticipant.completed_at >= monday)
        .group_by(User.id).order_by(func.count(ChallengeParticipant.id).desc(), func.max(ChallengeParticipant.completed_at).desc(), User.id).limit(limit)).all()
    return [TopNerdResponse(rank=index, user_id=user.id, username=user.username, display_name=user.display_name, avatar_url=user.avatar_url, completed_count=count, day_streak=user.day_streak) for index, (user, count, _) in enumerate(rows, 1)]


def get_recent_activity(db: Session, limit: int = 10) -> list[RecentActivityResponse]:
    rows = db.execute(select(Activity, User, Challenge).join(User, User.id == Activity.user_id).outerjoin(Challenge, Challenge.id == Activity.challenge_id)
        .where(*PUBLIC_USER, Activity.action.in_(VALID_ACTIVITY_ACTIONS), or_(Activity.challenge_id.is_(None), and_(*PUBLIC_CHALLENGE)))
        .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(limit)).all()
    return [RecentActivityResponse(id=activity.id, user_id=user.id, username=user.username, display_name=user.display_name, avatar_url=user.avatar_url, action=activity.action, challenge_id=challenge.id if challenge else None, challenge_title=challenge.title if challenge else None, challenge_slug=challenge.slug if challenge else None, created_at=activity.created_at.replace(tzinfo=timezone.utc)) for activity, user, challenge in rows]


def search_discover(db: Session, query: str, result_type: str, limit: int, offset: int):
    term = query.strip().lower()
    pattern, prefix = f"%{term}%", f"{term}%"
    users_statement = select(User).where(*PUBLIC_USER, or_(func.lower(User.username).like(pattern), func.lower(User.display_name).like(pattern)))
    user_rank = case((func.lower(User.username) == term, 0), (func.lower(User.display_name) == term, 0), (func.lower(User.username).like(prefix), 1), (func.lower(User.display_name).like(prefix), 1), else_=2)
    user_total = db.scalar(select(func.count()).select_from(users_statement.subquery())) or 0
    users = [] if result_type == "challenges" else db.scalars(users_statement.order_by(user_rank, User.username, User.id).offset(offset).limit(limit)).all()
    match = or_(func.lower(Challenge.title).like(pattern), func.lower(Challenge.short_description).like(pattern), func.lower(Challenge.full_description).like(pattern), func.lower(Category.name).like(pattern))
    challenge_rank = case((func.lower(Challenge.title) == term, 0), (func.lower(Challenge.title).like(prefix), 1), else_=2)
    matching_challenges = (
        select(
            Challenge.id.label("challenge_id"),
            challenge_rank.label("search_rank"),
            Challenge.published_at,
        )
        .select_from(Challenge)
        .join(Challenge.categories, isouter=True)
        .where(*PUBLIC_CHALLENGE, match)
        .distinct()
        .subquery()
    )
    challenge_total = db.scalar(select(func.count()).select_from(matching_challenges)) or 0
    challenge_statement = _public_query().join(
        matching_challenges,
        Challenge.id == matching_challenges.c.challenge_id,
    )
    challenges = [] if result_type == "users" else list(db.scalars(
        challenge_statement
        .order_by(
            matching_challenges.c.search_rank,
            Challenge.published_at.desc(),
            Challenge.id,
        )
        .offset(offset)
        .limit(limit)
    ).unique().all())
    return users, _responses(challenges, db), user_total, challenge_total
