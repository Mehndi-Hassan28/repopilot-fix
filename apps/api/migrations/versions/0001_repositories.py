"""Create repository metadata table."""

import sqlalchemy as sa
from alembic import op

revision = "0001_repositories"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("full_name", sa.String(201), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("default_branch", sa.String(255), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("github_id", name="uq_repositories_github_id"),
        sa.UniqueConstraint("full_name", name="uq_repositories_full_name"),
    )
    op.create_index("ix_repositories_github_id", "repositories", ["github_id"])
    op.create_index("ix_repositories_full_name", "repositories", ["full_name"])


def downgrade():
    op.drop_index("ix_repositories_full_name", table_name="repositories")
    op.drop_index("ix_repositories_github_id", table_name="repositories")
    op.drop_table("repositories")
