"""Enforce unique constraint on github_pull_requests.verification_run_id.

Revision ID: 0009_github_pull_requests_unique_run
Revises: 0008_github_pull_requests
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_github_pull_requests_unique_run"
down_revision = "0008_github_pull_requests"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Safely detect existing duplicate records before applying unique constraint.
    # We do NOT silently delete or truncate data.
    duplicates = bind.execute(
        sa.text(
            """
            SELECT verification_run_id, COUNT(*) AS cnt
            FROM github_pull_requests
            GROUP BY verification_run_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    if duplicates:
        details = ", ".join(f"run_id={row[0]} ({row[1]} records)" for row in duplicates)
        raise RuntimeError(
            f"Cannot apply unique constraint to github_pull_requests.verification_run_id: "
            f"existing duplicate records detected: [{details}]. "
            f"Manual deduplication is required before applying this migration; "
            f"data was not modified or deleted."
        )

    op.drop_index(
        "ix_github_pull_requests_verification_run_id",
        table_name="github_pull_requests",
    )
    op.create_index(
        "ix_github_pull_requests_verification_run_id",
        "github_pull_requests",
        ["verification_run_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_github_pull_requests_verification_run_id",
        table_name="github_pull_requests",
    )
    op.create_index(
        "ix_github_pull_requests_verification_run_id",
        "github_pull_requests",
        ["verification_run_id"],
        unique=False,
    )
