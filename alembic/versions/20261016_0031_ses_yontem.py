"""voice_audios.yontem (sts | tts)

Revision ID: 0031_ses_yontem
Revises: 0030_urun_kimligi
Create Date: 2026-10-16

Ninni yalnız uzmanın referans kaydından speech-to-speech ile üretildiyse
listelenir; eski düz okuma (TTS) ninnileri /voice/stories'ten çıkar (dosyalar
SİLİNMEZ). Hangi dosyanın nasıl üretildiğini ayırmak için yeni sütun. Eski
satırlar NULL kalır = TTS (ANNE_SESI_STS canlıda o güne dek hiç açılmadı).
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_ses_yontem"
down_revision = "0030_urun_kimligi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voice_audios", sa.Column("yontem", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("voice_audios", "yontem")
