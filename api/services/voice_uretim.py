"""
Ses paketi üretimi — "üret ve bırak".

AKIŞ (Faz 2):
    cloning → generating → (paket üretilir) → ElevenLabs sesi SİLİNİR → ready
Kısmi başarıda %50 kuralı: yarıdan fazlası üretildiyse `ready` (eksikler
`error` alanında listelenir), altındaysa `failed` + aylık hak İADE edilir.

EŞZAMANLILIK: en fazla 2 üretim aynı anda (ELEVENLABS_ESZAMANLI). Sebep tek
başına hız değil; ElevenLabs hesap kotası ve slot tavanı paylaşımlı, paralel
üretim ikisini de hızla tüketiyor.

DAYANIKLILIK: her dosya yazıldıktan SONRA ilerleme DB'ye işlenir. Süreç
yeniden başlarsa `generating` kalmış işler `bekleyenleri_devam_ettir` ile
kaldığı yerden sürer — tamamlanmış içerikler yeniden üretilmez (voice_audios
+ depoda dosya varlığı ile anlaşılır).

ARKA PLAN: ayrı bir kuyruk altyapısı (Celery/RQ) YOK ve eklenmiyor — tek
konteyner çalışıyor, iş sayısı günde birkaç. Basit bir ThreadPool yeter ve
bir bağımlılık daha getirmez.
"""
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from api import tts
from api.db import SessionLocal, upsert
from api.models import VoiceAudio, VoiceProfile
from api.services import storage
from api.services import voice as voice_svc
from api.services import voice_paket

logger = logging.getLogger("tavsan.voice.uretim")

ESZAMANLI = max(1, int(os.getenv("VOICE_ESZAMANLI") or 2))
DENEME_SAYISI = 3                     # içerik başına TTS denemesi
KISMI_BASARI_ORANI = 0.5              # bunun ALTI → failed + hak iadesi

# Bitiş sayılan durumlar (yeniden kuyruğa alınmaz).
BITMIS = ("ready", "released", "failed", "replaced")

_havuz: ThreadPoolExecutor | None = None
_kilit = threading.Lock()
_calisan: set[str] = set()            # aynı profil iki kez kuyruğa girmesin


def _havuzu_al() -> ThreadPoolExecutor:
    global _havuz
    with _kilit:
        if _havuz is None:
            _havuz = ThreadPoolExecutor(max_workers=ESZAMANLI,
                                        thread_name_prefix="voice-uretim")
            logger.info("Ses üretim havuzu açıldı (eşzamanlı=%d)", ESZAMANLI)
        return _havuz


def kuyruga_al(voice_profile_id) -> bool:
    """Üretimi arka planda başlat. Zaten kuyruktaysa tekrar eklemez."""
    anahtar = str(voice_profile_id)
    with _kilit:
        if anahtar in _calisan:
            return False
        _calisan.add(anahtar)
    _havuzu_al().submit(_guvenli_uret, anahtar)
    return True


def _guvenli_uret(anahtar: str) -> None:
    """Havuz iş sarmalayıcısı — istisna havuzu sessizce öldürmesin."""
    try:
        uret(anahtar)
    except Exception:
        logger.exception("Ses paketi üretimi çöktü: profil=%s", anahtar)
        _durum_yaz(anahtar, status="failed", error="Üretim beklenmedik hatayla durdu")
    finally:
        with _kilit:
            _calisan.discard(anahtar)


def _durum_yaz(anahtar: str, **alanlar) -> None:
    db = SessionLocal()
    try:
        p = db.get(VoiceProfile, _uuid(anahtar))
        if p is None:
            return
        for k, v in alanlar.items():
            setattr(p, k, v)
        db.commit()
    except Exception:
        logger.exception("Durum yazılamadı: profil=%s", anahtar)
    finally:
        db.close()


def _uuid(x):
    import uuid as _u
    return x if isinstance(x, _u.UUID) else _u.UUID(str(x))


def uret(voice_profile_id) -> dict:
    """Paketi üret, bitince ElevenLabs sesini serbest bırak.

    Döner: {"uretilen": n, "toplam": N, "status": ...}
    SAF DEĞİL: DB'ye yazar, ağa çıkar. Testlerde tts/voice_svc mock'lanır."""
    pid = _uuid(voice_profile_id)
    icerikler = voice_paket.paket_icerikleri()
    toplam = len(icerikler)

    db = SessionLocal()
    try:
        profil = db.get(VoiceProfile, pid)
        if profil is None:
            return {"uretilen": 0, "toplam": toplam, "status": "yok"}
        if profil.status in BITMIS:
            return {"uretilen": profil.progress_done, "toplam": toplam,
                    "status": profil.status}
        voice_id = profil.elevenlabs_voice_id
        user_id = profil.user_id
        if not voice_id:
            # Ses zaten serbest bırakılmış ama durum güncellenmemiş.
            logger.warning("Üretim: profil %s için voice_id yok", pid)
            profil.status = "failed"
            profil.error = "ElevenLabs ses kimliği yok"
            db.commit()
            return {"uretilen": 0, "toplam": toplam, "status": "failed"}

        profil.status = "generating"
        profil.progress_total = toplam
        # Yeniden başlatmada zaten üretilmiş içerikler sayılır.
        mevcut = {a.content_id for a in profil.audios}
        profil.progress_done = len(mevcut)
        db.commit()
    finally:
        db.close()

    depo = storage.depo()
    basarisiz: list[str] = []
    uretilen = len(mevcut)

    for icerik in icerikler:
        cid = icerik["id"]
        if cid in mevcut:
            continue                       # restart: bu içerik hazır
        yol = storage.ses_yolu(user_id, pid, cid)
        veri = None
        for deneme in range(1, DENEME_SAYISI + 1):
            veri = _seslendir(voice_id, icerik["text"], user_id)
            if veri:
                break
            logger.warning("TTS başarısız (deneme %d/%d): profil=%s icerik=%s",
                           deneme, DENEME_SAYISI, pid, cid)
        if not veri:
            basarisiz.append(cid)
            continue
        try:
            boyut = depo.yaz(yol, veri, "audio/mpeg")
        except Exception:
            logger.exception("Depoya yazılamadı: %s", yol)
            basarisiz.append(cid)
            continue

        db = SessionLocal()
        try:
            # ATOMİK UPSERT (v2.4.4). Eskiden düz INSERT'ti ve UNIQUE
            # (voice_profile_id, content_id) ihlali "yazılamadı" sayılıyordu:
            # dosya depoya YAZILMIŞ olmasına rağmen içerik BAŞARISIZ'a düşüyor,
            # %50 kuralı paketi `failed`e çevirip aylık hakkı iade edebiliyordu.
            # Çakışma iki yoldan geliyor: aynı profil için ikinci bir üretim
            # turu ve yeniden başlatmada yarışan iki iş. Artık çakışma hata
            # değil, güncelleme — yeni dosya yolu/boyutu satıra yazılır.
            st = upsert.insert(VoiceAudio).values(
                voice_profile_id=pid, content_id=cid, storage_path=yol,
                bytes=boyut, duration_sec=_sure_tahmini(boyut))
            db.execute(st.on_conflict_do_update(
                index_elements=["voice_profile_id", "content_id"],
                set_={"storage_path": st.excluded.storage_path,
                      "bytes": st.excluded.bytes,
                      "duration_sec": st.excluded.duration_sec}))
            # İLERLEME SAYILARAK yazılır, artırılarak DEĞİL: iki iş aynı anda
            # `+1` yaparsa biri kaybolur (lost update) ve çubuk eksik kalır.
            p = db.get(VoiceProfile, pid)
            if p is not None:
                p.progress_done = (db.query(VoiceAudio)
                                   .filter(VoiceAudio.voice_profile_id == pid)
                                   .count())
            db.commit()
            uretilen += 1
        except Exception:
            db.rollback()
            logger.exception("voice_audios yazılamadı: profil=%s icerik=%s", pid, cid)
            basarisiz.append(cid)
        finally:
            db.close()

    return _tamamla(pid, voice_id, uretilen, toplam, basarisiz)


def _seslendir(voice_id: str, metin: str, user_id) -> bytes | None:
    """Tek içeriğin MP3'ü. Masal profili (yavaş, duraklamalı anlatım)."""
    from api.konusma_metni import masal_metni_hazirla
    from api.services import usage as usage_svc
    konusma = masal_metni_hazirla(metin)
    return tts.synthesize(konusma, voice_id=voice_id, profil=tts.MASAL_PROFILI,
                          usage_op=usage_svc.OP_TTS, user_id=user_id)


def _sure_tahmini(boyut: int) -> int:
    """MP3 süresi (sn) — ElevenLabs 128 kbps CBR döndürüyor.

    Kesin süre için çözücü gerekir; mobil yalnız "yaklaşık kaç dakika"
    gösterdiği için bu tahmin yeterli ve bir bağımlılık getirmiyor."""
    return max(1, round(boyut * 8 / 128000))


def _tamamla(pid, voice_id: str, uretilen: int, toplam: int,
             basarisiz: list[str]) -> dict:
    """Paket bitti: sesi serbest bırak, durumu yaz, gerekiyorsa hakkı iade et."""
    yeterli = toplam > 0 and (uretilen / toplam) > KISMI_BASARI_ORANI

    # Ses HER İKİ durumda da silinir: başarısız üretimde slotu tutmanın anlamı
    # yok, başarılıda zaten model bu.
    silindi = _sesi_birak(voice_id)

    db = SessionLocal()
    try:
        p = db.get(VoiceProfile, _uuid(pid))
        if p is None:
            return {"uretilen": uretilen, "toplam": toplam, "status": "yok"}
        p.progress_done, p.progress_total = uretilen, toplam
        if silindi:
            p.elevenlabs_voice_id = None
            p.released_at = datetime.now(timezone.utc)
        if yeterli:
            p.status = "released" if silindi else "ready"
            p.error = (None if not basarisiz else
                       "Üretilemedi: " + ", ".join(basarisiz))
        else:
            p.status = "failed"
            p.error = (f"Paketin {uretilen}/{toplam} parçası üretilebildi; "
                       "kayıt hakkınız iade edildi.")
            # K-hak iadesi: kullanıcı hemen yeniden deneyebilsin.
            from api.routers.voice import CLONE_COOLDOWN_DAYS
            from datetime import timedelta
            p.last_cloned_at = (datetime.now(timezone.utc)
                                - timedelta(days=CLONE_COOLDOWN_DAYS))
        db.commit()
        durum = p.status
    finally:
        db.close()

    logger.info("Ses paketi bitti: profil=%s %d/%d durum=%s ses_silindi=%s",
                pid, uretilen, toplam, durum, silindi)
    return {"uretilen": uretilen, "toplam": toplam, "status": durum}


def _sesi_birak(voice_id: str) -> bool:
    """ElevenLabs'teki klon sesini sil. Başarısızlık AKIŞI DURDURMAZ.

    Silinemezse `released_at` NULL kalır ve günlük temizlik işi tekrar dener
    (Faz 4.1) — slot en geç ertesi gün geri döner."""
    if not voice_id:
        return False
    sonuc = voice_svc.delete_voice(voice_id)
    if sonuc.get("ok"):
        return True
    logger.warning("Klon sesi SİLİNEMEDİ (voice_id=%s): %s — günlük temizlik "
                   "tekrar deneyecek", voice_id, sonuc.get("error"))
    return False


def bekleyenleri_devam_ettir() -> int:
    """Süreç yeniden başladığında yarım kalan üretimleri kuyruğa al.

    `generating` kalmış her profil, tamamlanmış içerikleri atlayarak devam
    eder. Bu olmadan restart, anneyi sonsuza kadar "hazırlanıyor" ekranında
    bırakırdı."""
    db = SessionLocal()
    try:
        yarim = (db.query(VoiceProfile)
                 .filter(VoiceProfile.status == "generating",
                         VoiceProfile.elevenlabs_voice_id.isnot(None))
                 .all())
        idler = [str(p.id) for p in yarim]
    finally:
        db.close()
    for pid in idler:
        kuyruga_al(pid)
    if idler:
        logger.info("Yarım kalan %d ses paketi kuyruğa alındı", len(idler))
    return len(idler)


def kapat() -> None:
    global _havuz
    with _kilit:
        if _havuz is not None:
            _havuz.shutdown(wait=False)
            _havuz = None
