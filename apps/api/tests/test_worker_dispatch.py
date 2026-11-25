"""Unit tests for on-demand cloud worker dispatch and status monitoring."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from repopilot_api.database import Base
from repopilot_api.models import Repository, User, VerificationRun
from repopilot_api.schemas import VerificationJobStatus
from repopilot_api.worker_dispatch import (
    dispatch_cloud_worker,
    get_cloud_worker_status,
    is_cloud_worker_dispatch_configured,
)


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_is_cloud_worker_dispatch_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_WORKER_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert not is_cloud_worker_dispatch_configured()

    monkeypatch.setenv("GITHUB_WORKER_DISPATCH_TOKEN", "ghp_mock_token_12345")
    assert is_cloud_worker_dispatch_configured()


def test_get_cloud_worker_status_no_worker(test_db, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKER_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    status = get_cloud_worker_status(test_db)
    assert not status["cloud_worker_available"]
    assert not status["cloud_worker_dispatch_configured"]
    assert "No active cloud verification worker" in status["worker_status_message"]


def test_get_cloud_worker_status_dispatch_ready(test_db, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKER_DISPATCH_TOKEN", "ghp_dispatch_secret")

    status = get_cloud_worker_status(test_db)
    assert status["cloud_worker_available"]
    assert status["cloud_worker_dispatch_configured"]
    assert "On-demand GitHub Actions" in status["worker_status_message"]


def test_get_cloud_worker_status_active_heartbeat(test_db, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKER_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    user = User()
    test_db.add(user)
    test_db.flush()

    repo = Repository(
        owner="repopilot",
        name="demo-calc-1",
        full_name="repopilot/demo-calc-1",
        default_branch="main",
        url="https://github.com/repopilot/demo-calculator",
        github_id=990000001,
    )
    test_db.add(repo)
    test_db.flush()

    now = datetime.now(UTC)
    active_run = VerificationRun(
        repository_id=repo.id,
        user_id=user.id,
        issue_number=1,
        commit_sha="a" * 40,
        sandbox_image="python:3.11-slim",
        status=VerificationJobStatus.RUNNING.value,
        worker_id="gha-worker-01",
        last_heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=60),
    )
    test_db.add(active_run)
    test_db.commit()

    status = get_cloud_worker_status(test_db)
    assert status["cloud_worker_available"]
    assert "gha-worker-01" in status["worker_status_message"]


@pytest.mark.anyio
async def test_dispatch_cloud_worker_success(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKER_DISPATCH_TOKEN", "ghp_test_token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "testowner/testrepo")

    mock_resp = AsyncMock()
    mock_resp.status_code = 204

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        result = await dispatch_cloud_worker(run_id=42, repository_id=7)
        assert result is True

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "https://api.github.com/repos/testowner/testrepo/dispatches" in args[0]
        assert kwargs["json"]["event_type"] == "repopilot_verify"
        assert kwargs["json"]["client_payload"]["run_id"] == 42


@pytest.mark.anyio
async def test_dispatch_cloud_worker_unconfigured(monkeypatch):
    monkeypatch.delenv("GITHUB_WORKER_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = await dispatch_cloud_worker(run_id=1, repository_id=1)
    assert result is False
