import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.identity import Identity
    from app.models.session import Session





class User(Base):

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
            String(36),
            primary_key = True,
            default = lambda : str(uuid.uuid4()),

    )

    username: Mapped[str | None] = mapped_column(
            String(24),
            nullable= True,
    )


    username_normalized: Mapped[str | None] = mapped_column(
            String(24),
            nullable = True,
            unique = True,
            index = True,
    )


    display_name : Mapped[str | None] = mapped_column(
            String(100),
            nullable = True,
    )


    avatar_url : Mapped[str | None] = mapped_column(
            String,
            nullable = True,
    )

    bio: Mapped[str | None] = mapped_column(String(500), nullable=True)

    aura_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


    created_at : Mapped[datetime] = mapped_column(
            DateTime,
            default = datetime.utcnow,
            nullable = False,
    )

    updated_at : Mapped[datetime] = mapped_column(
            DateTime,
            default = datetime.utcnow,
            onupdate = datetime.utcnow,
            nullable = False,

    )

    identities : Mapped[list["Identity"]] = relationship(
        
        back_populates="user",
        cascade="all, delete-orphan"
    )

    sessions : Mapped[list["Session"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
