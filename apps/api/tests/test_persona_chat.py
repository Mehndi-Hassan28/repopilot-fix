"""Tests for context-aware persona behavior in RepoPilot Fix assistant.

Verifies:
1. Software Engineer persona focuses on AST symbols, unified diffs, and code architecture.
2. Security Auditor persona focuses on sandboxes, non-root users, and SHA-256 spec hashes.
3. QA Lead persona focuses on baseline reproduction, delta test matrices, and zero regressions.
4. Chat endpoint integrates persona parameter into assistant responses.
"""

from fastapi.testclient import TestClient

from repopilot_api.chat import MockProvider, ask
from repopilot_api.main import app
from repopilot_api.models import ChatSession, IndexingJob, Repository, RepositoryAccess, User


def _setup_test_repo_and_session(db):
    user = User()
    db.add(user)
    db.commit()

    repo = Repository(
        github_id=999,
        owner="testowner",
        name="testrepo",
        full_name="testowner/testrepo",
        url="https://github.com/testowner/testrepo",
        default_branch="main",
        stars=10,
    )
    db.add(repo)
    db.commit()

    access = RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner")
    db.add(access)

    job = IndexingJob(
        repository_id=repo.id,
        commit_sha="1111222233334444555566667777888899990000",
        status="completed",
        file_count=5,
    )
    db.add(job)
    db.commit()

    session = ChatSession(
        repository_id=repo.id,
        owner_user_id=user.id,
        commit_sha=job.commit_sha,
    )
    db.add(session)
    db.commit()
    return user, repo, session


def test_mock_provider_persona_general_behavior():
    provider = MockProvider()

    # Security Auditor persona
    sec_answer = provider.answer_general("How does verification work?", persona="security_auditor")
    assert "Security & Compliance Auditor" in sec_answer
    assert "--network none" in sec_answer
    assert "SHA-256" in sec_answer or "SHA256" in sec_answer

    # QA Lead persona
    qa_answer = provider.answer_general("How does verification work?", persona="qa_lead")
    assert "QA & Reliability Lead" in qa_answer
    assert "Baseline Reproduction" in qa_answer
    assert "new_failed_test_ids == 0" in qa_answer

    # Default / Software Engineer persona
    swe_answer = provider.answer_general("recursion", persona="software_engineer")
    assert "Recursion" in swe_answer


def test_mock_provider_persona_coding_behavior():
    provider = MockProvider()

    sec_code = provider.answer_coding("How to handle inputs safely?", persona="security_auditor")
    assert "Security Audit" in sec_code
    assert "least privilege" in sec_code.lower() or "boundary" in sec_code.lower()

    qa_code = provider.answer_coding("Write a test case", persona="qa_lead")
    assert "QA Test Case" in qa_code
    assert "reproduction" in qa_code.lower()


def test_ask_verified_fix_persona_tailoring(test_session):
    with test_session() as db:
        _, _, session = _setup_test_repo_and_session(db)
        provider = MockProvider()

        # Security Auditor in verified fix
        msg_sec = ask(
            db, session, "Investigate issue #42", provider=provider, persona="security_auditor"
        )
        assert "Security Auditor" in msg_sec.content
        assert "Spec Hash" in msg_sec.content

        # QA Lead in verified fix
        msg_qa = ask(db, session, "Investigate issue #42", provider=provider, persona="qa_lead")
        assert "QA & Reliability Lead" in msg_qa.content
        assert "Zero Regressions" in msg_qa.content


def test_chat_api_endpoint_with_persona(test_session, monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "mock")
    with test_session() as db:
        user, repo, session = _setup_test_repo_and_session(db)
        session_id = session.id

    client = TestClient(app)

    # Simulate authenticated session
    headers = {
        "x-user-id": str(user.id),
        "x-csrf-token": "test-csrf-token",
        "cookie": "session_user_id=1; csrf_token=test-csrf-token",
    }

    from datetime import UTC, datetime

    from repopilot_api.dependencies import get_db
    from repopilot_api.models import AuthSession
    from repopilot_api.security import AuthContext, get_current_auth, require_csrf

    auth = AuthContext(
        user=user,
        session=AuthSession(
            user_id=user.id, token_hash="h", csrf_hash="c", expires_at=datetime.now(UTC)
        ),
    )

    def override_get_db():
        with test_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_csrf] = lambda: auth
    app.dependency_overrides[get_current_auth] = lambda: auth

    try:
        response = client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "question": "What are the security constraints?",
                "mode": "general",
                "persona": "security_auditor",
            },
            headers=headers,
        )
        assert response.status_code == 200, response.json()
        data = response.json()
        assert "Security & Compliance Auditor" in data["content"]
        assert "--network none" in data["content"]
    finally:
        app.dependency_overrides.clear()
