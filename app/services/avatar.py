"""Profile pictures: validating, resizing and storing what a user uploads.

Unlike the exercise catalog - 704 fixed public files, identical for everyone -
an avatar is personal, mutable, and arrives from an untrusted device. That
changes three things:

* the bytes are re-encoded rather than stored as received, so nothing the
  server hands back is a file a stranger chose the contents of;
* every upload gets a **new key**, so a changed avatar can never be served
  from a cache keyed on the old one;
* the URL is signed, because a profile picture is not public data.
"""

from __future__ import annotations

import io
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.services.storage import delete_object, presigned_url, put_object

# Rendered at 40-100 pt. 512 px covers that on any screen and survives a
# future larger header; a phone photo is 3-8 MB and 4000 px, which would be
# absurd to send down for a circle the size of a thumbnail.
AVATAR_SIZE = 512
AVATAR_QUALITY = 82
AVATAR_CONTENT_TYPE = "image/webp"

# Generous for a photo, small enough that a malicious upload cannot occupy the
# server for long. Checked against the actual bytes read, never against a
# Content-Length header the client controls.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Formats Pillow will open for us. Anything else is refused by name here as
# well as by the decode attempt below, so the error can say something useful.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}


class InvalidImage(ValueError):
    """The upload is not an image we can use."""


def avatar_key(user_id: uuid.UUID) -> str:
    """A fresh key for one upload.

    New uuid every time, deliberately. Overwriting a fixed key would leave
    every cache - the app's disk cache, any CDN, the signed-URL cache - serving
    the previous picture with no way to tell it had changed.
    """
    return f"avatar/{user_id}/{uuid.uuid4().hex}.webp"


def avatar_url(storage_key: str | None) -> str | None:
    """Sign an avatar key into a URL, or None when there is no avatar.

    Signed against the **upload** bucket, not the catalog one. Both are signed
    with the read-only credential, which can read either; the write credential
    is scoped to the upload bucket and cannot even list the catalog.
    """
    if not storage_key:
        return None
    return presigned_url(storage_key, bucket=settings.upload_bucket)


def process_upload(raw: bytes) -> bytes:
    """Turn an uploaded file into a square webp avatar.

    Re-encoding is the point, not a side effect:

    * **EXIF is dropped.** Phone photos carry GPS coordinates. An avatar that
      leaks where the user lives is a real privacy failure, and stripping it
      is not optional.
    * The declared content type is never trusted - Pillow has to actually
      decode the bytes, so a `.jpg` full of something else fails here rather
      than being served back to other users.
    * The result is one predictable format and size, so the client can cache
      and lay it out without surprises.
    """
    if not raw:
        raise InvalidImage("Empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidImage(
            f"Image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format or "").upper() not in ALLOWED_FORMATS:
                raise InvalidImage(f"Unsupported image format: {image.format}")

            # Applies the EXIF rotation, then discards the EXIF with it -
            # without this a portrait photo would be stored sideways.
            image = ImageOps.exif_transpose(image)

            # Flatten transparency onto white: a PNG with an alpha channel
            # would otherwise turn black when saved as webp.
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                flattened = Image.new("RGB", image.size, (255, 255, 255))
                flattened.paste(image, mask=image.split()[-1])
                image = flattened
            else:
                image = image.convert("RGB")

            # Centre-crop to a square before resizing. The UI draws avatars in
            # a circle, so a non-square image would be cropped anyway - doing
            # it here means the stored file has no wasted pixels.
            square = ImageOps.fit(
                image, (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS, centering=(0.5, 0.5)
            )

            buffer = io.BytesIO()
            square.save(buffer, "WEBP", quality=AVATAR_QUALITY, method=6)
            return buffer.getvalue()
    except InvalidImage:
        raise
    except UnidentifiedImageError as exc:
        raise InvalidImage("That file is not an image") from exc
    except Exception as exc:  # noqa: BLE001 - a corrupt image is user error
        raise InvalidImage("Could not read that image") from exc


def store_avatar(user_id: uuid.UUID, raw: bytes, previous_key: str | None) -> str:
    """Process, upload, and remove whatever the user had before.

    The old object is deleted only after the new one is safely up: losing the
    current avatar because the replacement failed would be the worse outcome.
    A failure to delete is swallowed - the user has their new picture, and a
    stray object is a housekeeping problem, not something to fail the request
    over.
    """
    processed = process_upload(raw)
    key = avatar_key(user_id)
    put_object(key, processed, AVATAR_CONTENT_TYPE)

    if previous_key and previous_key != key:
        try:
            delete_object(previous_key)
        except Exception:  # noqa: BLE001
            pass

    return key
