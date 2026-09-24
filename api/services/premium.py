"""
Premium kararı + RevenueCat olay işleme (B4).

TEK KARAR NOKTASI: `durum(db, user)`. deps.premium_karari, require_premium,
GET /subscriptions/status ve kilit alanları (locked / premium_required) hepsi
buradan okur. Öncelik sırası:
    1. beta     — BETA_MODE açık: herkes premium (bugünkü prod davranışı)
    2. store    — aktif mağaza aboneliği (RevenueCat; deneme dahil)
    3. manual   — admin hediyesi (premium_haklari)
    4. kurucu   — lansmandan önce kayıt olan, lansmandan sonraki 30 gün

REVENUECAT: webhook `app_user_id` = bizim users.id. Olay idempotent işlenir
(revenuecat_olaylari.id = event.id). Durum makinesi:
    INITIAL_PURCHASE / RENEWAL / UNCANCELLATION → active, will_renew=True
    NON_RENEWING_PURCHASE → active, bitiş = satın alma + ürün günü (45)
    CANCELLATION  → will_renew=False (erişim expires_at'e kadar SÜRER)
    BILLING_ISSUE → billing_issue (grace süresi varsa o ana kadar erişim)
    EXPIRATION    → expired
    PRODUCT_CHANGE→ sonraki_urun (erişim değişmez; yeni ürün RENEWAL ile gelir)
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from api.config import (KURUCU_PREMIUM_GUN, PREMIUM_ENTITLEMENT, URUNLER,
                        get_settings)
from api.models import PremiumHak, RevenueCatOlayi, Subscription, User

logger = logging.getLogger("tavsan.premium")

TR = timezone(timedelta(hours=3))
ERISIMLI_DURUMLAR = ("active", "billing_issue")
ISLENEN_OLAYLAR = ("INITIAL_PURCHASE", "RENEWAL", "CANCELLATION", "EXPIRATION",
                   "BILLING_ISSUE", "PRODUCT_CHANGE", "NON_RENEWING_PURCHASE",
                   "UNCANCELLATION")
MAGAZA_PLATFORM = {"APP_STORE": "ios", "MAC_APP_STORE": "ios",
                   "PLAY_STORE": "android", "AMAZON": "android"}


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Karar
# ---------------------------------------------------------------------------
def aktif_abonelik(db: Session, user: User, simdi: datetime | None = None
                   ) -> Subscription | None:
    """Erişim veren mağaza aboneliği; birden çoksa en geç biten (süresiz önce)."""
    simdi = simdi or _simdi()
    adaylar = [s for s in db.query(Subscription)
               .filter(Subscription.user_id == user.id,
                       Subscription.status.in_(ERISIMLI_DURUMLAR)).all()
               if s.expires_at is None or _utc(s.expires_at) > simdi]
    if not adaylar:
        return None
    return max(adaylar, key=lambda s: (s.expires_at is None,
                                       _utc(s.expires_at) or simdi))


def aktif_hak(db: Session, user: User, simdi: datetime | None = None
              ) -> PremiumHak | None:
    simdi = simdi or _simdi()
    haklar = [h for h in db.query(PremiumHak).filter(PremiumHak.user_id == user.id).all()
              if _utc(h.baslangic) <= simdi < _utc(h.bitis)]
    return max(haklar, key=lambda h: _utc(h.bitis)) if haklar else None


def lansman_ani() -> datetime | None:
    """Lansman günü 00:00 (Türkiye saati), UTC olarak."""
    lt = get_settings().lansman_tarihi
    if lt is None:
        return None
    return datetime(lt.year, lt.month, lt.day, tzinfo=TR).astimezone(timezone.utc)


# Kurucu üyeliğe HİÇ girmeyen hesaplar: test alanları, resmi hesap (.invalid),
# admin/moderatörler ve env KURUCU_HARIC (virgülle e-posta) ile elle çıkarılanlar.
KURUCU_TEST_ALANLARI = ("@example.com", "@tavsanduman.com", "@tavsansmoke.com",
                        ".invalid")


def kurucu_haric_epostalar() -> set[str]:
    return {e.strip().lower() for e in (os.getenv("KURUCU_HARIC") or "").split(",")
            if "@" in e}


def kurucu_disinda_mi(db: Session | None, user: User) -> bool:
    eposta = (user.email or "").lower()
    if eposta.endswith(KURUCU_TEST_ALANLARI) or eposta in kurucu_haric_epostalar():
        return True
    if db is not None:
        from api.models import CommunityProfile
        prof = (db.query(CommunityProfile)
                .filter(CommunityProfile.user_id == user.id).first())
        if prof is not None and prof.is_moderator:
            return True
    return False


def kurucu_uye_mi(user: User, db: Session | None = None,
                  kesim: datetime | None = None) -> bool:
    """Lansmandan ÖNCE kayıt olmuş, hariç tutulmamış hesap mı? LANSMAN_TARIHI
    yoksa kimse değil (`kesim` verilirse o an esas alınır — liste önizlemesi)."""
    an = kesim or lansman_ani()
    if an is None or user.created_at is None or _utc(user.created_at) >= an:
        return False
    return not kurucu_disinda_mi(db, user)


def kurucu_listesi(db: Session, kesim: datetime | None = None) -> list[dict]:
    """Kurucu üyeler (ya da `kesim` ile "bugün lansman olsa" önizlemesi).
    E-posta MASKELİ döner; sıralama kayıt tarihine göre."""
    from sqlalchemy import func
    from api.models import Baby, ChatMessage, SleepLog
    an = kesim or lansman_ani() or _simdi()
    out = []
    for u in db.query(User).order_by(User.created_at).all():
        if not kurucu_uye_mi(u, db, kesim=an):
            continue
        son = [t for t in (
            db.query(func.max(SleepLog.created_at)).filter(SleepLog.user_id == u.id).scalar(),
            db.query(func.max(ChatMessage.created_at)).filter(ChatMessage.user_id == u.id).scalar(),
            u.app_version_seen_at, u.updated_at) if t is not None]
        out.append({"user_id": str(u.id), "eposta": maskele(u.email),
                    "kayit": _utc(u.created_at).isoformat(),
                    "bebek": db.query(Baby).filter(Baby.user_id == u.id).count(),
                    "son_etkinlik": max(_utc(t) for t in son).isoformat() if son else None})
    return out


def maskele(eposta: str) -> str:
    ad, _, alan = (eposta or "").partition("@")
    return f"{ad[:2]}***@{alan}" if alan else "***"


def kurucu_penceresi() -> tuple[datetime, datetime] | None:
    an = lansman_ani()
    return None if an is None else (an, an + timedelta(days=KURUCU_PREMIUM_GUN))


def durum(db: Session, user: User, simdi: datetime | None = None) -> dict:
    """{premium, source, expires_at, product_id, will_renew, period_type,
    kurucu_uye}. source: beta | store | manual | kurucu | none."""
    simdi = simdi or _simdi()
    abonelik = aktif_abonelik(db, user, simdi)
    kurucu = kurucu_uye_mi(user, db)
    sonuc = {"premium": False, "source": "none", "expires_at": None,
             "product_id": None, "will_renew": None, "period_type": None,
             "kurucu_uye": kurucu}
    if abonelik is not None:                       # beta'da da ayrıntı görünsün
        sonuc.update(expires_at=_utc(abonelik.expires_at),
                     product_id=abonelik.product_id,
                     will_renew=abonelik.will_renew,
                     period_type=abonelik.period_type)
    if get_settings().beta_mode:
        sonuc.update(premium=True, source="beta")
        return sonuc
    if abonelik is not None:
        sonuc.update(premium=True, source="store")
        return sonuc
    hak = aktif_hak(db, user, simdi)
    if hak is not None:
        sonuc.update(premium=True, source="manual", expires_at=_utc(hak.bitis))
        return sonuc
    pencere = kurucu_penceresi()
    if kurucu and pencere and pencere[0] <= simdi < pencere[1]:
        sonuc.update(premium=True, source="kurucu", expires_at=pencere[1])
    return sonuc


# ---------------------------------------------------------------------------
# Manuel hak
# ---------------------------------------------------------------------------
def hak_ver(db: Session, user: User, gun: int, veren: User | None,
            aciklama: str | None = None) -> PremiumHak:
    """`gun` gün premium. Süren bir hak varsa ONUN bitişinden uzatılır (hediye
    üst üste binmez, eklenir)."""
    simdi = _simdi()
    mevcut = aktif_hak(db, user, simdi)
    bas = _utc(mevcut.bitis) if mevcut is not None else simdi
    hak = PremiumHak(user_id=user.id, baslangic=bas,
                     bitis=bas + timedelta(days=int(gun)),
                     veren_user_id=veren.id if veren else None,
                     aciklama=aciklama, created_at=simdi)
    db.add(hak)
    db.commit()
    db.refresh(hak)
    logger.info("Manuel premium: user=%s gun=%d bitis=%s veren=%s",
                user.id, gun, hak.bitis.isoformat(), getattr(veren, "id", None))
    return hak


# ---------------------------------------------------------------------------
# RevenueCat webhook
# ---------------------------------------------------------------------------
def _ms(v) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def kullanici_bul(db: Session, olay: dict) -> User | None:
    """app_user_id bizim users.id'dir. Anonim kimlikle ($RCAnonymousID) gelen
    olayda takma adlar (aliases / original_app_user_id) da denenir."""
    adaylar = [olay.get("app_user_id"), olay.get("original_app_user_id"),
               *(olay.get("aliases") or []), *(olay.get("transferred_to") or [])]
    for aday in adaylar:
        try:
            uid = uuid.UUID(str(aday))
        except (TypeError, ValueError):
            continue
        u = db.get(User, uid)
        if u is not None:
            return u
    return None


def _abonelik_satiri(db: Session, user: User, urun: str, store: str | None
                     ) -> Subscription:
    s = (db.query(Subscription)
         .filter(Subscription.user_id == user.id, Subscription.product_id == urun)
         .one_or_none())
    if s is None:
        s = Subscription(user_id=user.id, product_id=urun,
                         platform=MAGAZA_PLATFORM.get(store or "", "ios"),
                         status="active")
        db.add(s)
    if store:
        s.store = store
        s.platform = MAGAZA_PLATFORM.get(store, s.platform)
    return s


def olay_isle(db: Session, govde: dict) -> tuple[str, str]:
    """(durum, açıklama) — durum: islendi | tekrar | atlandi | kullanici_yok.

    Webhook uç noktası her durumda 200 döner (yetki hariç): RevenueCat 200
    görmezse aynı olayı saatlerce yeniden dener; bizim tarafta çözülemeyen
    bir olayı (bilinmeyen kullanıcı) tekrar denemek düzeltmez."""
    olay = (govde or {}).get("event") or {}
    olay_id = str(olay.get("id") or "").strip()
    tur = str(olay.get("type") or "").strip()
    if not olay_id or not tur:
        return "atlandi", "event.id ya da event.type yok"
    if db.get(RevenueCatOlayi, olay_id) is not None:
        return "tekrar", "bu olay daha önce işlendi"

    user = kullanici_bul(db, olay)
    sonuc = ("atlandi", f"{tur} işlenmiyor")
    if user is None:
        sonuc = ("kullanici_yok", f"app_user_id eşleşmedi: {olay.get('app_user_id')}")
    elif tur in ISLENEN_OLAYLAR:
        sonuc = _uygula(db, user, tur, olay)

    db.add(RevenueCatOlayi(id=olay_id, type=tur,
                           app_user_id=str(olay.get("app_user_id") or "")[:200] or None,
                           user_id=user.id if user else None,
                           sonuc=sonuc[1][:200], raw=govde, received_at=_simdi()))
    db.commit()
    logger.info("RevenueCat olayı: id=%s tür=%s user=%s → %s",
                olay_id, tur, getattr(user, "id", None), sonuc[1])
    return sonuc


def _uygula(db: Session, user: User, tur: str, olay: dict) -> tuple[str, str]:
    urun = str(olay.get("product_id") or "").strip()
    if not urun:
        return "atlandi", "product_id yok"
    ent = olay.get("entitlement_ids") or ([olay["entitlement_id"]]
                                          if olay.get("entitlement_id") else None)
    if ent is not None and PREMIUM_ENTITLEMENT not in ent and tur != "EXPIRATION":
        return "atlandi", f"{PREMIUM_ENTITLEMENT} entitlement'ı yok: {ent}"

    s = _abonelik_satiri(db, user, urun, olay.get("store"))
    s.raw_event = {"event": olay}
    s.environment = olay.get("environment") or s.environment
    if olay.get("period_type"):
        s.period_type = olay["period_type"]
    bitis = _ms(olay.get("expiration_at_ms"))
    satin = _ms(olay.get("purchased_at_ms"))

    if tur in ("INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION"):
        s.status, s.will_renew = "active", True
        s.purchased_at = satin or s.purchased_at
        s.expires_at = bitis or s.expires_at
        s.sonraki_urun = None
    elif tur == "NON_RENEWING_PURCHASE":
        gun = int((URUNLER.get(urun) or {}).get("gun") or 0)
        s.status, s.will_renew = "active", False
        s.purchased_at = satin or _simdi()
        s.expires_at = bitis or (s.purchased_at + timedelta(days=gun) if gun else None)
        s.period_type = s.period_type or "NORMAL"
    elif tur == "CANCELLATION":
        s.will_renew = False
        s.expires_at = bitis or s.expires_at
    elif tur == "BILLING_ISSUE":
        s.status = "billing_issue"
        grace = _ms(olay.get("grace_period_expiration_at_ms"))
        s.expires_at = grace or bitis or s.expires_at
    elif tur == "EXPIRATION":
        s.status, s.will_renew = "expired", False
        s.expires_at = bitis or s.expires_at or _simdi()
    elif tur == "PRODUCT_CHANGE":
        s.sonraki_urun = olay.get("new_product_id")
    db.flush()
    return "islendi", f"{tur}: {urun} → {s.status}"


# ---------------------------------------------------------------------------
# RevenueCat REST (webhook kaçarsa)
# ---------------------------------------------------------------------------
RC_URL = "https://api.revenuecat.com/v1/subscribers/{}"


def _iso(v) -> datetime | None:
    if not v:
        return None
    try:
        return _utc(datetime.fromisoformat(str(v).replace("Z", "+00:00")))
    except ValueError:
        return None


def musteri_cek(user_id) -> dict:
    """RevenueCat'ten ham müşteri kaydı. Hata → RuntimeError (Türkçe)."""
    import requests
    anahtar = get_settings().revenuecat_api_key
    if not anahtar:
        raise RuntimeError("Mağaza bağlantısı yapılandırılmamış")
    r = requests.get(RC_URL.format(user_id), timeout=15,
                     headers={"Authorization": f"Bearer {anahtar}",
                              "Content-Type": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"Mağaza bilgisi alınamadı (HTTP {r.status_code})")
    return r.json()


def musteriden_guncelle(db: Session, user: User, veri: dict) -> list[str]:
    """RevenueCat `subscriber` gövdesiyle tabloyu güncelle. Dönen: dokunulan ürünler."""
    abone = (veri or {}).get("subscriber") or {}
    simdi = _simdi()
    dokunulan = []
    for urun, a in (abone.get("subscriptions") or {}).items():
        s = _abonelik_satiri(db, user, urun, a.get("store"))
        s.expires_at = _iso(a.get("expires_date"))
        s.purchased_at = _iso(a.get("purchase_date")) or s.purchased_at
        s.period_type = a.get("period_type") or s.period_type
        s.environment = "SANDBOX" if a.get("is_sandbox") else "PRODUCTION"
        s.will_renew = a.get("unsubscribe_detected_at") is None
        if s.expires_at is not None and s.expires_at <= simdi:
            s.status = "expired"
        elif a.get("billing_issues_detected_at"):
            s.status = "billing_issue"
        else:
            s.status = "active"
        dokunulan.append(urun)
    for urun, alimlar in (abone.get("non_subscriptions") or {}).items():
        if not alimlar:
            continue
        son = max(alimlar, key=lambda x: x.get("purchase_date") or "")
        s = _abonelik_satiri(db, user, urun, son.get("store"))
        s.purchased_at = _iso(son.get("purchase_date"))
        gun = int((URUNLER.get(urun) or {}).get("gun") or 0)
        s.expires_at = s.purchased_at + timedelta(days=gun) if (s.purchased_at and gun) else None
        s.will_renew = False
        s.status = "active" if (s.expires_at is None or s.expires_at > simdi) else "expired"
        dokunulan.append(urun)
    db.commit()
    return dokunulan
