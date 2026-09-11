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
                timestamp_type = "DATETIME" if connection.dialect.name == "sqlite" else "TIMESTAMP"
                user_additions = {
                        "bio": "VARCHAR(500)",
                        "aura_points": "INTEGER NOT NULL DEFAULT 0",
                        "day_streak": "INTEGER NOT NULL DEFAULT 0",
                        "last_progress_at": timestamp_type,
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


