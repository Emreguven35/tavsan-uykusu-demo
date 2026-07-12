"""auth tokens — refresh_tokens + password_reset_tokens

Revision ID: 0002_auth_tokens
Revises: 0001_initial
Create Date: 2026-07-12

Refresh token'lar DB'de saklanır (logout / tüm cihazlardan çıkış). Ham token değil,
SHA-256 hash'i yazılır. password_reset_tokens aynı desende (Faz 2 basit sıfırlama).
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID

# revision identifiers, used by Alembic.
revision = "0002_auth_tokens"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens",
                    ["token_hash"], unique=True)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens",
                    ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("refresh_tokens")
