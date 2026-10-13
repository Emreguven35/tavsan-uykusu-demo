"""premium abonelik: users.deneme_* + sor_sayaclari

Revision ID: 0028_premium_kademe
Revises: 0027_engel_ses
Create Date: 2026-10-13

1. Mağaza denemesi (RevenueCat TRIAL) — kademe sütun olarak tutulmaz
   (services.premium.durum hesaplar); denemenin başlangıcı/bitişi ve "deneme hakkı
   kullanıldı" anı kullanıcıda: users.deneme_baslangic / deneme_bitis /
   deneme_kullanildi_at.
2. Sor günlük sayacı (kullanıcı, Türkiye günü) — model çağrısından ÖNCE koşullu
   atomik artırma: sor_sayaclari.
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID

revision = "0028_premium_kademe"
down_revision = "0027_engel_ses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ad in ("deneme_baslangic", "deneme_bitis", "deneme_kullanildi_at"):
        op.add_column("users", sa.Column(ad, sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "sor_sayaclari",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("gun", sa.Date(), nullable=False),
        sa.Column("sayi", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("user_id", "gun", name="uq_sor_sayac_user_gun"),
    )
    op.create_index("ix_sor_sayaclari_user_id", "sor_sayaclari", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sor_sayaclari_user_id", table_name="sor_sayaclari")
    op.drop_table("sor_sayaclari")
    for ad in ("deneme_kullanildi_at", "deneme_bitis", "deneme_baslangic"):
        op.drop_column("users", ad)
