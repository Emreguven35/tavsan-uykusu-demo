"""kapsama telemetrisi — chat_messages.retrieval_layer + top_score

Revision ID: 0005_chat_telemetry
Revises: 0004_notifications
Create Date: 2026-08-03

Faz 6.4. Hangi sorunun hangi fallback katmanında cevaplandığını kaydeder:
  k1 = metodolojiden doğrudan
  k2 = en yakın bilgi (yaş bandı genişletme / düşük eşik)
  k3 = genel ilke + netleştirme sorusu
  k4 = kapsam dışı

Haftalık kapsama analizi (korpusta karşılıksız kalan sorular):
    SELECT content, top_score, created_at
      FROM chat_messages
     WHERE role = 'user'
       AND retrieval_layer IN ('k3', 'k4')
       AND created_at >= now() - interval '7 days'
     ORDER BY created_at DESC;

GERİYE UYUMLU: iki sütun da nullable, varsayılan YOK — mevcut satırlar NULL kalır.
Cache hit'lerde de NULL yazılır (retrieval yapılmadığından katman ölçülemez).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_chat_telemetry"
down_revision = "0004_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages",
                  sa.Column("retrieval_layer", sa.String(2), nullable=True))
    op.add_column("chat_messages",
                  sa.Column("top_score", sa.Float(), nullable=True))
    # Haftalık k3/k4 sorgusu bu indeksi kullanır.
    op.create_index("ix_chat_messages_retrieval_layer", "chat_messages",
                    ["retrieval_layer"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_retrieval_layer", table_name="chat_messages")
    op.drop_column("chat_messages", "top_score")
    op.drop_column("chat_messages", "retrieval_layer")
