"""KVKK: consents + users.son_export_at

Revision ID: 0020_kvkk
Revises: 0019_revenuecat
Create Date: 2026-09-25

consents: salt-ekleme onay defteri (tür, metin sürümü, onay, zaman, ip_hash).
users.son_export_at: GET /account/export 24 saatte bir sınırı.
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_kvkk"
down_revision = "0019_revenuecat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tur", sa.String(length=30), nullable=False),
        sa.Column("metin_surumu", sa.String(length=40), nullable=False),
        sa.Column("onay", sa.Boolean(), nullable=False),
        sa.Column("zaman", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("kaynak", sa.String(length=20), nullable=False,
                  server_default="uygulama"),
    )
    op.create_index("ix_consents_user_id", "consents", ["user_id"])
    op.create_index("ix_consents_zaman", "consents", ["zaman"])
    op.add_column("users", sa.Column("son_export_at", sa.DateTime(timezone=True),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("users", "son_export_at")
    op.drop_index("ix_consents_zaman", table_name="consents")
    op.drop_index("ix_consents_user_id", table_name="consents")
    op.drop_table("consents")
