"""plan_uretim_isleri: sahip + parametreler + deneme

Revision ID: 0021_is_dayanikliligi
Revises: 0020_kvkk
Create Date: 2026-09-25

2026-09-25 olayı: deploy SIGTERM'i LLM çağrısındaki plan işini öldürdü, satır
sonsuza dek "processing" kaldı. `sahip` (süreç kimliği; NULL = yetim),
`parametreler` (yeniden koşmak için girdiler) ve `deneme` ile kapanışta
yetim bırakılan iş başka süreçte bir kez yeniden koşulur (plan_jobs.bakim).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021_is_dayanikliligi"
down_revision = "0020_kvkk"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("plan_uretim_isleri",
                  sa.Column("sahip", sa.String(length=120), nullable=True))
    op.add_column("plan_uretim_isleri", sa.Column("parametreler", _JSONB, nullable=True))
    op.add_column("plan_uretim_isleri",
                  sa.Column("deneme", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("plan_uretim_isleri", "deneme")
    op.drop_column("plan_uretim_isleri", "parametreler")
    op.drop_column("plan_uretim_isleri", "sahip")
