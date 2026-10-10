# NerdMaxxing API Contract

## Overview

- **Base path:** `/api/v1`
- **Format:** JSON request and response bodies
- **Authentication:** Send an access token on protected endpoints:

  ```http
  Authorization: Bearer <access_token>
  ```

- **Timestamps:** ISO 8601 datetime strings in UTC.
- **IDs:** Strings.
- **File uploads:** Uploaded files are stored in the configured S3-compatible blob storage.
- **Validation failures:** FastAPI returns `422 Unprocessable Entity` with a `detail` array.
- **Rate limiting:** Google authentication, token refresh, and username availability are rate limited per client IP.

## Common Responses

Successful protected calls may return `401 Unauthorized` when the bearer token is missing, invalid, expired, or belongs to a revoked session.

Application errors use this shape:

```json
{
  "detail": "Human-readable explanation."
}
```

### Cloudflare R2 storage configuration

Uploads require these environment variables:

```text
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_BUCKET=your-bucket
R2_ACCESS_KEY_ID=your-r2-access-key-id
R2_SECRET_ACCESS_KEY=your-r2-secret
R2_REGION=auto
R2_PUBLIC_URL=https://images.example.com
```

Create an R2 API token with object read/write access to the bucket. Configure a public custom domain or `r2.dev` URL and set `R2_PUBLIC_URL` so profile image URLs remain readable after the upload response; without it, the API returns a one-hour presigned URL. Uploads are limited to 10 MB and support JPEG, JPG, PNG, WebP, AVIF, HEIC, HEIF, PDF, and plain text.

## Health

### `GET /`

Returns service availability.

```json
{
  "status": "ok"
}
```

## Authentication

### `POST /api/v1/auth/google`

Exchanges a Google ID token for an application access and refresh token. Rate limited to 10 requests per minute.

Request:

```json
{
  "id_token": "google-id-token"
}
```

Response `200 OK`:

```json
{
  "access_token": "jwt",
  "refresh_token": "opaque-refresh-token",
  "token_type": "bearer",
  "user_id": "user-id",
  "username": null,
  "needs_username": true,
  "is_new_user": true,
  "display_name": "Ada Lovelace",
  "avatar_url": "https://example.com/avatar.png"
}
```

Returns `401 Unauthorized` for an invalid Google token or an unverified Google email address.

### `POST /api/v1/auth/refresh`

Rotates a refresh token and returns a new token pair. Rate limited to 30 requests per minute.

Request:

```json
{
  "refresh_token": "opaque-refresh-token"
}
```

Response `200 OK`:

```json
{
  "access_token": "jwt",
  "refresh_token": "new-opaque-refresh-token",
  "token_type": "bearer"
}
```

Returns `401 Unauthorized` when the refresh token is invalid, expired, or revoked.

### `POST /api/v1/auth/logout`

Revokes the supplied refresh token.

Request:

```json
{
  "refresh_token": "opaque-refresh-token"
}
```

Response: `204 No Content`.

## Challenge Discussions

Discussion reads are public for published public challenges. Creating comments or replies requires an authenticated user with an active participation (`ACCEPTED` or `IN_PROGRESS`). Comments are body-only, accept `COMMENT` or `QUESTION` as `type`, and are limited to 2,200 characters. Replies are one level deep and use the same limit.

`GET /api/v1/challenges/{slug}/discussions` returns `{ "items": [...], "next_cursor": "..." }`, ordered by newest activity. Pass `cursor` to continue. Replies use the same cursor envelope and are returned oldest-first for conversation order.

Comments and replies are soft-deleted. Authors may edit their own content for 15 minutes and may delete it at any time. Only a question author or challenge creator may resolve a question. Posting is limited to 10 comments or replies per minute per client IP.

Available endpoints:

- `POST /api/v1/challenges/{slug}/discussions`
- `GET /api/v1/discussions/{discussion_id}`
- `PATCH|DELETE /api/v1/discussions/{discussion_id}`
- `GET|POST /api/v1/discussions/{discussion_id}/replies`
- `PATCH|DELETE /api/v1/discussions/{discussion_id}/replies/{reply_id}`
- `PATCH /api/v1/discussions/{discussion_id}/resolve`
- `POST /api/v1/discussions/{discussion_id}/report`
- `POST /api/v1/discussions/{discussion_id}/replies/{reply_id}/report`

## Users

### `GET /api/v1/users/me/stats`

Requires authentication. The existing activity fields are preserved and rank fields are added:

```json
{
  "active_challenge_count": 3,
  "completed_challenge_count": 16,
  "day_streak": 9,
  "aura_points": 860,
  "rank": "B",
  "rank_progress": 72,
  "next_rank": "A",
  "aura_to_next_rank": 140
}
```

Ranks are based on lifetime aura and use these centralized thresholds: `E` 0-99, `D` 100-249, `C` 250-499, `B` 500-999, `A` 1000-1999, and `S` 2000+. New users start at `E`. `rank_progress` is the percentage through the current interval. `S` always returns `rank_progress: 100`, `next_rank: null`, and `aura_to_next_rank: 0`.

### `GET /api/v1/leaderboard`

Public endpoint. Query parameters:

- `period`: `week`, `month`, or `all_time`; defaults to `week`.
- `metric`: `aura`, `completed`, or `streak`; defaults to `aura`.
- `player_rank`: optional `E`, `D`, `C`, `B`, `A`, or `S` filter.
- `limit`: defaults to 20, range 1-100.
- `offset`: defaults to 0 and must be nonnegative.

Unsupported values, limits above 100, and negative offsets return `422`.

Response:

```json
{
  "period": "week",
  "metric": "aura",
  "entries": [
    {
      "rank": 1,
      "user_id": "user-id",
      "username": "maya_chen",
      "display_name": "Maya Chen",
      "avatar_url": "https://example.com/avatar.png",
      "player_rank": "A",
      "aura_points": 1280,
      "completed_challenge_count": 24,
      "day_streak": 18,
      "metric_value": 1280,
      "is_current_user": false
    }
  ],
  "viewer": {"rank": 3, "metric_value": 860, "user_id": "current-user-id"},
  "total": 1240
}
```

`rank` is leaderboard position; `player_rank` is the E-to-S progression rank. Aura and completion period boundaries use UTC: the current Monday 00:00 for `week`, the first day of the current month at 00:00 for `month`, and no lower bound for `all_time`. Streak rankings use the current streak and require progress during the selected period except for `all_time`.

Entries are ordered by metric descending, aura descending, achievement timestamp ascending, and user ID ascending. Competition ranking is used for equal metric values (`1, 2, 2, 4`), and `viewer.rank` uses the same metric ranking. Pagination is server-side and stable across pages.

Only public users and public, published challenges are included. Deleted, suspended, private, or opted-out users and private/draft challenge completions are excluded. Only public profile fields are returned; email and private challenge data are never exposed. Users with zero activity remain eligible and return zero metric values. `viewer` is null for unauthenticated requests or when the authenticated user is not eligible for the selected public ranking.

### Notification routing payloads

`GET /api/v1/users/me/notifications` preserves routing metadata for clickable notifications:

```json
{
  "notification_type": "FOLLOW",
  "actor_id": "user-a-id",
  "actor_username": "ada",
  "actor_name": "Ada Lovelace",
  "body": "ada started following you.",
  "is_read": false
}
```

Comment replies include the parent comment and challenge route:

```json
{
  "notification_type": "COMMENT_REPLY",
  "actor_id": "user-b-id",
  "actor_username": "grace",
  "actor_name": "Grace Hopper",
  "challenge_id": "challenge-id",
  "challenge_slug": "learn-debugging",
  "discussion_id": "parent-comment-id",
  "reply_id": "reply-id",
  "body": "grace replied to your comment.",
  "is_read": false
}
```

The same routing fields are included in FCM data payloads. Direct invitation notifications use `invitation_id` and `invitation_status`; group challenge invitation notifications use `group_invitation_id`.

### `GET /api/v1/users/username-availability?username={username}`

Checks availability for a username. Rate limited to 60 requests per minute. Usernames must be 3-24 characters and contain only letters, numbers, or underscores.

Response `200 OK`:

```json
{
  "username": "ada_lovelace",
  "available": true
}
```

Returns `422 Unprocessable Entity` for an invalid username.

### `GET /api/v1/users/{username}`

Public profile view. The response includes the user’s current E-to-S `rank`, calculated from lifetime aura using the same thresholds as personal stats and the leaderboard. The `completed_challenges` collection contains only challenges that are both `PUBLIC` and `PUBLISHED`, and that the user has completed. Private, draft, active, and incomplete challenges are excluded. The completed challenge count uses the same filter.

### `GET /api/v1/users/{user_id}/follow-status`

Requires authentication. Returns whether the authenticated user follows the specified user.

Response `200 OK`:

```json
{
  "is_following": true
}
```

Following a user with `POST /api/v1/users/{user_id}/follow` creates a `FOLLOW` notification for the followed user. The notification includes the follower's `actor_id` and is also sent to active push tokens when Firebase is configured.

### `POST /api/v1/users/me/username`

Requires authentication. Sets the current user's first username.

Request:

```json
{
  "username": "ada_lovelace"
}
```

Response `201 Created`:

```json
{
  "username": "ada_lovelace"
}
```

Returns `409 Conflict` when a username already exists for the caller or is unavailable.

### `PATCH /api/v1/users/me/username`

Requires authentication. Updates the current user's username. The request, response, and validation rules are the same as `POST /api/v1/users/me/username`.

Returns `409 Conflict` when the requested username is unavailable.

### `PATCH /api/v1/users/me/profile`

Requires authentication. Accepts `multipart/form-data` with optional `name`, `bio`, and `avatar` fields. The `avatar` field must be an image file and replaces the current profile avatar in blob storage.

### `GET /api/v1/users/me/followers?search={search}&limit={limit}&offset={offset}`

Requires authentication. Lists the authenticated user's followers for selecting challenge invitees. `search` matches usernames and display names case-insensitively. `limit` defaults to `20` and must be 1-100.

### `GET /api/v1/users/me/invitations`

Requires authentication. Lists pending challenge invitations addressed to the authenticated user, newest first.

### `POST /api/v1/users/me/push-tokens`

Requires authentication. Registers or reactivates an FCM device token for the authenticated user. The client should call this after sign-in and whenever Firebase refreshes the token.

Request:

```json
{
  "token": "fcm-registration-token",
  "platform": "android"
}
```

`platform` must be `ios`, `android`, or `web`. The token is associated with the authenticated user; clients must not send a user ID.

### `DELETE /api/v1/users/me/push-tokens/{token}`

Requires authentication. Removes the authenticated user's registered FCM token. Call this when signing out if the device should stop receiving that user's pushes.

## Groups

### `POST /api/v1/groups`

Requires authentication. Creates a group and makes the creator its first active member.

Request:

```json
{
  "name": "Distributed Systems Study",
  "description": "A focused study group.",
  "visibility": "PRIVATE"
}
```

`visibility` is `PUBLIC` or `PRIVATE` and defaults to `PUBLIC`.

### `GET /api/v1/groups`

Requires authentication. Lists public groups. Supports `limit` (1-100, default 20) and `offset` (default 0). Each group includes the caller's `membership_status` when applicable.

### `GET /api/v1/groups/me`

Requires authentication. Lists every group where the authenticated user has active membership. This is also the group collection included in authenticated and public user profile responses as `groups`.

### `GET /api/v1/groups/{group_id}`

Requires authentication. Returns a group and its active member count.

### `POST /api/v1/groups/{group_id}/join`

Requires authentication. Joins a public group immediately and returns an `ACTIVE` membership. For a private group, creates a `PENDING` request for the creator's approval. Repeated requests return `409 Conflict`.

### `DELETE /api/v1/groups/{group_id}/leave`

Requires authentication. Removes the caller's active membership or pending request. The creator cannot leave their own group.

### `GET /api/v1/groups/{group_id}/join-requests`

Requires authentication by the group creator. Lists pending requests with the requester's user information.

### `POST /api/v1/groups/{group_id}/join-requests/{user_id}/approve`

Requires authentication by the group creator. Converts the pending request to an active membership.

### `DELETE /api/v1/groups/{group_id}/join-requests/{user_id}`

Requires authentication by the group creator. Rejects and removes the pending request.

### `GET /api/v1/groups/{group_id}/messages`

Requires active group membership. Returns `{ "items": [...], "limit": 50, "offset": 0 }` in chronological order. Normal messages retain their body and author fields and have `type: "TEXT"`.

Challenge invitation messages have `type: "CHALLENGE_INVITATION"` and include:

```json
{
  "id": "message-id",
  "group_id": "group-id",
  "type": "CHALLENGE_INVITATION",
  "body": "Alex invited the group to join a challenge.",
  "created_at": "2026-09-30T12:00:00Z",
  "challenge_invitation": {
    "id": "invitation-id",
    "challenge_id": "challenge-id",
    "challenge_slug": "challenge-slug",
    "challenge_title": "Challenge title",
    "status": "OPEN",
    "my_response": "PENDING",
    "response_counts": { "pending": 4, "accepted": 2, "declined": 1 }
  }
}
```

`my_response` is calculated for the authenticated caller. Counts are visible to active group members; the invitation message remains in chat after individual responses.

### `POST /api/v1/challenges/{challenge_slug}/group-invitations`

Requires the caller to be an active member of the group and an active participant in the published, public challenge. The caller is excluded, as are active group members who already participate in the challenge. Only active members at creation time receive `PENDING` response records.

Request:

```json
{ "group_id": "group-id" }
```

Response `201 Created` contains `invitation_id` and the created typed group message. A second open invitation for the same challenge and group returns `409 Conflict`. Invitations expire after 30 days; removed members cannot respond, and members joining later are not added to an existing invitation. Once all recorded members respond, the invitation is closed and a later invitation may be created.

### `POST /api/v1/group-invitations/{invitation_id}/respond`

Requires active membership and a response record for the invitation. A response can be `ACCEPTED` or `DECLINED`:

```json
{ "response": "ACCEPTED" }
```

Acceptance creates normal participation for the responding user only and enforces the five-active-challenge limit. Declining only updates that member's response. Repeated responses return `409 Conflict`; expired invitations return `410 Gone`.

Response `200 OK`:

```json
{
  "invitation_id": "invitation-id",
  "response": "ACCEPTED",
  "participation": { "id": "participation-id", "challenge_id": "challenge-id" },
  "response_counts": { "pending": 3, "accepted": 3, "declined": 1 }
}
```

Unauthenticated requests return `401`, non-members or non-invitees return `403`, missing resources return `404`, duplicate/already-responded/already-participating requests return `409`, and malformed bodies return `422`, all using `{ "detail": "Human-readable explanation." }`.

## Challenges

### `GET /api/v1/challenges?limit={limit}&offset={offset}`

Lists public, published challenges, newest first. `limit` defaults to `20` and must be 1-100. `offset` defaults to `0` and must be non-negative.

Response `200 OK`: an array of [Challenge](#challenge-object) objects.

### `GET /api/v1/challenges/private?limit={limit}&offset={offset}`

Requires authentication. Lists the authenticated user's private challenges, newest first. `limit` defaults to `20` and must be 1-100. `offset` defaults to `0` and must be non-negative.

Response `200 OK`: an array of [Challenge](#challenge-object) objects with `status` and `visibility` set to `PRIVATE`.

### `GET /api/v1/challenges/{slug}`

Gets one public, published challenge by slug.

Response `200 OK`: a [Challenge](#challenge-object) object.

Returns `404 Not Found` when no public, published challenge matches the slug.

### `GET /api/v1/challenges/{slug}/detail`

Returns the complete challenge journey read model. Authentication is optional:
anonymous callers receive public metadata, aggregate statistics, and participant
previews; the authenticated caller's participation supplies personal metric
values and `attempts`. Callers without participation receive challenge metric
definitions without personal values and no attempts.

Response `200 OK`:

```json
{
  "challenge": "Challenge object",
  "stats": {
    "participant_count": 47,
    "completed_participant_count": 12
  },
  "metrics": [
    {
      "key": "speed",
      "label": "Speed",
      "kind": "RATE",
      "unit": "WPM",
      "current": 42,
      "target": 60,
      "best": 46,
      "average": 41,
      "direction": "AT_LEAST",
      "is_primary": true,
      "format": "INTEGER"
    }
  ],
  "requirements": [],
  "milestones": [],
  "attempts": [],
  "participants": [],
  "verification": {
    "type": "SELF_REPORTED",
    "kind": "SELF_REPORTED",
    "provider": null,
    "evidence": {
      "allowed_types": ["TEXT", "FILE"],
      "requires_file": false,
      "requires_explanation": false,
      "max_file_size_bytes": null,
      "max_duration_seconds": null,
      "allowed_mime_types": []
    },
    "completion": {
      "mode": "SELF_CONFIRMATION",
      "requires_review": false
    },
    "requirements": [],
    "required_runs": 1,
    "instructions": "Submit evidence that demonstrates the target metric and satisfies the challenge requirements."
  },
  "verification_state": {
    "status": "NOT_STARTED",
    "submission_id": null,
    "rejection_reason": null,
    "can_retry": true
  }
}
```

`milestones` are ordered by `order_index`; each status is `LOCKED`, `CURRENT`,
or `COMPLETED`. Each milestone may include a `resources` array containing
`resource_id` and `required`, linking the milestone to the challenge's resource
collection. `attempts` are newest first and limited to the latest 20
progress records. `participants` are limited to five previews, while `stats`
always contains complete counts. `verification.kind` is one of
`SELF_REPORTED`, `VIDEO_UPLOAD`, or `EXTERNAL_ACCOUNT`. `completion.mode` is
one of `AUTOMATIC`, `REVIEW`, or `SELF_CONFIRMATION`. `verification_state.status`
is one of `NOT_STARTED`, `READY`, `PENDING`, `PROCESSING`, `VERIFIED`, or
`REJECTED`.

For `VIDEO_UPLOAD`, the canonical evidence configuration is `VIDEO`, required,
with a 50 MiB maximum, 120-second maximum duration, and MIME types
`video/mp4` and `video/quicktime`; completion is `REVIEW` and requires review.
For `EXTERNAL_ACCOUNT`, `provider` contains the provider ID, display name,
connection URL, current connection state, and account when connected.

### `POST /api/v1/challenges`

Requires authentication. Creates a private challenge owned by the caller. Accepts `multipart/form-data` with a `payload` field containing the challenge JSON, an optional `image` file, and one `resource_files` file for each resource in `payload.resources`, in the same order.

Request:

```json
{
  "title": "Build a personal knowledge system",
  "image_url": null,
  "resources": [
    {
      "title": "Getting Started",
      "url": null,
      "resource_type": "LINK",
      "rationale": "Provides the foundation for the challenge."
    }
  ],
  "short_description": "Create a system for capturing and finding useful knowledge.",
  "full_description": "Define the workflow, choose tools, and create an initial set of notes.",
  "difficulty_level": "BEGINNER",
  "estimated_effort_min_minutes": 60,
  "estimated_effort_max_minutes": 180,
  "verification_type": "SELF_REPORTED"
}
```

Response `201 Created`: a [Challenge](#challenge-object) object with `status` and `visibility` set to `PRIVATE`.

Rules:

- `title`: 3-160 characters.
- `image`: optional JPEG, PNG, or WebP file; the default image is used when omitted.
- `resources`: 1-20 resources.
- Each resource requires a corresponding uploaded `resource_files` file.
- `short_description`: 1-300 characters.
- `full_description`: at least 1 character.
- Each resource `title` is 1-160 characters and `rationale` is 1-1000 characters.
- `estimated_effort_min_minutes` and `estimated_effort_max_minutes`, when provided, must be at least 1; minimum cannot exceed maximum.
- `aura_points` is calculated by the backend from difficulty and estimated effort; challenge creators do not provide it.

## Challenge Invitations

### `POST /api/v1/challenges/{slug}/invitations`

Requires authentication and an existing participation in the challenge. Invites one of the caller's followers.

Request:

```json
{
  "invitee_id": "user-id"
}
```

Response `201 Created`: a pending invitation. The invitee receives an in-app notification whose body identifies the challenger and challenge.

Returns `403 Forbidden` when the target does not follow the caller, or when the caller has not joined the challenge. Returns `409 Conflict` when the invitation was already accepted or the target already participates.

### `POST /api/v1/invitations/{invitation_id}/accept`

Requires authentication by the invited user. Accepts the invitation and creates an accepted participation. The normal five-active-challenge limit applies.

Response `200 OK`: a [Participation](#participation-object) object.

### `POST /api/v1/invitations/{invitation_id}/decline`

Requires authentication by the invited user. Marks the pending invitation as `DECLINED` and returns the invitation.

### `POST /api/v1/challenges/{slug}/invite-link`

Requires authentication and an existing participation in the challenge. Creates a random, expiring link valid for 30 days. The token is stored hashed and the returned URL can be shared through any messaging app.

Response `200 OK`:

```json
{
  "url": "https://app.example.com/challenge-invites/token",
  "inviter_id": "user-id",
  "inviter_username": "ada_lovelace",
  "challenge_id": "challenge-id",
  "expires_at": "2026-10-05T12:00:00Z"
}
```

The URL base is configured with `APP_BASE_URL` and defaults to `http://localhost:3000`.

### `GET /api/v1/invitations/links/{token}`

Public endpoint. Returns the challenge title, inviter, and link status so a client can render a preview before sign-in.

### `POST /api/v1/invitations/links/{token}/accept`

Requires authentication. Accepts a valid share link and creates an accepted participation. Share links are available to any user except the inviter and are subject to the five-active-challenge limit.

### `GET /api/v1/users/me/notifications?unread_only={boolean}&limit={limit}&offset={offset}`

Requires authentication. Lists the authenticated user's in-app notifications, newest first. Set `unread_only=true` to filter to unread notifications. Challenge invitation notifications include the current `invitation_status`, so the status remains accurate after accepting or declining.

Notification response example:

```json
{
  "id": "notification-id",
  "notification_type": "CHALLENGE_INVITATION",
  "title": "New challenge invitation",
  "body": "ada_lovelace challenged you to Build a habit.",
  "invitation_id": "invitation-id",
  "invitation_status": "ACCEPTED",
  "is_read": true,
  "created_at": "2026-09-13T12:00:00Z"
}
```

### `PATCH /api/v1/notifications/{notification_id}/read`

Requires authentication. Marks an owned notification as read.

## Participation

### `GET /api/v1/participation/me`

Requires authentication. Lists all of the current user's challenge participations, most recently active first.

Response `200 OK`: an array of [Participation](#participation-object) objects.

### `POST /api/v1/participation/challenges/{slug}/accept`

Requires authentication. Accepts a public, published challenge.

Response `201 Created`: a [Participation](#participation-object) object.

Returns `404 Not Found` for an unknown or non-public challenge. Returns `409 Conflict` when already accepted or when the caller already has five `ACCEPTED` or `IN_PROGRESS` participations.

### `PATCH /api/v1/participation/{participant_id}`

Requires authentication. Changes the caller's participation state.

Request:

```json
{
  "status": "IN_PROGRESS"
}
```

Response `200 OK`: a [Participation](#participation-object) object.

Permitted transitions:

| Current status | Allowed target statuses |
| --- | --- |
| `ACCEPTED` | `IN_PROGRESS`, `PAUSED`, `REMOVED` |
| `IN_PROGRESS` | `PAUSED`, `REMOVED` |
| `PAUSED` | `IN_PROGRESS`, `REMOVED` |

Returns `404 Not Found` for a participation not owned by the caller and `409 Conflict` for an invalid transition.

### `DELETE /api/v1/participation/{participant_id}`

Requires authentication. Unenrolls the caller from the challenge and deletes the participation and its progress logs.

Response `204 No Content`.

Returns `404 Not Found` for a participation not owned by the caller.

### `POST /api/v1/participation/{participant_id}/progress`

Requires authentication. Logs challenge-defined metrics and an optional note for an active participation. `hours_spent` is optional and must be greater than 0 and no more than 24 when supplied. Unknown metric keys and invalid values are rejected.

```json
{
  "metrics": {
    "distance": 6.4,
    "duration": 38
  },
  "note": "Built the first prototype."
}
```

Response `201 Created`: a progress log object. Logging progress updates the participation activity timestamp and the user's streak. A streak resets to `0` after more than 24 hours without a progress log.

### `GET /api/v1/participation/{participant_id}/progress`

Requires authentication. Lists progress logs for a participation owned by the caller, newest first.

### `POST /api/v1/participation/{participant_id}/milestones/{milestone_id}/resources/{resource_id}/complete`

Requires authentication and an active participation owned by the caller. Marks an attached milestone resource as complete. The operation is idempotent.

Request:

```json
{
  "resource_minutes": 25,
  "milestone_minutes": 90,
  "note": "Completed the wrist and shoulder preparation routine.",
  "log_progress": true
}
```

Response `200 OK` includes `resource_id`, `milestone_id`, `completed`, `completed_at`, resource timing, `milestone_status`, `milestone_completed`, `challenge_status`, and `ready_for_proof`. When all required resources in all milestones are complete, `challenge_status` is `READY_FOR_PROOF` and `ready_for_proof` is `true`.

### `POST /api/v1/participation/{participant_id}/metric-attempts`

Requires authentication and an active participation owned by the caller. Records a configured challenge metric attempt and evaluates it against the metric target.

```json
{
  "metric_key": "typing_speed",
  "value": 60,
  "unit": "WPM",
  "note": "Completed a timed typing test."
}
```

The response includes the attempt ID, metric, value, unit, timestamp, and `meets_target`.

### `GET /api/v1/users/me/stats`

Requires authentication. Returns the current user's activity summary: `active_challenge_count`, `completed_challenge_count`, `day_streak`, and `aura_points`.

## External Linking
### Chess.com account linking

OAuth is not assumed or faked. The current MVP uses Chess.com's documented
public profile API: `POST /api/v1/integrations/chess_com/start` validates the
profile and returns a cryptographically random, expiring code. The user
temporarily places that exact code in the public Chess.com profile `Location`
field. `POST /api/v1/integrations/chess_com/confirm` fetches the profile again
server-side, verifies the player ID and exact location code, consumes the
single-use challenge, and links the account transactionally. This profile proof
is not equivalent to OAuth and is intentionally replaceable.

`GET /api/v1/integrations/chess_com/status` returns the authenticated user's
verified connection. `DELETE /api/v1/integrations/chess_com/disconnect`
removes it and invalidates pending challenges. `GET
/api/v1/integrations/chess_com/rating/rapid` fetches a short-lived server-side
cache of the public Rapid rating and returns its observation timestamp.

The legacy `POST /api/v1/integrations/chess_com/connect` endpoint returns `410`
and cannot create an unverified link. Chess.com requests use the configured
identifiable `CHESS_COM_USER_AGENT`; no login scraping or client-supplied
ratings are accepted.

## Evidence

### `POST /api/v1/evidence/participation/{participant_id}`

Requires authentication. Submits evidence for the caller's existing challenge participation. The participation must be `ACCEPTED`, `IN_PROGRESS`, or `PAUSED` (or `READY_FOR_PROOF` for legacy self-reported flows); the challenge ID is taken from that participation, so evidence cannot exist without a challenge. The video is stored privately in the R2 bucket configured by `R2_EVIDENCE_BUCKET`.

Request:

```text
explanation=I completed the work and published the notes.
text_content=I completed the work and learned the key concepts.
external_url=https://example.com/legacy-proof
file=<uploaded evidence file>
```

For `SELF_REPORTED`, the existing explanation, text, external URL, and file
behavior remains supported. For `VIDEO_UPLOAD`, `file` and `explanation` are
required, the file must use the challenge's allowed MIME type, must not exceed
the configured byte limit, and is marked `PROCESSING`; duration is checked when
`ffprobe` is available. For `EXTERNAL_ACCOUNT`, the configured provider must be
connected by the authenticated user; provider usernames are never accepted
from the client. Each participant can have one pending, processing, or verified
submission.

Response `201 Created`:

```json
{
  "id": "submission-id",
  "challenge_id": "challenge-id",
  "participant_id": "participant-id",
  "user_id": "user-id",
  "status": "PROCESSING",
  "verification_kind": "VIDEO_UPLOAD",
  "provider_id": null,
  "explanation": "The full attempt is visible.",
  "file_url": "https://private-presigned-url",
  "file_name": "proof.mp4",
  "mime_type": "video/mp4",
  "review_reason": null,
  "submitted_at": "2026-10-08T12:00:00Z",
  "reviewed_at": null
}
```

`status` is one of `PENDING`, `PROCESSING`, `VERIFIED`, or `REJECTED`.
Evidence object URLs are private one-hour presigned URLs and are returned only
to the uploader or an admin.

For video uploads from clients that enforce a request payload limit (such as
Vercel serverless functions), use the direct-upload flow:

1. `POST /api/v1/evidence/participation/{participant_id}/upload-url` with
   `file_name`, `content_type`, and `file_size` query parameters. The response
   contains a short-lived `upload_url` and `storage_key`.
2. Upload the video bytes directly to `upload_url` with an HTTP `PUT`, using
   the returned `Content-Type`.
3. Submit the evidence to the endpoint above without a `file`, using the
   fields `uploaded_key`, `uploaded_file_name`, `uploaded_content_type`, and
   `uploaded_file_size`, together with the required `explanation`.

The API verifies that the uploaded object belongs to the caller's challenge,
matches the declared size and MIME type, and is within the challenge limit.

### `GET /api/v1/evidence/{submission_id}`

Requires authentication by the uploader or an admin. Returns the evidence metadata and a one-hour presigned `video_url`. Evidence is never exposed through a public R2 URL.

### `DELETE /api/v1/evidence/{submission_id}`

Requires authentication by the uploader or an admin. Deletes the video from R2 and removes its database records. Returns `204 No Content`.

### `POST /api/v1/evidence/{submission_id}/self-verify`

Requires authentication by the uploader. Verifies the pending submission when the challenge requirements are satisfied.

### Seeded verification challenges

The seed script creates these stable public challenges:

| Slug | Kind | Key behavior |
| --- | --- | --- |
| `daily-reading` | `SELF_REPORTED` | Explanation required; self-confirmation |
| `one-minute-plank` | `VIDEO_UPLOAD` | MP4/MOV, 50 MiB, 120 seconds, duration at least 60 seconds, review required |
| `chess-rated-game` | `EXTERNAL_ACCOUNT` | `chess_com`, one rated game, automatic completion |

The mock Chess.com account is `provider_user_id=seed-chess-user-1`,
`username=demo_player`. It is test data only; no real OAuth or provider
credentials are used.

## Discover

### `GET /api/v1/discover`

Authentication is optional. Anonymous callers receive public feed data and an
empty `recommended` collection. Authenticated recommendations exclude completed
and active challenges. Every challenge collection is bounded to at most 20
items, and every challenge is `PUBLISHED`, `PUBLIC`, and serialized with the
same Challenge object shape returned by the challenges endpoints. Empty data is
represented by `[]`; `featured` is `null` when trending has no results.

The response is:

```json
{
  "featured": null,
  "trending": [],
  "categories": [],
  "new_challenges": [],
  "recommended": [],
  "legendary": [],
  "unexpected": [],
  "top_nerds": [],
  "recent_activity": []
}
```

`featured` is always the first item in the current `trending` ranking. Trending
uses recent participation activity, completion weight, and exponential time
decay over the last 30 days; it is not a newest or lifetime-popularity list.
New challenges require a non-null `published_at` and are newest first.
Categories include only active categories attached to at least one public,
published challenge, and `challenge_count` counts only those challenges.
Legendary uses the challenge's `legendary` flag. Unexpected is a randomized
public selection per request. Recommendations use the caller's completed and
active categories and difficulty history plus saved challenges; insufficient
history produces an empty list.

`top_nerds` contains up to 10 non-deleted, non-suspended, public users ranked by
completed public challenges from Monday 00:00:00 UTC through the request time.
Ties use latest completion time and then user ID. `completed_count` is weekly,
while `day_streak` is the persisted current streak. `recent_activity` contains
up to 10 supported public events, newest first, and only includes public users
and public, published challenges. Timestamps are ISO 8601 UTC.

### `GET /api/v1/discover/search?q={query}&type={type}&limit={limit}&offset={offset}`

This endpoint is public and searches server-side. `q` is trimmed and must have
at least two characters. `type` is `all`, `users`, or `challenges`; `limit`
defaults to 20 and must be 1-100; `offset` defaults to 0 and must not be
negative. Invalid values return FastAPI's standard `422` response.

Users are matched case-insensitively by username and display name. Challenges
are matched by title, short description, full description, and category name.
Exact matches sort before prefix matches, followed by relevance and stable
tie-breakers. Results include `total_users` and `total_challenges`, while
pagination is applied independently to the selected result collections.
Deleted, suspended, and private users plus draft, private, and otherwise
non-public challenges never appear.

Returns `404 Not Found` for a participation not owned by the caller and `409 Conflict` when its state cannot accept evidence.

### `POST /api/v1/evidence/{submission_id}/self-verify`

Requires authentication. Verifies the caller's pending evidence submission. This completes the participation and awards a verified skill named after the challenge if it has not already been awarded.

Response `200 OK`: an [Evidence submission](#evidence-submission-object) object with `status` set to `VERIFIED`.

Returns `404 Not Found` for a submission not owned by the caller and `409 Conflict` when the submission is no longer pending.

## Skills

### `GET /api/v1/skills/me`

Requires authentication. Lists the current user's earned skills, newest first.

Response `200 OK`: an array of [User skill](#user-skill-object) objects.

## Object Definitions

### Challenge object

```json
{
  "id": "challenge-id",
  "title": "Build a personal knowledge system",
  "image_url": "https://example.com/knowledge-system.png",
  "resources": [
    {
      "id": "resource-id",
      "title": "Getting Started",
      "url": "https://example.com/guide",
      "resource_type": "LINK",
      "rationale": "Provides the foundation for the challenge.",
      "order_index": 0
    }
  ],
  "slug": "build-a-personal-knowledge-system-a1b2c3d4",
  "short_description": "Create a system for capturing and finding useful knowledge.",
  "full_description": "Define the workflow, choose tools, and create an initial set of notes.",
  "creator_id": "user-id",
  "difficulty_level": "BEGINNER",
  "aura_points": 20,
  "status": "PUBLISHED",
  "visibility": "PUBLIC",
  "estimated_effort_min_minutes": 60,
  "estimated_effort_max_minutes": 180,
  "verification_type": "SELF_REPORTED",
  "created_at": "2026-09-05T12:00:00Z",
  "updated_at": "2026-09-05T12:00:00Z",
  "published_at": "2026-09-05T12:00:00Z"
}
```

### Participation object

```json
{
  "id": "participant-id",
  "challenge_id": "challenge-id",
  "user_id": "user-id",
  "status": "IN_PROGRESS",
  "completion_status": "NOT_COMPLETED",
  "verification_status": "NOT_REQUIRED",
  "started_at": "2026-09-05T12:00:00Z",
  "last_activity_at": "2026-09-05T12:00:00Z",
  "completed_at": null
}
```

### Evidence submission object

```json
{
  "id": "submission-id",
  "challenge_id": "challenge-id",
  "participant_id": "participant-id",
  "user_id": "user-id",
  "status": "PENDING",
  "explanation": "I completed the work and published the notes.",
  "submitted_at": "2026-09-05T12:00:00Z",
  "reviewed_at": null
}
```

### User skill object

```json
{
  "id": "skill-id",
  "user_id": "user-id",
  "source_challenge_id": "challenge-id",
  "skill_name": "Build a personal knowledge system",
  "verification_status": "VERIFIED",
  "earned_at": "2026-09-05T12:00:00Z"
}
```