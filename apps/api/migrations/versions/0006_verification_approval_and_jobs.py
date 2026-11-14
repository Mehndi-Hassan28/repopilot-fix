"""Add human approval and durable job management fields to verification_runs and audit table.

Revision ID: 0006_verification_approval_and_jobs
Revises: 0005_verification_engine
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_verification_approval_and_jobs"
down_revision = "0005_verification_engine"
branch_labels = None
depends_on = None


def upgrade():
    # Additive columns to verification_runs
    op.add_column("verification_runs", sa.Column("spec_hash", sa.String(64), nullable=True))
    op.add_column(
        "verification_runs", sa.Column("approved_spec_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "verification_runs",
        sa.Column(
            "approved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "verification_runs",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "verification_runs", sa.Column("rejection_reason", sa.String(500), nullable=True)
    )
    op.add_column(
        "verification_runs", sa.Column("idempotency_key", sa.String(64), nullable=True)
    )
    op.add_column(
        "verification_runs",
        sa.Column("test_command", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column("verification_runs", sa.Column("hypothesis", sa.Text(), nullable=True))
    op.add_column(
        "verification_runs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "verification_runs",
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "verification_runs",
        sa.Column("patches_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "verification_runs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "verification_runs",
        sa.Column(
            "cancelled_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "verification_runs",
        sa.Column("cancellation_reason", sa.String(500), nullable=True),
    )

    op.create_index("ix_verification_runs_spec_hash", "verification_runs", ["spec_hash"])
    op.create_index(
        "ix_verification_runs_idempotency_key", "verification_runs", ["idempotency_key"]
    )

    # Audit events table
    op.create_table(
        "verification_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "verification_run_id",
            sa.Integer(),
            sa.ForeignKey("verification_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_verification_audit_events_verification_run_id",
        "verification_audit_events",
        ["verification_run_id"],
    )
    op.create_index(
        "ix_verification_audit_events_event_type",
        "verification_audit_events",
        ["event_type"],
    )


def downgrade():
    op.drop_table("verification_audit_events")
    op.drop_index("ix_verification_runs_idempotency_key", table_name="verification_runs")
    op.drop_index("ix_verification_runs_spec_hash", table_name="verification_runs")
    op.drop_column("verification_runs", "cancellation_reason")
    op.drop_column("verification_runs", "cancelled_by_user_id")
    op.drop_column("verification_runs", "cancelled_at")
    op.drop_column("verification_runs", "patches_json")
    op.drop_column("verification_runs", "max_retries")
    op.drop_column("verification_runs", "retry_count")
    op.drop_column("verification_runs", "hypothesis")
    op.drop_column("verification_runs", "test_command")
    op.drop_column("verification_runs", "idempotency_key")
    op.drop_column("verification_runs", "rejection_reason")
    op.drop_column("verification_runs", "approved_at")
    op.drop_column("verification_runs", "approved_by_user_id")
    op.drop_column("verification_runs", "approved_spec_hash")
    op.drop_column("verification_runs", "spec_hash")
