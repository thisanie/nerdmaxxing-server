import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.challenge import Challenge
from app.models.group import Group, GroupMembership, GroupMessage
from app.models.follow import UserFollow
from app.models.invitation import (
    ChallengeInvitation,
    GroupChallengeInvitation,
    GroupChallengeInvitationResponse,
    Notification,
)
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.invitation import (
    ChallengeInvitationCreate,
    ChallengeInvitationResponse,
    ChallengeInviteLinkPreviewResponse,
    ChallengeInviteLinkResponse,
    NotificationResponse,
)
from app.schemas.participation import ParticipationResponse
from app.schemas.group import (
    GroupChallengeInvitationCreate,
    GroupInvitationCreatedResponse,
    GroupChallengeInvitationSummary,
    GroupInvitationResponseCounts,
    GroupInvitationResponseRequest,
)
from app.api.groups import group_message_response
from app.services.push_notification_service import send_notification_push


router = APIRouter(prefix="/api/v1", tags=["Invitations"])
ACTIVE_STATUSES = ("ACCEPTED", "IN_PROGRESS")
INVITE_LINK_TTL = timedelta(days=30)
GROUP_INVITATION_TTL = timedelta(days=30)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_joinable_challenge(db: Session, slug: str) -> Challenge:
    challenge = db.scalar(
        select(Challenge).where(
            Challenge.slug == slug,
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")
    return challenge


def require_participant(db: Session, challenge_id: str, user_id: str) -> None:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.status != "REMOVED",
        )
    )
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Join the challenge before inviting someone.",
        )


def invitation_response(
    invitation: ChallengeInvitation, challenge: Challenge, inviter: User
) -> ChallengeInvitationResponse:
    return ChallengeInvitationResponse(
        id=invitation.id,
        challenge_id=invitation.challenge_id,
        inviter_id=invitation.inviter_id,
        invitee_id=invitation.invitee_id,
        status=invitation.status,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        challenge_title=challenge.title,
        inviter=inviter,
    )


def ensure_can_accept(db: Session, challenge_id: str, user_id: str) -> None:
    if db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.user_id == user_id,
        )
    ) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already joined this challenge.",
        )
    active_count = db.scalar(
        select(func.count())
        .select_from(ChallengeParticipant)
        .where(
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.status.in_(ACTIVE_STATUSES),
        )
    ) or 0
    if active_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have reached the limit of five active challenges.",
        )


def group_response_counts(db: Session, invitation_id: str) -> GroupInvitationResponseCounts:
    counts = {
        response: db.scalar(
            select(func.count())
            .select_from(GroupChallengeInvitationResponse)
            .where(
                GroupChallengeInvitationResponse.invitation_id == invitation_id,
                GroupChallengeInvitationResponse.response == response,
            )
        ) or 0
        for response in ("PENDING", "ACCEPTED", "DECLINED")
    }
    return GroupInvitationResponseCounts(
        pending=counts["PENDING"], accepted=counts["ACCEPTED"], declined=counts["DECLINED"]
    )


def close_expired_group_invitation(db: Session, invitation: GroupChallengeInvitation) -> None:
    if invitation.status == "OPEN" and invitation.expires_at and invitation.expires_at <= datetime.utcnow():
        invitation.status = "EXPIRED"


def group_invitation_counts_dict(db: Session, invitation_id: str) -> dict[str, int]:
    return group_response_counts(db, invitation_id).model_dump()


def accept_invitation(
    db: Session, invitation: ChallengeInvitation, user_id: str
) -> ChallengeParticipant:
    if invitation.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This invitation is no longer available.",
        )
    if invitation.expires_at is not None and invitation.expires_at <= datetime.utcnow():
        invitation.status = "EXPIRED"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invitation has expired.",
        )
    if invitation.invitee_id is not None and invitation.invitee_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation is for another user.")
    if invitation.inviter_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot accept your own invitation.")

    ensure_can_accept(db, invitation.challenge_id, user_id)
    participant = ChallengeParticipant(challenge_id=invitation.challenge_id, user_id=user_id)
    invitation.status = "ACCEPTED"
    invitation.accepted_at = datetime.utcnow()
    db.query(Notification).filter(
        Notification.invitation_id == invitation.id,
        Notification.user_id == user_id,
    ).update({Notification.is_read: True}, synchronize_session=False)
    db.add(participant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have already joined this challenge.")
    db.refresh(participant)
    return participant


@router.post(
    "/challenges/{slug}/invitations",
    response_model=ChallengeInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite_follower(
    slug: str,
    payload: ChallengeInvitationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeInvitationResponse:
    challenge = get_joinable_challenge(db, slug)
    require_participant(db, challenge.id, current_user.id)
    if payload.invitee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot invite yourself.")

    invitee = db.get(User, payload.invitee_id)
    if invitee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if db.get(UserFollow, {"follower_id": invitee.id, "following_id": current_user.id}) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only invite your followers.")
    if db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.user_id == invitee.id,
            ChallengeParticipant.status != "REMOVED",
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This user has already joined the challenge.")
    if db.scalar(
        select(ChallengeInvitation).where(
            ChallengeInvitation.challenge_id == challenge.id,
            ChallengeInvitation.invitee_id == invitee.id,
            ChallengeInvitation.status == "PENDING",
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This user already has a pending invitation.")

    invitation = db.scalar(
        select(ChallengeInvitation).where(
            ChallengeInvitation.challenge_id == challenge.id,
            ChallengeInvitation.inviter_id == current_user.id,
            ChallengeInvitation.invitee_id == invitee.id,
        )
    )
    if invitation is not None and invitation.status == "ACCEPTED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This invitation was already accepted.")
    if invitation is None:
        invitation = ChallengeInvitation(
            challenge_id=challenge.id,
            inviter_id=current_user.id,
            invitee_id=invitee.id,
        )
        db.add(invitation)
        db.flush()
    else:
        db.query(Notification).filter(
            Notification.invitation_id == invitation.id,
        ).delete(synchronize_session=False)
        invitation.status = "PENDING"
        invitation.created_at = datetime.utcnow()
        invitation.accepted_at = None

    notification = Notification(
            user_id=invitee.id,
            notification_type="CHALLENGE_INVITATION",
            title="New challenge invitation",
            body=f"{current_user.username or current_user.display_name or 'Someone'} challenged you to {challenge.title}.",
            invitation_id=invitation.id,
        )
    db.add(notification)
    db.commit()
    db.refresh(invitation)
    send_notification_push(db, notification)
    return invitation_response(invitation, challenge, current_user)


@router.post(
    "/challenges/{slug}/group-invitations",
    response_model=GroupInvitationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_group_invitation(
    slug: str,
    payload: GroupChallengeInvitationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupInvitationCreatedResponse:
    challenge = get_joinable_challenge(db, slug)
    group = db.get(Group, payload.group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    if db.scalar(
        select(GroupMembership.id).where(
            GroupMembership.group_id == group.id,
            GroupMembership.user_id == current_user.id,
            GroupMembership.status == "ACTIVE",
        )
    ) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active group membership is required.")
    if db.scalar(
        select(ChallengeParticipant.id).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.user_id == current_user.id,
            ChallengeParticipant.status.in_(ACTIVE_STATUSES),
        )
    ) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active challenge participation is required to invite a group.",
        )

    active_invitation = db.scalar(
        select(GroupChallengeInvitation).where(
            GroupChallengeInvitation.group_id == group.id,
            GroupChallengeInvitation.challenge_id == challenge.id,
            GroupChallengeInvitation.status == "OPEN",
        )
    )
    if active_invitation is not None:
        close_expired_group_invitation(db, active_invitation)
        if active_invitation.status == "OPEN":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This group already has a pending invitation.")

    invitation = GroupChallengeInvitation(
        group_id=group.id,
        challenge_id=challenge.id,
        invited_by=current_user.id,
        expires_at=datetime.utcnow() + GROUP_INVITATION_TTL,
    )
    db.add(invitation)
    db.flush()
    invitee_ids = db.scalars(
        select(GroupMembership.user_id).where(
            GroupMembership.group_id == group.id,
            GroupMembership.status == "ACTIVE",
            GroupMembership.user_id != current_user.id,
        )
    ).all()
    existing_participants = set(
        db.scalars(
            select(ChallengeParticipant.user_id).where(
                ChallengeParticipant.challenge_id == challenge.id,
                ChallengeParticipant.user_id.in_(invitee_ids),
                ChallengeParticipant.status != "REMOVED",
            )
        ).all()
    ) if invitee_ids else set()
    invitee_ids = [user_id for user_id in invitee_ids if user_id not in existing_participants]
    for user_id in invitee_ids:
        db.add(GroupChallengeInvitationResponse(invitation_id=invitation.id, user_id=user_id))
    if not invitee_ids:
        invitation.status = "CLOSED"

    message = GroupMessage(
        group_id=group.id,
        author_id=current_user.id,
        message_type="CHALLENGE_INVITATION",
        challenge_invitation_id=invitation.id,
        body=f"{current_user.display_name or current_user.username or 'Someone'} invited the group to join a challenge.",
    )
    db.add(message)
    db.flush()
    notifications = []
    for user_id in invitee_ids:
        notification = Notification(
            user_id=user_id,
            notification_type="GROUP_MESSAGE",
            title=f"New challenge invitation in {group.name}",
            body=message.body,
            actor_id=current_user.id,
            actor_username=current_user.username,
            actor_name=current_user.display_name,
            invitation_id=invitation.id,
            challenge_id=challenge.id,
            challenge_slug=challenge.slug,
            challenge_title=challenge.title,
            group_id=group.id,
            group_message_id=message.id,
        )
        db.add(notification)
        notifications.append(notification)
    db.commit()
    for notification in notifications:
        send_notification_push(db, notification)
    db.refresh(message)
    return GroupInvitationCreatedResponse(
        invitation_id=invitation.id,
        invitation=GroupChallengeInvitationSummary(
            id=invitation.id,
            group_id=invitation.group_id,
            challenge_id=invitation.challenge_id,
            invited_by=invitation.invited_by,
            status=invitation.status,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
        ),
        message=group_message_response(message, db, current_user.id),
    )


@router.post("/group-invitations/{invitation_id}/respond")
def respond_to_group_invitation(
    invitation_id: str,
    payload: GroupInvitationResponseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    invitation = db.get(GroupChallengeInvitation, invitation_id)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group invitation not found.")
    membership = db.scalar(
        select(GroupMembership.id).where(
            GroupMembership.group_id == invitation.group_id,
            GroupMembership.user_id == current_user.id,
            GroupMembership.status == "ACTIVE",
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active group membership is required.")
    response = db.scalar(
        select(GroupChallengeInvitationResponse).where(
            GroupChallengeInvitationResponse.invitation_id == invitation.id,
            GroupChallengeInvitationResponse.user_id == current_user.id,
        )
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You were not invited to this challenge.")
    get_joinable_challenge_by_id(db, invitation.challenge_id)
    close_expired_group_invitation(db, invitation)
    if invitation.status == "EXPIRED":
        db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invitation has expired.")
    if response.response != "PENDING":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have already responded to this invitation.")

    participant = None
    if payload.response == "ACCEPTED":
        ensure_can_accept(db, invitation.challenge_id, current_user.id)
        participant = ChallengeParticipant(challenge_id=invitation.challenge_id, user_id=current_user.id)
        db.add(participant)
    response.response = payload.response
    response.responded_at = datetime.utcnow()
    pending = db.scalar(
        select(func.count())
        .select_from(GroupChallengeInvitationResponse)
        .where(
            GroupChallengeInvitationResponse.invitation_id == invitation.id,
            GroupChallengeInvitationResponse.response == "PENDING",
        )
    ) or 0
    if pending == 0:
        invitation.status = "CLOSED"
    db.query(Notification).filter(
        Notification.invitation_id == invitation.id,
        Notification.user_id == current_user.id,
    ).update({Notification.is_read: True}, synchronize_session=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have already joined this challenge.")
    if participant is not None:
        db.refresh(participant)
    return {
        "invitation_id": invitation.id,
        "response": response.response,
        "participation": ParticipationResponse.model_validate(participant) if participant else None,
        "response_counts": group_invitation_counts_dict(db, invitation.id),
    }


@router.get("/users/me/invitations", response_model=list[ChallengeInvitationResponse])
def list_my_invitations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ChallengeInvitationResponse]:
    rows = db.execute(
        select(ChallengeInvitation, Challenge, User)
        .join(Challenge, Challenge.id == ChallengeInvitation.challenge_id)
        .join(User, User.id == ChallengeInvitation.inviter_id)
        .where(
            ChallengeInvitation.invitee_id == current_user.id,
            ChallengeInvitation.status == "PENDING",
        )
        .order_by(ChallengeInvitation.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [invitation_response(invitation, challenge, inviter) for invitation, challenge, inviter in rows]


@router.post("/invitations/{invitation_id}/accept", response_model=ParticipationResponse)
def accept_direct_invitation(
    invitation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeParticipant:
    invitation = db.scalar(
        select(ChallengeInvitation).where(
            ChallengeInvitation.id == invitation_id,
            ChallengeInvitation.invitee_id == current_user.id,
        )
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found.")
    return accept_invitation(db, invitation, current_user.id)


@router.post("/invitations/{invitation_id}/decline", response_model=ChallengeInvitationResponse)
def decline_invitation(
    invitation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeInvitationResponse:
    row = db.execute(
        select(ChallengeInvitation, Challenge, User)
        .join(Challenge, Challenge.id == ChallengeInvitation.challenge_id)
        .join(User, User.id == ChallengeInvitation.inviter_id)
        .where(
            ChallengeInvitation.id == invitation_id,
            ChallengeInvitation.invitee_id == current_user.id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found.")
    invitation, challenge, inviter = row
    if invitation.status != "PENDING":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This invitation is no longer pending.")
    invitation.status = "DECLINED"
    db.query(Notification).filter(
        Notification.invitation_id == invitation.id,
        Notification.user_id == current_user.id,
    ).update({Notification.is_read: True}, synchronize_session=False)
    db.commit()
    db.refresh(invitation)
    return invitation_response(invitation, challenge, inviter)


@router.post("/challenges/{slug}/invite-link", response_model=ChallengeInviteLinkResponse)
def create_invite_link(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeInviteLinkResponse:
    challenge = get_joinable_challenge(db, slug)
    require_participant(db, challenge.id, current_user.id)
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + INVITE_LINK_TTL
    db.add(
        ChallengeInvitation(
            challenge_id=challenge.id,
            inviter_id=current_user.id,
            token_hash=token_hash(raw_token),
            expires_at=expires_at,
        )
    )
    db.commit()
    return ChallengeInviteLinkResponse(
        url=f"{settings.app_base_url.rstrip('/')}/challenge-invites/{raw_token}",
        inviter_id=current_user.id,
        inviter_username=current_user.username,
        challenge_id=challenge.id,
        expires_at=expires_at,
    )


@router.get("/invitations/links/{token}", response_model=ChallengeInviteLinkPreviewResponse)
def preview_invite_link(token: str, db: Session = Depends(get_db)) -> ChallengeInviteLinkPreviewResponse:
    invitation = db.scalar(
        select(ChallengeInvitation).where(ChallengeInvitation.token_hash == token_hash(token))
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation link not found.")
    challenge = db.get(Challenge, invitation.challenge_id)
    inviter = db.get(User, invitation.inviter_id)
    if challenge is None or inviter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation link not found.")
    invite_status = invitation.status
    if invitation.status == "PENDING" and invitation.expires_at is not None and invitation.expires_at <= datetime.utcnow():
        invite_status = "EXPIRED"
    return ChallengeInviteLinkPreviewResponse(
        challenge_id=challenge.id,
        challenge_title=challenge.title,
        inviter_id=inviter.id,
        inviter_username=inviter.username,
        status=invite_status,
        expires_at=invitation.expires_at,
    )


@router.post("/invitations/links/{token}/accept", response_model=ParticipationResponse)
def accept_invite_link(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChallengeParticipant:
    invitation = db.scalar(
        select(ChallengeInvitation).where(ChallengeInvitation.token_hash == token_hash(token))
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation link not found.")
    get_joinable_challenge_by_id(db, invitation.challenge_id)
    return accept_invitation(db, invitation, current_user.id)


def get_joinable_challenge_by_id(db: Session, challenge_id: str) -> Challenge:
    challenge = db.scalar(
        select(Challenge).where(
            Challenge.id == challenge_id,
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found.")
    return challenge


@router.get("/users/me/notifications", response_model=list[NotificationResponse])
def list_my_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[NotificationResponse]:
    statement = (
        select(Notification, ChallengeInvitation.status)
        .outerjoin(ChallengeInvitation, ChallengeInvitation.id == Notification.invitation_id)
        .where(Notification.user_id == current_user.id)
    )
    if unread_only:
        statement = statement.where(Notification.is_read.is_(False))
    rows = db.execute(
        statement.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [
        NotificationResponse(
            id=notification.id,
            notification_type=notification.notification_type,
            title=notification.title,
            body=notification.body,
            actor_id=notification.actor_id,
            actor_username=notification.actor_username,
            actor_name=notification.actor_name,
            invitation_id=notification.invitation_id,
            discussion_id=notification.discussion_id,
            reply_id=notification.reply_id,
            challenge_id=notification.challenge_id,
            challenge_slug=notification.challenge_slug,
            challenge_title=notification.challenge_title,
            group_id=notification.group_id,
            group_message_id=notification.group_message_id,
            invitation_status=invitation_status,
            is_read=notification.is_read,
            created_at=notification.created_at,
        )
        for notification, invitation_status in rows
    ]


@router.patch("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    invitation_status = db.scalar(
        select(ChallengeInvitation.status).where(
            ChallengeInvitation.id == notification.invitation_id,
        )
    )
    return NotificationResponse(
        id=notification.id,
        notification_type=notification.notification_type,
        title=notification.title,
        body=notification.body,
        actor_id=notification.actor_id,
        actor_username=notification.actor_username,
        actor_name=notification.actor_name,
        invitation_id=notification.invitation_id,
        discussion_id=notification.discussion_id,
        reply_id=notification.reply_id,
        challenge_id=notification.challenge_id,
        challenge_slug=notification.challenge_slug,
        challenge_title=notification.challenge_title,
        group_id=notification.group_id,
        group_message_id=notification.group_message_id,
        invitation_status=invitation_status,
        is_read=notification.is_read,
        created_at=notification.created_at,
    )
