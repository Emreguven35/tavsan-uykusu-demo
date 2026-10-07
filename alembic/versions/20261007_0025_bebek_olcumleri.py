"""bebek_olcumleri: bebeğin boy/kilo ölçüm geçmişi

Revision ID: 0025_bebek_olcumleri
Revises: 0024_bakim_kaynagi
Create Date: 2026-10-07

İlk açılış akışında (sonraki build) bebek oluşturulurken boy/kilo gelir. Yalnız
saklanır; hiçbir hesapta kullanılmaz. Bebek silinince ölçümleri de silinir.
Hassasiyet: boy NUMERIC(4,1), kilo NUMERIC(4,2).
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_bebek_olcumleri"
down_revision = "0024_bakim_kaynagi"
branch_labels = None
depends_on = None

from api.db.base import GUID as _GUID   # önceki migrasyonlarla aynı tip (PG: UUID)


def upgrade() -> None:
    op.create_table(
        "bebek_olcumleri",
        sa.Column("id", _GUID, primary_key=True),
        sa.Column("baby_id", _GUID, sa.ForeignKey("babies.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("olcum_tarihi", sa.Date(), nullable=False),
        sa.Column("boy_cm", sa.Numeric(4, 1), nullable=True),
        sa.Column("kilo_kg", sa.Numeric(4, 2), nullable=True),
        sa.Column("kaynak", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("boy_cm IS NOT NULL OR kilo_kg IS NOT NULL",
                           name="ck_bebek_olcumleri_dolu"),
    )
    op.create_index("ix_bebek_olcumleri_baby_tarih", "bebek_olcumleri",
                    ["baby_id", "olcum_tarihi"])


def downgrade() -> None:
    op.drop_index("ix_bebek_olcumleri_baby_tarih", table_name="bebek_olcumleri")
    op.drop_table("bebek_olcumleri")
