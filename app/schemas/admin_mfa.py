from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MfaFactorRead(BaseModel):
    id: uuid.UUID
    friendly_name: str | None = None
    created_at: datetime | None = None


class MfaStatusRead(BaseModel):
    # An authenticator app is set up and confirmed on this account.
    enrolled: bool
    # This session already passed the code check ("aal2").
    verified: bool
    factors: list[MfaFactorRead]


class MfaEnrollRequest(BaseModel):
    # What the admin calls this device, e.g. "Điện thoại". Must be unique
    # per account; defaults to a timestamped name.
    friendly_name: str | None = Field(default=None, min_length=1, max_length=60)


class MfaEnrollResponse(BaseModel):
    factor_id: uuid.UUID
    # A data URI (`data:image/svg+xml;...`), usable straight in <img src>.
    qr_code: str
    # For typing in by hand when the camera cannot scan.
    secret: str
    uri: str


class MfaVerifyRequest(BaseModel):
    factor_id: uuid.UUID
    code: str = Field(pattern=r"^\d{6}$")
