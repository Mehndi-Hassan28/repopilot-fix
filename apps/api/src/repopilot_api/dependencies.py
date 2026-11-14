"""Shared FastAPI dependencies."""

from repopilot_api.database import SessionLocal


def get_db():
    with SessionLocal() as session:
        yield session
