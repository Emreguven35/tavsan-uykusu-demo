"""
KVKK (B5) — onay defteri ve veri dışa aktarma.

ONAY: consents tablosu SALT EKLEMEDİR; güncel durum her türün en son satırıdır.
Metin sürümleri config.KVKK_METIN_SURUMLERI'nde, metinler data/kvkk/{tur}.md'de.
Kullanıcının son onayı GÜNCEL sürüm için değilse `guncelleme_gerekli: true` —
mobil metni yeniden gösterir.

DIŞA AKTARMA (md. 11 — verinin kopyası): kullanıcıya ait her tablo JSON'a
dökülür. Dökülmeyenler bilinçlidir: parola özeti, oturum/parola sıfırlama
token'ları (güvenlik sırrı), ham mağaza makbuzu ve ham ödeme olayı (yalnız
özet alanlar), cihaz push token'ının kendisi (maskeli). 24 saatte bir.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from api.config import KVKK_METIN_SURUMLERI, get_settings
from api.models import Consent, User

KVKK_KLASOR = Path(__file__).resolve().parent.parent.parent / "data" / "kvkk"
EXPORT_ARALIGI = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Onay
# ---------------------------------------------------------------------------
def ip_ozeti(ip: str | None) -> str | None:
    if not ip or ip == "unknown":
        return None
    anahtar = hmac.new(get_settings().jwt_secret.encode("utf-8"),
                       b"tavsan-kvkk-ip-v1", hashlib.sha256).digest()
    return hmac.new(anahtar, ip.encode("utf-8"), hashlib.sha256).hexdigest()


def metin(tur: str) -> str | None:
    p = KVKK_KLASOR / f"{tur}.md"
    return p.read_text("utf-8") if tur in KVKK_METIN_SURUMLERI and p.exists() else None


def onay_kaydet(db: Session, user: User, tur: str, onay: bool,
                metin_surumu: str | None, ip: str | None,
                kaynak: str = "uygulama", commit: bool = True) -> Consent:
    if tur not in KVKK_METIN_SURUMLERI:
        raise ValueError(f"bilinmeyen onay türü: {tur}")
    satir = Consent(user_id=user.id, tur=tur,
                    metin_surumu=(metin_surumu or KVKK_METIN_SURUMLERI[tur])[:40],
                    onay=bool(onay), zaman=datetime.now(timezone.utc),
                    ip_hash=ip_ozeti(ip), kaynak=kaynak)
    db.add(satir)
    if commit:
        db.commit()
        db.refresh(satir)
    return satir


def durum(db: Session, user: User) -> dict:
    """{tur: {onay, metin_surumu, zaman, guncel_surum, guncelleme_gerekli}}."""
    son: dict[str, Consent] = {}
    for c in (db.query(Consent).filter(Consent.user_id == user.id)
              .order_by(Consent.zaman).all()):
        son[c.tur] = c                                   # sıralı → sonuncusu kalır
    out = {}
    for tur, surum in KVKK_METIN_SURUMLERI.items():
        c = son.get(tur)
        out[tur] = {
            "onay": None if c is None else c.onay,
            "metin_surumu": None if c is None else c.metin_surumu,
            "zaman": None if c is None else c.zaman,
            "guncel_surum": surum,
            # Onay yok, reddedilmiş ya da eski sürüm → metin yeniden sorulmalı.
            "guncelleme_gerekli": c is None or not c.onay or c.metin_surumu != surum,
        }
    return out


# ---------------------------------------------------------------------------
# Dışa aktarma
# ---------------------------------------------------------------------------
def _deger(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _satir(obj, haric: tuple[str, ...] = ()) -> dict:
    return {k.name: _deger(getattr(obj, k.name))
            for k in obj.__table__.columns if k.name not in haric}


def export_bekleme(user: User) -> int:
    """Bir sonraki dışa aktarmaya kalan saniye (0 = şimdi olabilir)."""
    son = user.son_export_at
    if son is None:
        return 0
    if son.tzinfo is None:
        son = son.replace(tzinfo=timezone.utc)
    kalan = (son + EXPORT_ARALIGI) - datetime.now(timezone.utc)
    return max(0, int(kalan.total_seconds()))


def disa_aktar(db: Session, user: User) -> dict:
    from api.models import (Baby, Block, ChatMessage, CommunityProfile, Like,
                            PlanFeedback, PremiumHak, PushToken, Reply, Report,
                            SentNotification, SilinenSleepLog, SleepLog,
                            SleepPlan, Subscription, Thread, VideoProgress,
                            VoiceAudio, VoiceProfile)

    def hepsi(model, *filtre, haric=()):
        return [_satir(x, haric) for x in db.query(model).filter(*filtre).all()]

    profiller = db.query(VoiceProfile).filter(VoiceProfile.user_id == user.id).all()
    return {
        "aciklama": ("Tavşan Uykusu hesabınızdaki kişisel verilerin kopyası "
                     "(KVKK md. 11). Parola ve oturum anahtarları güvenlik "
                     "nedeniyle dahil edilmez."),
        "olusturma_zamani": datetime.now(timezone.utc).isoformat(),
        "hesap": _satir(user, haric=("password_hash",)),
        "bebekler": hepsi(Baby, Baby.user_id == user.id),
        "uyku_kayitlari": hepsi(SleepLog, SleepLog.user_id == user.id),
        "silinen_uyku_kayitlari": hepsi(SilinenSleepLog, SilinenSleepLog.user_id == user.id),
        "planlar": hepsi(SleepPlan, SleepPlan.user_id == user.id),
        "plan_geri_bildirimleri": hepsi(PlanFeedback, PlanFeedback.user_id == user.id),
        "sohbet": hepsi(ChatMessage, ChatMessage.user_id == user.id),
        "topluluk": {
            "profil": hepsi(CommunityProfile, CommunityProfile.user_id == user.id),
            "konular": hepsi(Thread, Thread.user_id == user.id),
            "cevaplar": hepsi(Reply, Reply.user_id == user.id),
            "begeniler": hepsi(Like, Like.user_id == user.id),
            "sikayetler": hepsi(Report, Report.reporter_id == user.id),
            "engellenenler": hepsi(Block, Block.user_id == user.id),
        },
        "egitim_video_ilerlemesi": hepsi(VideoProgress, VideoProgress.user_id == user.id),
        "anne_sesi": {
            "profiller": [_satir(p) for p in profiller],
            "uretilen_icerikler": [
                _satir(a, haric=("storage_path",)) for a in db.query(VoiceAudio)
                .filter(VoiceAudio.voice_profile_id.in_([p.id for p in profiller])).all()
            ] if profiller else [],
        },
        "abonelikler": hepsi(Subscription, Subscription.user_id == user.id,
                             haric=("receipt_data", "raw_event")),
        "premium_haklari": hepsi(PremiumHak, PremiumHak.user_id == user.id),
        "onaylar": hepsi(Consent, Consent.user_id == user.id),
        "bildirim_cihazlari": [
            {**_satir(t, haric=("expo_token",)),
             "expo_token": (t.expo_token[:18] + "…") if t.expo_token else None}
            for t in db.query(PushToken).filter(PushToken.user_id == user.id).all()],
        "gonderilen_bildirimler": hepsi(SentNotification, SentNotification.user_id == user.id),
    }
