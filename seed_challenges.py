import json
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.time import utcnow
from app.models.challenge import Challenge, ChallengeResource
from app.models.user import User

SEED_USERNAME = "nerdmaxxing_seed"
SEED_USER_ID = "00000000-0000-0000-0000-000000000001"


def load_seed_data() -> dict:
    return json.loads((Path(__file__).parent / "seed_challenges.json").read_text(encoding="utf-8"))


def make_slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "challenge"
    return f"{base}-{uuid.uuid4().hex[:8]}"


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

        created = 0
        updated = 0
        for item in load_seed_data()["challenges"]:
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
                    image_url=item["image_url"],
                    short_description=item["short_description"],
                    full_description=item["full_description"],
                    creator_id=owner.id,
                    difficulty_level=item["difficulty_level"],
                    status="PUBLISHED",
                    visibility="PUBLIC",
                    verification_type=item["verification_type"],
                    published_at=utcnow(),
                )
                db.add(challenge)
                db.flush()
                created += 1
            else:
                challenge.image_url = item["image_url"]
                challenge.short_description = item["short_description"]
                challenge.full_description = item["full_description"]
                challenge.difficulty_level = item["difficulty_level"]
                challenge.verification_type = item["verification_type"]
                challenge.status = "PUBLISHED"
                challenge.visibility = "PUBLIC"
                challenge.published_at = challenge.published_at or utcnow()
                challenge.resources.clear()
                updated += 1

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
