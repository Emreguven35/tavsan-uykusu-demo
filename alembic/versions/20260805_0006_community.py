"""anne topluluğu — community_profiles, threads, replies, likes, reports, blocks, moderation_log

Revision ID: 0006_community
Revises: 0005_chat_telemetry
Create Date: 2026-08-05

Faz T. Metin tabanlı anne topluluğu (DM/görsel/profil/iç içe cevap YOK).

Hesap silme davranışı:
  - community_profiles.user_id  → ondelete CASCADE (profil gider)
  - threads/replies.user_id     → ondelete SET NULL (içerik KALIR, "Silinmiş kullanıcı")
  - likes/blocks.user_id        → ondelete CASCADE
  - reports.reporter_id, moderation_log.actor_id → ondelete SET NULL (iz kalır)

Tipler dialect-variant (api.db.base): PG'de uuid, SQLite'ta CHAR(32).
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID

revision = "0006_community"
down_revision = "0005_chat_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- community_profiles ---------------------------------------------------
    op.create_table(
        "community_profiles",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nickname", sa.String(24), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_expert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_moderator", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_community_profiles_user"),
        sa.UniqueConstraint("nickname", name="uq_community_profiles_nickname"),
    )
    op.create_index("ix_community_profiles_user_id", "community_profiles", ["user_id"])
    op.create_index("ix_community_profiles_nickname", "community_profiles", ["nickname"])

    # --- threads --------------------------------------------------------------
    op.create_table(
        "threads",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="published"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expert_replied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_activity_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_threads_user_id", "threads", ["user_id"])
    op.create_index("ix_threads_category", "threads", ["category"])
    op.create_index("ix_threads_status", "threads", ["status"])
    op.create_index("ix_threads_last_activity_at", "threads", ["last_activity_at"])
    # Liste sorgusu: kategori + durum filtresi, last_activity_at DESC sıralama.
    op.create_index("ix_threads_cat_status_activity", "threads",
                    ["category", "status", "last_activity_at"])

    # --- replies --------------------------------------------------------------
    op.create_table(
        "replies",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("thread_id", GUID,
                  sa.ForeignKey("threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="published"),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_replies_thread_id", "replies", ["thread_id"])
    op.create_index("ix_replies_user_id", "replies", ["user_id"])
    op.create_index("ix_replies_status", "replies", ["status"])
    op.create_index("ix_replies_created_at", "replies", ["created_at"])

    # --- likes ----------------------------------------------------------------
    op.create_table(
        "likes",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", sa.String(8), nullable=False),
        sa.Column("target_id", GUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "target_type", "target_id",
                            name="uq_likes_user_target"),
    )
    op.create_index("ix_likes_user_id", "likes", ["user_id"])
    op.create_index("ix_likes_target_id", "likes", ["target_id"])

    # --- reports --------------------------------------------------------------
    op.create_table(
        "reports",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("target_type", sa.String(8), nullable=False),
        sa.Column("target_id", GUID, nullable=False),
        sa.Column("reporter_id", GUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("reporter_id", "target_type", "target_id",
                            name="uq_reports_reporter_target"),
    )
    op.create_index("ix_reports_target_id", "reports", ["target_id"])
    op.create_index("ix_reports_reporter_id", "reports", ["reporter_id"])
    op.create_index("ix_reports_resolved", "reports", ["resolved"])

    # --- blocks ---------------------------------------------------------------
    op.create_table(
        "blocks",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blocked_user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "blocked_user_id", name="uq_blocks_pair"),
    )
    op.create_index("ix_blocks_user_id", "blocks", ["user_id"])
    op.create_index("ix_blocks_blocked_user_id", "blocks", ["blocked_user_id"])

    # --- moderation_log -------------------------------------------------------
    op.create_table(
        "moderation_log",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("target_type", sa.String(8), nullable=False),
        sa.Column("target_id", GUID, nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("actor_id", GUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_moderation_log_target_id", "moderation_log", ["target_id"])


def downgrade() -> None:
    op.drop_table("moderation_log")
    op.drop_table("blocks")
    op.drop_table("reports")
    op.drop_table("likes")
    op.drop_table("replies")
    op.drop_table("threads")
    op.drop_table("community_profiles")
