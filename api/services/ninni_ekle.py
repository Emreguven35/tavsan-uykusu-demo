"""
"Ninnilerini al" — STS öncesi üretilmiş pakete yalnız eksik ninnileri ekle.
Bayrak: ANNE_SESI_NINNI_EKLE (varsayılan KAPALI).

NEDEN (2026-11-03): ANNE_SESI_STS açılmadan önce paketi üretilmiş annelerde
ninni yok (ninni düz okunmaz; eski TTS ninnileri 10-16'dan beri gizli). Annenin
ORİJİNAL kaydı saklanmıyor (rıza metni: "sunucularımızda saklanmaz"), o yüzden
ninni ancak YENİDEN KAYITLA eklenebilir.

AKIŞ (POST /voice/clone amac=ninni_ekle):
  1. Uygunluk (uygunluk): bayrak açık · güncel profil `released` paket (önizleme
     değil) · premium · tek seferlik hak kullanılmamış (users.ses_ninni_ekle_at
     NULL) · iş sürmüyor · pakette STS'si EKSİK ninni var. Aylık kayıt
     beklemesine BAKILMAZ; açık rıza yine zorunlu (router).
  2. Hak ÖNCE alınır (koşullu UPDATE — iki eşzamanlı istek iki klon açmasın),
     geçici klon açılır; klon açılamazsa hak geri verilir.
  3. Üretim (uret): yalnız eksik ninniler STS ile; TTS'e düşülmez. Eski TTS
     ninni dosyasının ÜZERİNE yazılır (aynı yol, satır upsert, yontem='sts').
     Masallara ve profilin `status`/`progress`/`error` alanlarına DOKUNULMAZ;
     "%50 altı = failed" kuralı UYGULANMAZ.
  4. Klon HER DURUMDA silinir (hata olsa da). Silinemezse ninni_ekle_voice_id
     dolu kalır; günlük temizlik 2 saatten sonra siler.
  5. Sonuç: en az bir ninni → ninni_ekle_durum='tamam'; hiçbiri →
     'uretilemedi' + hak İADE (ses_ninni_ekle_at=NULL; kart yeniden görünür).
     Üretilemeyen ninni listelenmez (stories: dosyası yok ya da TTS).

SÜREÇ YENİDEN BAŞLARSA: 'hazirlaniyor' + klonu olan işler devam_ettir ile
kaldığı yerden sürer (üretilmiş ninni yeniden üretilmez).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from api.config import _bayrak

logger = logging.getLogger("tavsan.voice.ninni_ekle")

BAYRAK = "ANNE_SESI_NINNI_EKLE"
AMAC = "ninni_ekle"
HAZIRLANIYOR, TAMAM, URETILEMEDI = "hazirlaniyor", "tamam", "uretilemedi"
# Günlük temizlik geçici klonu bu süre boyunca korur (iş birkaç dakika sürer).
KORUMA = timedelta(hours=2)


def acik_mi() -> bool:
    return _bayrak(BAYRAK)


def _kuyruk_anahtari(pid) -> str:
    return f"ninni:{pid}"


# ---------------------------------------------------------------------------
# Uygunluk — SAF (DB okumaz; tools/ninni_ekle.py kuru çalıştırma da kullanır)
# ---------------------------------------------------------------------------
def eksik_ninniler(yontemler: dict[str, str | None]) -> list[dict]:
    """Paketteki (STS üretilebilir) ninnilerden bu profilde STS dosyası
    OLMAYANLAR. yontemler: content_id → voice_audios.yontem."""
    from api.services import voice_paket
    return [x for x in voice_paket.paket_icerikleri()
            if x.get("type") == "ninni" and yontemler.get(x["id"]) != "sts"]


def uygunluk(profil, yontemler: dict[str, str | None], hak_kullanildi_at,
             premium: bool) -> dict:
    """{"uygun": bool, "sebep": str, "eksik": [ninni_id]} — bayrak HARİÇ.
    profil None olabilir. Sebep kodları mobil/log içindir."""
    eksik = [x["id"] for x in eksik_ninniler(yontemler)] if profil is not None else []

    def sonuc(sebep):
        return {"uygun": sebep == "uygun", "sebep": sebep, "eksik": eksik}

    if profil is None:
        return sonuc("profil_yok")
    if getattr(profil, "amac", None) == "onizleme":
        return sonuc("onizleme_profili")
    if profil.status != "released":
        return sonuc("paket_bitmemis")
    if getattr(profil, "ninni_ekle_durum", None) == HAZIRLANIYOR:
        return sonuc("suruyor")
    if hak_kullanildi_at is not None:
        return sonuc("hak_kullanildi")
    if not premium:
        return sonuc("premium_degil")
    if not eksik:
        return sonuc("eksik_ninni_yok")
    return sonuc("uygun")


def kredi_tahmini(ninni_idler: list[str]) -> int:
    """Eksik ninnilerin referans süresi × voice_sts.KREDI_SANIYE."""
    from api.services import mp3, voice_sts
    toplam = 0.0
    for cid in ninni_idler:
        yol = voice_sts.referans_yolu(cid)
        if yol is not None:
            toplam += mp3.sure(yol.read_bytes())
    return voice_sts.kredi(toplam) if toplam else 0


# ---------------------------------------------------------------------------
# DB'li sarmalayıcılar
# ---------------------------------------------------------------------------
def _yontemler(db, profil) -> dict[str, str | None]:
    from api.models import VoiceAudio
    if profil is None:
        return {}
    return {a.content_id: a.yontem for a in db.query(VoiceAudio).filter(
        VoiceAudio.voice_profile_id == profil.id).all()}


def degerlendir(db, user, profil, premium: bool) -> dict:
    """Bayrak dahil uygunluk. Bayrak kapalıysa uygun=False, sebep='kapali'."""
    d = uygunluk(profil, _yontemler(db, profil),
                 getattr(user, "ses_ninni_ekle_at", None), premium)
    if not acik_mi():
        d = dict(d, uygun=False, sebep="kapali")
    return d


def api_alanlari(db, user, profil, premium: bool) -> dict:
    """Mobil sözleşmesi: /voice/stories ve /voice/voice-status'a eklenir."""
    d = degerlendir(db, user, profil, premium)
    durum = getattr(profil, "ninni_ekle_durum", None) if profil is not None else None
    return {"ninni_ekle_uygun": d["uygun"],
            "ninni_ekle_sayisi": len(d["eksik"]) if d["uygun"] else 0,
            "ninni_ekle_durumu": durum if acik_mi() else None}


def hakki_al(db, user) -> bool:
    """Tek seferlik hakkı ATOMİK al (koşullu UPDATE). İki eşzamanlı istekten
    yalnız biri True alır."""
    from api.models import User
    n = (db.query(User)
         .filter(User.id == user.id, User.ses_ninni_ekle_at.is_(None))
         .update({User.ses_ninni_ekle_at: datetime.now(timezone.utc)},
                 synchronize_session=False))
    db.commit()
    db.refresh(user)
    return n == 1


def hakki_iade_et(db, user_id) -> None:
    from api.models import User
    db.query(User).filter(User.id == user_id).update(
        {User.ses_ninni_ekle_at: None}, synchronize_session=False)
    db.commit()
    logger.info("Ninni ekleme hakkı iade edildi: user=%s", user_id)


# ---------------------------------------------------------------------------
# Üretim
# ---------------------------------------------------------------------------
def kuyruga_al(voice_profile_id) -> bool:
    from api.services import voice_uretim as vu
    anahtar = _kuyruk_anahtari(voice_profile_id)
    with vu._kilit:
        if anahtar in vu._calisan:
            return False
        vu._calisan.add(anahtar)
    vu._havuzu_al().submit(_guvenli_uret, str(voice_profile_id))
    return True


def _guvenli_uret(pid: str) -> None:
    from api.services import voice_uretim as vu
    try:
        uret(pid)
    except Exception:
        logger.exception("Ninni ekleme çöktü: profil=%s", pid)
        # uret döngü hatalarını kendisi yakalar; buraya düşen hata döngüden
        # ÖNCEdir (ör. DB) — o ana dek ninni yazılmadı.
        _sonlandir(pid, uretilen=0, beklenmedik=True)
    finally:
        with vu._kilit:
            vu._calisan.discard(_kuyruk_anahtari(pid))


def uret(voice_profile_id) -> dict:
    """Eksik ninnileri geçici klonla STS üret, klonu sil. Döner:
    {"uretilen": n, "eksik": N, "durum": ...}. SAF DEĞİL (DB, ağ)."""
    from api.db import SessionLocal, upsert
    from api.models import VoiceAudio, VoiceProfile
    from api.services import storage
    from api.services import voice_uretim as vu

    pid = vu._uuid(voice_profile_id)
    db = SessionLocal()
    try:
        p = db.get(VoiceProfile, pid)
        if p is None or p.ninni_ekle_durum != HAZIRLANIYOR:
            return {"uretilen": 0, "eksik": 0, "durum": getattr(p, "ninni_ekle_durum", None)}
        voice_id, user_id = p.ninni_ekle_voice_id, p.user_id
        eksik = eksik_ninniler(_yontemler(db, p))
    finally:
        db.close()

    if not voice_id:
        logger.warning("Ninni ekleme: profil %s için geçici klon yok", pid)
        return _sonlandir(pid, uretilen=0, eksik=len(eksik))
    uretilen, basarisiz = 0, []
    try:
        depo = storage.depo()
        for icerik in eksik:
            cid = icerik["id"]
            if not vu._profil_var(pid):
                logger.info("Ninni ekleme durdu: profil=%s silinmiş", pid)
                break
            veri = vu._sts_dene(icerik, voice_id, user_id, pid)
            if not veri:                         # NİNNİ DÜZ OKUNMAZ
                logger.warning("Ninni ekleme: STS üretilemedi profil=%s icerik=%s",
                               pid, cid)
                basarisiz.append(cid)
                continue
            # Eski TTS ninni dosyası varsa AYNI yola yazılır (üzerine).
            yol = storage.ses_yolu(user_id, pid, cid)
            try:
                boyut = depo.yaz(yol, veri, "audio/mpeg")
            except Exception:
                logger.exception("Ninni ekleme: depoya yazılamadı (%s)", cid)
                basarisiz.append(cid)
                continue
            db = SessionLocal()
            try:
                if db.get(VoiceProfile, pid) is None:
                    vu._dosyayi_sil(depo, yol)
                    logger.info("Ninni ekleme durdu: profil=%s silinmiş, %s geri alındı",
                                pid, cid)
                    break
                st = upsert.insert(VoiceAudio).values(
                    voice_profile_id=pid, content_id=cid, storage_path=yol,
                    bytes=boyut, duration_sec=vu._sure_tahmini(boyut), yontem="sts")
                db.execute(st.on_conflict_do_update(
                    index_elements=["voice_profile_id", "content_id"],
                    set_={"storage_path": st.excluded.storage_path,
                          "bytes": st.excluded.bytes,
                          "duration_sec": st.excluded.duration_sec,
                          "yontem": st.excluded.yontem}))
                db.commit()
                uretilen += 1
            except Exception:
                db.rollback()
                logger.exception("Ninni ekleme: voice_audios yazılamadı (%s)", cid)
                basarisiz.append(cid)
            finally:
                db.close()
    except Exception:
        # Beklenmedik hata: o ana dek yazılan ninniler kalır, sonuç aşağıda.
        logger.exception("Ninni ekleme döngüsü çöktü: profil=%s", pid)
    finally:
        # Klon HER DURUMDA silinir (istisna dahil).
        silindi = vu._sesi_birak(voice_id)
    return _sonlandir(pid, uretilen=uretilen, silindi=silindi,
                      basarisiz=basarisiz, eksik=len(eksik))


def _sonlandir(pid, *, uretilen: int, silindi: bool | None = None,
               basarisiz: list | None = None, eksik: int = 0,
               beklenmedik: bool = False) -> dict:
    """Durumu yaz; hiç ninni yoksa hakkı iade et. silindi=True → klon alanı
    temizlenir; False → dolu kalır (günlük temizlik 2 sa sonra siler).
    beklenmedik=True (uret dışı çöküş) → klon burada silinmeye çalışılır."""
    from api.db import SessionLocal
    from api.models import VoiceProfile
    from api.services import voice_uretim as vu

    db = SessionLocal()
    try:
        p = db.get(VoiceProfile, vu._uuid(pid))
        if p is None:
            return {"uretilen": uretilen, "eksik": eksik, "durum": "yok"}
        if beklenmedik and p.ninni_ekle_voice_id:
            silindi = vu._sesi_birak(p.ninni_ekle_voice_id)
        if silindi:
            p.ninni_ekle_voice_id = None
        p.ninni_ekle_durum = TAMAM if uretilen > 0 else URETILEMEDI
        uid = p.user_id
        db.commit()
        durum = p.ninni_ekle_durum
        if uretilen == 0:
            hakki_iade_et(db, uid)
    finally:
        db.close()
    logger.info("Ninni ekleme bitti: profil=%s %d/%d durum=%s başarısız=%s klon_silindi=%s",
                pid, uretilen, eksik, durum, basarisiz or [], silindi)
    return {"uretilen": uretilen, "eksik": eksik, "durum": durum}


def devam_ettir() -> int:
    """Süreç yeniden başlayınca yarım kalan ninni eklemelerini kuyruğa al."""
    from api.db import SessionLocal
    from api.models import VoiceProfile
    db = SessionLocal()
    try:
        idler = [str(p.id) for p in db.query(VoiceProfile).filter(
            VoiceProfile.ninni_ekle_durum == HAZIRLANIYOR,
            VoiceProfile.ninni_ekle_voice_id.isnot(None)).all()]
    finally:
        db.close()
    for pid in idler:
        kuyruga_al(pid)
    if idler:
        logger.info("Yarım kalan %d ninni ekleme işi kuyruğa alındı", len(idler))
    return len(idler)


def korunan_klonlar(db, simdi: datetime | None = None) -> set[str]:
    """Günlük temizliğin silmemesi gereken geçici klonlar (iş sürüyor, < 2 sa)."""
    from api.models import VoiceProfile
    simdi = simdi or datetime.now(timezone.utc)
    out = set()
    for p in db.query(VoiceProfile).filter(
            VoiceProfile.ninni_ekle_voice_id.isnot(None)).all():
        bas = p.ninni_ekle_at
        if bas is not None and bas.tzinfo is None:
            bas = bas.replace(tzinfo=timezone.utc)
        if p.ninni_ekle_durum == HAZIRLANIYOR and bas and simdi - bas < KORUMA:
            out.add(p.ninni_ekle_voice_id)
    return out


def tum_gecici_klonlar(db) -> set[str]:
    """DB'de anılan tüm geçici klonlar (slot kurtarma 'sahipsiz' saymasın)."""
    from api.models import VoiceProfile
    return {vid for (vid,) in db.query(VoiceProfile.ninni_ekle_voice_id).filter(
        VoiceProfile.ninni_ekle_voice_id.isnot(None)).all() if vid}
