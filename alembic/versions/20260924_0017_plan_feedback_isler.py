"""plan_feedback + plan_uretim_isleri

Revision ID: 0017_plan_feedback_isler
Revises: 0016_uyku_sesleri
Create Date: 2026-09-24

plan_feedback: annenin plan bloğuna geri bildirimi + o anın plan/kayıt/yaş
anlık görüntüsü (İlayda günlük denetimi bunu okur). (user_id, client_id)
tekil — mobil yeniden gönderimi kopya yazmaz.

plan_uretim_isleri: asenkron plan işlerinin worker'lar arası paylaşılan
durumu (D-3: bellek kaydı 4 worker'da 404 veriyordu).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_plan_feedback_isler"
down_revision = "0016_uyku_sesleri"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "plan_feedback",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baby_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("babies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("block_key", sa.String(length=40), nullable=False),
        sa.Column("block_time", sa.String(length=20), nullable=True),
        sa.Column("secenek", sa.String(length=80), nullable=True),
        sa.Column("metin", sa.Text(), nullable=True),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("plan_date", sa.Date(), nullable=True),
        sa.Column("anlik", _JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "client_id",
                            name="uq_plan_feedback_user_client"),
    )
    op.create_index("ix_plan_feedback_user_id", "plan_feedback", ["user_id"])
    op.create_index("ix_plan_feedback_baby_id", "plan_feedback", ["baby_id"])
    op.create_index("ix_plan_feedback_created_at", "plan_feedback",
                    ["created_at"])

    op.create_table(
        "plan_uretim_isleri",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("baby_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_plan_uretim_isleri_user_id", "plan_uretim_isleri",
                    ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_uretim_isleri_user_id",
                  table_name="plan_uretim_isleri")
    op.drop_table("plan_uretim_isleri")
    op.drop_index("ix_plan_feedback_created_at", table_name="plan_feedback")
    op.drop_index("ix_plan_feedback_baby_id", table_name="plan_feedback")
    op.drop_index("ix_plan_feedback_user_id", table_name="plan_feedback")
    op.drop_table("plan_feedback")
