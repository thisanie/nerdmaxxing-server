import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.aura import AuraTransaction
from app.models.user import User
from app.services.aura_service import award_aura


BULK_WELCOME_REFERENCE = "BULK_WELCOME_BONUS_V1"


def grant_welcome_bonus(db: Session, *, apply: bool) -> tuple[int, int]:
    users = db.scalars(select(User).where(User.is_deleted.is_(False))).all()
    if not users:
        return 0, 0

    user_ids = [user.id for user in users]
    already_rewarded = set(
        db.scalars(
            select(AuraTransaction.reference_id).where(
                AuraTransaction.reference_type.in_(
                    ("ACCOUNT_CREATION", BULK_WELCOME_REFERENCE)
                ),
                AuraTransaction.reference_id.in_(user_ids),
            )
        ).all()
    )
    eligible_users = [user for user in users if user.id not in already_rewarded]

    if apply and eligible_users:
        for user in eligible_users:
            award_aura(
                db,
                user,
                100,
                "Welcome bonus",
                reference_type=BULK_WELCOME_REFERENCE,
                reference_id=user.id,
            )
        db.commit()

    return len(eligible_users), len(already_rewarded)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grant the one-time 100 Aura welcome bonus.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit eligible rewards. Without this flag, only show the dry-run count.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        eligible_count, skipped_count = grant_welcome_bonus(db, apply=args.apply)

    if args.apply:
        print(f"Granted 100 Aura to {eligible_count} users; skipped {skipped_count} already rewarded.")
    else:
        print(f"Would grant 100 Aura to {eligible_count} users; skipped {skipped_count} already rewarded.")
        print("Run with --apply to commit these rewards.")


if __name__ == "__main__":
    main()