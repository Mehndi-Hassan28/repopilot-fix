"""Add github_pull_requests table for secure PR creation and idempotency tracking.

Revision ID: 0008_github_pull_requests
Revises: 0007_verification_worker_leases
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_github_pull_requests"
down_revision = "0007_verification_worker_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "github_pull_requests",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("verification_run_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_url", sa.String(500), nullable=False),
        sa.Column("branch_name", sa.String(255), nullable=False),
        sa.Column("base_branch", sa.String(255), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("patch_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["verification_run_id"], ["verification_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_github_pull_requests_repository_id",
        "github_pull_requests",
        ["repository_id"],
    )
    op.create_index(
        "ix_github_pull_requests_verification_run_id",
        "github_pull_requests",
        ["verification_run_id"],
    )
    op.create_index(
        "ix_github_pull_requests_issue_number",
        "github_pull_requests",
        ["issue_number"],
    )
    op.create_index(
        "ix_github_pull_requests_created_by_user_id",
        "github_pull_requests",
        ["created_by_user_id"],
    )


def downgrade():
    op.drop_index(
        "ix_github_pull_requests_created_by_user_id",
        table_name="github_pull_requests",
    )
    op.drop_index(
        "ix_github_pull_requests_issue_number",
        table_name="github_pull_requests",
    )
    op.drop_index(
        "ix_github_pull_requests_verification_run_id",
        table_name="github_pull_requests",
    )
    op.drop_index(
        "ix_github_pull_requests_repository_id",
        table_name="github_pull_requests",
    )
    op.drop_table("github_pull_requests")
