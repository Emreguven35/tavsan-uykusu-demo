"""chat_messages.retrieval_layer: String(2) → String(32)

ACİL DÜZELTME. Kolon Faz 6.4'te 'k1'..'k4' için String(2) açılmıştı. Faz E
(duygusal ton) kriz kapısını ekleyip retrieval_layer'a 'ruhsal_kriz' (11
karakter) yazmaya başladı; Postgres bunu kabul etmeyip
StringDataRightTruncation fırlatıyor. Sonuç: kendine ya da bebeğine zarar
vermekten söz eden anne, destek mesajı yerine 500 alıyordu.

SQLite VARCHAR uzunluğunu ZORLAMAZ; yerel testler bu yüzden yeşil geçti.
Postgres'e özgü bu tuzağa karşı testte de uzunluk kontrolü eklendi
(tests/test_kapsama.py).

Bu migration ayrıca yeni 'k3_5' katmanına yer açar (kapsama düzeltmesi).

Revision ID: 0007_retrieval_layer_genislet
Revises: 0006_community
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_retrieval_layer_genislet"
down_revision = "0006_community"
branch_labels = None
depends_on = None

# Kolon uzunluğu tek yerden: model (String(32)) ile hizalı kalmalı.
YENI_UZUNLUK = 32
ESKI_UZUNLUK = 2


def upgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.alter_column(
            "retrieval_layer",
            existing_type=sa.String(ESKI_UZUNLUK),
            type_=sa.String(YENI_UZUNLUK),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Geri alırken 2 karaktere sığmayan değerler kırpılmak yerine NULL'lanır:
    # telemetri satırı kaybolur ama veri bozulmaz ve migration patlamaz.
    op.execute(
        "UPDATE chat_messages SET retrieval_layer = NULL "
        f"WHERE retrieval_layer IS NOT NULL AND LENGTH(retrieval_layer) > {ESKI_UZUNLUK}"
    )
    with op.batch_alter_table("chat_messages") as batch:
        batch.alter_column(
            "retrieval_layer",
            existing_type=sa.String(YENI_UZUNLUK),
            type_=sa.String(ESKI_UZUNLUK),
            existing_nullable=True,
        )
