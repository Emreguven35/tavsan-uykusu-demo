"""blocks.anonim_kaynak + users.ses_son_kayit_at

Revision ID: 0027_engel_ses
Revises: 0026_saklama
Create Date: 2026-10-07

1. İçerikten engelleme (POST /community/block/icerik): anonim gönderiden açılan
   engelde liste yazarı göstermez → blocks.anonim_kaynak.
2. "Sesimi sil" ses profilini siler; aylık kayıt hakkı etkilenmesin diye son
   kayıt anı kullanıcıda saklanır → users.ses_son_kayit_at.
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_engel_ses"
down_revision = "0026_saklama"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("blocks", sa.Column("anonim_kaynak", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.add_column("users", sa.Column("ses_son_kayit_at", sa.DateTime(timezone=True),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ses_son_kayit_at")
    op.drop_column("blocks", "anonim_kaynak")
