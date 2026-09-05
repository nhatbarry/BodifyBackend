from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# `pool_pre_ping` is not optional once the database is a hosted one behind a
# pooler. The pooler drops connections that have been idle, and SQLAlchemy has
# no way to know: the next request checks out a socket the other end has
# already closed and fails. It fails intermittently, only after a quiet spell,
# and the error says nothing about pooling - which is a long afternoon to
# diagnose and one keyword to prevent. The cost is a `SELECT 1` per checkout.
#
# `pool_recycle` retires connections before the pooler's own idle timeout gets
# to them, so the pre-ping rarely has to do the recovering.
engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)