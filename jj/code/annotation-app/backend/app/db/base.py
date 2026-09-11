from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import models so they are registered on Base.metadata for Alembic/autocreate.
import app.db.models  # noqa: F401,E402
