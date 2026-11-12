"""Code intelligence jobs, files, and symbols."""

import sqlalchemy as sa
from alembic import op

revision = "0002_code_intelligence"
down_revision = "0001_repositories"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "indexing_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repository_id", "commit_sha", name="uq_index_job_repo_sha"),
    )
    op.create_index("ix_indexing_jobs_repository_id", "indexing_jobs", ["repository_id"])
    op.create_index("ix_indexing_jobs_commit_sha", "indexing_jobs", ["commit_sha"])
    op.create_index("ix_indexing_jobs_status", "indexing_jobs", ["status"])
    op.create_table(
        "indexed_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("indexing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("language", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.UniqueConstraint("job_id", "path", name="uq_indexed_file_job_path"),
    )
    for col in ("job_id", "path", "language"):
        op.create_index(f"ix_indexed_files_{col}", "indexed_files", [col])
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "file_id",
            sa.Integer(),
            sa.ForeignKey("indexed_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "file_id", "kind", "name", "line", name="uq_symbol_file_kind_name_line"
        ),
    )
    for col in ("file_id", "kind", "name"):
        op.create_index(f"ix_code_symbols_{col}", "code_symbols", [col])


def downgrade():
    op.drop_table("code_symbols")
    op.drop_table("indexed_files")
    op.drop_table("indexing_jobs")
