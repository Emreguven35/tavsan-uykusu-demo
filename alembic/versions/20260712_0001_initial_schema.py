"""initial schema — users, babies, sleep_logs, sleep_plans, subscriptions,
chat_messages, voice_profiles

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-12

Not: Tipler dialect-variant (api.db.base). PostgreSQL'de uuid + JSONB, SQLite'ta
CHAR(32) + JSON üretilir. server_default now() → PG'de now(), SQLite'ta
CURRENT_TIMESTAMP (SQLAlchemy func.now() dialect'e göre derler).
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID, JSONBType

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _ts_cols() -> list:
    """created_at + updated_at (server_default now())."""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    # --- users ---------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        *_ts_cols(),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # --- babies --------------------------------------------------------------
    op.create_table(
        "babies",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("feeding_type", sa.String(40), nullable=True),
        sa.Column("crying_tolerance", sa.String(40), nullable=True),
        sa.Column("parent_experience", sa.String(40), nullable=True),
        sa.Column("sleep_environment", sa.String(80), nullable=True),
        sa.Column("sleep_method", sa.String(80), nullable=True),
        sa.Column("night_wakes", sa.Integer(), nullable=True),
        sa.Column("night_feeds", sa.Integer(), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_babies_user_id", "babies", ["user_id"])

    # --- sleep_logs ----------------------------------------------------------
    op.create_table(
        "sleep_logs",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baby_id", GUID,
                  sa.ForeignKey("babies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("client_id", sa.String(64), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", "client_id", name="uq_sleep_logs_user_client"),
    )
    op.create_index("ix_sleep_logs_user_id", "sleep_logs", ["user_id"])
    op.create_index("ix_sleep_logs_baby_id", "sleep_logs", ["baby_id"])

    # --- sleep_plans ---------------------------------------------------------
    op.create_table(
        "sleep_plans",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baby_id", GUID,
                  sa.ForeignKey("babies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("content", JSONBType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sleep_plans_user_id", "sleep_plans", ["user_id"])
    op.create_index("ix_sleep_plans_baby_id", "sleep_plans", ["baby_id"])

    # --- subscriptions -------------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(10), nullable=False),
        sa.Column("product_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_data", sa.Text(), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    # --- chat_messages -------------------------------------------------------
    op.create_table(
        "chat_messages",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])

    # --- voice_profiles ------------------------------------------------------
    op.create_table(
        "voice_profiles",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("elevenlabs_voice_id", sa.String(120), nullable=True),
        sa.Column("sample_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_voice_profiles_user_id", "voice_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_table("voice_profiles")
    op.drop_table("chat_messages")
    op.drop_table("subscriptions")
    op.drop_table("sleep_plans")
    op.drop_table("sleep_logs")
    op.drop_table("babies")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
