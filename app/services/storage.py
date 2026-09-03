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
_url_cache: dict[str, tuple[str, float]] = {}
_url_lock = Lock()


def presigned_url(storage_key: str, expires_in: int = EXPIRES_IN) -> str:
    """Signed GET URL for one object in the media bucket.

    Stable for URL_REUSE_SECONDS, so repeat requests for the same object get
    byte-identical URLs and downstream caches can recognise them.

    Signing is local computation - it does not call the storage service, so a
    URL comes back even when the bucket is unreachable.
    """
    now = time.monotonic()

    cached = _url_cache.get(storage_key)
    if cached is not None and now - cached[1] < URL_REUSE_SECONDS:
        return cached[0]

    url = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.storage_bucket, "Key": storage_key},
        ExpiresIn=expires_in,
    )

    # Two threads racing here would only sign the same object twice and store
    # the same-ish URL, so the lock is about keeping the dict consistent, not
    # about avoiding duplicate work.
    with _url_lock:
        _url_cache[storage_key] = (url, now)
    return url
