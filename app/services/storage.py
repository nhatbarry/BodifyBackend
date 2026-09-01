from functools import lru_cache

import boto3
from botocore.client import Config

from app.core.config import settings

# R2/S3 presigned links are short-lived on purpose: the URL itself grants read
# access, so it should not outlive the screen that requested it.
DEFAULT_EXPIRES_IN = 3600


@lru_cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def presigned_url(storage_key: str, expires_in: int = DEFAULT_EXPIRES_IN) -> str:
    """Signed GET URL for one object in the media bucket.

    Signing is local computation - it does not call the storage service, so a
    URL comes back even when the bucket is unreachable.
    """
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.storage_bucket, "Key": storage_key},
        ExpiresIn=expires_in,
    )
