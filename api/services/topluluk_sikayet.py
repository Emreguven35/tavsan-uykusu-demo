"""
Topluluk şikâyetleri — App Store 1.2 (UGC) gereksinimleri (2026-10-13).

  • Yeni şikâyette moderatöre e-posta (Resend; services/mailer.send_email).
    İçerik, yazar ve şikâyet sebebi yazar. Gönderim arka planda ve hatası
    şikâyeti düşürmez (send_email exception fırlatmaz).
  • İstek sınırı: kullanıcı başına Türkiye günü içinde en fazla 20 şikâyet.
  • Otomatik gizleme yalnız 24 saatten ESKİ hesapların şikâyetlerini sayar:
    yeni açılmış hesaplarla içerik gizletilemesin.
"""
from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import CommunityProfile, Report, Thread, User
from api.zaman import bugun_tr, simdi_utc, tr_gun_araligi

GUNLUK_SIKAYET_SINIRI = 20
HESAP_YASI_ESIGI = timedelta(hours=24)
MODERATOR_EPOSTA_VARSAYILAN = "tavsanuykusu@hotmail.com"
SEBEP_ADLARI = {"spam": "Spam", "hakaret": "Hakaret", "tibbi_risk": "Tıbbi risk",
                "reklam": "Reklam", "uygunsuz": "Uygunsuz içerik", "diger": "Diğer"}
GOVDE_UZUNLUK = 2000


def moderator_eposta() -> str:
    return (os.getenv("MODERATOR_EPOSTA") or MODERATOR_EPOSTA_VARSAYILAN).strip()


def bugun_sikayet_sayisi(db: Session, user_id) -> int:
    bas, son = tr_gun_araligi(bugun_tr())
    return (db.query(func.count(Report.id))
            .filter(Report.reporter_id == user_id,
                    Report.created_at >= bas, Report.created_at < son).scalar() or 0)


def sinir_doldu_mu(db: Session, user_id) -> bool:
    return bugun_sikayet_sayisi(db, user_id) >= GUNLUK_SIKAYET_SINIRI


def sayilan_sikayetci_sayisi(db: Session, target_type: str, target_id) -> int:
    """Otomatik gizlemede sayılan FARKLI şikâyetçi sayısı — yalnız hesabı
    24 saatten eski olanlar."""
    esik = simdi_utc() - HESAP_YASI_ESIGI
    return (db.query(func.count(func.distinct(Report.reporter_id)))
            .join(User, User.id == Report.reporter_id)
            .filter(Report.target_type == target_type,
                    Report.target_id == target_id,
                    User.created_at <= esik).scalar() or 0)


def yazar_bilgisi(db: Session, obj) -> dict:
    """Moderatörün göreceği yazar: kullanıcı kimliği, GERÇEK takma ad (içerik
    anonim olsa da), profil durumu. Hesap silinmişse kimlik None."""
    uid = getattr(obj, "user_id", None)
    prof = None
    if uid is not None:
        prof = (db.query(CommunityProfile)
                .filter(CommunityProfile.user_id == uid).one_or_none())
    return {"user_id": uid,
            "nickname": prof.nickname if prof is not None else None,
            "status": prof.status if prof is not None else None,
            "anonim": bool(getattr(obj, "anonim", False))}


def eposta_metni(db: Session, rapor: Report, obj, sikayetci: User) -> tuple[str, str]:
    """(konu, metin). İstek içinde kurulur, gönderim arka planda yapılır."""
    y = yazar_bilgisi(db, obj)
    tur = "Konu" if rapor.target_type == "thread" else "Cevap"
    sebep = SEBEP_ADLARI.get(rapor.reason, rapor.reason)
    sk_prof = (db.query(CommunityProfile)
               .filter(CommunityProfile.user_id == sikayetci.id).one_or_none())
    satirlar = [
        f"Toplulukta yeni bir şikâyet var ({tur.lower()}).",
        "",
        f"Şikâyet sebebi: {sebep}",
    ]
    if rapor.note:
        satirlar.append(f"Şikâyet notu: {rapor.note}")
    satirlar += [
        "",
        f"Yazar: {y['nickname'] or '(profil yok)'}"
        + (" — içerik ANONİM paylaşılmış" if y["anonim"] else ""),
        f"Yazar kullanıcı kimliği: {y['user_id'] or '(hesap silinmiş)'}",
        f"Yazar durumu: {y['status'] or '-'}",
        "",
        f"{tur} kimliği: {rapor.target_id}",
    ]
    if isinstance(obj, Thread):
        satirlar.append(f"Başlık: {obj.title}")
    else:
        satirlar.append(f"Konu kimliği: {getattr(obj, 'thread_id', None)}")
    govde = (getattr(obj, "body", "") or "")
    if len(govde) > GOVDE_UZUNLUK:
        govde = govde[:GOVDE_UZUNLUK] + " …"
    satirlar += [
        "",
        "İçerik:",
        govde,
        "",
        f"Şikâyet eden: {sk_prof.nickname if sk_prof else '(profil yok)'} "
        f"({sikayetci.id})",
        "",
        "Uygulamadaki moderatör kuyruğundan gizleyebilir, kaldırabilir ya da "
        "yazarı yasaklayabilirsiniz.",
    ]
    konu = f"[Topluluk şikâyeti] {sebep} — {tur.lower()}"
    return konu, "\n".join(satirlar)


def moderatore_gonder(konu: str, metin: str) -> dict:
    from api.services import mailer
    return mailer.send_email(moderator_eposta(), konu, metin)
