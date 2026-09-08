"""api_usage — dış servis çağrılarının gerçek kullanım/maliyet kaydı

Anthropic ve ElevenLabs çağrılarının GERÇEK usage verisi burada tutulur
(tahmin değil). İçerik saklanmaz — yalnız sayaçlar, model adı ve user_id.

user_id ondelete=SET NULL: kullanıcı hesabını silince muhasebe kaydı kalır ama
kime ait olduğu düşer (KVKK + maliyet geçmişinin bütünlüğü birlikte).

Revision ID: 0008_api_usage
Revises: 0007_retrieval_layer_genislet
"""
import sqlalchemy as sa
from alembic import op

from api.db.base import GUID

revision = "0008_api_usage"
down_revision = "0007_retrieval_layer_genislet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_usage",
        sa.Column("id", GUID, primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("user_id", GUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_api_usage_created_at", "api_usage", ["created_at"])
    op.create_index("ix_api_usage_service", "api_usage", ["service"])
    op.create_index("ix_api_usage_operation", "api_usage", ["operation"])
    op.create_index("ix_api_usage_user_id", "api_usage", ["user_id"])
    op.create_index("ix_api_usage_created_service", "api_usage",
                    ["created_at", "service"])
    op.create_index("ix_api_usage_created_operation", "api_usage",
                    ["created_at", "operation"])


def downgrade() -> None:
    op.drop_index("ix_api_usage_created_operation", table_name="api_usage")
    op.drop_index("ix_api_usage_created_service", table_name="api_usage")
    op.drop_index("ix_api_usage_user_id", table_name="api_usage")
    op.drop_index("ix_api_usage_operation", table_name="api_usage")
    op.drop_index("ix_api_usage_service", table_name="api_usage")
    op.drop_index("ix_api_usage_created_at", table_name="api_usage")
    op.drop_table("api_usage")
