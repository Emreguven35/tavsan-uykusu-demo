"""kapanis_kaynagi='bakim' (bakım betiğinin eski kapanışları) + K13.3 notu kalkar

Revision ID: 0024_bakim_kaynagi
Revises: 0023_kapanis_kaynagi
Create Date: 2026-10-07

1. scripts/acik_sayac_kapat.py artık kapattığı kayda 'bakim' yazıyor. Daha önce
   kapattıkları (09-30 koşusu, prod'da 10 kayıt) notlarından bulunur: betiğin
   "otomatik kapatıldı" notu; K16.1 ("yeni kayıt açıldı") ve K13.3 ("manuel
   kayıt girildi") notları hariç, yalnız KAPALI ve kaynağı boş kayıtlar.
   Arşivdeki 11. kayıt sonradan kullanıcı tarafından değiştirildi, notu yok —
   kullanıcının kaydıdır, işaretlenmez.
2. K13.3 artık not yazmıyor (timeline bayrağı mobilde gerçek bitişi "kontrol
   edilmeli" gösteriyordu). v2.7.6 yayındayken yazılmış K13.3 notları silinir.

Ham SQL: updated_at'e dokunmaz.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_bakim_kaynagi"
down_revision = "0023_kapanis_kaynagi"
branch_labels = None
depends_on = None

_K13_NOT = "otomatik kapatıldı: manuel kayıt girildi"


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE sleep_logs SET kapanis_kaynagi = 'bakim' "
        "WHERE ended_at IS NOT NULL AND kapanis_kaynagi IS NULL "
        "AND notes LIKE :bakim AND notes NOT LIKE :k16 AND notes NOT LIKE :k13"
    ).bindparams(bakim="%otomatik kapatıldı%", k16="%yeni kayıt açıldı%",
                 k13="%manuel kayıt girildi%"))
    op.execute(sa.text(
        "UPDATE sleep_logs SET notes = NULLIF(TRIM(REPLACE(REPLACE(REPLACE("
        "notes, :sonda, ''), :basta, ''), :yalin, '')), '') WHERE notes LIKE :k13"
    ).bindparams(sonda=" | " + _K13_NOT, basta=_K13_NOT + " | ", yalin=_K13_NOT,
                 k13="%manuel kayıt girildi%"))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE sleep_logs SET kapanis_kaynagi = NULL WHERE kapanis_kaynagi = 'bakim'"))
