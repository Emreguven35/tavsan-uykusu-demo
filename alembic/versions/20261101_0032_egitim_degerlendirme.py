"""egitim_degerlendirmeleri — 13. gün eğitim değerlendirmesi

Revision ID: 0032_egitim_degerlendirme
Revises: 0031_ses_yontem
Create Date: 2026-11-01

Annenin 13. gün cevapları (kendi_donuyor, yatir_cik, uzun_aglama) ve karar
(mezun | devam | yeniden_basla). "yeniden_basla"da eski denemenin başlangıcı,
günü ve aşaması bu satırda saklanır (babies.training_started_at yarına çekilir).
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID

revision = "0032_egitim_degerlendirme"
down_revision = "0031_ses_yontem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "egitim_degerlendirmeleri",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("baby_id", GUID, sa.ForeignKey("babies.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("gun", sa.Date(), nullable=False),
        sa.Column("deneme_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("egitim_baslangic", sa.Date(), nullable=True),
        sa.Column("egitim_gunu", sa.Integer(), nullable=True),
        sa.Column("mevcut_asama", sa.String(length=30), nullable=True),
        sa.Column("kendi_donuyor", sa.Boolean(), nullable=False),
        sa.Column("yatir_cik", sa.Boolean(), nullable=False),
        sa.Column("uzun_aglama", sa.Boolean(), nullable=True),
        sa.Column("sonuc", sa.String(length=20), nullable=False),
        sa.Column("devam_turu", sa.String(length=20), nullable=True),
        sa.Column("yeniden_degerlendirme_tarihi", sa.Date(), nullable=True),
        sa.Column("yeni_baslangic", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_egitim_degerlendirmeleri_user_id", "egitim_degerlendirmeleri",
                    ["user_id"])
    op.create_index("ix_egitim_degerlendirmeleri_baby_id", "egitim_degerlendirmeleri",
                    ["baby_id"])


def downgrade() -> None:
    op.drop_index("ix_egitim_degerlendirmeleri_baby_id", table_name="egitim_degerlendirmeleri")
    op.drop_index("ix_egitim_degerlendirmeleri_user_id", table_name="egitim_degerlendirmeleri")
    op.drop_table("egitim_degerlendirmeleri")
