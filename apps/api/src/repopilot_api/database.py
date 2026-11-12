import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL", "postgresql+psycopg://repopilot:repopilot@localhost:5432/repopilot"
    )


def make_engine(url: str | None = None):
    return create_engine(url or database_url(), pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
