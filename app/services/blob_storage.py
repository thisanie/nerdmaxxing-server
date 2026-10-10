import mimetypes
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/avif",
    "image/heic",
    "image/heif",
    "application/pdf",
    "text/plain",
}
VIDEO_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
    "video/3gpp",
    "video/mpeg",
}


def _client(bucket: str | None = None):
    selected_bucket = bucket or settings.r2_bucket
    if not settings.r2_endpoint_url or not selected_bucket or not settings.r2_access_key_id or not settings.r2_secret_access_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudflare R2 storage is not configured.",
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        region_name=settings.r2_region,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(s3={"addressing_style": "path"}),
    )


async def upload_blob(
    file: UploadFile,
    prefix: str,
    *,
    bucket: str | None = None,
    public_url: str | None = None,
    return_key: bool = False,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
    allowed_content_types: set[str] | None = None,
) -> str:
    content_type = file.content_type or "application/octet-stream"
    if content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(file.filename or "")[0] or content_type
    if content_type not in (allowed_content_types or ALLOWED_CONTENT_TYPES):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {content_type}.",
        )

    content = await file.read(max_upload_bytes + 1)
    if len(content) > max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Files must be {max_upload_bytes // (1024 * 1024)} MB or smaller.",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded files cannot be empty.",
        )

    extension = PurePosixPath(file.filename or "").suffix.lower()
    if not extension:
        extension = mimetypes.guess_extension(content_type) or ""
    key = f"{prefix}/{uuid.uuid4().hex}{extension}"
    try:
        client = _client(bucket)
        selected_bucket = bucket or settings.r2_bucket
        client.put_object(
            Bucket=selected_bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            ContentDisposition="inline",
        )
    except (BotoCoreError, ClientError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not upload file to blob storage.",
        )
    if return_key:
        return key
    if public_url is None:
        public_url = settings.r2_public_url
    if public_url:
        return f"{public_url.rstrip('/')}/{key}"
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket or settings.r2_bucket, "Key": key},
        ExpiresIn=3600,
    )


def delete_blob(key: str, bucket: str) -> None:
    try:
        _client(bucket).delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not delete file from blob storage.",
        )


def private_blob_url(key: str, bucket: str) -> str:
    try:
        return _client(bucket).generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
    except (BotoCoreError, ClientError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not create a private evidence URL.",
        )