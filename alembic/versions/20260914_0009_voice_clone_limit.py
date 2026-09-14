"""voice_profiles.last_cloned_at — aylık ses klonlama limiti

Revision ID: 0009_voice_clone_limit
Revises: 0008_api_usage
Create Date: 2026-09-14

Gizlilik politikası "ses kaydı ayda bir kez yenilenebilir" diyordu ama kodda
HİÇBİR kontrol yoktu: kullanıcı istediği kadar klon açabiliyordu. Sonuç —
ElevenLabs slot ve ücret birikmesi + gereksiz biyometrik veri saklama. Politika
ile davranış ayrışmıştı; bu sütun limitin kaynağıdır.

GERİYE UYUMLU: nullable, varsayılan YOK. Mevcut satırlarda NULL kalır ve limit
hesabı COALESCE(last_cloned_at, created_at) ile yürür — yani eski kullanıcılara
sessizce fazladan bir klonlama hakkı DOĞMAZ (created_at zaten klonlama anıdır).
Sütunu backfill ETMİYORUZ: created_at fiilen aynı bilgi, kopyalamak veriyi
çiftler ve ikisi ileride ayrışabilir.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_voice_clone_limit"
down_revision = "0008_api_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voice_profiles",
                  sa.Column("last_cloned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("voice_profiles", "last_cloned_at")
