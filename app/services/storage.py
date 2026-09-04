import time
from functools import lru_cache
from threading import Lock

import boto3
from botocore.client import Config

from app.core.config import settings

# How long a signed link stays valid. Seven days is the maximum SigV4 allows,
# and it is the right end of the trade-off for this bucket: the media is the
# public exercise catalog - 704 fixed files, the same for every user - not
# anybody's private data. A short expiry would protect nothing and would strand
# any URL a client had already cached.
EXPIRES_IN = 7 * 24 * 3600

# How long the *same* URL keeps being handed out. This is the number that
# matters for speed. boto3 stamps X-Amz-Date to the second, so signing on every
# request produces a different URL every second - and every cache downstream is
# keyed on the URL, so none of them can ever hit. Serving one stable URL per
# day makes the browser cache, any CDN in front, and the app's own disk cache
# all work as intended, and still leaves six days of validity on a URL handed
# out at the very end of its window.
URL_REUSE_SECONDS = 24 * 3600


@lru_cache
def _write_client():
    """A second client, the only one holding write credentials.

    Separate from `_client()` so the catalog read path physically cannot write:
    a bug in a read endpoint has no credential to damage 1400 media files with.
    """
    if not settings.can_write_storage:
        raise StorageWriteUnavailable(
            "No write credentials configured "
            "(STORAGE_WRITE_ACCESS_KEY / STORAGE_WRITE_SECRET_KEY)"
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint,
        aws_access_key_id=settings.storage_write_access_key,
        aws_secret_access_key=settings.storage_write_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


class StorageWriteUnavailable(RuntimeError):
    """Raised when an upload is attempted with no write credential."""


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


# key -> (url, generated_at). Bounded by the catalog: 704 exercises with at
# most one thumbnail and one video each, so a few thousand short strings.
_url_cache: dict[tuple[str, str], tuple[str, float]] = {}
_url_lock = Lock()


def presigned_url(
    storage_key: str,
    expires_in: int = EXPIRES_IN,
    bucket: str | None = None,
) -> str:
    """Signed GET URL for one object.

    Defaults to the exercise catalog bucket; user uploads pass their own. Both
    are signed with the read-only credential, which can read either - the write
    credential is scoped to the upload bucket and cannot even list the catalog.

    Stable for URL_REUSE_SECONDS, so repeat requests for the same object get
    byte-identical URLs and downstream caches can recognise them.

    Signing is local computation - it does not call the storage service, so a
    URL comes back even when the bucket is unreachable.
    """
    bucket = bucket or settings.storage_bucket
    now = time.monotonic()

    # Keyed on the bucket too: the same key can exist in both, and handing back
    # a catalog URL for an avatar would be a quiet, confusing failure.
    cache_key = (bucket, storage_key)
    cached = _url_cache.get(cache_key)
    if cached is not None and now - cached[1] < URL_REUSE_SECONDS:
        return cached[0]

    url = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": storage_key},
        ExpiresIn=expires_in,
    )

    # Two threads racing here would only sign the same object twice and store
    # the same-ish URL, so the lock is about keeping the dict consistent, not
    # about avoiding duplicate work.
    with _url_lock:
        _url_cache[cache_key] = (url, now)
    return url


def put_object(storage_key: str, data: bytes, content_type: str) -> None:
    """Write one object into the upload bucket. Needs the write credential.

    Uploads are small and single-shot - an avatar is tens of kilobytes - so
    there is no multipart handling here.
    """
    bucket = settings.upload_bucket
    _write_client().put_object(
        Bucket=bucket,
        Key=storage_key,
        Body=data,
        ContentType=content_type,
    )
    with _url_lock:
        _url_cache.pop((bucket, storage_key), None)


def delete_object(storage_key: str) -> None:
    """Remove one object from the upload bucket.

    Deleting a key that does not exist is the state the caller wanted, and R2
    reports it as success anyway.
    """
    bucket = settings.upload_bucket
    _write_client().delete_object(Bucket=bucket, Key=storage_key)
    with _url_lock:
        _url_cache.pop((bucket, storage_key), None)
