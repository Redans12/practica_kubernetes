"""Conexión a PostgreSQL (SQLAlchemy síncrono + psycopg 3)."""
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine():
    s = get_settings()
    return create_engine(
        s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,      # descarta conexiones muertas tras una caída de la BD
        pool_recycle=1800,
        connect_args={"connect_timeout": 3},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db():
    """Dependencia de FastAPI: una sesión por petición."""
    db: Session = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
