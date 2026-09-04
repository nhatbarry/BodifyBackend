"""Avatar uploads: what the server refuses, and what it strips.

The image processing is tested directly rather than through the endpoint,
because it is the part that matters and it needs no storage credentials to
exercise. The endpoint tests cover the paths around it.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.services.avatar import (
    AVATAR_SIZE,
    MAX_UPLOAD_BYTES,
    InvalidImage,
    avatar_key,
    process_upload,
)
from tests.conftest import API


def _photo(width: int, height: int, fmt: str = "JPEG", exif: bytes | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (120, 80, 200))
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()


# ============================================================
# Processing
# ============================================================


def test_a_photo_becomes_a_square_webp():
    """The UI draws avatars in a circle, so a non-square image would be
    cropped anyway; doing it here means no wasted pixels are stored."""
    processed = process_upload(_photo(1600, 900))

    with Image.open(io.BytesIO(processed)) as result:
        assert result.format == "WEBP"
        assert result.size == (AVATAR_SIZE, AVATAR_SIZE)


def test_a_phone_photo_shrinks_by_orders_of_magnitude():
    original = _photo(3000, 4000)
    processed = process_upload(original)
    assert len(processed) < len(original) / 10


def test_exif_is_stripped():
    """Phone photos carry GPS coordinates. An avatar that leaks where the user
    lives is a real privacy failure, not a tidiness issue."""
    exif = Image.Exif()
    exif[0x010F] = "Bodify Test Camera"  # Make
    original = _photo(800, 800, exif=exif.tobytes())

    # Sanity: the tag really is in the input, or this test proves nothing.
    with Image.open(io.BytesIO(original)) as source:
        assert source.getexif().get(0x010F) == "Bodify Test Camera"

    with Image.open(io.BytesIO(process_upload(original))) as result:
        assert not result.getexif()


def test_orientation_is_applied_not_discarded():
    """Without exif_transpose a portrait photo is stored sideways."""
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW
    processed = process_upload(_photo(1000, 500, exif=exif.tobytes()))
    with Image.open(io.BytesIO(processed)) as result:
        assert result.size == (AVATAR_SIZE, AVATAR_SIZE)


def test_transparency_is_flattened_onto_white():
    """A PNG with an alpha channel turns black when saved as webp unless the
    transparency is composited first."""
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    with Image.open(io.BytesIO(process_upload(buffer.getvalue()))) as result:
        assert result.convert("RGB").getpixel((10, 10)) == (255, 255, 255)


@pytest.mark.parametrize(
    "payload, reason",
    [
        pytest.param(b"", "empty", id="empty upload"),
        pytest.param(b"not an image at all", "image", id="plain text"),
        pytest.param(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image", id="truncated png header"
        ),
    ],
)
def test_things_that_are_not_images_are_refused(payload, reason):
    """A declared content type is whatever the client says. Only a successful
    decode proves anything."""
    with pytest.raises(InvalidImage):
        process_upload(payload)


def test_an_oversized_upload_is_refused_before_decoding():
    with pytest.raises(InvalidImage, match="larger than"):
        process_upload(b"\x00" * (MAX_UPLOAD_BYTES + 1))


def test_every_upload_gets_a_new_key():
    """Overwriting one fixed key would leave every cache - the app's disk
    cache, any CDN, the signed-URL cache - serving the previous picture."""
    import uuid

    user = uuid.uuid4()
    assert avatar_key(user) != avatar_key(user)
    assert avatar_key(user).startswith(f"avatar/{user}/")


# ============================================================
# The endpoint
# ============================================================


def test_avatar_upload_requires_a_signed_in_caller(client: TestClient):
    response = client.post(
        f"{API}/profile/avatar", files={"file": ("a.jpg", _photo(100, 100), "image/jpeg")}
    )
    assert response.status_code == 401


def test_a_non_image_answers_422_not_500(client: TestClient, owner):
    response = client.post(
        f"{API}/profile/avatar",
        headers=owner,
        files={"file": ("payload.jpg", b"definitely not a jpeg", "image/jpeg")},
    )
    assert response.status_code == 422


@pytest.mark.skipif(
    settings.can_write_storage,
    reason="write credentials are configured, so uploads should succeed",
)
def test_upload_says_so_plainly_when_storage_is_not_configured(
    client: TestClient, owner
):
    """A configuration gap is not the caller's fault, and should not surface
    as an opaque AccessDenied from R2."""
    response = client.post(
        f"{API}/profile/avatar",
        headers=owner,
        files={"file": ("a.jpg", _photo(300, 300), "image/jpeg")},
    )
    assert response.status_code == 503


@pytest.mark.skipif(
    not settings.can_write_storage, reason="needs R2 write credentials"
)
def test_upload_then_remove_round_trip(client: TestClient, owner):
    """The whole path, against the real bucket.

    The fetch matters as much as the upload: it proves the URL was signed
    against the *upload* bucket with a credential that can read it. Signing an
    avatar against the catalog bucket would produce a plausible-looking URL
    that 403s, and nothing else here would catch that.
    """
    import urllib.request

    uploaded = client.post(
        f"{API}/profile/avatar",
        headers=owner,
        files={"file": ("a.jpg", _photo(900, 1200), "image/jpeg")},
    )
    assert uploaded.status_code == 200
    url = uploaded.json()["avatar_url"]
    assert url and url.startswith("http"), "the key should be signed on the way out"

    with urllib.request.urlopen(url, timeout=20) as response:
        body = response.read()
    assert response.status == 200
    with Image.open(io.BytesIO(body)) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (AVATAR_SIZE, AVATAR_SIZE)

    removed = client.delete(f"{API}/profile/avatar", headers=owner)
    assert removed.status_code == 200
    assert removed.json()["avatar_url"] is None

    # The object is really gone, not just unlinked from the row.
    try:
        urllib.request.urlopen(url, timeout=20)
        raise AssertionError("the deleted object is still downloadable")
    except urllib.error.HTTPError as exc:
        assert exc.code in (403, 404)
