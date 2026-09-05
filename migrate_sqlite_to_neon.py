import argparse

from sqlalchemy import create_engine, delete, select

import app.models
from app.core.config import settings
from app.models.base import Base


def make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def migrate(source_url: str, clear_destination: bool) -> None:
    source_engine = make_engine(source_url)
    destination_engine = make_engine(settings.database_url)
    tables = list(Base.metadata.sorted_tables)

    with destination_engine.begin() as destination:
        Base.metadata.create_all(destination_engine)

        if clear_destination:
            for table in reversed(tables):
                destination.execute(delete(table))
        else:
            populated_tables = [
                table.name
                for table in tables
                if destination.execute(select(table).limit(1)).first() is not None
            ]
            if populated_tables:
                names = ", ".join(populated_tables)
                raise RuntimeError(
                    f"Neon already contains data in: {names}. "
                    "Re-run with --clear only if replacing it is intended."
                )

        with source_engine.connect() as source:
            for table in tables:
                rows = source.execute(select(table)).mappings().all()
                if rows:
                    destination.execute(table.insert(), [dict(row) for row in rows])
                print(f"{table.name}: copied {len(rows)} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copy the local SQLite database to Neon.")
    parser.add_argument(
        "--source",
        default="sqlite:///./nerdmaxxing.db",
        help="SQLAlchemy URL for the source database",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing Neon rows before copying",
    )
    args = parser.parse_args()
    migrate(args.source, args.clear)