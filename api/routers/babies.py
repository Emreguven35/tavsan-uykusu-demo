"""
babies router — /api/v1/babies

Hepsi auth korumalı ve user_id scoped: kullanıcı yalnız KENDİ bebeklerini görür/değiştirir.

Boy/kilo (2026-10, ilk açılış akışı): POST ve PATCH /babies isteğe bağlı
`boy_cm`/`kilo_kg` alır ve bebek_olcumleri'ne bir ölçüm yazar; yanıtlar
`son_olcum` taşır. Ölçüm geçmişi: POST/GET /babies/{id}/olcumler. Bu değerler
HİÇBİR HESABA girmez (plan motoru, eğitim planı, sohbet, denetim).
"""
import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import Baby, BebekOlcumu, User
from api.schemas.baby import (BabyCreate, BabyResp, BabyUpdate, OlcumIn, OlcumResp,
                              SonOlcum)
from api.services import plan_service
from api.zaman import bugun_tr

logger = logging.getLogger("tavsan.babies")
router = APIRouter(prefix="/babies", tags=["babies"])


def _dogum_tarihini_dogrula(dogum) -> None:
    hata = plan_service.dogum_tarihi_hatasi(dogum)
    if hata:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=hata)


def _egitim_baslangicini_suz(db: Session, baby: Baby) -> None:
    """Denetim B3 — eğitim programı yürümeyen bebekte (bekleme/yenidoğan planı,
    ya da plansız ve 5 aydan küçük) training_started_at YAZILMAZ.

    Mobil Eğitim sekmesini ilk açışta bu alanı HER bebek için gönderiyor; 3
    aylık bebekler denetimde "eğitimin 7. günü · aşama: Kapı" görünüyordu.
    İstek REDDEDİLMEZ (eski istemci hata ekranı göstermesin), alan sessizce
    boş bırakılır. Gerçek başlangıcı 5 ay geçişi sunucuda yazar
    (plan_service.egitim_gecisini_baslat)."""
    if baby.training_started_at is not None and not plan_service.egitim_aktif_mi(db, baby):
        logger.info("training_started_at yok sayıldı (eğitim aktif değil): baby=%s",
                    baby.id)
        baby.training_started_at = None


# --- Boy/kilo ----------------------------------------------------------------
# Aralıklar bilinçli olarak GENİŞ (prematüre bebek 24. haftadan kabul ediliyor).
BOY_ARALIK = (30.0, 120.0)
KILO_ARALIK = (0.5, 25.0)
BOY_MESAJ = "Boy 30–120 cm arasında olmalı."
KILO_MESAJ = "Kilo 0,5–25 kg arasında olmalı."
OLCUM_ALANLARI = ("boy_cm", "kilo_kg")


def _yuvarla(deger: float, basamak: str) -> float:
    return float(Decimal(str(deger)).quantize(Decimal(basamak), rounding=ROUND_HALF_UP))


def olcum_dogrula(boy: float | None, kilo: float | None
                  ) -> tuple[float | None, float | None, JSONResponse | None]:
    """Yuvarla (boy 1, kilo 2 ondalık — şemadaki NUMERIC ile aynı) ve aralığı
    denetle. Hata: 422 + Türkçe `detail` + hatalı `alan`. Genel 422 işleyicisi
    yalnız "gönderilen bilgiler geçersiz" diyor; anne neyin yanlış olduğunu
    görmeli."""
    def _hata(mesaj: str, alan: str) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"detail": mesaj, "alan": alan})
    if boy is not None:
        boy = _yuvarla(boy, "0.1") if boy == boy else None    # NaN → aralık dışı
        if boy is None or not BOY_ARALIK[0] <= boy <= BOY_ARALIK[1]:
            return None, None, _hata(BOY_MESAJ, "boy_cm")
    if kilo is not None:
        kilo = _yuvarla(kilo, "0.01") if kilo == kilo else None
        if kilo is None or not KILO_ARALIK[0] <= kilo <= KILO_ARALIK[1]:
            return None, None, _hata(KILO_MESAJ, "kilo_kg")
    return boy, kilo, None


def _olcum_yaz(db: Session, baby: Baby, boy: float | None, kilo: float | None,
               kaynak: str, tarih=None) -> BebekOlcumu | None:
    """Ölçüm satırı ekle. Aynı bebek + aynı gün + aynı değerler zaten varsa YENİ
    SATIR AÇILMAZ, mevcut döner (onboarding yeniden gönderimi kopya üretmesin)."""
    if boy is None and kilo is None:
        return None
    tarih = tarih or bugun_tr()
    mevcut = (db.query(BebekOlcumu)
              .filter(BebekOlcumu.baby_id == baby.id,
                      BebekOlcumu.olcum_tarihi == tarih,
                      # Decimal: Postgres'te numeric = numeric KESİN eşitlik
                      # (float parametre double'a çevrilip karşılaştırılırdı).
                      BebekOlcumu.boy_cm.is_(None) if boy is None
                      else BebekOlcumu.boy_cm == Decimal(str(boy)),
                      BebekOlcumu.kilo_kg.is_(None) if kilo is None
                      else BebekOlcumu.kilo_kg == Decimal(str(kilo)))
              .first())
    if mevcut is not None:
        return mevcut
    satir = BebekOlcumu(baby_id=baby.id, olcum_tarihi=tarih, boy_cm=boy,
                        kilo_kg=kilo, kaynak=kaynak)
    db.add(satir)
    return satir


def _son_olcumler(db: Session, baby_ids: list) -> dict:
    """{baby_id: SonOlcum} — TEK sorgu (liste ucu bebek başına sorgu atmasın)."""
    if not baby_ids:
        return {}
    son: dict = {}
    for o in (db.query(BebekOlcumu).filter(BebekOlcumu.baby_id.in_(baby_ids))
              .order_by(BebekOlcumu.olcum_tarihi, BebekOlcumu.created_at,
                        BebekOlcumu.id)):
        son[o.baby_id] = o                              # sıralı → sonuncusu kalır
    return {bid: SonOlcum(olcum_tarihi=o.olcum_tarihi, boy_cm=o.boy_cm,
                          kilo_kg=o.kilo_kg) for bid, o in son.items()}


def _yanit(db: Session, babies: list[Baby]) -> list[BabyResp]:
    son = _son_olcumler(db, [b.id for b in babies])
    return [BabyResp.model_validate(b).model_copy(update={"son_olcum": son.get(b.id)})
            for b in babies]


@router.post("", response_model=BabyResp, status_code=status.HTTP_201_CREATED)
def create_baby(req: BabyCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _dogum_tarihini_dogrula(req.birth_date)
    boy, kilo, hata = olcum_dogrula(req.boy_cm, req.kilo_kg)
    if hata is not None:
        return hata                              # bebek de oluşturulmaz
    baby = Baby(user_id=user.id, **req.model_dump(exclude=set(OLCUM_ALANLARI)))
    db.add(baby)
    db.flush()
    _egitim_baslangicini_suz(db, baby)
    _olcum_yaz(db, baby, boy, kilo, "onboarding")
    db.commit()
    db.refresh(baby)
    return _yanit(db, [baby])[0]


@router.get("", response_model=list[BabyResp])
def list_babies(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    return _yanit(db, (db.query(Baby).filter(Baby.user_id == user.id)
                       .order_by(Baby.created_at).all()))


@router.patch("/{baby_id}", response_model=BabyResp)
def update_baby(baby_id: uuid.UUID, req: BabyUpdate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    baby = get_owned_baby(baby_id, db, user)
    degisen = req.model_dump(exclude_unset=True)
    if "birth_date" in degisen:
        _dogum_tarihini_dogrula(degisen["birth_date"])
    boy, kilo, hata = olcum_dogrula(degisen.pop("boy_cm", None),
                                    degisen.pop("kilo_kg", None))
    if hata is not None:
        return hata
    # Yalnız gönderilen alanları güncelle (exclude_unset).
    for field, value in degisen.items():
        setattr(baby, field, value)
    if degisen.get("training_started_at") is not None:
        _egitim_baslangicini_suz(db, baby)
    _olcum_yaz(db, baby, boy, kilo, "profil")
    db.commit()
    db.refresh(baby)
    return _yanit(db, [baby])[0]


@router.post("/{baby_id}/olcumler", response_model=OlcumResp,
             status_code=status.HTTP_201_CREATED)
def olcum_ekle(baby_id: uuid.UUID, req: OlcumIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Profilden ölçüm ekle. Tarih verilmezse bugün (TR). Aynı gün aynı değerler
    zaten varsa mevcut ölçüm döner (yeni satır yok)."""
    baby = get_owned_baby(baby_id, db, user)
    if req.boy_cm is None and req.kilo_kg is None:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"detail": "Boy ya da kilo girilmeli.", "alan": None})
    boy, kilo, hata = olcum_dogrula(req.boy_cm, req.kilo_kg)
    if hata is not None:
        return hata
    tarih = req.olcum_tarihi or bugun_tr()
    if tarih > bugun_tr():
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"detail": "Ölçüm tarihi bugünden sonra olamaz.",
                                     "alan": "olcum_tarihi"})
    if baby.birth_date is not None and tarih < baby.birth_date:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"detail": "Ölçüm tarihi doğum tarihinden önce olamaz.",
                                     "alan": "olcum_tarihi"})
    satir = _olcum_yaz(db, baby, boy, kilo, "profil", tarih)
    db.commit()
    db.refresh(satir)
    return satir


@router.get("/{baby_id}/olcumler", response_model=list[OlcumResp])
def olcumleri_listele(baby_id: uuid.UUID, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Ölçüm geçmişi, yeniden eskiye."""
    baby = get_owned_baby(baby_id, db, user)
    return (db.query(BebekOlcumu).filter(BebekOlcumu.baby_id == baby.id)
            .order_by(BebekOlcumu.olcum_tarihi.desc(), BebekOlcumu.created_at.desc(),
                      BebekOlcumu.id.desc())
            .all())
