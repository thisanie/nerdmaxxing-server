from types import SimpleNamespace
from datetime import datetime
from unittest.mock import patch

from app.api.discussions import _notify_reply_participants
from app.api.groups import notify_group_members
from app.api.invitations import list_my_notifications
from app.api.users import follow_user
from app.models.discussion import Discussion, DiscussionReply
from app.models.invitation import ChallengeInvitation, Notification
from app.models.user import User


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, get_values=None, reply_authors=None, rows=None):
        self.get_values = get_values or {}
        self.reply_authors = reply_authors or []
        self.added = []
        self.rows = rows or []

    def get(self, model, key):
        if isinstance(key, dict):
            return self.get_values.get(model.__name__)
        return self.get_values.get((model.__name__, key), self.get_values.get(model.__name__))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        return None

    def flush(self):
        return None

    def scalars(self, statement):
        return FakeRows(self.reply_authors)

    def execute(self, statement):
        return FakeRows(self.rows)


def test_follow_creates_routable_actor_notification():
    follower = SimpleNamespace(id="user-a", username="ada", display_name="Ada Lovelace")
    target = SimpleNamespace(id="user-b")
    db = FakeDb(get_values={"User": target, "UserFollow": None})

    with patch("app.api.users.send_notification_push"):
        follow_user(target.id, db, follower)

    notification = next(item for item in db.added if isinstance(item, Notification))
    assert notification.notification_type == "FOLLOW"
    assert notification.actor_id == "user-a"
    assert notification.actor_username == "ada"
    assert notification.actor_name == "Ada Lovelace"
    assert notification.body == "ada started following you."


def test_comment_reply_contains_parent_and_challenge_routing():
    discussion = Discussion(id="discussion-1", challenge_id="challenge-1", author_id="user-a", body="Question")
    reply = DiscussionReply(id="reply-1", discussion_id="discussion-1", author_id="user-b", body="Answer")
    actor = SimpleNamespace(username="grace", display_name="Grace Hopper")
    challenge = SimpleNamespace(id="challenge-1", slug="debugging")
    db = FakeDb(get_values={"User": actor})

    _notify_reply_participants(discussion, reply, challenge, db)

    notification = next(item for item in db.added if isinstance(item, Notification))
    assert notification.notification_type == "COMMENT_REPLY"
    assert notification.actor_username == "grace"
    assert notification.challenge_id == "challenge-1"
    assert notification.challenge_slug == "debugging"
    assert notification.discussion_id == "discussion-1"
    assert notification.reply_id == "reply-1"
    assert notification.body == "grace replied to your comment."


def test_reply_to_own_comment_does_not_notify_author():
    discussion = Discussion(id="discussion-1", challenge_id="challenge-1", author_id="user-a", body="Question")
    reply = DiscussionReply(id="reply-1", discussion_id="discussion-1", author_id="user-a", body="Follow-up")
    actor = SimpleNamespace(username="ada", display_name="Ada Lovelace")
    challenge = SimpleNamespace(id="challenge-1", slug="debugging")
    db = FakeDb(get_values={"User": actor})

    _notify_reply_participants(discussion, reply, challenge, db)

    assert not [item for item in db.added if isinstance(item, Notification)]


def test_notification_list_preserves_routing_fields_and_invitation_status():
    notification = Notification(
        id="notification-1",
        user_id="user-b",
        notification_type="COMMENT_REPLY",
        title="New comment reply",
        body="grace replied to your comment.",
        actor_id="user-a",
        actor_username="grace",
        actor_name="Grace Hopper",
        challenge_id="challenge-1",
        challenge_slug="debugging",
        discussion_id="discussion-1",
        reply_id="reply-1",
        is_read=False,
        created_at=datetime.utcnow(),
    )
    invitation = Notification(
        id="notification-2",
        user_id="user-b",
        notification_type="CHALLENGE_INVITATION",
        title="Invitation",
        body="Join this challenge.",
        invitation_id="invitation-1",
        is_read=False,
        created_at=datetime.utcnow(),
    )
    group_invitation = Notification(
        id="notification-3",
        user_id="user-b",
        notification_type="GROUP_MESSAGE",
        title="Group invitation",
        body="Join this challenge with your group.",
        group_invitation_id="group-invitation-1",
        group_id="group-1",
        group_message_id="message-1",
        is_read=False,
        created_at=datetime.utcnow(),
    )
    db = FakeDb(rows=[(notification, None), (invitation, "PENDING"), (group_invitation, None)])
    response = list_my_notifications(db, SimpleNamespace(id="user-b"), limit=20, offset=0)

    assert response[0].actor_username == "grace"
    assert response[0].challenge_slug == "debugging"
    assert response[0].discussion_id == "discussion-1"
    assert response[0].reply_id == "reply-1"
    assert response[1].invitation_id == "invitation-1"
    assert response[1].invitation_status == "PENDING"
    assert response[2].group_invitation_id == "group-invitation-1"


def test_group_message_notification_preserves_group_routing_fields():
    group = SimpleNamespace(id="group-1", name="Study Group")
    message = SimpleNamespace(id="message-1", body="New idea")
    actor = SimpleNamespace(id="user-a", username="ada", display_name="Ada Lovelace")
    db = FakeDb(reply_authors=["user-b"])

    with patch("app.api.groups.send_notification_push") as send_push:
        notifications = notify_group_members(group, message, actor, db)

    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.notification_type == "GROUP_MESSAGE"
    assert notification.group_id == "group-1"
    assert notification.group_message_id == "message-1"
    send_push.assert_not_called()
