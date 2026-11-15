"""Add durable worker lease, fencing token, and heartbeat fields to verification_runs.

Milestone 3.6 Phase 4:
- worker_id: unique identity of claiming worker
- fencing_token: monotonically increasing integer fencing stale workers
- lease_expires_at: timestamp when current worker lease expires
- last_heartbeat_at: timestamp of most recent worker heartbeat

Revision ID: 0007_verification_worker_leases
Revises: 0006_verification_approval_and_jobs
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_verification_worker_leases"
down_revision = "0006_verification_approval_and_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("verification_runs", sa.Column("worker_id", sa.String(100), nullable=True))
    op.add_column(
        "verification_runs",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "verification_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "verification_runs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_verification_runs_lease_expires_at",
        "verification_runs",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_verification_runs_status_lease_expires_at",
        "verification_runs",
        ["status", "lease_expires_at"],
    )


def downgrade():
    op.drop_index("ix_verification_runs_status_lease_expires_at", table_name="verification_runs")
    op.drop_index("ix_verification_runs_lease_expires_at", table_name="verification_runs")
    op.drop_column("verification_runs", "last_heartbeat_at")
    op.drop_column("verification_runs", "lease_expires_at")
    op.drop_column("verification_runs", "fencing_token")
    op.drop_column("verification_runs", "worker_id")
