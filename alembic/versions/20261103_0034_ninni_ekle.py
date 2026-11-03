"""users.ses_ninni_ekle_at + voice_profiles.ninni_ekle_*

Revision ID: 0034_ninni_ekle
Revises: 0033_ai_cevap_bildirimleri
Create Date: 2026-11-03

"Ninnilerini al" (ANNE_SESI_NINNI_EKLE): paketi STS'den önce üretilmiş anne
BİR KEZ yeniden kaydedip yalnız STS'si eksik ninnileri mevcut profiline ekler.
  users.ses_ninni_ekle_at        tek seferlik hakkın kullanıldığı an (NULL = hak var;
                                 hiç ninni üretilemezse yeniden NULL = iade)
  voice_profiles.ninni_ekle_durum   hazirlaniyor | tamam | uretilemedi | NULL
  voice_profiles.ninni_ekle_voice_id  geçici klon (iş bitince NULL; günlük temizlik korur)
  voice_profiles.ninni_ekle_at      işin başladığı an
Eski satırlar NULL kalır. Değerler en fazla 12 karakter (Postgres VARCHAR).
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_ninni_ekle"
down_revision = "0033_ai_cevap_bildirimleri"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ses_ninni_ekle_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.add_column("voice_profiles", sa.Column("ninni_ekle_durum", sa.String(length=12),
                                              nullable=True))
    op.add_column("voice_profiles", sa.Column("ninni_ekle_voice_id",
                                              sa.String(length=120), nullable=True))
    op.add_column("voice_profiles", sa.Column("ninni_ekle_at", sa.DateTime(timezone=True),
                                              nullable=True))


def downgrade() -> None:
    op.drop_column("voice_profiles", "ninni_ekle_at")
    op.drop_column("voice_profiles", "ninni_ekle_voice_id")
    op.drop_column("voice_profiles", "ninni_ekle_durum")
    op.drop_column("users", "ses_ninni_ekle_at")
