"""babies eğitim takibi — training_started_at / training_completed_at

Revision ID: 0003_baby_training
Revises: 0002_auth_tokens
Create Date: 2026-08-03

Faz 6.1R (İlayda protokolü): mobildeki 14 günlük eğitim modülü bu tarihleri set eder.
Regresyon tespiti training_completed_at üzerinden çalışır — eğitim bitiminden ≥13 gün
sonra "kendine dalamama" sinyali görülürse kullanıcıya programı baştan başlatma
önerilir (otomatik hiçbir şey üretilmez).

GERİYE UYUMLU: iki sütun da nullable, varsayılan YOK — mevcut satırlar NULL kalır
ve eski istemciler etkilenmez.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_baby_training"
down_revision = "0002_auth_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("babies", sa.Column("training_started_at", sa.Date(), nullable=True))
    op.add_column("babies", sa.Column("training_completed_at", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("babies", "training_completed_at")
    op.drop_column("babies", "training_started_at")
