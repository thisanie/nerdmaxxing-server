import base64
import binascii
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, limiter
from app.models.challenge import Challenge
from app.models.discussion import Discussion, DiscussionReply, DiscussionReport
from app.models.invitation import Notification
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.discussion import (
    DiscussionCreate,
    DiscussionDetailResponse,
    DiscussionResponse,
    DiscussionUpdate,
    ReplyCreate,
    ReplyResponse,
    ReplyUpdate,
    ReportCreate,
)
from app.services.push_notification_service import send_notification_push


router = APIRouter(prefix="/api/v1", tags=["Discussions"])
ACTIVE_STATUSES = ("ACCEPTED", "IN_PROGRESS")
EDIT_WINDOW = timedelta(minutes=15)


def _cursor(value: datetime, item_id: str) -> str:
    payload = json.dumps({"time": value.isoformat(), "id": item_id}).encode()
    return base64.urlsafe_b64encode(payload).decode()


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(value.encode()).decode())
        return datetime.fromisoformat(payload["time"]), payload["id"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(status_code=422, detail="Invalid cursor.")


def _public_challenge(slug: str, db: Session) -> Challenge:
    challenge = db.scalar(
        select(Challenge).where(
            Challenge.slug == slug,
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    )
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    return challenge


def _require_active_participant(challenge_id: str, user_id: str, db: Session) -> None:
    participant = db.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.status.in_(ACTIVE_STATUSES),
        )
    )
    if participant is None:
        raise HTTPException(status_code=403, detail="An active challenge participation is required.")


def _public_discussion(discussion_id: str, db: Session) -> Discussion:
    discussion = db.scalar(
        select(Discussion)
        .join(Challenge, Challenge.id == Discussion.challenge_id)
        .where(
            Discussion.id == discussion_id,
            Challenge.status == "PUBLISHED",
            Challenge.visibility == "PUBLIC",
        )
    )
    if discussion is None or discussion.is_deleted:
        raise HTTPException(status_code=404, detail="Discussion not found.")
    return discussion


def _discussion_response(discussion: Discussion, db: Session) -> DiscussionResponse:
    author = db.get(User, discussion.author_id)
    return DiscussionResponse(
        id=discussion.id,
        challenge_id=discussion.challenge_id,
        author=author,
        type=discussion.type,
        body="[deleted]" if discussion.is_deleted else discussion.body,
        reply_count=discussion.reply_count,
        is_resolved=discussion.is_resolved,
        created_at=discussion.created_at,
        updated_at=discussion.updated_at,
        is_deleted=discussion.is_deleted,
    )


def _reply_response(reply: DiscussionReply, db: Session) -> ReplyResponse:
    author = db.get(User, reply.author_id)
    return ReplyResponse(
        id=reply.id,
        discussion_id=reply.discussion_id,
        author=author,
        body="[deleted]" if reply.is_deleted else reply.body,
        created_at=reply.created_at,
        updated_at=reply.updated_at,
        is_deleted=reply.is_deleted,
    )


def _notify_reply_participants(
    discussion: Discussion, reply: DiscussionReply, challenge: Challenge, db: Session
) -> None:
    recipient_ids = {discussion.author_id}
    recipient_ids.update(
        db.scalars(
            select(DiscussionReply.author_id).where(
                DiscussionReply.discussion_id == discussion.id,
                DiscussionReply.author_id != reply.author_id,
            )
        ).all()
    )
    recipient_ids.discard(reply.author_id)
    for recipient_id in recipient_ids:
        notification = Notification(
            user_id=recipient_id,
            notification_type="DISCUSSION_REPLY",
            title="New challenge comment",
            body="Someone replied to a challenge discussion you follow.",
            actor_id=reply.author_id,
            discussion_id=discussion.id,
            reply_id=reply.id,
            challenge_id=challenge.id,
            challenge_slug=challenge.slug,
        )
        db.add(notification)
        db.flush()
        send_notification_push(db, notification)


@router.get("/challenges/{slug}/discussions")
def list_discussions(
    slug: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict:
    challenge = _public_challenge(slug, db)
    statement = select(Discussion).where(
        Discussion.challenge_id == challenge.id,
        Discussion.is_deleted.is_(False),
    )
    decoded = _decode_cursor(cursor)
    if decoded:
        cursor_time, cursor_id = decoded
        statement = statement.where(
            or_(Discussion.updated_at < cursor_time, (Discussion.updated_at == cursor_time) & (Discussion.id < cursor_id))
        )
    discussions = list(db.scalars(statement.order_by(Discussion.updated_at.desc(), Discussion.id.desc()).limit(limit + 1)).all())
    next_cursor = _cursor(discussions[limit - 1].updated_at, discussions[limit - 1].id) if len(discussions) > limit else None
    return {"items": [_discussion_response(item, db) for item in discussions[:limit]], "next_cursor": next_cursor}


@router.post("/challenges/{slug}/discussions", response_model=DiscussionResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.discussion_post_rate_limit)
def create_discussion(
    request: Request,
    slug: str,
    payload: DiscussionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiscussionResponse:
    challenge = _public_challenge(slug, db)
    _require_active_participant(challenge.id, current_user.id, db)
    discussion = Discussion(challenge_id=challenge.id, author_id=current_user.id, type=payload.type, body=payload.body)
    db.add(discussion)
    db.commit()
    db.refresh(discussion)
    return _discussion_response(discussion, db)


@router.get("/discussions/{discussion_id}", response_model=DiscussionDetailResponse)
def get_discussion(discussion_id: str, db: Session = Depends(get_db)) -> DiscussionDetailResponse:
    discussion = _public_discussion(discussion_id, db)
    replies = db.scalars(
        select(DiscussionReply).where(DiscussionReply.discussion_id == discussion.id).order_by(DiscussionReply.created_at.asc())
    ).all()
    return DiscussionDetailResponse(**_discussion_response(discussion, db).model_dump(), replies=[_reply_response(reply, db) for reply in replies])


@router.get("/discussions/{discussion_id}/replies")
def list_replies(
    discussion_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict:
    discussion = _public_discussion(discussion_id, db)
    statement = select(DiscussionReply).where(DiscussionReply.discussion_id == discussion_id)
    decoded = _decode_cursor(cursor)
    if decoded:
        cursor_time, cursor_id = decoded
        statement = statement.where(
            or_(DiscussionReply.created_at > cursor_time, (DiscussionReply.created_at == cursor_time) & (DiscussionReply.id > cursor_id))
        )
    replies = list(db.scalars(statement.order_by(DiscussionReply.created_at.asc(), DiscussionReply.id.asc()).limit(limit + 1)).all())
    next_cursor = _cursor(replies[limit - 1].created_at, replies[limit - 1].id) if len(replies) > limit else None
    return {"items": [_reply_response(item, db) for item in replies[:limit]], "next_cursor": next_cursor}


@router.post("/discussions/{discussion_id}/replies", response_model=ReplyResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.discussion_post_rate_limit)
def create_reply(
    request: Request,
    discussion_id: str,
    payload: ReplyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReplyResponse:
    discussion = db.get(Discussion, discussion_id)
    if discussion is None or discussion.is_deleted:
        raise HTTPException(status_code=404, detail="Discussion not found.")
    if discussion.is_locked:
        raise HTTPException(status_code=409, detail="Discussion is locked.")
    _require_active_participant(discussion.challenge_id, current_user.id, db)
    challenge = db.get(Challenge, discussion.challenge_id)
    reply = DiscussionReply(discussion_id=discussion.id, author_id=current_user.id, body=payload.body)
    discussion.reply_count += 1
    discussion.updated_at = datetime.utcnow()
    db.add(reply)
    db.flush()
    _notify_reply_participants(discussion, reply, challenge, db)
    db.commit()
    db.refresh(reply)
    return _reply_response(reply, db)


def _editable(owner_id: str, created_at: datetime, current_user: User) -> None:
    if owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own content.")
    if datetime.utcnow() - created_at > EDIT_WINDOW:
        raise HTTPException(status_code=409, detail="The edit window has expired.")


@router.patch("/discussions/{discussion_id}", response_model=DiscussionResponse)
def update_discussion(discussion_id: str, payload: DiscussionUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> DiscussionResponse:
    discussion = db.get(Discussion, discussion_id)
    if discussion is None or discussion.is_deleted:
        raise HTTPException(status_code=404, detail="Discussion not found.")
    _editable(discussion.author_id, discussion.created_at, current_user)
    discussion.body = payload.body
    discussion.updated_at = datetime.utcnow()
    db.commit()
    return _discussion_response(discussion, db)


@router.delete("/discussions/{discussion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_discussion(discussion_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    discussion = db.get(Discussion, discussion_id)
    if discussion is None or discussion.is_deleted:
        raise HTTPException(status_code=404, detail="Discussion not found.")
    if discussion.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own content.")
    discussion.is_deleted = True
    discussion.body = "[deleted]"
    db.commit()


@router.patch("/discussions/{discussion_id}/replies/{reply_id}", response_model=ReplyResponse)
def update_reply(discussion_id: str, reply_id: str, payload: ReplyUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> ReplyResponse:
    reply = db.scalar(select(DiscussionReply).where(DiscussionReply.id == reply_id, DiscussionReply.discussion_id == discussion_id))
    if reply is None or reply.is_deleted:
        raise HTTPException(status_code=404, detail="Reply not found.")
    _editable(reply.author_id, reply.created_at, current_user)
    reply.body = payload.body
    reply.updated_at = datetime.utcnow()
    db.commit()
    return _reply_response(reply, db)


@router.delete("/discussions/{discussion_id}/replies/{reply_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reply(discussion_id: str, reply_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    reply = db.scalar(select(DiscussionReply).where(DiscussionReply.id == reply_id, DiscussionReply.discussion_id == discussion_id))
    if reply is None or reply.is_deleted:
        raise HTTPException(status_code=404, detail="Reply not found.")
    if reply.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own content.")
    reply.is_deleted = True
    reply.body = "[deleted]"
    db.commit()


@router.patch("/discussions/{discussion_id}/resolve", response_model=DiscussionResponse)
def resolve_discussion(discussion_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> DiscussionResponse:
    discussion = db.get(Discussion, discussion_id)
    if discussion is None or discussion.is_deleted:
        raise HTTPException(status_code=404, detail="Discussion not found.")
    challenge = db.get(Challenge, discussion.challenge_id)
    if discussion.type != "QUESTION":
        raise HTTPException(status_code=409, detail="Only question discussions can be resolved.")
    if current_user.id not in {discussion.author_id, challenge.creator_id}:
        raise HTTPException(status_code=403, detail="Only the author or challenge creator can resolve this question.")
    discussion.is_resolved = True
    db.commit()
    if discussion.author_id != current_user.id:
        db.add(Notification(user_id=discussion.author_id, notification_type="DISCUSSION_RESOLVED", title="Question resolved", body="Your challenge question was resolved.", actor_id=current_user.id, discussion_id=discussion.id, challenge_id=challenge.id, challenge_slug=challenge.slug))
        db.commit()
    return _discussion_response(discussion, db)


@router.post("/discussions/{discussion_id}/report", status_code=status.HTTP_201_CREATED)
def report_discussion(discussion_id: str, payload: ReportCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    discussion = db.get(Discussion, discussion_id)
    if discussion is None:
        raise HTTPException(status_code=404, detail="Discussion not found.")
    db.add(DiscussionReport(discussion_id=discussion.id, reporter_id=current_user.id, reason=payload.reason.upper()))
    db.commit()
    return {"reported": True}


@router.post("/discussions/{discussion_id}/replies/{reply_id}/report", status_code=status.HTTP_201_CREATED)
def report_reply(discussion_id: str, reply_id: str, payload: ReportCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    reply = db.scalar(select(DiscussionReply).where(DiscussionReply.id == reply_id, DiscussionReply.discussion_id == discussion_id))
    if reply is None:
        raise HTTPException(status_code=404, detail="Reply not found.")
    db.add(DiscussionReport(discussion_id=discussion_id, reply_id=reply.id, reporter_id=current_user.id, reason=payload.reason.upper()))
    db.commit()
    return {"reported": True}
