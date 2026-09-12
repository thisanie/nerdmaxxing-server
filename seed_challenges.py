import json
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utcnow
from app.models.challenge import Challenge, ChallengeResource
from app.models.category import Category
from app.models.user import User

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
FEATURED_TITLE = "Solve a Rubik's Cube Under 2 Minutes"
LEGENDARY_TITLES = {
    "Reach a 500 lb Combined Lift",
    "Complete a 100-Day Coding Streak",
    "Master the Splits",
}
SEED_EFFORT_RANGES = {
    "Solve a Rubik's Cube Under 2 Minutes": (300, 600),
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
    return settings.default_challenge_image_url


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

        db.commit()
        print(f"Seeded {created} new challenges and refreshed {updated} existing challenges.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
