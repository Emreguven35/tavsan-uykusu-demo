"""users.app_version + community_profiles.is_official

Revision ID: 0018_surum_resmi_rozet
Revises: 0017_plan_feedback_isler
Create Date: 2026-09-25

users.app_version / app_version_seen_at: mobilin X-App-Version başlığından
annenin son görülen sürümü (denetim raporu eski sürümdeki anneleri sayar).

community_profiles.is_official: "Resmi" rozeti — Tavşan Uykusu Ekibi hesabı.
Kurgusal isimli tohum hesaplarının içeriği bu hesaba taşınır.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_surum_resmi_rozet"
down_revision = "0017_plan_feedback_isler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("app_version", sa.String(length=40),
                                     nullable=True))
    op.add_column("users", sa.Column("app_version_seen_at",
                                     sa.DateTime(timezone=True), nullable=True))
    op.add_column("community_profiles",
                  sa.Column("is_official", sa.Boolean(), nullable=False,
                            server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("community_profiles", "is_official")
    op.drop_column("users", "app_version_seen_at")
    op.drop_column("users", "app_version")
