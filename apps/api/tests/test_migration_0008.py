"""Verification test for Alembic migration 0008_github_pull_requests.

Verifies:
1. Online migration upgrades through 0007 to 0008 cleanly.
2. The github_pull_requests table is created with expected columns and indexes.
3. Downgrade from 0008 to 0007 cleanly removes the table and its indexes.
4. Re-upgrade back to head succeeds idempotently.
5. All operations are isolated to a temporary schema and cleanly disposed.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from alembic import command
from alembic.config import Config

ALEMBIC_INI_PATH = (Path(__file__).resolve().parents[1] / "alembic.ini").as_posix()


def _get_scratch_url() -> str | None:
    raw_url = os.getenv("DATABASE_URL", "")
    if not raw_url and os.path.exists(".env"):
        for line in open(".env"):
            if line.startswith("DATABASE_URL="):
                raw_url = line.strip().split("=", 1)[1].strip("\"'")
                break
    if not raw_url:
        raw_url = "postgresql+psycopg://repopilot:repopilot@localhost:5432/repopilot_scratch"
    try:
        parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://"))
        db_name = parsed.path.lstrip("/")
        if db_name != "repopilot_scratch":
            return None
        # Verify connectable
        with psycopg.connect(
            raw_url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=3
        ):
            pass
        return raw_url
    except Exception:
        return None


SCRATCH_URL = _get_scratch_url()
pytestmark = pytest.mark.skipif(
    SCRATCH_URL is None,
    reason="PostgreSQL repopilot_scratch database is required for real schema migration tests.",
)


def test_alembic_migration_0008_lifecycle():
    """Verify upgrade to 0008, column and index integrity, downgrade to 0007, and re-upgrade."""
    schema_name = "test_alembic_0008_schema"
    raw_conn_url = SCRATCH_URL.replace("postgresql+psycopg://", "postgresql://")

    # 1. Prepare isolated test schema
    with psycopg.connect(raw_conn_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema_name}")

    try:
        # Configure Alembic to target the isolated test schema
        os.environ["DATABASE_URL"] = f"{SCRATCH_URL}?options=-csearch_path%3D{schema_name}"
        cfg = Config(ALEMBIC_INI_PATH)

        # Upgrade through 0007
        command.upgrade(cfg, "0007_verification_worker_leases")

        # Upgrade to 0008_github_pull_requests
        command.upgrade(cfg, "0008_github_pull_requests")

        # Verify table columns in isolated schema
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = 'github_pull_requests'
                    ORDER BY ordinal_position
                    """,
                    (schema_name,),
                )
                columns = cur.fetchall()
                col_names = [c[0] for c in columns]
                expected_cols = [
                    "id",
                    "repository_id",
                    "verification_run_id",
                    "created_by_user_id",
                    "issue_number",
                    "pr_number",
                    "pr_url",
                    "branch_name",
                    "base_branch",
                    "head_sha",
                    "patch_hash",
                    "title",
                    "body",
                    "created_at",
                ]
                assert col_names == expected_cols, f"Column mismatch: {col_names}"

                # Verify indexes
                cur.execute(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = %s AND tablename = 'github_pull_requests'
                    """,
                    (schema_name,),
                )
                indexes = {r[0] for r in cur.fetchall()}
                expected_indexes = {
                    "github_pull_requests_pkey",
                    "ix_github_pull_requests_repository_id",
                    "ix_github_pull_requests_verification_run_id",
                    "ix_github_pull_requests_issue_number",
                    "ix_github_pull_requests_created_by_user_id",
                }
                assert expected_indexes.issubset(indexes), (
                    f"Missing indexes: {expected_indexes - indexes}"
                )

        # Test downgrade to 0007
        command.downgrade(cfg, "0007_verification_worker_leases")

        # Verify table is dropped after downgrade
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (f"{schema_name}.github_pull_requests",))
                table = cur.fetchone()[0]
                assert table is None, "github_pull_requests table still exists after downgrade!"

        # Test re-upgrade to head (0008)
        command.upgrade(cfg, "head")
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (f"{schema_name}.github_pull_requests",))
                table = cur.fetchone()[0]
                assert table is not None, "github_pull_requests table missing after re-upgrade!"

    finally:
        with psycopg.connect(raw_conn_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
