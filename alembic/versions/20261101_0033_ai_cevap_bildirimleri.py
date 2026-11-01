"""ai_cevap_bildirimleri + chat_messages.baby_id / soru_id

Revision ID: 0033_ai_cevap_bildirimleri
Revises: 0032_egitim_degerlendirme
Create Date: 2026-11-01

Sor cevabını bildirme (Play AI içerik politikası). POST /chat yanıtı artık
asistan satırının id'sini (mesaj_id) döndürüyor; bildirim o satıra bağlanır.
chat_messages'a cevabın bebeği (baby_id) ve soru satırı (soru_id) eklenir —
bildirimdeki soru kopyası zaman damgası tahminine dayanmasın. Eski satırlar NULL.
"""
from alembic import op
import sqlalchemy as sa

from api.db.base import GUID

revision = "0033_ai_cevap_bildirimleri"
down_revision = "0032_egitim_degerlendirme"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_messages") as b:
        b.add_column(sa.Column("baby_id", GUID, nullable=True))
        b.add_column(sa.Column("soru_id", GUID, nullable=True))
        b.create_foreign_key("fk_chat_messages_baby_id", "babies", ["baby_id"], ["id"],
                             ondelete="SET NULL")
    op.create_table(
        "ai_cevap_bildirimleri",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("user_id", GUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("baby_id", GUID, sa.ForeignKey("babies.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("mesaj_id", GUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sebep", sa.String(length=32), nullable=False),
        sa.Column("not", sa.Text(), nullable=True),
        sa.Column("soru_metni", sa.Text(), nullable=True),
        sa.Column("cevap_metni", sa.Text(), nullable=False),
        sa.Column("durum", sa.String(length=16), nullable=False, server_default="yeni"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("user_id", "mesaj_id", name="uq_ai_cevap_bildirim_user_mesaj"),
    )
    op.create_index("ix_ai_cevap_bildirimleri_user_id", "ai_cevap_bildirimleri", ["user_id"])
    op.create_index("ix_ai_cevap_bildirimleri_created_at", "ai_cevap_bildirimleri",
                    ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_cevap_bildirimleri_created_at", table_name="ai_cevap_bildirimleri")
    op.drop_index("ix_ai_cevap_bildirimleri_user_id", table_name="ai_cevap_bildirimleri")
    op.drop_table("ai_cevap_bildirimleri")
    with op.batch_alter_table("chat_messages") as b:
        b.drop_constraint("fk_chat_messages_baby_id", type_="foreignkey")
        b.drop_column("soru_id")
        b.drop_column("baby_id")
