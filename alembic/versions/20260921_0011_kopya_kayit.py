"""(baby_id, client_id) tekil indeksi + silinen_sleep_logs arşivi (K18)

Revision ID: 0011_kopya_kayit
Revises: 0010_baby_kalici_profil
Create Date: 2026-09-21

K18.1 — `sleep_logs.client_id` ZATEN VAR (0001'den beri, VARCHAR(64) nullable)
ve üzerinde `uq_sleep_logs_user_client` (user_id, client_id) tekil kısıtı var.
Spec (baby_id, client_id) istiyor; ekleniyor.

DÜRÜSTLÜK NOTU: mevcut (user_id, client_id) kısıtı DAHA GENİŞ kapsar — bir bebek
tek bir kullanıcıya ait olduğu için (user_id, client_id) tekilse (baby_id,
client_id) de zorunlu olarak tekildir. Yani yeni indeks fazladan bir SATIR
engellemiyor; sözleşmeyi açık hale getiriyor ve bebek bazlı upsert sorgusunu
indeksli kılıyor (K18.2 bu sorguyu her batch'te koşuyor).

Asıl kopya sorunu bu indeksle ÇÖZÜLMÜYOR: prod'da ölçülen 41 kopya çiftinin
tamamında client_id'ler FARKLI — zayıf ağda yeniden gönderim mobilde yeni bir
client_id üretiyor. Çözüm K18.2'nin ±3 dk penceresi ve K18.3 temizliği.

NULL client_id'ler tekil kısıtı İHLAL ETMEZ (SQL'de NULL != NULL), dolayısıyla
backend'de oluşturulan kayıtlar etkilenmez.

silinen_sleep_logs: K18.3 temizliğinin sildiği satırların tam JSON arşivi.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_kopya_kayit"
down_revision = "0010_baby_kalici_profil"
branch_labels = None
depends_on = None

_GUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_index("uq_sleep_logs_baby_client", "sleep_logs",
                    ["baby_id", "client_id"], unique=True)

    op.create_table(
        "silinen_sleep_logs",
        sa.Column("id", _GUID, primary_key=True),
        sa.Column("sleep_log_id", _GUID, nullable=False),
        sa.Column("user_id", _GUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baby_id", _GUID, nullable=False),
        sa.Column("veri", sa.Text(), nullable=False),
        sa.Column("sebep", sa.String(length=200), nullable=False),
        sa.Column("korunan_log_id", _GUID, nullable=True),
        sa.Column("silindi_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_silinen_sleep_logs_sleep_log_id", "silinen_sleep_logs",
                    ["sleep_log_id"])
    op.create_index("ix_silinen_sleep_logs_user_id", "silinen_sleep_logs",
                    ["user_id"])
    op.create_index("ix_silinen_sleep_logs_baby_id", "silinen_sleep_logs",
                    ["baby_id"])


def downgrade() -> None:
    op.drop_index("ix_silinen_sleep_logs_baby_id", table_name="silinen_sleep_logs")
    op.drop_index("ix_silinen_sleep_logs_user_id", table_name="silinen_sleep_logs")
    op.drop_index("ix_silinen_sleep_logs_sleep_log_id",
                  table_name="silinen_sleep_logs")
    op.drop_table("silinen_sleep_logs")
    op.drop_index("uq_sleep_logs_baby_client", table_name="sleep_logs")
