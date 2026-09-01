from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRead(BaseModel):
    """Read-only view of a Supabase Auth user (auth.users).

    There is no Create/Update schema here - accounts are created/managed
    entirely through the Supabase Auth API, not this backend.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr | None
    created_at: datetime | None
