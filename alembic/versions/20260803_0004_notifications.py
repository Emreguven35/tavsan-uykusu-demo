"""bildirim altyapısı — push_tokens + sent_notifications + users.notification_prefs

Revision ID: 0004_notifications
Revises: 0003_baby_training
Create Date: 2026-08-03

Faz 6.2. GERİYE UYUMLU:
  - İki YENİ tablo (mevcut veriye dokunmaz).
  - users.notification_prefs nullable — mevcut satırlar NULL kalır; uygulama NULL'ı
    varsayılan tercihlere (plan_reminders=true, daily_summary=true) çevirir.
    NOT NULL + server_default seçilmedi çünkü büyük tabloda kilit maliyeti var ve
    uygulama katmanı zaten varsayılana düşüyor.
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID, JSONBType

# revision identifiers, used by Alembic.
revision = "0004_notifications"
down_revision = "0003_baby_training"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notification_prefs", JSONBType, nullable=True))

    op.create_table(
        "push_tokens",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expo_token", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(20), nullable=True),
        sa.Column("device_name", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_push_tokens_user_id", "push_tokens", ["user_id"])
    op.create_index("ix_push_tokens_expo_token", "push_tokens",
                    ["expo_token"], unique=True)

    op.create_table(
        "sent_notifications",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", GUID,
                  sa.ForeignKey("sleep_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_key", sa.String(80), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "plan_id", "block_key",
                            name="uq_sent_notifications_user_plan_block"),
    )
    op.create_index("ix_sent_notifications_user_id", "sent_notifications", ["user_id"])
    op.create_index("ix_sent_notifications_plan_id", "sent_notifications", ["plan_id"])


def downgrade() -> None:
    op.drop_table("sent_notifications")
    op.drop_table("push_tokens")
    op.drop_column("users", "notification_prefs")
