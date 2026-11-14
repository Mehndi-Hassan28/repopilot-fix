"""Add verification runs and proof reports tables.

Revision ID: 0005_verification_engine
Revises: 0004_auth_ownership
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_verification_engine"
down_revision = "0004_auth_ownership"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "verification_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("baseline_exit_code", sa.Integer(), nullable=True),
        sa.Column("baseline_stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("baseline_stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("patched_exit_code", sa.Integer(), nullable=True),
        sa.Column("patched_stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("patched_stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("patch_hash", sa.String(64), nullable=True),
        sa.Column("patch_diff", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("sandbox_image", sa.String(255), nullable=False),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_verification_runs_repository_id", "verification_runs", ["repository_id"])
    op.create_index("ix_verification_runs_commit_sha", "verification_runs", ["commit_sha"])
    op.create_index("ix_verification_runs_issue_number", "verification_runs", ["issue_number"])
    op.create_index("ix_verification_runs_user_id", "verification_runs", ["user_id"])
    op.create_index("ix_verification_runs_status", "verification_runs", ["status"])

    op.create_table(
        "proof_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "verification_run_id",
            sa.Integer(),
            sa.ForeignKey("verification_runs.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("verdict", sa.String(30), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("baseline_summary", sa.Text(), nullable=False),
        sa.Column("patched_summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False, server_default="high"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_proof_reports_verification_run_id", "proof_reports", ["verification_run_id"]
    )
    op.create_index("ix_proof_reports_verdict", "proof_reports", ["verdict"])


def downgrade():
    op.drop_index("ix_proof_reports_verdict", table_name="proof_reports")
    op.drop_index("ix_proof_reports_verification_run_id", table_name="proof_reports")
    op.drop_table("proof_reports")
    op.drop_index("ix_verification_runs_status", table_name="verification_runs")
    op.drop_index("ix_verification_runs_user_id", table_name="verification_runs")
    op.drop_index("ix_verification_runs_issue_number", table_name="verification_runs")
    op.drop_index("ix_verification_runs_commit_sha", table_name="verification_runs")
    op.drop_index("ix_verification_runs_repository_id", table_name="verification_runs")
    op.drop_table("verification_runs")
