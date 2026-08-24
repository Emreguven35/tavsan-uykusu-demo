"""
admin router — /api/v1/admin/*

GET /usage: maliyet raporu. YALNIZ moderatör (community_profiles.is_moderator).
Topluluk moderatör kapısıyla AYNI kontrol kullanılır — ikinci bir yetki kavramı
uydurmak yerine mevcut olanı yeniden kullanmak, yetkinin tek yerden yönetilmesini
sağlar (scripts/grant_moderator.py).

KVKK: rapor toplamlar üretir; hiçbir uçta mesaj/masal/bebek içeriği dönmez.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Integer, func
from sqlalchemy.orm import Session

from api.config import GUNLUK_MALIYET_ESIGI_USD
from api.db import get_db
from api.deps import get_current_user
from api.models import ApiUsage, ChatMessage, CommunityProfile, User
from api.schemas.admin import (
    CacheOzet, CevapCacheOzet, GunItem, KirilimItem, PromptCacheOzet, UsageResp,
)
from api.services import usage as usage_svc

logger = logging.getLogger("tavsan.admin")
router = APIRouter(prefix="/admin", tags=["admin"])

VARSAYILAN_GUN = 30           # from/to verilmezse son 30 gün
MAX_ARALIK_GUN = 366          # tek istekte en fazla 1 yıl (tarama sınırı)


def _require_moderator(db: Session, user: User) -> None:
    prof = (db.query(CommunityProfile)
            .filter(CommunityProfile.user_id == user.id).first())
    if prof is None or not prof.is_moderator:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Moderatör yetkisi gerekli")


def _gun_sinirlari(gun: date, son: bool = False) -> datetime:
    t = datetime.max.time() if son else datetime.min.time()
    return datetime.combine(gun, t, tzinfo=timezone.utc)


def _kirilim(db: Session, kolon, bas, bit) -> list[KirilimItem]:
    rows = (db.query(kolon,
                     func.coalesce(func.sum(ApiUsage.estimated_cost_usd), 0.0),
                     func.count(ApiUsage.id))
            .filter(ApiUsage.created_at >= bas, ApiUsage.created_at <= bit)
            .group_by(kolon).all())
    return sorted((KirilimItem(ad=r[0] or "-", usd=round(float(r[1] or 0), 6),
                               cagri=int(r[2])) for r in rows),
                  key=lambda x: x.usd, reverse=True)


def _gunluk(db: Session, bas, bit) -> list[GunItem]:
    """Gün bazında seri.

    Gruplama Python tarafında yapılır: SQLite ile Postgres'in tarih kesme
    fonksiyonları farklı (`date()` vs `date_trunc`) ve testler SQLite'ta koşuyor.
    Tek bir SQL lehçesine bağlanmak yerine satırları çekip burada toplarız;
    aralık en fazla 1 yıl olduğu için hacim küçük kalır."""
    rows = (db.query(ApiUsage.created_at, ApiUsage.estimated_cost_usd)
            .filter(ApiUsage.created_at >= bas, ApiUsage.created_at <= bit).all())
    kova: dict[date, list] = {}
    for olusma, tutar in rows:
        if olusma is None:
            continue
        g = olusma.date()
        h = kova.setdefault(g, [0.0, 0])
        h[0] += float(tutar or 0.0)
        h[1] += 1
    return [GunItem(gun=g, usd=round(v[0], 6), cagri=v[1])
            for g, v in sorted(kova.items())]


def _prompt_cache(db: Session, bas, bit) -> PromptCacheOzet:
    rows = (db.query(ApiUsage.model,
                     func.coalesce(func.sum(ApiUsage.cached_tokens), 0),
                     func.coalesce(func.sum(ApiUsage.input_tokens), 0),
                     func.coalesce(func.sum(ApiUsage.cache_write_tokens), 0))
            .filter(ApiUsage.created_at >= bas, ApiUsage.created_at <= bit,
                    ApiUsage.service == usage_svc.SERVIS_ANTHROPIC)
            .group_by(ApiUsage.model).all())
    okunan = tam = yazilan = 0
    kazanc = 0.0
    for model, c, i, w in rows:
        okunan += int(c or 0)
        tam += int(i or 0)
        yazilan += int(w or 0)
        kazanc += usage_svc.cache_kazanci(model, int(c or 0))
    payda = okunan + tam
    return PromptCacheOzet(okunan_token=okunan, tam_fiyatli_token=tam,
                           yazilan_token=yazilan,
                           oran=round(okunan / payda, 4) if payda else 0.0,
                           kazanc_usd=round(kazanc, 6))


def _cevap_cache(db: Session, bas, bit) -> CevapCacheOzet:
    """Uygulama cevap cache'i — chat_messages.cached üzerinden.

    Cache HIT'te dış servis çağrısı OLMADIĞI için api_usage'da satır yoktur;
    isabet oranını oradan okumak imkânsız. Kazanç, aynı dönemde LLM'e giden
    sohbetlerin ORTALAMA maliyetiyle çarpılarak tahmin edilir — bu yüzden alan
    adı 'tahmini'."""
    toplam, hit = (db.query(
        func.count(ChatMessage.id),
        func.coalesce(func.sum(func.cast(ChatMessage.cached, Integer)), 0))
        .filter(ChatMessage.role == "assistant",
                ChatMessage.created_at >= bas, ChatMessage.created_at <= bit)
        .one())
    toplam, hit = int(toplam or 0), int(hit or 0)

    ort = (db.query(func.coalesce(func.avg(ApiUsage.estimated_cost_usd), 0.0))
           .filter(ApiUsage.created_at >= bas, ApiUsage.created_at <= bit,
                   ApiUsage.operation == usage_svc.OP_CHAT).scalar()) or 0.0
    return CevapCacheOzet(toplam=toplam, hit=hit,
                          oran=round(hit / toplam, 4) if toplam else 0.0,
                          tahmini_kazanc_usd=round(hit * float(ort), 6))


@router.get("/usage", response_model=UsageResp)
def usage_raporu(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user),
                 baslangic: date | None = Query(default=None, alias="from"),
                 bitis: date | None = Query(default=None, alias="to"),
                 group_by: str = Query(default="day",
                                       pattern="^(day|operation|service)$")):
    _require_moderator(db, user)

    bugun = datetime.now(timezone.utc).date()
    bitis = bitis or bugun
    baslangic = baslangic or (bitis - timedelta(days=VARSAYILAN_GUN - 1))
    if baslangic > bitis:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="from, to'dan sonra olamaz")
    if (bitis - baslangic).days > MAX_ARALIK_GUN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Aralık en fazla {MAX_ARALIK_GUN} gün olabilir")

    bas, bit = _gun_sinirlari(baslangic), _gun_sinirlari(bitis, son=True)

    toplam, adet = (db.query(
        func.coalesce(func.sum(ApiUsage.estimated_cost_usd), 0.0),
        func.count(ApiUsage.id))
        .filter(ApiUsage.created_at >= bas, ApiUsage.created_at <= bit).one())

    servis = _kirilim(db, ApiUsage.service, bas, bit)
    operasyon = _kirilim(db, ApiUsage.operation, bas, bit)
    gunluk = _gunluk(db, bas, bit)

    gruplar = {"day": gunluk, "service": servis, "operation": operasyon}[group_by]
    asanlar = [g.gun for g in gunluk if g.usd >= GUNLUK_MALIYET_ESIGI_USD]

    return UsageResp(
        baslangic=baslangic, bitis=bitis, group_by=group_by,
        toplam_usd=round(float(toplam or 0.0), 6), cagri_sayisi=int(adet or 0),
        gruplar=gruplar, servis=servis, operasyon=operasyon, gunluk=gunluk,
        cache=CacheOzet(prompt_cache=_prompt_cache(db, bas, bit),
                        cevap_cache=_cevap_cache(db, bas, bit)),
        gunluk_esik_usd=GUNLUK_MALIYET_ESIGI_USD, esigi_asan_gunler=asanlar,
    )
