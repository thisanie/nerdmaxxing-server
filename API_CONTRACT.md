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

### Blob storage configuration

Uploads require these environment variables:

```text
AWS_ENDPOINT_URL_S3=https://your-branch.storage.c-2.us-east-2.aws.neon.tech
NEON_STORAGE_BUCKET=your-bucket
AWS_ACCESS_KEY_ID=your-neon-token-id
AWS_SECRET_ACCESS_KEY=your-neon-s3-secret
AWS_REGION=us-east-2
S3_PUBLIC_URL=https://your-public-bucket-url
```

Create the bucket in Neon with `public_read` access if API responses should contain directly readable object URLs; set `S3_PUBLIC_URL` to the bucket's public base URL. Uploads are limited to 10 MB and support JPEG, PNG, WebP, PDF, and plain text.

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

## Users

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

Public profile view. The `completed_challenges` collection contains only challenges that are both `PUBLIC` and `PUBLISHED`, and that the user has completed. Private, draft, active, and incomplete challenges are excluded. The completed challenge count uses the same filter.

### `GET /api/v1/users/{user_id}/follow-status`

Requires authentication. Returns whether the authenticated user follows the specified user.

Response `200 OK`:

```json
{
  "is_following": true
}
```

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

### `POST /api/v1/participation/{participant_id}/progress`

Requires authentication. Logs time and an optional note for an active participation. `hours_spent` must be greater than 0 and no more than 24.

```json
{
  "hours_spent": 1.5,
  "note": "Built the first prototype."
}
```

Response `201 Created`: a progress log object. Logging progress updates the participation activity timestamp and the user's streak. A streak resets to `0` after more than 24 hours without a progress log.

### `GET /api/v1/participation/{participant_id}/progress`

Requires authentication. Lists progress logs for a participation owned by the caller, newest first.

### `GET /api/v1/users/me/stats`

Requires authentication. Returns the current user's activity summary: `active_challenge_count`, `completed_challenge_count`, `day_streak`, and `aura_points`.

## Evidence

### `POST /api/v1/evidence/participation/{participant_id}`

Requires authentication. Submits evidence for the caller's participation. Accepts `multipart/form-data` with optional `explanation`, optional `text_content`, and an optional `file`. The participation must be `ACCEPTED`, `IN_PROGRESS`, or `PAUSED`; submission moves it to `SUBMITTED` with pending verification.

Request:

```json
explanation=I completed the work and published the notes.
text_content=The work is complete.
file=<uploaded PDF, image, or text file>
```

At least one of `text_content` or `file` is required. `explanation` is limited to 5,000 characters and `text_content` to 20,000 characters.

Response `201 Created`: an [Evidence submission](#evidence-submission-object) object.

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