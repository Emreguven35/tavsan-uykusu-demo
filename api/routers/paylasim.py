"""
paylasim router — /api/v1/paylasim/* (2026-11-02)

Paylaşılabilir kartların verisi. Hesap api/services/paylasim.py'de; burası
sahiplik, sorgu penceresi ve bebek/eğitim alanları.

  GET /paylasim/haftalik[?baby_id=…][&bitis=YYYY-MM-DD]   (bitis varsayılan: dün)
  GET /paylasim/mezuniyet[?baby_id=…]                     (mezun kararı yoksa 404)

baby_id OPSİYONEL: verilmezse aktif bebek (deps.aktif_bebek — en yeni bebek,
eğitim videolarıyla aynı kural); bebeği olmayan kullanıcıda 404.
Kimlik doğrulamalı; başkasının bebeği → 404 (varlık sızdırılmaz).
"""
import logging
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import aktif_bebek, get_current_user
from api.models import Baby, EgitimDegerlendirme, SleepLog, User
from api.schemas.paylasim import HaftalikKartResp, MezuniyetKartResp
from api.services import egitim_degerlendirme as ed
from api.services import paylasim
from api.zaman import bugun_tr, tr_gun_araligi

logger = logging.getLogger("tavsan.paylasim")
router = APIRouter(prefix="/paylasim", tags=["paylasim"])


def _bant(baby: Baby) -> dict | None:
    """Kısa uyku eşiği yaşa bağlı; çözülemezse None (motor varsayılana düşer)."""
    from engine import yas_bantlari
    from engine.parameter_engine import hesapla_yas_ay
    if baby.birth_date is None:
        return None
    try:
        ay = hesapla_yas_ay(baby.birth_date.isoformat(),
                            int(baby.dogum_haftasi or 40))["duzeltilmis_ay"]
        return yas_bantlari.yas_bandi_getir(ay)
    except Exception:
        logger.exception("Bant çözülemedi (baby=%s)", baby.id)
        return None


def _kayitlar(db: Session, baby: Baby, ilk: date, son: date) -> list[SleepLog]:
    return (db.query(SleepLog)
            .filter(SleepLog.baby_id == baby.id,
                    SleepLog.started_at >= tr_gun_araligi(ilk)[0],
                    SleepLog.started_at < tr_gun_araligi(son)[1])
            .all())


def _bebek(db: Session, user: User, baby_id: uuid.UUID | None) -> Baby:
    baby = aktif_bebek(baby_id, db, user)
    if baby is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bebek bulunamadı")
    return baby


def _egitim_gunu(db: Session, baby: Baby, bugun: date) -> int | None:
    """Yalnız eğitim durumu "devam" iken gerçek gün (1..program uzunluğu)."""
    if baby.training_started_at is None:
        return None
    from api.services.plan_service import TYPE_EGITIM, tip_turet
    icerik = ed._son_plan_icerigi(db, baby)
    if not icerik or tip_turet(icerik) != TYPE_EGITIM:
        return None
    alanlar = ed.gun_alanlari(db, baby, bugun, ed.program_gun_sayisi(icerik))
    if alanlar["egitim_durumu"] != ed.D_DEVAM:
        return None
    return alanlar["egitim_gunu_gercek"]


@router.get("/haftalik", response_model=HaftalikKartResp)
def haftalik(baby_id: uuid.UUID | None = Query(default=None),
             bitis: date | None = Query(default=None,
                                        description="Haftanın son günü (dahil). Varsayılan: dün."),
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    baby = _bebek(db, user, baby_id)
    bugun = bugun_tr()
    bitis = bitis or (bugun - timedelta(days=1))
    if bitis > bugun:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="bitis bugünden ileri olamaz")
    rows = _kayitlar(db, baby, paylasim.haftalik_sorgu_baslangici(bitis), bitis)
    govde = paylasim.haftalik_hesapla(rows, bitis, _bant(baby))
    return HaftalikKartResp(bebek_adi=baby.name, egitim_gunu=_egitim_gunu(db, baby, bugun),
                            **govde)


@router.get("/mezuniyet", response_model=MezuniyetKartResp)
def mezuniyet(baby_id: uuid.UUID | None = Query(default=None),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    baby = _bebek(db, user, baby_id)
    # training_completed_at TEK BAŞINA yetmez (eski sürümler değerlendirmesiz
    # dolduruyordu): bu denemenin "mezun" değerlendirmesi gerekir.
    q = db.query(EgitimDegerlendirme).filter(EgitimDegerlendirme.baby_id == baby.id,
                                             EgitimDegerlendirme.sonuc == ed.MEZUN)
    if baby.training_started_at is not None:
        q = q.filter(EgitimDegerlendirme.egitim_baslangic == baby.training_started_at)
    satir = q.order_by(EgitimDegerlendirme.created_at.desc()).first()
    bas = (satir.egitim_baslangic or baby.training_started_at) if satir is not None else None
    if satir is None or bas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Tamamlanmış eğitim bulunamadı")
    rows = _kayitlar(db, baby, paylasim.mezuniyet_sorgu_baslangici(bas), satir.gun)
    govde = paylasim.mezuniyet_hesapla(rows, bas, satir.gun, baby.night_wakes, _bant(baby))
    return MezuniyetKartResp(bebek_adi=baby.name, tamamlanma_tarihi=satir.gun, **govde)
