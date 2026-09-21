"""
Günlük ses temizliği (Faz 4.1) + hesap silme temizliği (Faz 4.2).

"ÜRET VE BIRAK" modelinde ElevenLabs'te ses BIRAKILMAMALIDIR. Üretim sonundaki
silme best-effort'tur (ağ hatası, upstream 5xx); bu iş o kalıntıları toplar.
Aksi hâlde tek bir başarısız silme 10 slotun birini süresiz tutar ve model
yeniden tıkanır — v2.2'de tam olarak bu yaşandı.

KORUNANLAR (asla silinmez):
  • ELEVENLABS_VOICE_ID — uygulamanın anlatıcı sesi, DB'de satırı yoktur.
  • status'ü `cloning` veya `generating` olan profillerin sesleri — üretim
    sürüyor, altından slot çekilemez.
"""
import logging
import os
from datetime import datetime, timezone

from api.db import SessionLocal
from api.models import VoiceAudio, VoiceProfile
from api.services import storage
from api.services import voice as voice_svc

logger = logging.getLogger("tavsan.voice.temizlik")


def gunluk_temizlik() -> dict:
    """ElevenLabs'teki artık sesleri sil. Döner: sayım sözlüğü.

    ASLA İSTİSNA FIRLATMAZ: zamanlayıcı işidir, bir hata bütün zamanlayıcıyı
    düşürmemeli."""
    sonuc = {"hesapta": 0, "korunan": 0, "silinen": 0, "hata": 0}
    try:
        liste = voice_svc.list_cloned_voices()
        if not liste.get("ok"):
            logger.warning("Temizlik: ses listesi alınamadı (%s)", liste.get("error"))
            return sonuc
        hesaptaki = {v["voice_id"] for v in liste["voices"] if v.get("voice_id")}
        sonuc["hesapta"] = len(hesaptaki)

        anlatici = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
        db = SessionLocal()
        try:
            # Üretimi SÜREN profillerin sesleri dokunulmaz.
            surenler = {
                p.elevenlabs_voice_id
                for p in db.query(VoiceProfile).filter(
                    VoiceProfile.status.in_(("cloning", "generating")),
                    VoiceProfile.elevenlabs_voice_id.isnot(None)).all()
                if p.elevenlabs_voice_id}
        finally:
            db.close()

        korunan = surenler | ({anlatici} if anlatici else set())
        sonuc["korunan"] = len(korunan & hesaptaki)

        for vid in sorted(hesaptaki - korunan):
            r = voice_svc.delete_voice(vid)
            if r.get("ok"):
                sonuc["silinen"] += 1
                logger.info("Temizlik: artık klon sesi silindi (%s)", vid)
                _released_isaretle(vid)
            else:
                sonuc["hata"] += 1
                logger.warning("Temizlik: silinemedi %s — %s", vid, r.get("error"))
        if sonuc["silinen"] or sonuc["hata"]:
            logger.info("Günlük ses temizliği: %s", sonuc)
    except Exception:
        logger.exception("Günlük ses temizliği çöktü")
    return sonuc


def _released_isaretle(voice_id: str) -> None:
    """Silinen sesin DB satırını da güncelle (elevenlabs_voice_id=NULL)."""
    db = SessionLocal()
    try:
        for p in db.query(VoiceProfile).filter(
                VoiceProfile.elevenlabs_voice_id == voice_id).all():
            p.elevenlabs_voice_id = None
            if p.released_at is None:
                p.released_at = datetime.now(timezone.utc)
            if p.status == "ready":
                p.status = "released"
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Temizlik: DB satırı güncellenemedi (%s)", voice_id)
    finally:
        db.close()


def kullanici_seslerini_sil(db, user) -> dict:
    """Faz 4.2 — hesap silinince ses verisini de sil (KVKK).

    ORM cascade `voice_profiles`/`voice_audios` satırlarını zaten siliyor ama
    İKİ ŞEYİ silmiyor: depodaki MP3 dosyaları ve ElevenLabs'te kalmış olabilecek
    klon sesi. Biyometrik veriden türetilmiş kayıtların hesap silindikten sonra
    diskte kalması kabul edilemez.

    `db.delete(user)` ÇAĞRILMADAN ÖNCE çalıştırılmalıdır (satırlara hâlâ
    erişilebilsin diye)."""
    sonuc = {"ses_silindi": 0, "dosya_silindi": 0}
    try:
        profiller = (db.query(VoiceProfile)
                     .filter(VoiceProfile.user_id == user.id).all())
        for p in profiller:
            if p.elevenlabs_voice_id:
                r = voice_svc.delete_voice(p.elevenlabs_voice_id)
                if r.get("ok"):
                    sonuc["ses_silindi"] += 1
                else:
                    logger.warning("Hesap silme: ElevenLabs sesi silinemedi "
                                   "(%s) — günlük temizlik deneyecek",
                                   p.elevenlabs_voice_id)
        try:
            sonuc["dosya_silindi"] = storage.depo().klasor_sil(
                storage.ses_klasoru(user.id))
        except Exception:
            logger.exception("Hesap silme: depo klasörü silinemedi (user=%s)",
                             user.id)
        # Satırları açıkça sil: cascade'e güvenmek yerine görünür olsun.
        for p in profiller:
            db.query(VoiceAudio).filter(
                VoiceAudio.voice_profile_id == p.id).delete()
        logger.info("Hesap silme ses temizliği: user=%s %s", user.id, sonuc)
    except Exception:
        logger.exception("Hesap silme ses temizliği çöktü (user=%s)", user.id)
    return sonuc
