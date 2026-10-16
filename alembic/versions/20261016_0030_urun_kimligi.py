"""subscriptions.product_id tek biçim (urun:plan → urun) + çift satır birleştirme

Revision ID: 0030_urun_kimligi
Revises: 0029_ses_onizleme
Create Date: 2026-10-16

Google Play abonelikleri RevenueCat'te "tavsan_premium_aylik:aylik" biçiminde
gelebiliyordu; webhook/refresh/verify artık ":" öncesini kullanıyor
(services.premium.urun_kimligi). Bu migration mevcut satırları aynı biçime
getirir: aynı (user_id, ürün) için birden çok satır varsa bitişi en geç olan
(eşitse en son güncellenen) kalır, diğerleri silinir. Şema değişmez.
Geri alma yok (birleşen satırlar geri ayrılamaz).
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0030_urun_kimligi"
down_revision = "0029_ses_onizleme"
branch_labels = None
depends_on = None

_EN_ESKI = datetime.min.replace(tzinfo=timezone.utc)


def _utc(t):
    if t is None:
        return _EN_ESKI
    if isinstance(t, str):              # SQLite ham metin dönebilir
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def upgrade() -> None:
    bag = op.get_bind()
    abon = sa.table("subscriptions", sa.column("id"), sa.column("user_id"),
                    sa.column("product_id"), sa.column("expires_at"),
                    sa.column("updated_at"))
    satirlar = bag.execute(sa.select(abon.c.id, abon.c.user_id, abon.c.product_id,
                                     abon.c.expires_at, abon.c.updated_at)).fetchall()
    gruplar: dict[tuple, list] = {}
    for r in satirlar:
        urun = str(r.product_id or "").strip().split(":")[0].strip()
        gruplar.setdefault((str(r.user_id), urun), []).append(r)
    for (_, urun), grup in gruplar.items():
        if len(grup) == 1 and grup[0].product_id == urun:
            continue
        kalan = max(grup, key=lambda r: (_utc(r.expires_at), _utc(r.updated_at)))
        for r in grup:
            if r.id != kalan.id:
                bag.execute(abon.delete().where(abon.c.id == r.id))
        if kalan.product_id != urun:
            bag.execute(abon.update().where(abon.c.id == kalan.id)
                        .values(product_id=urun))


def downgrade() -> None:
    pass
