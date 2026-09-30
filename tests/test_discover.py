from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models
from app.models.activity import Activity
from app.models.base import Base
from app.models.category import Category
from app.models.challenge import Challenge
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.services.discovery_service import (
    get_recent_activity,
    get_recommended,
    get_top_nerds,
    get_trending,
    search_discover,
)


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def challenge(identifier, title, published_at, categories=None):
    item = Challenge(
        id=identifier,
        title=title,
        slug=title.lower().replace(" ", "-"),
        image_url="https://example.com/image.png",
        short_description="A public challenge.",
        full_description="A public challenge with useful details.",
        creator_id="creator",
        status="PUBLISHED",
        visibility="PUBLIC",
        published_at=published_at,
    )
    item.categories = categories or []
    return item


def test_empty_discover_collections_are_empty():
    db = make_db()
    assert get_trending(db) == []
    assert get_recent_activity(db) == []
    assert get_top_nerds(db) == []


def test_trending_is_ranked_by_recent_activity():
    db = make_db()
    now = datetime.utcnow()
    first = challenge("first", "First", now)
    second = challenge("second", "Second", now)
    db.add_all([first, second, User(id="creator", username="creator")])
    db.flush()
    db.add_all([
        ChallengeParticipant(id="p1", challenge_id=first.id, user_id="creator", started_at=now, last_activity_at=now),
        ChallengeParticipant(id="p2", challenge_id=second.id, user_id="creator", started_at=now - timedelta(days=20), last_activity_at=now - timedelta(days=20)),
    ])
    db.commit()
    ranked = get_trending(db)
    assert [item.id for item in ranked[:2]] == ["first", "second"]


def test_public_search_filters_private_users_and_challenges_and_paginates():
    db = make_db()
    now = datetime.utcnow()
    public_user = User(id="public", username="jamie", display_name="Jamie M.")
    private_user = User(id="private", username="jamie_private", display_name="Jamie Private", is_private=True)
    visible = challenge("visible", "Jamie Chess", now)
    hidden = challenge("hidden", "Jamie Private", now)
    hidden.visibility = "PRIVATE"
    db.add_all([public_user, private_user, visible, hidden])
    db.commit()
    users, challenges, total_users, total_challenges = search_discover(db, "jamie", "all", 1, 0)
    assert [user.id for user in users] == ["public"]
    assert [item.id for item in challenges] == ["visible"]
    assert total_users == 1
    assert total_challenges == 1


def test_weekly_leaderboard_excludes_old_completions_and_private_users():
    db = make_db()
    now = datetime.utcnow()
    user = User(id="user", username="weekly", day_streak=6)
    creator = User(id="creator", username="creator")
    public_challenge = challenge("weekly-challenge", "Weekly", now)
    old_challenge = challenge("old-challenge", "Old", now)
    db.add_all([user, creator, public_challenge, old_challenge])
    db.flush()
    start_of_week = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    db.add_all([
        ChallengeParticipant(id="current", challenge_id=public_challenge.id, user_id=user.id, completion_status="COMPLETED", completed_at=start_of_week + timedelta(hours=1)),
        ChallengeParticipant(id="old", challenge_id=old_challenge.id, user_id=user.id, completion_status="COMPLETED", completed_at=start_of_week - timedelta(minutes=1)),
    ])
    db.commit()
    leaderboard = get_top_nerds(db)
    assert [(item.user_id, item.completed_count, item.day_streak) for item in leaderboard] == [("user", 1, 6)]


def test_recommendations_exclude_completed_and_active_challenges():
    db = make_db()
    now = datetime.utcnow()
    user = User(id="user", username="learner")
    creator = User(id="creator", username="creator")
    category = Category(id="strategy", name="Strategy", slug="strategy")
    completed = challenge("completed", "Completed", now, [category])
    active = challenge("active", "Active", now, [category])
    candidate = challenge("candidate", "Candidate", now, [category])
    db.add_all([user, creator, category, completed, active, candidate])
    db.flush()
    db.add_all([
        ChallengeParticipant(id="done", challenge_id=completed.id, user_id=user.id, completion_status="COMPLETED"),
        ChallengeParticipant(id="working", challenge_id=active.id, user_id=user.id, status="IN_PROGRESS"),
    ])
    db.commit()
    assert [item.id for item in get_recommended(db, user)] == ["candidate"]


def test_recent_activity_is_newest_first_and_public_only():
    db = make_db()
    now = datetime.utcnow()
    public_user = User(id="public", username="public")
    private_user = User(id="private", username="private", is_private=True)
    visible = challenge("visible", "Visible", now)
    hidden = challenge("hidden", "Hidden", now)
    hidden.status = "DRAFT"
    db.add_all([public_user, private_user, visible, hidden])
    db.flush()
    db.add_all([
        Activity(id="old", user_id=public_user.id, challenge_id=visible.id, action="JOINED_CHALLENGE", created_at=now - timedelta(hours=1)),
        Activity(id="new", user_id=public_user.id, challenge_id=visible.id, action="COMPLETED_CHALLENGE", created_at=now),
        Activity(id="private", user_id=private_user.id, challenge_id=visible.id, action="JOINED_CHALLENGE", created_at=now + timedelta(hours=1)),
        Activity(id="draft", user_id=public_user.id, challenge_id=hidden.id, action="JOINED_CHALLENGE", created_at=now + timedelta(hours=2)),
    ])
    db.commit()
    assert [item.id for item in get_recent_activity(db)] == ["new", "old"]
