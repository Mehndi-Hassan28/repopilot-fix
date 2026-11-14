"""Add GitHub identities, server sessions, and nullable ownership scopes.

Legacy repositories and conversations are deliberately left unassigned.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_auth_ownership"
down_revision = "0003_repository_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("login", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_oauth_identity_subject"),
    )
    op.create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_index(
        "ix_auth_sessions_user_expiry", "auth_sessions", ["user_id", "expires_at"]
    )
    op.create_table(
        "oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"])
    op.create_table(
        "repository_accesses",
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("repository_id", "user_id", name="pk_repository_accesses"),
        sa.CheckConstraint("role IN ('owner', 'collaborator')", name="ck_repository_access_role"),
    )
    op.create_index("ix_repository_accesses_user_id", "repository_accesses", ["user_id"])
    op.add_column(
        "chat_sessions",
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_chat_sessions_owner_user_id", "chat_sessions", ["owner_user_id"])


def downgrade():
    op.drop_index("ix_chat_sessions_owner_user_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "owner_user_id")
    op.drop_index("ix_repository_accesses_user_id", table_name="repository_accesses")
    op.drop_table("repository_accesses")
    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index("ix_auth_sessions_user_expiry", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
    op.drop_table("users")
