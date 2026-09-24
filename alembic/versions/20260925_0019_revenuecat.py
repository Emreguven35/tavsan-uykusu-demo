"""RevenueCat: subscriptions genişletme + revenuecat_olaylari + premium_haklari

Revision ID: 0019_revenuecat
Revises: 0018_surum_resmi_rozet
Create Date: 2026-09-25

subscriptions'a RevenueCat alanları eklenir (hepsi NULL olabilir — /verify ile
gelmiş eski satırlar bozulmaz). revenuecat_olaylari webhook idempotency
defteridir (event.id birincil anahtar). premium_haklari admin hediyesidir.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_revenuecat"
down_revision = "0018_surum_resmi_rozet"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    for kolon in (
        sa.Column("store", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("will_renew", sa.Boolean(), nullable=True),
        sa.Column("environment", sa.String(length=12), nullable=True),
        sa.Column("sonraki_urun", sa.String(length=120), nullable=True),
        sa.Column("raw_event", _JSONB, nullable=True),
    ):
        op.add_column("subscriptions", kolon)

    op.create_table(
        "revenuecat_olaylari",
        sa.Column("id", sa.String(length=100), primary_key=True),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("app_user_id", sa.String(length=200), nullable=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sonuc", sa.String(length=200), nullable=False),
        sa.Column("raw", _JSONB, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_revenuecat_olaylari_user_id", "revenuecat_olaylari",
                    ["user_id"])

    op.create_table(
        "premium_haklari",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baslangic", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bitis", sa.DateTime(timezone=True), nullable=False),
        sa.Column("veren_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("aciklama", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_premium_haklari_user_id", "premium_haklari", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_premium_haklari_user_id", table_name="premium_haklari")
    op.drop_table("premium_haklari")
    op.drop_index("ix_revenuecat_olaylari_user_id", table_name="revenuecat_olaylari")
    op.drop_table("revenuecat_olaylari")
    for ad in ("raw_event", "sonraki_urun", "environment", "will_renew",
               "purchased_at", "period_type", "store"):
        op.drop_column("subscriptions", ad)
