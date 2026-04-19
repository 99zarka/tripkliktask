"""
database.py
-----------
Manages the SQLAlchemy engine and session.

Environment logic:
  - When running locally (outside Docker), reads DATABASE_URL from .env
    which points to the remote Aiven PostgreSQL instance.
  - When running inside Docker, DATABASE_URL is injected by docker-compose
    and points to the containerized Postgres service.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

# Load .env for local development (no-op inside Docker where vars are injected)
load_dotenv()

AIVEN_URL = (
    "postgres://avnadmin:AVNS_d6wr1pxxL-JGTF-VDAk"
    "@pg-tripkliktask-zarka-5f8e.d.aivencloud.com:27736"
    "/defaultdb?sslmode=require"
)

DATABASE_URL = os.getenv("DATABASE_URL", AIVEN_URL)

# psycopg2 requires "postgresql://" not "postgres://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # Detect stale connections
    pool_size=10,
    max_overflow=20,
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and ensures it is closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables and enable required PostgreSQL extensions."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS earthdistance CASCADE;"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    print("[DB] Schema created / verified.")
