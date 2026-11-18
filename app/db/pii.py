"""Masked personal data: the admin requests of anyone but a superadmin run
under the database role `bodify_pii_masked` (sql_script/ddl_pii_masking.sql).

That role cannot read `auth.*`, `user_profiles` or the free text people
typed; accounts come from `masked.accounts`, which masks them for it. So the
rule is Postgres's, not this code's: a query written later that forgets to
mask is refused, not answered.

`SET LOCAL` ends with the transaction, so a pooled connection goes back
clean, and a request that commits midway (every catalog write does) has the
role put back on its next transaction by the `after_begin` hook below.
"""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

MASKED_ROLE = "bodify_pii_masked"
_SET_ROLE = f"SET LOCAL ROLE {MASKED_ROLE}"
_FLAG = "pii_masked"


def mask_personal_data(db: Session) -> None:
    """From now until the session closes, every transaction is masked."""
    db.info[_FLAG] = True
    # The token check already opened a transaction; switch it too.
    if db.in_transaction():
        db.execute(text(_SET_ROLE))


def is_masked(db: Session) -> bool:
    return bool(db.info.get(_FLAG))


@event.listens_for(Session, "after_begin")
def _reapply(session: Session, transaction, connection) -> None:
    if session.info.get(_FLAG):
        connection.exec_driver_sql(_SET_ROLE)
