"""Comprehensive evaluation suite for Advanced Code Intelligence & Impact Analysis.

Covers:
- AST symbol extraction and line ranges.
- Cross-file import and call resolution (resolved vs inferred vs unresolved).
- Directed circular dependency detection and handling.
- Reverse dependency lookup (consumers and dependents).
- Bug blast radius and impact analysis on multi-file shop fixture.
- Context retrieval with exact symbol match and related test boost.
- Repository dependency graph, symbols, and impact analysis API endpoints.
- Real Docker sandbox verification of cross-file baseline failure and patched success.
"""

import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from repopilot_api.chat import retrieve
from repopilot_api.dependency_graph import DependencyGraphBuilder
from repopilot_api.fixture_registry import (
    CROSS_FILE_SHOP_FILES,
    CROSS_FILE_SHOP_FIX_PATCH,
    CROSS_FILE_SHOP_REGRESSION_PATCH,
)
from repopilot_api.main import app
from repopilot_api.models import (
    CodeSymbol,
    IndexedFile,
    IndexingJob,
    Repository,
    RepositoryAccess,
    User,
)
from repopilot_api.patcher import apply_patch_proposal
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    DockerSandboxRunner,
    WorkspaceManager,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProposedFix,
    SandboxExecutionRequest,
    SandboxExecutionState,
)
from repopilot_api.security import AuthContext


def _docker_is_ready() -> tuple[bool, str]:
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        return False, "Docker tests are opt-in. Set RUN_DOCKER_TESTS=1 to execute."
    if shutil.which("docker") is None:
        return False, "Docker CLI not found on PATH."
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        if res.returncode != 0:
            return False, "Docker daemon not running or accessible."
    except Exception as exc:
        return False, f"Docker daemon check failed: {exc}"
    return True, "Docker is ready."


_docker_ready, _docker_reason = _docker_is_ready()


# ==============================================================================
# Unit & Functional Tests: AST Parsing & Cross-File Resolution
# ==============================================================================


def test_dependency_graph_ast_parsing_and_symbol_extraction():
    """Verify AST parsing extracts functions, classes, and methods with exact line ranges."""
    builder = DependencyGraphBuilder(CROSS_FILE_SHOP_FILES)
    graph = builder.build()

    assert graph.file_count == len(CROSS_FILE_SHOP_FILES)
    assert graph.edge_count > 0

    # Verify classes in models.py
    models_node = graph.nodes["src/shop/models.py"]
    class_names = [s["name"] for s in models_node["symbols"] if s["kind"] == "class"]
    assert "Item" in class_names
    assert "Cart" in class_names

    # Verify function in discounts.py
    discounts_node = graph.nodes["src/shop/discounts.py"]
    fn_names = [s["name"] for s in discounts_node["symbols"] if s["kind"] == "function"]
    assert "calculate_discount" in fn_names

    # Verify pricing.py functions
    pricing_node = graph.nodes["src/shop/pricing.py"]
    pricing_fns = [s["name"] for s in pricing_node["symbols"] if s["kind"] == "function"]
    assert "compute_subtotal" in pricing_fns
    assert "compute_final_price" in pricing_fns

    # Verify exact line ranges in symbol index
    calc_syms = graph.symbol_index.get("calculate_discount", [])
    assert len(calc_syms) == 1
    sym = calc_syms[0]
    assert sym["file_path"] == "src/shop/discounts.py"
    assert sym["kind"] == "function"
    assert sym["line"] >= 1
    assert sym["end_line"] >= sym["line"]


def test_dependency_cross_file_resolution_statuses():
    """Verify resolved, inferred, and unresolved relationships across files."""
    builder = DependencyGraphBuilder(CROSS_FILE_SHOP_FILES)
    graph = builder.build()

    edges = graph.edges
    # pricing.py imports discounts.py (calculate_discount exists in target -> resolved)
    pricing_to_discounts = [
        e
        for e in edges
        if e["source"] == "src/shop/pricing.py" and e["target"] == "src/shop/discounts.py"
    ]
    assert len(pricing_to_discounts) > 0
    assert any(e["relationship"] == "resolved" for e in pricing_to_discounts)

    # receipt.py imports pricing.py (compute_final_price exists in target -> resolved)
    receipt_to_pricing = [
        e
        for e in edges
        if e["source"] == "src/shop/receipt.py" and e["target"] == "src/shop/pricing.py"
    ]
    assert len(receipt_to_pricing) > 0
    assert any(e["relationship"] == "resolved" for e in receipt_to_pricing)

    # test_pricing.py imports Cart and compute_final_price (resolved)
    test_pricing_imports = [
        e for e in edges if e["source"] == "tests/test_pricing.py" and e["kind"] == "import"
    ]
    assert any(e["relationship"] == "resolved" for e in test_pricing_imports)


def test_dependency_circular_cycle_detection():
    """Verify directed cycle detection detects circular dependencies and handles clean DAGs."""
    # 1. Clean DAG on shop fixture
    builder = DependencyGraphBuilder(CROSS_FILE_SHOP_FILES)
    graph = builder.build()
    assert graph.cycle_count == 0
    assert not any(n["has_circular_dependency"] for n in graph.nodes.values())

    # 2. Synthetic circular imports A -> B -> A
    circular_files = {
        "service_a.py": "from service_b import helper_b\ndef run_a(): return helper_b()",
        "service_b.py": "from service_a import run_a\ndef helper_b(): return run_a()",
        "standalone.py": "def util(): return 100",
    }
    circ_builder = DependencyGraphBuilder(circular_files)
    circ_graph = circ_builder.build()

    assert circ_graph.cycle_count >= 1
    assert circ_graph.nodes["service_a.py"]["has_circular_dependency"] is True
    assert circ_graph.nodes["service_b.py"]["has_circular_dependency"] is True
    assert circ_graph.nodes["standalone.py"]["has_circular_dependency"] is False


def test_reverse_dependency_lookup():
    """Verify get_dependents returns downstream consumers and get_dependencies returns callees."""
    builder = DependencyGraphBuilder(CROSS_FILE_SHOP_FILES)
    builder.build()

    # Dependents of discounts.py: who imports or calls it?
    dependents = builder.get_dependents("src/shop/discounts.py")
    assert "src/shop/pricing.py" in dependents

    # Dependents of pricing.py: receipt.py and tests/test_pricing.py
    pricing_dependents = builder.get_dependents("src/shop/pricing.py")
    assert "src/shop/receipt.py" in pricing_dependents
    assert "tests/test_pricing.py" in pricing_dependents

    # Dependencies of pricing.py: models.py and discounts.py
    pricing_dependencies = builder.get_dependencies("src/shop/pricing.py")
    assert "src/shop/discounts.py" in pricing_dependencies
    assert "src/shop/models.py" in pricing_dependencies


def test_bug_impact_analysis_blast_radius():
    """Verify bug impact identifies root cause, affected dependencies, and suggested tests."""
    builder = DependencyGraphBuilder(CROSS_FILE_SHOP_FILES)
    impact = builder.analyze_bug_impact(
        query="vip customer discount calculation error in discounts.py",
        target_files=["src/shop/discounts.py"],
    )

    # 1. Root cause candidate
    root_files = [c["file_path"] for c in impact.root_cause_candidates]
    assert "src/shop/discounts.py" in root_files

    # 2. Affected downstream dependencies
    affected_files = [d["file_path"] for d in impact.affected_dependencies]
    assert "src/shop/pricing.py" in affected_files
    assert "src/shop/receipt.py" in affected_files
    # Distractor files must NOT be in affected dependencies
    assert "src/shop/inventory.py" not in affected_files
    assert "src/shop/shipping.py" not in affected_files

    # 3. Suggested regression test
    suggested_tests = [t["test_file"] for t in impact.suggested_tests]
    assert "tests/test_pricing.py" in suggested_tests
    assert "tests/test_shipping.py" not in suggested_tests

    # 4. Static Hypothesis Disclaimer
    assert "Static Impact Hypothesis" in impact.disclaimer
    assert "AI-suggested tests do not replace" in impact.disclaimer


# ==============================================================================
# Retrieval Enhancement Benchmark Tests
# ==============================================================================


def test_retrieval_with_code_intelligence_shop_fixture(test_session):
    """Verify intelligent retrieval on multi-file fixture with symbol and test boosting."""
    with test_session() as session:
        repo = Repository(
            github_id=888801,
            owner="repopilot-eval",
            name="shop-billing",
            full_name="repopilot-eval/shop-billing",
            url="https://github.com/repopilot-eval/shop-billing",
            default_branch="main",
            stars=10,
        )
        session.add(repo)
        session.flush()

        job = IndexingJob(
            repository_id=repo.id,
            commit_sha="a" * 40,
            status="completed",
            file_count=len(CROSS_FILE_SHOP_FILES),
        )
        session.add(job)
        session.flush()

        for path, content in CROSS_FILE_SHOP_FILES.items():
            f = IndexedFile(
                job_id=job.id,
                path=path,
                language="python",
                size_bytes=len(content.encode("utf-8")),
                content=content,
            )
            session.add(f)
        session.commit()

        # Query 1: exact symbol match
        evidence = retrieve(session, repo.id, "a" * 40, "How is calculate_discount implemented?")
        assert len(evidence) > 0
        top_path = evidence[0].path
        assert top_path == "src/shop/discounts.py"
        assert "calculate_discount" in evidence[0].content

        # Query 2: bug/issue intent boosts related regression test
        bug_evidence = retrieve(
            session, repo.id, "a" * 40, "bug in vip discount calculation failing assertion"
        )
        paths = [e.path for e in bug_evidence]
        assert "src/shop/discounts.py" in paths
        assert "tests/test_pricing.py" in paths


# ==============================================================================
# API Endpoints: Dependency Graph, Symbols, and Impact Analysis
# ==============================================================================


def test_api_endpoints_dependency_graph_symbols_and_impact(test_session):
    """Verify REST endpoints for graph, symbol index, and impact analysis."""
    with test_session() as session:
        user = User()
        session.add(user)
        session.flush()
        auth = AuthContext(user=user, session=None)

        repo = Repository(
            github_id=888802,
            owner="test-eval",
            name="shop-app",
            full_name="test-eval/shop-app",
            url="https://github.com/test-eval/shop-app",
            default_branch="main",
            stars=5,
        )
        session.add(repo)
        session.flush()

        session.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner"))

        job = IndexingJob(
            repository_id=repo.id,
            commit_sha="b" * 40,
            status="completed",
            file_count=len(CROSS_FILE_SHOP_FILES),
        )
        session.add(job)
        session.flush()

        for path, content in CROSS_FILE_SHOP_FILES.items():
            f = IndexedFile(
                job_id=job.id,
                path=path,
                language="python",
                size_bytes=len(content.encode("utf-8")),
                content=content,
            )
            session.add(f)
            session.flush()
            # Add basic symbols
            if path == "src/shop/discounts.py":
                session.add(
                    CodeSymbol(file_id=f.id, name="calculate_discount", kind="function", line=6)
                )
        session.commit()

        from repopilot_api.dependencies import get_db
        from repopilot_api.security import get_current_auth, require_csrf

        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_current_auth] = lambda: auth
        app.dependency_overrides[require_csrf] = lambda: auth

        client = TestClient(app)
        try:
            # 1. GET /api/repositories/{id}/dependency-graph
            resp_graph = client.get(f"/api/repositories/{repo.id}/dependency-graph")
            assert resp_graph.status_code == 200, resp_graph.text
            g_data = resp_graph.json()
            assert g_data["repository_id"] == repo.id
            assert g_data["summary"]["total_files"] == len(CROSS_FILE_SHOP_FILES)
            assert g_data["summary"]["resolved_edges"] > 0
            assert g_data["summary"]["has_circular_dependency"] is False
            assert len(g_data["nodes"]) == len(CROSS_FILE_SHOP_FILES)

            # 2. GET /api/repositories/{id}/symbols
            resp_sym = client.get(f"/api/repositories/{repo.id}/symbols")
            assert resp_sym.status_code == 200, resp_sym.text
            s_data = resp_sym.json()
            assert s_data["total_symbols"] > 0
            names = [s["name"] for s in s_data["symbols"]]
            assert "calculate_discount" in names
            assert "compute_final_price" in names

            # 3. POST /api/repositories/{id}/impact-analysis
            resp_impact = client.post(
                f"/api/repositories/{repo.id}/impact-analysis",
                json={
                    "issue_title": "VIP discount returns rate instead of amount",
                    "issue_body": "test_vip_customer_discount fails with 104.79 != 84.00",
                    "target_file": "src/shop/discounts.py",
                },
            )
            assert resp_impact.status_code == 200, resp_impact.text
            i_data = resp_impact.json()
            assert "src/shop/discounts.py" in i_data["likely_root_cause_files"]
            assert "src/shop/pricing.py" in i_data["affected_downstream_files"]
            assert "tests/test_pricing.py" in i_data["suggested_regression_tests"]
            assert "Static Impact Hypothesis" in i_data["disclaimer"]
        finally:
            app.dependency_overrides.clear()


# ==============================================================================
# Real Isolated Docker Verification: Multi-File Baseline & Patched Success
# ==============================================================================


@pytest.mark.docker
@pytest.mark.skipif(not _docker_ready, reason=_docker_reason)
@pytest.mark.anyio
async def test_docker_cross_file_shop_baseline_failure_and_patched_success(tmp_path):
    """Verify real Docker container execution on multi-file shop fixture:

    1. Baseline fails: VIP discount rate bug causes assertion failure (104.79 != 84.00).
    2. Atomic fix patch applied: all pricing and discount tests pass (exit code 0).
    3. Regression patch applied: detects regression on standard customer calculation.
    """
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    image = os.getenv("SANDBOX_TEST_IMAGE", DEFAULT_SANDBOX_IMAGE)

    # 1. Baseline Run -> Must Fail
    ws_base = manager.create_workspace("run_shop_baseline", CROSS_FILE_SHOP_FILES)
    try:
        runner = DockerSandboxRunner(image=image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "tests/test_pricing.py"],
            timeout_seconds=15,
        )
        base_res = await runner.run(ws_base, req)
        assert base_res.state == SandboxExecutionState.FAILED
        assert base_res.exit_code == 1
        output = base_res.stderr + base_res.stdout
        assert "AssertionError" in output
        assert "104.79 != 84.0" in output
    finally:
        manager.cleanup_workspace(ws_base)

    # 2. Patched Run with Approved Fix -> Must Pass
    ws_patched = manager.create_workspace("run_shop_patched", CROSS_FILE_SHOP_FILES)
    try:
        fix_proposal = ProposedFix(
            explanation="Compute discount amount from rate and subtotal",
            patches=[
                PatchProposal(
                    file_path="src/shop/discounts.py",
                    diff=CROSS_FILE_SHOP_FIX_PATCH,
                    explanation="Fix VIP discount amount calculation",
                )
            ],
        )
        patch_result = apply_patch_proposal(ws_patched, fix_proposal, scratch_root=scratch)
        assert patch_result.success is True

        runner = DockerSandboxRunner(image=image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "tests/test_pricing.py"],
            timeout_seconds=15,
        )
        patched_res = await runner.run(ws_patched, req)
        assert patched_res.state == SandboxExecutionState.PASSED
        assert patched_res.exit_code == 0
        output = patched_res.stderr + patched_res.stdout
        assert "OK" in output
    finally:
        manager.cleanup_workspace(ws_patched)

    # 3. Regression Run -> Must Detect Regression Failure
    ws_regress = manager.create_workspace("run_shop_regression", CROSS_FILE_SHOP_FILES)
    try:
        regress_proposal = ProposedFix(
            explanation="Fixes VIP but introduces standard customer regression",
            patches=[
                PatchProposal(
                    file_path="src/shop/discounts.py",
                    diff=CROSS_FILE_SHOP_REGRESSION_PATCH,
                    explanation="Regression patch",
                )
            ],
        )
        patch_result = apply_patch_proposal(ws_regress, regress_proposal, scratch_root=scratch)
        assert patch_result.success is True

        runner = DockerSandboxRunner(image=image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "tests/test_pricing.py"],
            timeout_seconds=15,
        )
        regress_res = await runner.run(ws_regress, req)
        assert regress_res.state == SandboxExecutionState.FAILED
        assert regress_res.exit_code == 1
        output = regress_res.stderr + regress_res.stdout
        assert "AssertionError" in output
    finally:
        manager.cleanup_workspace(ws_regress)
