from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


is_sqlite = settings.database_url.startswith("sqlite")
engine_options = {
        "connect_args": {"check_same_thread": False} if is_sqlite else {},
}

if not is_sqlite:
        engine_options.update(
                pool_pre_ping=True,
                pool_recycle=300,
        )


engine = create_engine(settings.database_url, **engine_options)

# Neon transaction poolers can terminate connections during DDL. Keep pooled
# connections for requests, but use the direct endpoint for startup schema work.
schema_database_url = settings.database_url.replace("-pooler.", ".")
schema_engine = (
        create_engine(schema_database_url, **engine_options)
        if schema_database_url != settings.database_url
        else engine
)


SessionLocal = sessionmaker(
        autocommit = False,
        autoflush = False,
        bind = engine,
        
        )


def ensure_local_schema(database_engine= schema_engine) -> None:
        """Apply additive schema changes until a versioned migration system exists."""
        with database_engine.begin() as connection:
                inspector = inspect(connection)
                user_columns = {column["name"] for column in inspector.get_columns("users")}
                timestamp_type = "DATETIME" if connection.dialect.name == "sqlite" else "TIMESTAMP"
                user_additions = {
                        "bio": "VARCHAR(500)",
                        "aura_points": "INTEGER NOT NULL DEFAULT 0",
                        "day_streak": "INTEGER NOT NULL DEFAULT 0",
                        "last_progress_at": timestamp_type,
                        "is_deleted": "BOOLEAN NOT NULL DEFAULT FALSE",
                        "is_suspended": "BOOLEAN NOT NULL DEFAULT FALSE",
                        "is_private": "BOOLEAN NOT NULL DEFAULT FALSE",
                }
                for name, definition in user_additions.items():
                        if user_columns and name not in user_columns:
                                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
                connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_users_public_ranking "
                        "ON users (is_deleted, is_suspended, is_private, aura_points)"
                ))
                connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_aura_transactions_created_user "
                        "ON aura_transactions (created_at, user_id)"
                ))
                connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_challenge_participants_completion_period "
                        "ON challenge_participants (completion_status, completed_at, user_id)"
                ))

                inspector = inspect(connection)
                columns = {column["name"] for column in inspector.get_columns("challenges")}
                additions = {
                        "image_url": "VARCHAR(2048) NOT NULL DEFAULT ''",
                        "estimated_duration_minutes": "INTEGER",
                        "image_key": "TEXT",
                        "featured": "BOOLEAN NOT NULL DEFAULT FALSE",
                        "legendary": "BOOLEAN NOT NULL DEFAULT FALSE",
                }
                for name, definition in additions.items():
                        if columns and name not in columns:
                                connection.execute(text(f"ALTER TABLE challenges ADD COLUMN {name} {definition}"))

                inspector = inspect(connection)
                milestone_columns = {column["name"] for column in inspector.get_columns("challenge_milestones")}
                if milestone_columns and "resources" not in milestone_columns:
                        connection.execute(text("ALTER TABLE challenge_milestones ADD COLUMN resources JSON"))

                inspector = inspect(connection)
                notification_columns = {column["name"] for column in inspector.get_columns("notifications")}
                for name, definition in {
                        "discussion_id": "VARCHAR(36)",
                        "reply_id": "VARCHAR(36)",
                        "group_invitation_id": "VARCHAR(36)",
                        "challenge_id": "VARCHAR(36)",
                        "challenge_slug": "VARCHAR(180)",
                        "actor_username": "VARCHAR(24)",
                        "actor_name": "VARCHAR(100)",
                        "group_id": "VARCHAR(36)",
                        "group_message_id": "VARCHAR(36)",
                        "challenge_title": "VARCHAR(160)",
                }.items():
                        if notification_columns and name not in notification_columns:
                                connection.execute(text(f"ALTER TABLE notifications ADD COLUMN {name} {definition}"))

                if (
                        connection.dialect.name != "sqlite"
                        and notification_columns
                        and "group_invitation_id" not in notification_columns
                ):
                        connection.execute(text(
                                "ALTER TABLE notifications ADD CONSTRAINT "
                                "notifications_group_invitation_id_fkey FOREIGN KEY "
                                "(group_invitation_id) REFERENCES group_challenge_invitations(id) ON DELETE CASCADE"
                        ))

                inspector = inspect(connection)
                group_message_columns = {column["name"] for column in inspector.get_columns("group_messages")}
                for name, definition in {
                        "type": "VARCHAR(40) NOT NULL DEFAULT 'TEXT'",
                        "challenge_invitation_id": "VARCHAR(36)",
                }.items():
                        if group_message_columns and name not in group_message_columns:
                                connection.execute(text(f"ALTER TABLE group_messages ADD COLUMN {name} {definition}"))


