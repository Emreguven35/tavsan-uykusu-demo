"""onay_kanitlari (silinmiş hesapların onay ispatı) + push_tokens.device_name boşaltılır

Revision ID: 0026_saklama
Revises: 0025_bebek_olcumleri
Create Date: 2026-10-07

1. onay_kanitlari: hesap silinince consents satırları buraya taşınır (kullanıcı
   kimliği yok; e-postanın tuzlu özeti var). 10 yıl sonra gece temizliği siler.
2. push_tokens.device_name artık saklanmıyor (iPhone'da çoğu zaman sahibinin
   adını taşıyor: "Ayşe'nin iPhone'u"); mevcut değerler tek seferlik boşaltılır.
   Kolon şimdilik kalır (eski şema/yanıt uyumu), yalnız hep NULL.
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_saklama"
down_revision = "0025_bebek_olcumleri"
branch_labels = None
depends_on = None

from api.db.base import GUID as _GUID   # önceki migrasyonlarla aynı tip (PG: UUID)


def upgrade() -> None:
    op.create_table(
        "onay_kanitlari",
        sa.Column("id", _GUID, primary_key=True),
        sa.Column("tur", sa.String(length=30), nullable=False),
        sa.Column("metin_surumu", sa.String(length=40), nullable=False),
        sa.Column("onay", sa.Boolean(), nullable=False),
        sa.Column("zaman", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kaynak", sa.String(length=20), nullable=False),
        sa.Column("eposta_ozeti", sa.String(length=64), nullable=False, index=True),
        sa.Column("hesap_silindi_at", sa.DateTime(timezone=True), nullable=False,
                  index=True),
    )
    op.execute(sa.text("UPDATE push_tokens SET device_name = NULL "
                       "WHERE device_name IS NOT NULL"))


def downgrade() -> None:
    op.drop_table("onay_kanitlari")
