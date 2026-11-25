import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from repopilot_api.database import Base


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def default_mock_provider(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "mock")


@pytest.fixture(autouse=True)
def reset_rate_limits():
    from repopilot_api.verification_service import _RATE_LIMIT_BUCKETS

    _RATE_LIMIT_BUCKETS.clear()
    yield
    _RATE_LIMIT_BUCKETS.clear()


@pytest.fixture
def test_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()
