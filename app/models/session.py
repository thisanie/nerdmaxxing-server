import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

class Session(Base):

    __tablename__ = "sessions"

    id : Mapped[str] = mapped_column(
            String(36),
            primary_key = True,
            default = lambda : str(uuid.uuid4()),
    )

    user_id : Mapped[str] = mapped_column(
            String(36),
            ForeignKey(
                "users.id",
                ondelete = "CASCADE",
            ),
            nullable = False,
            index = True,
    )

    user: Mapped["User"] = relationship(
        back_populates="sessions",
    )
    refresh_token_hash : Mapped[str] = mapped_column(
            String,
            nullable = False,
            unique = True,
    )

    expires_at : Mapped[datetime] = mapped_column(
            DateTime,
            nullable = False,

    )


    created_at : Mapped[datetime] = mapped_column(
            DateTime,
            default = datetime.utcnow,
            nullable = False,
    )

    last_used_at: Mapped[datetime | None ] = mapped_column(
            DateTime,
            nullable = True,
    )

    revoked_at : Mapped[datetime | None] = mapped_column(
            DateTime,
            nullable = True,
    )









