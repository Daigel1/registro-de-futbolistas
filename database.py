import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Preferencia: DATABASE_URL (Postgres en Render). Si no existe, usa SQLite local.
_db_url = (os.environ.get("DATABASE_URL") or "").strip()
if _db_url.startswith("postgres://"):
    _db_url = "postgresql+psycopg://" + _db_url[len("postgres://") :]
elif _db_url.startswith("postgresql://"):
    _db_url = "postgresql+psycopg://" + _db_url[len("postgresql://") :]

if _db_url:
    SQLALCHEMY_DATABASE_URL = _db_url
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
else:
    # Siempre el mismo archivo junto a este módulo (no depende del cwd al arrancar uvicorn)
    _DB_FILE = Path(__file__).resolve().parent / "jugadores.db"
    SQLALCHEMY_DATABASE_URL = "sqlite:///" + _DB_FILE.as_posix()
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
