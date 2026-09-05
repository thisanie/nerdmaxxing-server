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
        """Apply small local SQLite additions until migrations are introduced."""
        if not settings.database_url.startswith("sqlite"):
                return

        with engine.begin() as connection:
                columns = {column["name"] for column in inspect(connection).get_columns("challenges")}
                if columns and "image_url" not in columns:
                        connection.execute(
                                text("ALTER TABLE challenges ADD COLUMN image_url VARCHAR(2048) NOT NULL DEFAULT ''")
                        )


