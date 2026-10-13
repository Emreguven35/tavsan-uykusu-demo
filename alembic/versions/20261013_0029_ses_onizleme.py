"""voice_profiles.amac + users.ses_paket_kaydi_at

Revision ID: 0029_ses_onizleme
Revises: 0028_premium_kademe
Create Date: 2026-10-13

Anne Sesi önizlemesi: premium olmayan anne de kayıt yapar, yalnız tek masalın
30 sn'lik önizlemesi üretilir (voice_profiles.amac = 'onizleme'). Premium alınca
BİR KEZ beklemesiz yeniden kayıt hakkı → users.ses_paket_kaydi_at.
Eski satırlar NULL kalır: amac NULL = tam paket.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_ses_onizleme"
down_revision = "0028_premium_kademe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voice_profiles", sa.Column("amac", sa.String(length=20),
                                              nullable=True))
    op.add_column("users", sa.Column("ses_paket_kaydi_at", sa.DateTime(timezone=True),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ses_paket_kaydi_at")
    op.drop_column("voice_profiles", "amac")
