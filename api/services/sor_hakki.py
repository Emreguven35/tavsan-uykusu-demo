"""
Sor günlük hakkı (premium abonelik, 2026-10-13).

Kademe bazlı: premium sınırsız, deneme günde SOR_GUNLUK_DENEME, ücretsiz 0
(ERISIM_KURALLARI["sor"] = "deneme" → ücretsizde Sor kapalı).

YARIŞ: hak model çağrısından ÖNCE alınır — `sor_sayaclari` satırında koşullu
atomik artırma (UPDATE … SET sayi = sayi + 1 WHERE sayi < limit). Aynı anda
gelen iki istek aynı son hakkı alamaz: veritabanı satırı kilitler, ikinci
UPDATE 0 satır döner. Model çağrısı hata verirse `iade` hakkı geri verir.
Sayaç Türkiye gününe göredir (api.zaman.bugun_tr).
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.config import SOR_GUNLUK_DENEME, SOR_GUNLUK_UCRETSIZ
from api.models import SorSayac, User
from api.services import erisim


def gunluk_limit(kademe: str) -> int | None:
    """Kademenin günlük Sor hakkı. None → sınırsız (premium)."""
    k = erisim.kademe_of(kademe)
    if k == erisim.PREMIUM:
        return None
    if erisim.kilitli_mi("sor", k):
        return SOR_GUNLUK_UCRETSIZ
    return SOR_GUNLUK_DENEME


def kullanilan(db: Session, user: User) -> int:
    from api.zaman import bugun_tr
    satir = (db.query(SorSayac.sayi)
             .filter(SorSayac.user_id == user.id, SorSayac.gun == bugun_tr()).scalar())
    return int(satir or 0)


def kalan(db: Session, user: User, kademe: str) -> int | None:
    """Bugün kalan hak (premium → None)."""
    limit = gunluk_limit(kademe)
    if limit is None:
        return None
    return max(0, limit - kullanilan(db, user))


def _satir_hazirla(db: Session, user: User, gun) -> None:
    """(kullanıcı, gün) satırı yoksa oluştur. Eşzamanlı oluşturmada benzersizlik
    ihlali olur — satır zaten var demektir, yutulur."""
    if (db.query(SorSayac.id)
            .filter(SorSayac.user_id == user.id, SorSayac.gun == gun).first()) is not None:
        return
    try:
        db.add(SorSayac(user_id=user.id, gun=gun, sayi=0))
        db.commit()
    except IntegrityError:
        db.rollback()


def hak_al(db: Session, user: User, limit: int):
    """Bugünün sayacını `limit`i aşmadan bir artır (commit'li). Hak alındıysa
    sayacın günü (iade aynı güne yapılsın — gece yarısı sınırı), yoksa None."""
    from api.zaman import bugun_tr
    gun = bugun_tr()
    _satir_hazirla(db, user, gun)
    n = (db.query(SorSayac)
         .filter(SorSayac.user_id == user.id, SorSayac.gun == gun,
                 SorSayac.sayi < limit)
         .update({SorSayac.sayi: SorSayac.sayi + 1}, synchronize_session=False))
    db.commit()
    return gun if n == 1 else None


def iade(db: Session, user: User, gun) -> None:
    """Model çağrısı başarısız → hakkı alındığı güne geri ver (sıfırın altına inmez)."""
    try:
        db.rollback()
        (db.query(SorSayac)
         .filter(SorSayac.user_id == user.id, SorSayac.gun == gun,
                 SorSayac.sayi > 0)
         .update({SorSayac.sayi: SorSayac.sayi - 1}, synchronize_session=False))
        db.commit()
    except Exception:
        db.rollback()
