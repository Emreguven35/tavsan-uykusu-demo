"""sleep_logs.kapanis_kaynagi: sunucunun otomatik kapattığı kaydın kaynağı

Revision ID: 0023_kapanis_kaynagi
Revises: 0022_topluluk_v2
Create Date: 2026-10-07

Mobil, sunucunun K16.1 ile kapattığı kaydı yerelde hâlâ açık tutup yeniden
gönderiyor; `_kaydi_guncelle` bitişi boşla ezince kayıt YENİDEN AÇILIYORDU
(prod: 09-23'ten beri açık nap, her senkronda geri açılıyor). Karar not
metninden değil bu alandan okunur.

Eski kayıtlar not metninden doldurulur: K16.1 notu ("otomatik kapatıldı: yeni
kayıt açıldı") taşıyan KAPALI kayıtlar → 'k16_1'. K13.3 eskiden not yazmıyordu,
o kapanışlar ayırt edilemez ve NULL kalır. Ham SQL: updated_at'e dokunmaz.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_kapanis_kaynagi"
down_revision = "0022_topluluk_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sleep_logs",
                  sa.Column("kapanis_kaynagi", sa.String(length=16), nullable=True))
    op.execute(sa.text(
        "UPDATE sleep_logs SET kapanis_kaynagi = 'k16_1' "
        "WHERE ended_at IS NOT NULL AND notes LIKE :k16"
    ).bindparams(k16="%yeni kayıt açıldı%"))


def downgrade() -> None:
    op.drop_column("sleep_logs", "kapanis_kaynagi")
