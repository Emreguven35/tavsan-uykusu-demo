"""
Ücretsiz / premium ayrımı — eğitim videoları ve Anne Sesi (2026-10-13).

Premium kararı services.premium.durum'dan gelir (TEK karar noktası, burada
yeniden yazılmaz). Tek fark: bu özelliklerde MAĞAZA DENEMESİ (period_type
TRIAL) premium SAYILMAZ. Deneme kullanıcısı videolarda yalnız açık üç videoyu,
Anne Sesi'nde yalnız 30 sn'lik önizlemeyi görür. Denemeyle birlikte admin
hediyesi ya da kurucu penceresi varsa onlar geçerlidir.

BETA_MODE açıkken herkes premium (source=beta) — bugünkü davranış değişmez.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from api.models import User

# Premium olmayanlara AÇIK eğitim videoları — başlıkla belirlenir (kategori
# değil). Başlık katalogda soru işaretiyle ya da devamıyla gelebilir
# ("… beslenme mesafesi nasıl olmalı?"): eşleşme normalize edilmiş başlığın
# bu adla BAŞLAMASIdır (kelime sınırında).
ACIK_VIDEOLAR = (
    "Uyku eğitimine giriş ve genel bilgilendirme",
    "Bebeğe uygun uyku planı nasıl hazırlanır?",
    "Uykuya geçmeden önce beslenme mesafesi",
)


def premium_mi(db: Session, user: User) -> bool:
    """Bu özellikler için premium mu? Deneme (TRIAL) premium sayılmaz."""
    from api.services import premium as premium_svc
    d = premium_svc.durum(db, user)
    if not d["premium"]:
        return False
    if d["source"] != "store" or (d.get("period_type") or "").upper() != "TRIAL":
        return True
    # Deneme aboneliği öne geçti; altında hediye/kurucu hakkı var mı?
    if premium_svc.aktif_hak(db, user) is not None:
        return True
    pencere = premium_svc.kurucu_penceresi()
    if d.get("kurucu_uye") and pencere:
        from datetime import datetime, timezone
        return pencere[0] <= datetime.now(timezone.utc) < pencere[1]
    return False


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFC", s or "").replace("İ", "i").replace("I", "ı")
    s = re.sub(r"[^\w\s]", " ", s.casefold())
    return " ".join(s.split())


_ACIK = tuple(_norm(a) for a in ACIK_VIDEOLAR)


def video_acik_mi(baslik: str | None) -> bool:
    b = _norm(baslik)
    return any(b == a or b.startswith(a + " ") for a in _ACIK)


def video_kilitli_mi(baslik: str | None, premium: bool) -> bool:
    return not premium and not video_acik_mi(baslik)
