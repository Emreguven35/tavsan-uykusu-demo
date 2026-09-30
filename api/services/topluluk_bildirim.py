"""
Topluluk bildirimleri (v2, 2026-10-01).

OLAYLAR
  • Konuna cevap        → "Sorunuza yeni bir cevap var"      + başlığın ilk 40 karakteri
  • Uzman cevabı        → "Tavşan Uykusu sorunuzu cevapladı"  + başlık
  • Cevabına cevap      → "Cevabınıza yanıt geldi"            + başlık
  • Faydalı (toplu)     → "Sorunuz N anneye faydalı geldi 💛"  (konu başına ≤ 3 saatte bir)
  Kendi eylemine bildirim YOK; cevap verenin kimliği metinde YAZMAZ.

KURALLAR
  • 23:00–07:00 (TR) gönderilmez: cevap olayları `bekliyor` kaydedilir, 07:00'den
    sonraki ilk turda TEK özet gider ("Gece sorunuza 2 cevap geldi").
  • Kullanıcı başına günde en fazla GUNLUK_TAVAN gönderim (topluluk).
  • Aynı olay iki kez gitmez: topluluk_bildirimleri (user_id, anahtar) UNIQUE.
  • Tercihler: topluluk_cevap_bildirimi (+ eski community_replies), topluluk_faydali_bildirimi.
  • Push verisi: {"type": "topluluk", "thread_id", "reply_id"?}.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.models import Like, Thread, User
from api.models.community_v2 import ToplulukBildirimi
from api.models.user import DEFAULT_NOTIFICATION_PREFS
from api.zaman import TR, simdi_utc, tr_gun_araligi

logger = logging.getLogger("tavsan.topluluk_bildirim")

GUNLUK_TAVAN = 5
FAYDALI_ARALIK = timedelta(hours=3)
SESSIZ_BAS, SESSIZ_BIT = 23, 7          # TR saat
BASLIK_UZUNLUK = 40

METIN = {
    "cevap": "Sorunuza yeni bir cevap var",
    "uzman": "Tavşan Uykusu sorunuzu cevapladı",
    "yanit": "Cevabınıza yanıt geldi",
}


def _tercihler(user: User) -> dict:
    p = dict(DEFAULT_NOTIFICATION_PREFS)
    if isinstance(getattr(user, "notification_prefs", None), dict):
        p.update(user.notification_prefs)
    return p


def cevap_acik_mi(user: User) -> bool:
    p = _tercihler(user)
    return bool(p.get("topluluk_cevap_bildirimi", True)) and bool(p.get("community_replies", True))


def faydali_acik_mi(user: User) -> bool:
    return bool(_tercihler(user).get("topluluk_faydali_bildirimi", True))


def sessiz_mi(an: datetime) -> bool:
    saat = an.astimezone(TR).hour
    return saat >= SESSIZ_BAS or saat < SESSIZ_BIT


def _bugun_gonderilen(db: Session, user_id, an: datetime) -> int:
    bas, son = tr_gun_araligi(an.astimezone(TR).date())
    return (db.query(func.count(ToplulukBildirimi.id))
            .filter(ToplulukBildirimi.user_id == user_id,
                    ToplulukBildirimi.durum == "gonderildi",
                    ToplulukBildirimi.olusturuldu_at >= bas,
                    ToplulukBildirimi.olusturuldu_at < son).scalar() or 0)


def _kaydet(db: Session, user_id, anahtar: str, tur: str, durum: str, an: datetime,
            thread_id=None, reply_id=None) -> bool:
    """Defter satırı. UNIQUE ihlali → bu olay zaten işlendi (False).

    Zaman DAİMA UTC yazılır: SQLite saat dilimini atıp çıplak saati saklıyor,
    TR saatiyle yazılırsa gün sınırı/3 saat kuralı 3 saat kayıyordu."""
    db.add(ToplulukBildirimi(user_id=user_id, anahtar=anahtar, tur=tur, durum=durum,
                             thread_id=thread_id, reply_id=reply_id,
                             olusturuldu_at=an.astimezone(timezone.utc)))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _veri(thread_id, reply_id=None) -> dict:
    d = {"type": "topluluk", "thread_id": str(thread_id) if thread_id else None}
    if reply_id is not None:
        d["reply_id"] = str(reply_id)
    return d


def _gonder(db: Session, user_id, anahtar: str, tur: str, baslik: str, govde: str,
            an: datetime, thread_id=None, reply_id=None, kuyruklanir: bool = True) -> str:
    """Sessiz saat / günlük tavan / tekrar kontrolüyle gönder. Dönen: durum."""
    from api.services.notifier import push_to_user      # döngüsel import olmasın
    if sessiz_mi(an):
        if kuyruklanir:
            _kaydet(db, user_id, anahtar, tur, "bekliyor", an, thread_id, reply_id)
            return "bekliyor"
        return "sessiz"
    if _bugun_gonderilen(db, user_id, an) >= GUNLUK_TAVAN:
        _kaydet(db, user_id, anahtar, tur, "atlandi", an, thread_id, reply_id)
        return "atlandi"
    if not _kaydet(db, user_id, anahtar, tur, "gonderildi", an, thread_id, reply_id):
        return "tekrar"
    push_to_user(db, user_id, baslik, govde, data=_veri(thread_id, reply_id))
    return "gonderildi"


def cevap_olayi(db: Session, thread: Thread, reply, uzman_mi: bool, ust_cevap=None,
                an: datetime | None = None) -> dict:
    """Yeni cevap → konu sahibine (ve yanıtlanan cevabın sahibine) bildirim."""
    an = an or simdi_utc()
    ozet = (thread.title or "")[:BASLIK_UZUNLUK]
    sonuc: dict = {}
    alicilar: list[tuple] = []
    if thread.user_id is not None and thread.user_id != reply.user_id:
        alicilar.append((thread.user_id, "uzman" if uzman_mi else "cevap"))
    if (ust_cevap is not None and ust_cevap.user_id is not None
            and ust_cevap.user_id not in (reply.user_id, thread.user_id)):
        alicilar.append((ust_cevap.user_id, "yanit"))
    for alici_id, tur in alicilar:
        alici = db.get(User, alici_id)
        if alici is None or not cevap_acik_mi(alici):
            sonuc[str(alici_id)] = "kapali"
            continue
        sonuc[str(alici_id)] = _gonder(db, alici_id, f"cevap:{reply.id}", tur, METIN[tur],
                                       ozet, an, thread.id, reply.id)
    return sonuc


def sabah_ozeti(db: Session, an: datetime | None = None) -> int:
    """Gece kuyruğa giren cevap olaylarını kullanıcı başına TEK bildirimde topla."""
    from api.services.notifier import push_to_user
    an = an or simdi_utc()
    if sessiz_mi(an):
        return 0
    gonderilen = 0
    kullanicilar = [r[0] for r in db.query(ToplulukBildirimi.user_id)
                    .filter(ToplulukBildirimi.durum == "bekliyor").distinct()]
    for user_id in kullanicilar:
        bekleyen = (db.query(ToplulukBildirimi)
                    .filter(ToplulukBildirimi.user_id == user_id,
                            ToplulukBildirimi.durum == "bekliyor").all())
        user = db.get(User, user_id)
        n = len(bekleyen)
        konular = {b.thread_id for b in bekleyen}
        for b in bekleyen:
            b.durum = "ozetlendi"
        db.commit()
        if user is None or n == 0 or not cevap_acik_mi(user):
            continue
        if _bugun_gonderilen(db, user_id, an) >= GUNLUK_TAVAN:
            continue
        anahtar = f"ozet:{an.astimezone(TR).date().isoformat()}"
        tek = next(iter(konular)) if len(konular) == 1 else None
        if _kaydet(db, user_id, anahtar, "ozet", "gonderildi", an, tek, None):
            push_to_user(db, user_id, f"Gece sorunuza {n} cevap geldi",
                         "Topluluktaki cevapları görmek için dokunun",
                         data=_veri(tek))
            gonderilen += 1
    return gonderilen


def faydali_turu(db: Session, an: datetime | None = None) -> int:
    """Son bildirimden bu yana gelen 'faydalı'ları konu başına toplu bildir.

    Konu sahibinin kendi beğenisi sayılmaz. Konu başına 3 saatte en fazla bir;
    yeni sayı 0 ise gönderilmez. Sessiz saatte hiç çalışmaz (birikir, sabah gider)."""
    an = an or simdi_utc()
    if sessiz_mi(an):
        return 0
    esik = func.coalesce(Thread.faydali_bildirim_at, Thread.created_at)
    rows = (db.query(Thread, func.count(Like.id))
            .join(Like, and_(Like.target_type == "thread", Like.target_id == Thread.id))
            .filter(Thread.status == "published", Thread.user_id.isnot(None),
                    Like.user_id != Thread.user_id, Like.created_at > esik)
            .group_by(Thread.id).all())
    gonderilen = 0
    for t, n in rows:
        son = t.faydali_bildirim_at
        if son is not None:
            son = son if son.tzinfo else son.replace(tzinfo=timezone.utc)
            if an - son < FAYDALI_ARALIK:
                continue
        if not n:
            continue
        sahip = db.get(User, t.user_id)
        if sahip is None or not faydali_acik_mi(sahip):
            continue
        durum = _gonder(db, t.user_id, f"faydali:{t.id}:{an:%Y%m%d%H%M}", "faydali",
                        f"Sorunuz {n} anneye faydalı geldi 💛",
                        (t.title or "")[:BASLIK_UZUNLUK], an, t.id, None,
                        kuyruklanir=False)
        if durum == "gonderildi":
            t.faydali_bildirim_at = an.astimezone(timezone.utc)
            db.commit()
            gonderilen += 1
    return gonderilen


def tur_calistir(db: Session, an: datetime | None = None) -> dict:
    """Zamanlayıcı turu (notifier._job her 15 dk)."""
    return {"ozet": sabah_ozeti(db, an), "faydali": faydali_turu(db, an)}
