"""Meal photos: what a user photographs on their plate.

Follows the avatar path (`services/avatar.py`) rather than inventing one - an
untrusted upload, re-encoded, stored under a fresh key in the upload bucket,
signed on read - with one difference: it is **not cropped**. An avatar is
drawn in a circle, so a square loses nothing; a plate photo is evidence of
what was eaten, and cropping it could cut a dish off the edge.

The processed file is also what the model is shown, never the original: it is
a fraction of the size to send, and it carries no EXIF - the photo of dinner
that goes to Google does not say where the user lives.
"""

from __future__ import annotations

import io
import uuid

from PIL import Image

from app.core.config import settings
from app.services.image_upload import decode_upload
from app.services.storage import delete_object, presigned_url, put_object

# The long side. Enough to tell rice from noodles and read a bowl's size on a
# full-width card, a few hundred KB at most against a 3-8 MB original.
PHOTO_MAX_SIDE = 1280
PHOTO_QUALITY = 80
PHOTO_CONTENT_TYPE = "image/webp"


def photo_key(user_id: uuid.UUID) -> str:
    """A fresh key per upload, for the same reason as avatars: a replaced
    photo must never be served from a cache keyed on the old one."""
    return f"meal/{user_id}/{uuid.uuid4().hex}.webp"


def photo_url(storage_key: str | None) -> str | None:
    if not storage_key:
        return None
    return presigned_url(storage_key, bucket=settings.upload_bucket)


def process_photo(raw: bytes) -> bytes:
    """Decode, shrink to fit PHOTO_MAX_SIDE, re-encode as webp. Never enlarges."""
    image = decode_upload(raw)
    image.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=PHOTO_QUALITY, method=6)
    return buffer.getvalue()


def store_photo(user_id: uuid.UUID, raw: bytes) -> tuple[str, bytes]:
    """Process and upload. Returns the key and the bytes that were stored, so
    the analysis can show the model exactly what the user will see."""
    processed = process_photo(raw)
    key = photo_key(user_id)
    put_object(key, processed, PHOTO_CONTENT_TYPE)
    return key, processed


def discard(storage_key: str | None) -> None:
    """Remove a photo nobody points at any more.

    Called only after the row has stopped referencing it, and failures are
    swallowed: the user's request succeeded, and a stray object is a
    housekeeping problem, not a reason to fail it.
    """
    if not storage_key:
        return
    try:
        delete_object(storage_key)
    except Exception:  # noqa: BLE001
        pass
