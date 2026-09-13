from __future__ import annotations

import logging

from firebase_admin import credentials, get_app, initialize_app, messaging
from firebase_admin.exceptions import InvalidArgumentError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.invitation import Notification, PushToken


logger = logging.getLogger(__name__)


def _firebase_configured() -> bool:
    return all(
        (
            settings.firebase_project_id,
            settings.firebase_client_email,
            settings.firebase_private_key,
        )
    )


def _get_firebase_app():
    if not _firebase_configured():
        return None
    try:
        return get_app()
    except ValueError:
        private_key = settings.firebase_private_key.replace("\\n", "\n")
        return initialize_app(
            credentials.Certificate(
                {
                    "type": "service_account",
                    "project_id": settings.firebase_project_id,
                    "private_key": private_key,
                    "client_email": settings.firebase_client_email,
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            )
        )


def send_notification_push(db: Session, notification: Notification) -> None:
    """Best-effort delivery; the persisted notification remains the source of truth."""
    try:
        firebase_app = _get_firebase_app()
    except Exception:
        logger.exception("Firebase push notifications are not configured correctly")
        return
    if firebase_app is None:
        return

    tokens = list(
        db.scalars(
            select(PushToken).where(
                PushToken.user_id == notification.user_id,
                PushToken.is_active.is_(True),
            )
        ).all()
    )
    for push_token in tokens:
        message = messaging.Message(
            token=push_token.token,
            notification=messaging.Notification(
                title=notification.title,
                body=notification.body,
            ),
            data={
                "notification_id": notification.id,
                "notification_type": notification.notification_type,
                "invitation_id": notification.invitation_id or "",
            },
        )
        try:
            messaging.send(message)
        except (messaging.UnregisteredError, InvalidArgumentError):
            push_token.is_active = False
            db.commit()
        except Exception:
            logger.exception("Failed to send push notification %s", notification.id)