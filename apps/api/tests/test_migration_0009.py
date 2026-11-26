"""Verification tests for Alembic migration 0009_github_pull_requests_unique_run.

Verifies:
1. Fresh installation upgrades all the way through 0009 cleanly.
2. Upgrade from an existing migration 0008 schema applies the unique index cleanly.
3. Safe duplicate detection: existing duplicates at 0008 safely block upgrade with RuntimeError
   without silently deleting or altering data.
4. Downgrade from 0009 to 0008 restores non-unique index.
5. Re-upgrade back to head succeeds cleanly.
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
        raw_url = (
            "postgresql+psycopg://repopilot:hassansaladchef2028@localhost:5432/repopilot_scratch"
        )
    try:
        parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://"))
        db_name = parsed.path.lstrip("/")
        if db_name != "repopilot_scratch":
            return None
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


def test_alembic_migration_0009_fresh_install():
    """Verify clean upgrade from 0000 to head (including 0009)."""
    schema_name = "test_alembic_0009_fresh"
    raw_conn_url = SCRATCH_URL.replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(raw_conn_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema_name}")

    try:
        os.environ["DATABASE_URL"] = f"{SCRATCH_URL}?options=-csearch_path%3D{schema_name}"
        cfg = Config(ALEMBIC_INI_PATH)
        command.upgrade(cfg, "head")

        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT i.relname, ix.indisunique
                    FROM pg_class t
                    JOIN pg_index ix ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = '{schema_name}'
                      AND t.relname = 'github_pull_requests'
                      AND i.relname = 'ix_github_pull_requests_verification_run_id'
                    """
                )
                row = cur.fetchone()
                assert row is not None
                assert row[1] is True, "Index must be unique"
    finally:
        with psycopg.connect(raw_conn_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_alembic_migration_0009_upgrade_from_0008_and_downgrade():
    """Verify upgrade from existing 0008, unique index enforcement, and downgrade."""
    schema_name = "test_alembic_0009_upgrade"
    raw_conn_url = SCRATCH_URL.replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(raw_conn_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema_name}")

    try:
        os.environ["DATABASE_URL"] = f"{SCRATCH_URL}?options=-csearch_path%3D{schema_name}"
        cfg = Config(ALEMBIC_INI_PATH)

        # 1. Upgrade to 0008
        command.upgrade(cfg, "0008_github_pull_requests")

        # Verify index exists and is NOT unique yet
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT i.relname, ix.indisunique
                    FROM pg_class t
                    JOIN pg_index ix ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = '{schema_name}'
                      AND t.relname = 'github_pull_requests'
                      AND i.relname = 'ix_github_pull_requests_verification_run_id'
                    """
                )
                row = cur.fetchone()
                assert row is not None
                assert row[1] is False, "0008 index must be non-unique"

        # 2. Upgrade to 0009
        command.upgrade(cfg, "0009_github_pull_requests_unique_run")

        # Verify index is now UNIQUE
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT i.relname, ix.indisunique
                    FROM pg_class t
                    JOIN pg_index ix ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = '{schema_name}'
                      AND t.relname = 'github_pull_requests'
                      AND i.relname = 'ix_github_pull_requests_verification_run_id'
                    """
                )
                row = cur.fetchone()
                assert row is not None
                assert row[1] is True, "0009 index must be unique"

        # 3. Downgrade to 0008
        command.downgrade(cfg, "0008_github_pull_requests")
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT i.relname, ix.indisunique
                    FROM pg_class t
                    JOIN pg_index ix ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = '{schema_name}'
                      AND t.relname = 'github_pull_requests'
                      AND i.relname = 'ix_github_pull_requests_verification_run_id'
                    """
                )
                row = cur.fetchone()
                assert row is not None
                assert row[1] is False, "Downgraded index must be non-unique"

        # 4. Re-upgrade back to head
        command.upgrade(cfg, "head")

    finally:
        with psycopg.connect(raw_conn_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def test_alembic_migration_0009_duplicate_detection_prevents_silent_data_loss():
    """Verify that existing duplicate records in 0008 prevent upgrade and data is preserved."""
    schema_name = "test_alembic_0009_duplicates"
    raw_conn_url = SCRATCH_URL.replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(raw_conn_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema_name}")

    try:
        os.environ["DATABASE_URL"] = f"{SCRATCH_URL}?options=-csearch_path%3D{schema_name}"
        cfg = Config(ALEMBIC_INI_PATH)

        # Upgrade to 0008
        command.upgrade(cfg, "0008_github_pull_requests")

        # Insert a repository and verification run
        with psycopg.connect(raw_conn_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {schema_name}.repositories (
                        github_id, owner, name, full_name, url, default_branch
                    )
                    VALUES (
                        9999, 'test', 'repo', 'test/repo', 'https://github.com/test/repo', 'main'
                    )
                    RETURNING id;
                    """
                )
                repo_id = cur.fetchone()[0]

                cur.execute(
                    f"""
                    INSERT INTO {schema_name}.verification_runs (
                        repository_id, commit_sha, issue_number, status, spec_hash, sandbox_image
                    )
                    VALUES (
                        {repo_id}, '0123456789abcdef0123456789abcdef01234567',
                        1, 'completed', 'spechash1', 'python:3.11-slim'
                    )
                    RETURNING id;
                    """
                )
                run_id = cur.fetchone()[0]

                # Insert TWO PR records for the same verification_run_id (duplicates)
                for pr_num in (101, 102):
                    cur.execute(
                        f"""
                        INSERT INTO {schema_name}.github_pull_requests (
                            repository_id, verification_run_id, issue_number, pr_number,
                            pr_url, branch_name, base_branch, head_sha, patch_hash, title, body
                        )
                        VALUES (
                            {repo_id}, {run_id}, 1, {pr_num},
                            'https://github.com/test/repo/pull/{pr_num}',
                            'repopilot/fix-1', 'main', '0123456789abcdef0123456789abcdef01234567',
                            'patchhash1', 'Test PR', 'Test Body'
                        );
                        """
                    )

        # Attempting to upgrade to 0009 must fail and raise RuntimeError
        with pytest.raises(Exception) as exc_info:
            command.upgrade(cfg, "0009_github_pull_requests_unique_run")

        assert "existing duplicate records detected" in str(exc_info.value)

        # Confirm data was NOT silently deleted
        with psycopg.connect(raw_conn_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM {schema_name}.github_pull_requests
                    WHERE verification_run_id = {run_id}
                    """
                )
                count = cur.fetchone()[0]
                assert count == 2, "Data must not be deleted or modified"

    finally:
        with psycopg.connect(raw_conn_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
