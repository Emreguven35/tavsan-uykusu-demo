"""voice_profiles genişletme + voice_audios ("üret ve bırak" ses paketi)

Revision ID: 0012_ses_paketi
Revises: 0011_kopya_kayit
Create Date: 2026-09-22

ElevenLabs'te klon sesi KALICI TUTULMAYACAK: hesabın 10 klon slotu var, 94+
kullanıcı. Klonlama biter bitmez masal/ninni paketi üretilip kendi depomuza
yazılıyor, ardından ElevenLabs'teki ses siliniyor. Slot artık yalnız üretim
süresince (dakikalar) tutuluyor.

GERİYE UYUMLU:
  • `elevenlabs_voice_id` ZATEN nullable — değişiklik gerekmiyor.
  • Yeni sayaç sütunları NOT NULL + server_default=0; mevcut 6 satır 0/0 ile
    başlar ve bu doğrudur (o paketler henüz üretilmedi).
  • `status` alan adı/tipi AYNI kalıyor; yalnız kabul edilen değer kümesi
    genişliyor. Enum YERİNE String bilinçli: Postgres enum'a değer eklemek
    migration gerektirir, akış hâlâ oturuyor.
  • Eski `pending`/`replaced` değerleri korunur, backfill YAPILMAZ.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_ses_paketi"
down_revision = "0011_kopya_kayit"
branch_labels = None
depends_on = None

_GUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("voice_profiles",
                  sa.Column("progress_done", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("voice_profiles",
                  sa.Column("progress_total", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("voice_profiles",
                  sa.Column("released_at", sa.DateTime(timezone=True),
                            nullable=True))
    op.add_column("voice_profiles",
                  sa.Column("error", sa.Text(), nullable=True))

    op.create_table(
        "voice_audios",
        sa.Column("id", _GUID, primary_key=True),
        sa.Column("voice_profile_id", _GUID,
                  sa.ForeignKey("voice_profiles.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("content_id", sa.String(length=80), nullable=False),
        sa.Column("storage_path", sa.String(length=400), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("voice_profile_id", "content_id",
                            name="uq_voice_audios_profile_content"),
    )
    op.create_index("ix_voice_audios_voice_profile_id", "voice_audios",
                    ["voice_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_audios_voice_profile_id", table_name="voice_audios")
    op.drop_table("voice_audios")
    op.drop_column("voice_profiles", "error")
    op.drop_column("voice_profiles", "released_at")
    op.drop_column("voice_profiles", "progress_total")
    op.drop_column("voice_profiles", "progress_done")
