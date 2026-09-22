"""education_videos + video_progress + babies.mevcut_asama

Revision ID: 0014_egitim_videolari
Revises: 0013_regresyon_cevap
Create Date: 2026-09-22

Uygulama içi eğitim videoları. Katalog DB'de tutulur (dosyada değil): yeni video
eklemek build gerektirmesin — `scripts/video_yukle.py` dosyayı volume'a koyar ve
satırı slug bazlı upsert eder.

`stage_tags` PG'de `text[]`: bir video birden çok aşamaya ait olabilir
(ör. kademeli azaltma → oda_ortasi + kapi) ve "bu aşamaya uyan videolar"
sorgusu dizi üzerinden yapılır.

`video_progress` (user_id, video_id) tekil: ilerleme UPSERT edilir, satır
yığılmaz. Oynatıcı saniyede bir değil, duraklama/çıkışta gönderir.

`babies.mevcut_asama` nullable: NULL = aşamayı plandan türet. Dolu = annenin
beyanı, türetmeyi devre dışı bırakır.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_egitim_videolari"
down_revision = "0013_regresyon_cevap"
branch_labels = None
depends_on = None

# Modellerdeki TextArrayType/JSONBType ile AYNI varyant mantığı.
_TEXT_ARRAY = sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql")
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "education_videos",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("order_in_category", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("duration_sec", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("video_url", sa.String(length=400), nullable=False),
        sa.Column("poster_url", sa.String(length=400), nullable=True),
        sa.Column("stage_tags", _TEXT_ARRAY, nullable=False),
        sa.Column("chapters", _JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_education_videos_slug", "education_videos", ["slug"],
                    unique=True)
    op.create_index("ix_education_videos_category", "education_videos",
                    ["category"])

    op.create_table(
        "video_progress",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("education_videos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("position_sec", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "video_id",
                            name="uq_video_progress_user_video"),
    )
    op.create_index("ix_video_progress_user_id", "video_progress", ["user_id"])
    op.create_index("ix_video_progress_video_id", "video_progress", ["video_id"])

    op.add_column("babies",
                  sa.Column("mevcut_asama", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("babies", "mevcut_asama")
    op.drop_index("ix_video_progress_video_id", table_name="video_progress")
    op.drop_index("ix_video_progress_user_id", table_name="video_progress")
    op.drop_table("video_progress")
    op.drop_index("ix_education_videos_category", table_name="education_videos")
    op.drop_index("ix_education_videos_slug", table_name="education_videos")
    op.drop_table("education_videos")
