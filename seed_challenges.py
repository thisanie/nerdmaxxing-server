import json
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utcnow
from app.models.challenge import Challenge, ChallengeMilestone, ChallengeResource
from app.models.category import Category
from app.models.user import User
from app.models.integration import ExternalAccountConnection

SEED_USERNAME = "nerdmaxxing_seed"
SEED_USER_ID = "00000000-0000-0000-0000-000000000001"
SEED_CATEGORIES = [
    ("brain-memory", "Brain & Memory", "🧠"),
    ("technology", "Technology", "💻"),
    ("games-strategy", "Games & Strategy", "♟"),
    ("knowledge", "Knowledge", "📚"),
    ("creative", "Creative", "🎨"),
    ("music", "Music", "🎵"),
    ("languages", "Languages", "🗣"),
    ("physical", "Physical", "🏃"),
    ("science", "Science", "🔬"),
    ("practical", "Practical", "🛠"),
]

CATEGORY_RULES = {
    "technology": ("typing", "coding"),
    "languages": ("spanish", "sign language"),
    "physical": ("run ", "push-ups", "splits", "plank", "lift", "swim", "handstand"),
    "brain-memory": ("rubik", "sudoku", "meditate"),
    "knowledge": ("books",),
    "music": ("guitar",),
    "practical": ("sugar", "save $", "cook"),
    "creative": ("juggle",),
}
FEATURED_TITLE = "Solve a Rubik's Cube in Under 2 Minutes"
LEGENDARY_TITLES = {
    "Reach a 500 lb Combined Lift",
    "Complete a 100-Day Coding Streak",
    "Master the Splits",
}
SEED_EFFORT_RANGES = {
    "Solve a Rubik's Cube in Under 2 Minutes": (600, 1800),
    "Achieve 60 WPM Typing Speed": (180, 300),
    "Learn Conversational Spanish": (1200, 2400),
    "Run a 5K": (480, 720),
    "Do 50 Push-Ups in a Row": (240, 480),
    "Master the Splits": (1200, 2400),
    "Meditate for 30 Days Straight": (300, 600),
    "Read 12 Books in a Year": (1200, 2400),
    "Learn to Juggle 3 Balls": (120, 300),
    "Hold a Plank for 3 Minutes": (180, 360),
    "Learn to Play a Song on Guitar": (600, 1200),
    "Complete a 30-Day No-Sugar Challenge": (180, 300),
    "Reach a 500 lb Combined Lift": (1200, 2400),
    "Learn Basic Sign Language": (600, 1200),
    "Complete a 100-Day Coding Streak": (1000, 2000),
    "Swim 1500m Without Stopping": (600, 1200),
    "Learn to Solve a Sudoku in Under 5 Minutes": (120, 300),
    "Do a Handstand for 30 Seconds": (600, 1200),
    "Save $1000 in 90 Days": (300, 600),
    "Cook 20 New Recipes": (1200, 2400),
    "Learn 500 Chess Opening Moves": (1200, 2400),
}
SEED_METRICS = {
    "Solve a Rubik's Cube in Under 2 Minutes": (120, "SECONDS"),
    "Achieve 60 WPM Typing Speed": (60, "WPM"),
    "Learn Conversational Spanish": (5, "MINUTES"),
    "Run a 5K": (5, "KILOMETERS"),
    "Do 50 Push-Ups in a Row": (50, "REPETITIONS"),
    "Master the Splits": (180, "DEGREES"),
    "Meditate for 30 Days Straight": (30, "DAYS"),
    "Read 12 Books in a Year": (12, "BOOKS"),
    "Learn to Juggle 3 Balls": (60, "SECONDS"),
    "Hold a Plank for 3 Minutes": (180, "SECONDS"),
    "Learn to Play a Song on Guitar": (1, "SONGS"),
    "Complete a 30-Day No-Sugar Challenge": (30, "DAYS"),
    "Reach a 500 lb Combined Lift": (500, "POUNDS"),
    "Learn Basic Sign Language": (100, "SIGNS"),
    "Complete a 100-Day Coding Streak": (100, "DAYS"),
    "Swim 1500m Without Stopping": (1500, "METERS"),
    "Learn to Solve a Sudoku in Under 5 Minutes": (5, "MINUTES"),
    "Do a Handstand for 30 Seconds": (30, "SECONDS"),
    "Save $1000 in 90 Days": (1000, "DOLLARS"),
    "Cook 20 New Recipes": (20, "RECIPES"),
    "Learn 500 Chess Opening Moves": (500, "MOVES"),
}

SPECIAL_CHALLENGES = [
    {
        "slug": "daily-reading",
        "title": "Read for 30 minutes",
        "short_description": "Read for thirty focused minutes.",
        "full_description": "Spend thirty minutes reading and explain what you learned.",
        "verification_type": "SELF_REPORTED",
        "verification_config": {
            "kind": "SELF_REPORTED",
            "instructions": "Read for thirty minutes and explain what you learned.",
            "required_runs": 1,
            "evidence": {
                "allowed_types": ["TEXT"],
                "requires_file": False,
                "requires_explanation": True,
                "max_file_size_bytes": None,
                "max_duration_seconds": None,
                "allowed_mime_types": [],
            },
            "completion": {"mode": "SELF_CONFIRMATION", "requires_review": False},
        },
    },
    {
        "slug": "one-minute-plank",
        "title": "Hold a one-minute plank",
        "short_description": "Hold a plank continuously for one minute.",
        "full_description": "Record one continuous attempt showing a one-minute plank with safe form.",
        "verification_type": "VIDEO_UPLOAD",
        "requirements": [{
            "metric_key": "duration",
            "key": "duration",
            "label": "Plank duration",
            "operator": "AT_LEAST",
            "value": 60,
            "unit": "seconds",
        }],
        "verification_config": {
            "kind": "VIDEO_UPLOAD",
            "instructions": "Record one continuous attempt showing your full body and the timer.",
            "required_runs": 1,
            "requirements": [{
                "key": "duration",
                "label": "Plank duration",
                "operator": "AT_LEAST",
                "value": 60,
                "unit": "seconds",
            }],
            "evidence": {
                "allowed_types": ["VIDEO"],
                "requires_file": True,
                "requires_explanation": True,
                "max_file_size_bytes": 52428800,
                "max_duration_seconds": 120,
                "allowed_mime_types": ["video/mp4", "video/quicktime"],
            },
            "completion": {"mode": "REVIEW", "requires_review": True},
        },
        "metrics": [{
            "key": "duration",
            "label": "Plank duration",
            "kind": "DURATION",
            "unit": "seconds",
            "target": 60,
            "direction": "AT_LEAST",
            "is_primary": True,
            "format": "INTEGER",
        }],
    },
    {
        "slug": "chess-rated-game",
        "title": "Complete a rated Chess.com game",
        "short_description": "Complete one rated game on Chess.com.",
        "full_description": "Connect Chess.com and complete a rated game.",
        "verification_type": "EXTERNAL_ACCOUNT",
        "requirements": [{
            "metric_key": "rated_games",
            "key": "rated_games",
            "label": "Rated games completed",
            "operator": "AT_LEAST",
            "value": 1,
            "unit": "game",
        }],
        "verification_config": {
            "kind": "EXTERNAL_ACCOUNT",
            "instructions": "Connect Chess.com and complete a rated game.",
            "required_runs": 1,
            "provider": {
                "id": "chess_com",
                "name": "Chess.com",
                "connect_url": "/api/v1/integrations/chess_com/connect",
            },
            "requirements": [{
                "key": "rated_games",
                "label": "Rated games completed",
                "operator": "AT_LEAST",
                "value": 1,
                "unit": "game",
            }],
            "evidence": {
                "allowed_types": ["ACCOUNT_CONNECTION"],
                "requires_file": False,
                "requires_explanation": False,
                "max_file_size_bytes": None,
                "max_duration_seconds": None,
                "allowed_mime_types": [],
            },
            "completion": {"mode": "AUTOMATIC", "requires_review": False},
        },
    },
]


def load_seed_data() -> dict:
    return json.loads((Path(__file__).parent / "seed_challenges.json").read_text(encoding="utf-8"))


def make_slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "challenge"
    return f"{base}-{uuid.uuid4().hex[:8]}"


def category_slugs_for(title: str) -> list[str]:
    normalized_title = title.lower()
    matches = [
        slug
        for slug, keywords in CATEGORY_RULES.items()
        if any(keyword in normalized_title for keyword in keywords)
    ]
    return matches[:3] or ["knowledge"]


def image_url_for(item: dict) -> str:
    return item.get("image_url") or settings.default_challenge_image_url


def seed_special_challenges(db, owner: User) -> None:
    for item in SPECIAL_CHALLENGES:
        challenge = db.scalar(select(Challenge).where(Challenge.slug == item["slug"]))
        if challenge is None:
            challenge = Challenge(
                slug=item["slug"],
                title=item["title"],
                image_url=settings.default_challenge_image_url,
                short_description=item["short_description"],
                full_description=item["full_description"],
                creator_id=owner.id,
                difficulty_level="BEGINNER",
                status="PUBLISHED",
                visibility="PUBLIC",
                published_at=utcnow(),
            )
            db.add(challenge)
        challenge.title = item["title"]
        challenge.short_description = item["short_description"]
        challenge.full_description = item["full_description"]
        challenge.verification_type = item["verification_type"]
        challenge.verification_config = item["verification_config"]
        challenge.requirements = item.get("requirements")
        challenge.metrics = item.get("metrics")
        challenge.required_runs = 1
        challenge.verification_instructions = item["verification_config"]["instructions"]
        challenge.status = "PUBLISHED"
        challenge.visibility = "PUBLIC"
        if item["slug"] == "chess-rated-game":
            connection = db.scalar(
                select(ExternalAccountConnection).where(
                    ExternalAccountConnection.user_id == owner.id,
                    ExternalAccountConnection.provider_id == "chess_com",
                )
            )
            if connection is None:
                db.add(ExternalAccountConnection(
                    user_id=owner.id,
                    provider_id="chess_com",
                    provider_user_id="seed-chess-user-1",
                    username="demo_player",
                ))


def seed() -> None:
    from app.main import app

    db = SessionLocal()
    try:
        owner = db.get(User, SEED_USER_ID)
        if owner is None:
            owner = User(
                id=SEED_USER_ID,
                username=SEED_USERNAME,
                username_normalized=SEED_USERNAME,
                display_name="NerdMaxxing",
            )
            db.add(owner)
            db.flush()

        for display_order, (slug, name, icon) in enumerate(SEED_CATEGORIES):
            category = db.scalar(select(Category).where(Category.slug == slug))
            if category is None:
                db.add(Category(slug=slug, name=name, icon=icon, display_order=display_order))
        db.flush()
        categories_by_slug = {
            category.slug: category
            for category in db.scalars(select(Category)).all()
        }

        created = 0
        updated = 0
        for item in load_seed_data()["challenges"]:
            effort_min, effort_max = SEED_EFFORT_RANGES[item["title"]]
            target_value, target_unit = SEED_METRICS[item["title"]]
            challenge = db.scalar(
                select(Challenge).where(
                    Challenge.title == item["title"],
                    Challenge.creator_id == owner.id,
                )
            )
            if challenge is None:
                challenge = Challenge(
                    title=item["title"],
                    slug=make_slug(item["title"]),
                    image_url=image_url_for(item),
                    short_description=item["short_description"],
                    full_description=item["full_description"],
                    creator_id=owner.id,
                    difficulty_level=item["difficulty_level"],
                    estimated_effort_min_minutes=effort_min,
                    estimated_effort_max_minutes=effort_max,
                    status="PUBLISHED",
                    visibility="PUBLIC",
                    verification_type=item["verification_type"],
                    target_value=target_value,
                    target_unit=target_unit,
                    min_accuracy_percent=95 if target_unit == "WPM" else None,
                    required_runs=item.get("required_runs", 3 if item["title"] == FEATURED_TITLE else 1),
                    verification_instructions=item.get("verification_instructions", "Submit evidence that demonstrates the target metric and satisfies the challenge requirements."),
                    metrics=item.get("metrics"),
                    requirements=item.get("requirements"),
                    estimated_duration_minutes=10080,
                    featured=item["title"] == FEATURED_TITLE,
                    legendary=item["title"] in LEGENDARY_TITLES,
                    published_at=utcnow(),
                )
                db.add(challenge)
                db.flush()
                created += 1
            else:
                challenge.image_url = image_url_for(item)
                challenge.short_description = item["short_description"]
                challenge.full_description = item["full_description"]
                challenge.difficulty_level = item["difficulty_level"]
                challenge.estimated_effort_min_minutes = effort_min
                challenge.estimated_effort_max_minutes = effort_max
                challenge.verification_type = item["verification_type"]
                challenge.target_value = target_value
                challenge.target_unit = target_unit
                challenge.min_accuracy_percent = 95 if target_unit == "WPM" else None
                challenge.required_runs = item.get("required_runs", 3 if item["title"] == FEATURED_TITLE else 1)
                challenge.verification_instructions = item.get("verification_instructions", "Submit evidence that demonstrates the target metric and satisfies the challenge requirements.")
                challenge.metrics = item.get("metrics")
                challenge.requirements = item.get("requirements")
                challenge.estimated_duration_minutes = challenge.estimated_duration_minutes or 10080
                challenge.featured = item["title"] == FEATURED_TITLE
                challenge.legendary = item["title"] in LEGENDARY_TITLES
                challenge.status = "PUBLISHED"
                challenge.visibility = "PUBLIC"
                challenge.published_at = utcnow()
                challenge.resources.clear()
                updated += 1

            challenge.categories = [
                categories_by_slug[slug]
                for slug in category_slugs_for(item["title"])
            ]

            if not challenge.resources:
                challenge.resources = [
                    ChallengeResource(
                        title=resource["title"],
                        url=resource["url"],
                        resource_type=resource["resource_type"],
                        rationale=resource["rationale"],
                        order_index=index,
                    )
                    for index, resource in enumerate(item["resources"])
                ]
            db.flush()
            resources_by_order = {
                resource.order_index: resource
                for resource in challenge.resources
            }
            challenge.milestones.clear()
            default_milestone = {
                "title": f"Reach {target_value:g} {target_unit}",
                "description": "Meet the challenge target metric using the challenge resources.",
                "target_value": target_value,
                "resource_order_indexes": list(resources_by_order),
            }
            challenge.milestones = [
                ChallengeMilestone(
                    order_index=index,
                    title=milestone["title"],
                    description=milestone["description"],
                    target_value=milestone["target_value"],
                    resources=[
                        {
                            "resource_id": resources_by_order[resource_index].id,
                            "required": item["resources"][resource_index].get("required", True),
                        }
                        for resource_index in milestone.get("resource_order_indexes", [])
                        if resource_index in resources_by_order
                    ],
                )
                for index, milestone in enumerate(
                    item.get("milestones", [default_milestone]),
                    start=1,
                )
            ]

        seed_special_challenges(db, owner)

        db.commit()
        print(f"Seeded {created} new challenges and refreshed {updated} existing challenges.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
