"""babies.regresyon_kendi_donuyor + regresyon_cevap_at (v1.4 regresyon akışı)

Revision ID: 0013_regresyon_cevap
Revises: 0012_ses_paketi
Create Date: 2026-09-22

İlayda (S9): regresyon şüphesinde ÖNCE anneye sorulur — "çocuk 20 dakika
beklerken kendi uykuya dönüyor mu?". Cevap akışı belirliyor:
  • evet  → kart 7 gün kapanır (tekrar sorulmaz),
  • hayır → 45 gün dolmadıysa "eğitime devam", dolduysa tıbbi yönlendirme.

Cevabı saklamak ŞART: aksi hâlde kart her açılışta yeniden sorulur ve anne
aynı soruyu günde defalarca görür. `regresyon_cevap_at` cevabın ne zaman
verildiğini tutar — 7 günlük sessizlik penceresi bundan hesaplanır.

İkisi de nullable: mevcut bebeklerde cevap YOK demektir ve akış birinci
kademeden (soru) başlar.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_regresyon_cevap"
down_revision = "0012_ses_paketi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("babies",
                  sa.Column("regresyon_kendi_donuyor", sa.Boolean(),
                            nullable=True))
    op.add_column("babies",
                  sa.Column("regresyon_cevap_at", sa.DateTime(timezone=True),
                            nullable=True))


def downgrade() -> None:
    op.drop_column("babies", "regresyon_cevap_at")
    op.drop_column("babies", "regresyon_kendi_donuyor")
