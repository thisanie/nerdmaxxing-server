import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChallengeInvitation(Base):
    __tablename__ = "challenge_invitations"
    __table_args__ = (
        UniqueConstraint(
            "challenge_id",
            "inviter_id",
            "invitee_id",
            name="uq_challenge_direct_invitation",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    challenge_id: Mapped[str] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inviter_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invitee_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GroupChallengeInvitation(Base):
    __tablename__ = "group_challenge_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    challenge_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invited_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GroupChallengeInvitationResponse(Base):
    __tablename__ = "group_challenge_invitation_responses"
    __table_args__ = (
        UniqueConstraint("invitation_id", "user_id", name="uq_group_invitation_response_member"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    invitation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("group_challenge_invitations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    response: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(24), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invitation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    group_invitation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("group_challenge_invitations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    discussion_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reply_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    challenge_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    challenge_slug: Mapped[str | None] = mapped_column(String(180), nullable=True)
    challenge_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    group_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    is_read: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PushToken(Base):
    __tablename__ = "push_tokens"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
