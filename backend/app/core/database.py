from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

log      = get_logger(__name__)
settings = get_settings()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,
    echo=settings.db_echo,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_all_tables() -> None:
    Base.metadata.create_all(bind=engine)
    log.info("database.tables_created")


def drop_all_tables() -> None:
    Base.metadata.drop_all(bind=engine)
    log.info("database.tables_dropped")


def check_connection() -> bool:
    """
    Attempt a real SQL query. Logs the URL being used (password masked)
    so startup failures are easy to diagnose.
    """
    # Mask password for logging
    safe_url = settings.database_url
    try:
        import re
        safe_url = re.sub(r":([^@/]+)@", ":***@", settings.database_url)
    except Exception:
        pass

    log.info("db.checking", url=safe_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("db.check_failed", url=safe_url, error=str(exc))
        return False
