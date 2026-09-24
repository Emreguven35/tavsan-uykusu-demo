"""sleep_sounds — uyku sesleri kataloğu

Revision ID: 0016_uyku_sesleri
Revises: 0015_plan_tekilligi
Create Date: 2026-09-24

Eğitim videolarıyla aynı desen: katalog DB'de, dosya volume'da
(`sounds/{slug}.m4a`). TOHUMLAMA BU MİGRASYONDA DEĞİL, `scripts/ses_yukle.py`
içinde: satır ancak dosya volume'a yüklendikten sonra (süre ve boyut
ölçülmüşken) yazılır. Migrasyonda tohumlamak, dosyası henüz olmayan sesleri
katalogda gösterirdi — mobilde çalmayan kart.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_uyku_sesleri"
down_revision = "0015_plan_tekilligi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sleep_sounds",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("order_in_category", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("duration_sec", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_url", sa.String(length=400), nullable=False),
        sa.Column("is_free", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_sleep_sounds_slug", "sleep_sounds", ["slug"],
                    unique=True)
    op.create_index("ix_sleep_sounds_category", "sleep_sounds", ["category"])


def downgrade() -> None:
    op.drop_index("ix_sleep_sounds_category", table_name="sleep_sounds")
    op.drop_index("ix_sleep_sounds_slug", table_name="sleep_sounds")
    op.drop_table("sleep_sounds")
