# Changelog

All notable changes to the RepoPilot Fix project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-11-25

### Added
- **Production-Grade Docker Compose & Containerization:**
  - Added multi-stage, unprivileged `apps/api/Dockerfile` (non-root `repopilot:1000`) and `apps/web/Dockerfile` (non-root `nextjs:1001`).
  - Added root `docker-compose.yml` orchestrating `db` (Postgres 16 Alpine), `api`, `worker`, and `web` with container healthchecks, persistent storage, and internal network segmentation (`backend-net`).
  - Added root `.dockerignore` strictly excluding `.git`, `.env*`, `.venv`, `node_modules`, and local agent configurations.
- **Operations & Deployment Documentation:**
  - Added [`docs/deployment/deployment_guide.md`](docs/deployment/deployment_guide.md) detailing architecture, reproducible local setup, zero-cost operations, manual Alembic migration procedures, and automated `pg_dump` backup/recovery workflows.
  - Added [`docs/evaluations/browser_smoke_test.md`](docs/evaluations/browser_smoke_test.md) certifying the complete 15-stage live HTTP user journey against running FastAPI and Next.js services.
- **Hackathon & Presentation Materials:**
  - Added [`docs/presentation/project_pitch.md`](docs/presentation/project_pitch.md) containing a 60-second elevator pitch, value propositions by stakeholder, and benchmark KPIs.
  - Added [`docs/presentation/demo_script.md`](docs/presentation/demo_script.md) providing a timed, minute-by-minute 5-minute live demonstration script.
  - Added [`docs/presentation/interview_questions.md`](docs/presentation/interview_questions.md) covering concurrency, AST retrieval, sandboxing, and benchmark methodology for technical interviews.
  - Added [`docs/architecture/system_architecture.md`](docs/architecture/system_architecture.md) detailing trust boundaries, schema relationships, and distributed worker lease state machines with Mermaid diagrams.
- **Safe Forward Schema Migration 0009:**
  - Added forward migration `0009_github_pull_requests_unique_run.py` enforcing a unique constraint on `github_pull_requests.verification_run_id`.
  - Implemented pre-flight duplicate detection (`SELECT ... HAVING COUNT(*) > 1`) that raises a descriptive `RuntimeError` rather than silently dropping existing records.
- **Project Governance & Security Policies:**
  - Added [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md).

### Changed
- **Version Bump:** Bumped project versions across `apps/api/pyproject.toml` and `apps/web/package.json` to `1.0.0`.
- **Security Hardening & Runtime Verification:**
  - Added automatic Docker daemon capability detection in `get_sandbox_runner()`, dynamically selecting `DockerSandboxRunner` when Docker is active with safe fallback to `MockSandboxRunner`.
  - Allowed real Docker container execution for authenticated internal demo fixtures without weakening sandbox boundaries or bypassing human approval.
  - Implemented strict zero-test handling in `TestOutputParser`: recognizes `Ran 0 tests` and pytest empty collections as `no_tests_discovered`. Zero discovered tests in baseline yields `INCONCLUSIVE`, and zero tests in patched strictly blocks `VERIFIED_FIX`.
  - Enforced evidence-first verification: `VERIFIED_FIX` strictly requires baseline exit 1 with failed tests, patched exit 0 with >0 passed tests, >=1 resolved failures, and 0 regressions.
  - Gated frontend and backend against premature completion: Stage 8 halts advance to Stage 9 on unverified verdicts; Stage 9 disables patch export and PR simulation unless verdict is `VERIFIED_FIX`.
  - Added `test_demo_real_docker_verification_e2e` and zero-test regression test suite. Expanded automated test suite to 361 passing tests.
  - Confirmed Docker daemon socket is restricted to the private background worker boundary and never exposed to the public API or web containers.
  - Clarified in-memory rate limiting as single-instance protection and documented Redis roadmap for distributed scaling.
  - Clarified prompt framing as defense-in-depth rather than guaranteed injection prevention.

---

## [0.9.0] - 2026-11-24

### Added
- **Durable Verification Worker & Distributed Leases:**
  - Implemented `WorkerLoop` in `apps/api/src/repopilot_api/worker.py` utilizing PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` for atomic job claiming.
  - Implemented 5-second lease heartbeats, fencing tokens, and stale lease recovery.
  - Implemented graceful worker shutdown during `SIGTERM`/`SIGINT` via non-cancelling drain mode.
- **Idempotent GitHub PR Integration:**
  - Added `github_pr_service.py` with pre-flight checks against existing open PRs and branches.
  - Enforced verification status gating requiring human approval and `VERIFIED_FIX` verdict.
- **Real-World AI Repair Benchmark:**
  - Evaluated 20 curated defect-pattern fixtures with Google AI Studio (`models/gemini-flash-lite-latest`).
  - Recorded 85.0% (17/20) verified repair success rate, 100% Recall@5, 0.925 MRR, and 0% regressions.

---

## [0.8.0] - 2026-11-22

### Added
- **Advanced Code Intelligence & Blast Radius Analysis:**
  - Added `ast_graph.py` parsing classes, functions, calls, and imports using Tree-sitter and Python standard library `ast`.
  - Implemented static bug blast radius calculation identifying downstream callers and suggested regression tests.
  - Implemented Tarjan/DFS recursion stack cycle detection.
  - Added pure-SVG interactive dependency graph visualizer in `apps/web/src/components/DependencyGraphWorkspace.tsx`.

---

## [0.5.0] - 2026-11-18

### Added
- **Dual Docker Sandbox Engine:**
  - Added `sandbox.py` with baseline reproduction and patched verification passes.
  - Hardened execution flags: `--network none`, `--read-only`, tmpfs, non-root user `1000:1000`, 512MB RAM cap, 1 CPU cap, 100 PID limit.
  - Added `proof_engine.py` classifying verdicts into `VERIFIED_FIX`, `FIX_FAILED`, `REGRESSION_DETECTED`, and `PATCH_APPLICATION_ERROR`.
- **Cryptographic Spec Hash & Human Approval:**
  - Binds candidate diff, commit SHA, test command, and sandbox image into immutable SHA-256 spec hash.
  - Enforced human sign-off requirement prior to sandbox dispatch.

---

## [0.1.0] - 2026-11-12

### Added
- Initial project scaffolding: FastAPI backend and Next.js 15 frontend.
- Public GitHub repository metadata import and Tree-sitter AST parsing.
- Grounded technical chat interface with 1-indexed source line citations.
- SQLAlchemy database integration with Alembic migrations.
