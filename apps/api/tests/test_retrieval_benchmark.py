"""Retrieval quality benchmark using realistic frontend/backend source distractors."""

import json
from pathlib import Path

from repopilot_api.chat import MockProvider, ask, retrieve
from repopilot_api.models import ChatSession, IndexedFile, IndexingJob, Repository

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_CASES = [
    {
        "question": "How does the code indexing process work?",
        "original_expected": {"apps/api/src/repopilot_api/code_intelligence.py"},
        # process_index orchestrates indexing in main.py; code_intelligence.py
        # supplies discovery and parsing, so either is relevant implementation.
        "expected": {
            "apps/api/src/repopilot_api/main.py",
            "apps/api/src/repopilot_api/code_intelligence.py",
        },
    },
    {
        "question": "Where is the GitHub repository import API implemented?",
        "original_expected": {"apps/api/src/repopilot_api/main.py"},
        "expected": {"apps/api/src/repopilot_api/main.py"},
    },
    {
        "question": "Where is FastAPI application initialization configured?",
        "original_expected": {"apps/api/src/repopilot_api/main.py"},
        "expected": {"apps/api/src/repopilot_api/main.py"},
    },
]
BENCHMARK_PATHS = [
    "apps/api/src/repopilot_api/code_intelligence.py",
    "apps/api/src/repopilot_api/main.py",
    "apps/web/src/app/page.tsx",
]
TEST_DISTRACTOR_PATH = "apps/api/tests/test_chat.py"
UNRELATED_QUESTION = "hii"
QUALITY_PATHS = [
    "apps/api/src/repopilot_api/chat.py",
    "apps/api/src/repopilot_api/code_intelligence.py",
    "apps/api/src/repopilot_api/database.py",
    "apps/api/src/repopilot_api/main.py",
    "apps/api/src/repopilot_api/models.py",
    "apps/api/src/repopilot_api/schemas.py",
    "apps/api/tests/test_chat.py",
    "apps/api/tests/test_code_intelligence.py",
    "apps/api/tests/test_repositories.py",
    "apps/api/migrations/versions/0002_code_intelligence.py",
    "apps/api/migrations/versions/0003_repository_chat.py",
    "apps/web/src/app/page.tsx",
]

# Each answerable item has independently assigned relevant source paths. None means
# the repository does not implement the requested feature, so retrieval should abstain.
QUALITY_CASES = [
    (
        "How does the code indexing process work?",
        {"apps/api/src/repopilot_api/main.py", "apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    (
        "Which file extensions can source indexing include?",
        {"apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    (
        "How does recursive GitHub tree discovery work?",
        {"apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    (
        "How are source file bytes fetched from GitHub?",
        {"apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    (
        "How are Python functions and classes extracted as symbols?",
        {"apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    (
        "What happens when the GitHub tree is truncated?",
        {"apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    ("How does an indexing job save each fetched file?", {"apps/api/src/repopilot_api/main.py"}),
    ("What happens when a single source file fetch fails?", {"apps/api/src/repopilot_api/main.py"}),
    (
        "Which endpoint returns indexing status and symbol counts?",
        {"apps/api/src/repopilot_api/main.py"},
    ),
    ("Which endpoint lists indexed source files?", {"apps/api/src/repopilot_api/main.py"}),
    (
        "How does the repository symbol search endpoint work?",
        {"apps/api/src/repopilot_api/main.py"},
    ),
    (
        "Where is the GitHub repository import API implemented?",
        {"apps/api/src/repopilot_api/main.py"},
    ),
    (
        "How are GitHub repository URLs checked before import?",
        {"apps/api/src/repopilot_api/schemas.py"},
    ),
    ("How does repository import avoid duplicate records?", {"apps/api/src/repopilot_api/main.py"}),
    (
        "How are GitHub rate limits handled during import?",
        {"apps/api/src/repopilot_api/main.py", "apps/api/src/repopilot_api/code_intelligence.py"},
    ),
    ("Where is the FastAPI application initialized?", {"apps/api/src/repopilot_api/main.py"}),
    ("Where is the SQLAlchemy engine configured?", {"apps/api/src/repopilot_api/database.py"}),
    (
        "How does an API request obtain a database session?",
        {"apps/api/src/repopilot_api/main.py", "apps/api/src/repopilot_api/database.py"},
    ),
    ("Which fields are stored on an indexing job?", {"apps/api/src/repopilot_api/models.py"}),
    (
        "How are parsed code symbols represented in the database?",
        {"apps/api/src/repopilot_api/models.py"},
    ),
    (
        "How does a chat session stay pinned to a repository commit?",
        {"apps/api/src/repopilot_api/main.py", "apps/api/src/repopilot_api/models.py"},
    ),
    (
        "How does retrieval stay within one repository and commit?",
        {"apps/api/src/repopilot_api/chat.py"},
    ),
    (
        "How are query words weighted when ranking source files?",
        {"apps/api/src/repopilot_api/chat.py"},
    ),
    (
        "How are provider citation labels converted to source citations?",
        {"apps/api/src/repopilot_api/chat.py"},
    ),
    (
        "How does cloud chat treat repository source text in its prompt?",
        {"apps/api/src/repopilot_api/chat.py"},
    ),
    ("How is the configured chat provider selected?", {"apps/api/src/repopilot_api/chat.py"}),
    (
        "What is the maximum allowed chat question length?",
        {"apps/api/src/repopilot_api/chat.py", "apps/api/src/repopilot_api/schemas.py"},
    ),
    (
        "How does the dashboard poll until repository indexing finishes?",
        {"apps/web/src/app/page.tsx"},
    ),
    ("How does the frontend submit a repository chat question?", {"apps/web/src/app/page.tsx"}),
    (
        "Which migration creates the code indexing tables?",
        {"apps/api/migrations/versions/0002_code_intelligence.py"},
    ),
    (
        "Which migration creates chat session and message tables?",
        {"apps/api/migrations/versions/0003_repository_chat.py"},
    ),
    (
        "How are repository and indexing symbols tested?",
        {"apps/api/tests/test_code_intelligence.py"},
    ),
    (
        "How is importing a repository tested for idempotency?",
        {"apps/api/tests/test_repositories.py"},
    ),
    ("How are verified chat citations tested?", {"apps/api/tests/test_chat.py"}),
    ("Where are OAuth callback state tokens validated?", None),
    ("Where does the app calculate cosine similarity for embeddings?", None),
    ("Which function creates and verifies JWT access tokens?", None),
    ("Where is the Redis background task queue configured?", None),
    ("How does the repository deletion API remove indexed files?", None),
    ("Where are Stripe subscriptions and billing webhooks handled?", None),
]


def _make_repository(db, paths=BENCHMARK_PATHS):
    repo = Repository(
        github_id=901,
        owner="team",
        name="benchmark",
        full_name="team/benchmark",
        url="https://github.com/team/benchmark",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    job = IndexingJob(repository_id=repo.id, commit_sha="b" * 40, status="completed")
    db.add(job)
    db.flush()
    for path in paths:
        content = (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
        db.add(
            IndexedFile(
                job_id=job.id,
                path=path,
                language="typescript" if path.endswith(".tsx") else "python",
                size_bytes=len(content.encode("utf-8")),
                content=content,
            )
        )
    db.flush()
    return repo, job


def _make_quality_repository(db):
    return _make_repository(db, QUALITY_PATHS)


def _metrics(ranked_paths, expected):
    reciprocal_rank = 0.0
    for rank, path in enumerate(ranked_paths, 1):
        if path in expected:
            reciprocal_rank = 1 / rank
            break
    return {
        "hit_at_1": any(path in expected for path in ranked_paths[:1]),
        "hit_at_3": any(path in expected for path in ranked_paths[:3]),
        "hit_at_5": any(path in expected for path in ranked_paths[:5]),
        "reciprocal_rank": reciprocal_rank,
    }


def test_retrieval_baseline_benchmark(test_session):
    with test_session() as db:
        repo, job = _make_repository(db)
        session = ChatSession(repository_id=repo.id, commit_sha=job.commit_sha)
        db.add(session)
        db.flush()
        per_question = []
        failures = []
        for case in BENCHMARK_CASES:
            evidence = retrieve(db, repo.id, job.commit_sha, case["question"])
            ranked_paths = [item.path for item in evidence]
            original_metrics = _metrics(ranked_paths, case["original_expected"])
            expanded_metrics = _metrics(ranked_paths, case["expected"])
            chat_response = ask(db, session, case["question"], MockProvider())
            citations = json.loads(chat_response.citations)
            per_question.append(
                {
                    "question": case["question"],
                    "original_expected": sorted(case["original_expected"]),
                    "expanded_expected": sorted(case["expected"]),
                    "top_5": ranked_paths[:5],
                    "citations": citations,
                    "original_metrics": original_metrics,
                    "expanded_metrics": expanded_metrics,
                }
            )
            if not expanded_metrics["hit_at_1"]:
                failures.append(
                    f"{case['question']!r}: expected one of {sorted(case['expected'])} "
                    f"at rank 1; got {ranked_paths[:5]}"
                )
        greeting_response = ask(db, session, UNRELATED_QUESTION, MockProvider())
        greeting_citations = json.loads(greeting_response.citations)

    case_count = len(BENCHMARK_CASES)
    metric_names = ("hit_at_1", "hit_at_3", "hit_at_5", "reciprocal_rank")
    aggregate = {
        label: {
            metric: sum(case[f"{label}_metrics"][metric] for case in per_question) / case_count
            for metric in metric_names
        }
        for label in ("original", "expanded")
    }
    results = {
        "aggregate": aggregate,
        "questions": per_question,
        "unrelated_question": {
            "question": UNRELATED_QUESTION,
            "citations": greeting_citations,
            "passed": not greeting_citations,
        },
    }
    print("\nRetrieval baseline benchmark:\n" + json.dumps(results, indent=2))

    assert not failures, "Expected implementation file was not ranked first:\n" + "\n".join(
        failures
    )
    assert not greeting_citations, "Unrelated greeting received a source citation"


def test_indexing_question_ranks_orchestrator_above_test_distractor(test_session):
    orchestrator = "apps/api/src/repopilot_api/main.py"
    with test_session() as db:
        repo, job = _make_repository(db, [*BENCHMARK_PATHS, TEST_DISTRACTOR_PATH])
        session = ChatSession(repository_id=repo.id, commit_sha=job.commit_sha)
        db.add(session)
        db.flush()

        evidence = retrieve(db, repo.id, job.commit_sha, BENCHMARK_CASES[0]["question"])
        paths = [item.path for item in evidence]
        response = ask(db, session, BENCHMARK_CASES[0]["question"], MockProvider())
        citations = json.loads(response.citations)

    assert paths[0] == orchestrator
    assert TEST_DISTRACTOR_PATH in paths
    process_index_line = next(
        number
        for number, line in enumerate(
            (REPOSITORY_ROOT / orchestrator).read_text(encoding="utf-8").splitlines(),
            1,
        )
        if line.startswith("def process_index(")
    )
    assert any(
        citation["path"] == orchestrator
        and citation["start_line"] <= process_index_line <= citation["end_line"]
        for citation in citations
    )


def test_retrieval_abstains_when_named_technology_is_absent(test_session):
    with test_session() as db:
        repo, job = _make_repository(db)
        results = retrieve(
            db,
            repo.id,
            job.commit_sha,
            "Where are OAuth callback state tokens validated?",
        )
    assert results == []


def test_retrieval_abstains_when_requested_operation_is_missing_from_citation_chunk(
    test_session,
):
    questions = [
        "How does the repository deletion API remove indexed files?",
        "Where can indexed files be deleted?",
    ]
    with test_session() as db:
        repo, job = _make_quality_repository(db)
        for question in questions:
            assert retrieve(db, repo.id, job.commit_sha, question) == []


def test_retrieval_quality_evaluation(test_session):
    answerable = [case for case in QUALITY_CASES if case[1] is not None]
    no_answer = [case for case in QUALITY_CASES if case[1] is None]
    totals = {name: 0.0 for name in ("hit_at_1", "hit_at_3", "hit_at_5", "reciprocal_rank")}
    false_positives = []
    ranking_failures = []
    answered_count = 0
    with test_session() as db:
        repo, job = _make_quality_repository(db)
        for question, expected in answerable:
            results = retrieve(db, repo.id, job.commit_sha, question)
            answered_count += bool(results)
            paths = [item.path for item in results]
            metrics = _metrics(paths, expected)
            for name, value in metrics.items():
                totals[name] += value
            if not metrics["hit_at_1"]:
                ranking_failures.append(
                    {
                        "question": question,
                        "expected": sorted(expected),
                        "top_3": [item.path for item in results[:3]],
                    }
                )
        for question, _ in no_answer:
            results = retrieve(db, repo.id, job.commit_sha, question)
            if results:
                false_positives.append((question, results[0].path, round(results[0].score, 4)))

    report = {
        "question_count": len(QUALITY_CASES),
        "answerable_count": len(answerable),
        "answerable_recall": answered_count / len(answerable),
        "no_answer_count": len(no_answer),
        "answerable_metrics": {name: value / len(answerable) for name, value in totals.items()},
        "rank1_failure_count": len(ranking_failures),
        "no_answer_false_positive_rate": len(false_positives) / len(no_answer),
        "no_answer_false_positives": false_positives,
    }
    print("\nRetrieval quality evaluation:\n" + json.dumps(report, indent=2))
    assert len(false_positives) / len(no_answer) <= 1 / 6, "No-answer false-positive rate regressed"
    assert totals["hit_at_5"] / len(answerable) >= 0.90, "Answerable recall@5 regressed"
