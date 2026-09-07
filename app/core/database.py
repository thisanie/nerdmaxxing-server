from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


engine = create_engine(
        settings.database_url,
        connect_args=(
                {"check_same_thread": False}
                if settings.database_url.startswith("sqlite")
                else {}
                ),
        )


SessionLocal = sessionmaker(
        autocommit = False,
        autoflush = False,
        bind = engine,
        
        )


def ensure_local_schema() -> None:
        """Apply additive schema changes until a versioned migration system exists."""
        with engine.begin() as connection:
                inspector = inspect(connection)
                user_columns = {column["name"] for column in inspector.get_columns("users")}
                user_additions = {
                        "bio": "VARCHAR(500)",
                        "aura_points": "INTEGER NOT NULL DEFAULT 0",
                }
                for name, definition in user_additions.items():
                        if user_columns and name not in user_columns:
                                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))

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


