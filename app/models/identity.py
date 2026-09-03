import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from app.core.time import utcnow
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base
if TYPE_CHECKING:
    from app.models.user import User



class Identity(Base):

    __tablename__ = "identities"

    __table_args__ = (
            UniqueConstraint(
                "provider",
                "provider_user_id",
                name = "uq_identity_provider_user",
            ),
    )

    id:Mapped[str] = mapped_column(
            String(36),
            primary_key = True,
            default = lambda: str(uuid.uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
            String(36),
            ForeignKey(
                "users.id",
                ondelete = "CASCADE",
            ),
            nullable = False,
            index = False,
    )

    user: Mapped["User"] = relationship(
 
        back_populates="identities",
    ) 
    provider: Mapped[str] = mapped_column(
            String(30),
            nullable= False,
    )

    provider_user_id : Mapped[str] = mapped_column(
            String(256),
            nullable = True,
    )

    provider_email : Mapped[str | None] = mapped_column(
            String(255),
            nullable = True,
    )

    created_at : Mapped[datetime] = mapped_column(
            DateTime,
            default = utcnow,
            nullable = False,

    )

    updated_at : Mapped[datetime] = mapped_column(
            DateTime,
            default = datetime.utcnow,
            onupdate = datetime.utcnow,
            nullable = False,

    )













