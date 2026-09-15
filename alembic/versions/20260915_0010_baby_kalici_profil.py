"""babies.saglik_problemi + babies.dogum_haftasi — kalıcı profil (v2.1 / Faz 2)

Revision ID: 0010_baby_kalici_profil
Revises: 0009_voice_clone_limit
Create Date: 2026-09-15

SORUN: bu iki alan yalnız POST /plans/generate gövdesindeki `profile_overrides`
sözlüğünde geliyordu ve HİÇBİR YERDE SAKLANMIYORDU. Ölçülen sonuç: bebek yaş
bandı atlayınca plan `run_adaptation` içinden
`generate_content(baby, None, dogum_haftasi)` ile YENİDEN üretiliyor —
`req_overrides=None` olduğu için `saglik_problemi` kayboluyor ve
"⚠️ Sağlık sorununuz var, doktor onayı ZORUNLU" uyarısı sessizce siliniyordu.
Yani en kritik kırmızı bayrak, bir yaş geçişinde kendiliğinden yok oluyordu.

`dogum_haftasi` aynı sebeple kalıcılaşıyor: prematüre düzeltmesi bu sayıdan
çıkıyor ve isteğe bağlı bir gövde alanında yaşayamaz.

GERİYE UYUMLU: ikisi de nullable, varsayılan YOK, backfill YOK.
  - `dogum_haftasi` NULL → motor 40 (miadında) sayar; eski davranışın aynısı.
  - `saglik_problemi` NULL → sağlık uyarısı üretilmez; eski davranışın aynısı.
Mevcut satırlar için hiçbir uyarı ne doğar ne kaybolur.

VARCHAR TUZAĞI: `saglik_problemi` bilerek Text — anne serbest metin yazıyor ve
String(N) seçilirse sınır yerelde (sqlite, uzunluk yok sayılır) görünmeyip
üretimde (Postgres) INSERT'i patlatır.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010_baby_kalici_profil"
down_revision = "0009_voice_clone_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("babies", sa.Column("saglik_problemi", sa.Text(), nullable=True))
    op.add_column("babies", sa.Column("dogum_haftasi", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("babies", "dogum_haftasi")
    op.drop_column("babies", "saglik_problemi")
