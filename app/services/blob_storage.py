import mimetypes
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
    "text/plain",
}


def _client():
    if not settings.s3_bucket or not settings.s3_access_key_id or not settings.s3_secret_access_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Blob storage is not configured.",
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(s3={"addressing_style": "path"}),
    )


async def upload_blob(file: UploadFile, prefix: str) -> str:
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {content_type}.",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Files must be 10 MB or smaller.",
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
        client = _client()
        client.put_object(
            Bucket=settings.s3_bucket,
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
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=3600,
    )