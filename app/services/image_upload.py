"""Reading a picture a user uploaded, before anything is done with it.

Shared by avatars and meal photos. Both arrive from an untrusted device, so
both get the same treatment before either is resized its own way:

* the declared content type is never trusted - Pillow has to actually decode
  the bytes, so a `.jpg` full of something else fails here rather than being
  served back to anyone;
* the EXIF rotation is applied and the EXIF itself discarded. Phone photos
  carry **GPS coordinates**, and a meal photo taken at home says where home is;
* transparency is flattened onto white, so nothing turns black as webp.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

# Generous for a photo, small enough that a malicious upload cannot occupy the
# server for long. Checked against the actual bytes read, never against a
# Content-Length header the client controls.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Formats Pillow will open for us. Anything else is refused by name here as
# well as by the decode attempt, so the error can say something useful.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}


class InvalidImage(ValueError):
    """The upload is not an image we can use."""


def decode_upload(raw: bytes) -> Image.Image:
    """Bytes from a device -> an upright RGB image with no metadata attached."""
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
                return flattened
            return image.convert("RGB")
    except InvalidImage:
        raise
    except UnidentifiedImageError as exc:
        raise InvalidImage("That file is not an image") from exc
    except Exception as exc:  # noqa: BLE001 - a corrupt image is user error
        raise InvalidImage("Could not read that image") from exc
