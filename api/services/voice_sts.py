"""
Anne Sesi — speech-to-speech (STS) üretimi. Bayrak: ANNE_SESI_STS (varsayılan KAPALI).

NEDEN: ninni ve masallar düz TTS ile okununca ezgi ve duygu yok. Uzman her
içeriği BİR KEZ gerçek ezgi ve duyguyla seslendirir; her annenin paketi bu
kayıtların annenin klon sesine dönüştürülmesiyle (ElevenLabs speech-to-speech,
eleven_multilingual_sts_v2) üretilir.

REFERANSLAR: data/anne_sesi_referans/{icerik_id}.mp3 (bkz. oradaki README).
Dosyası olmayan içerik TTS'e düşer.

PARÇALAMA: istek başına ses uzunluğu sınırlı (ElevenLabs dokümanı rakam
vermiyor; 5 dk diyen kaynak var). Referans PARCA_SN'lik parçalara çerçeve
sınırında bölünür, her parça ayrı dönüştürülür, çıktı tek MP3'te birleşir.

İZİN: anahtarın speech_to_speech izni yoksa (401/403) açık bir HATA loglanır,
bu süreç boyunca STS bir daha denenmez ve içerik TTS'e düşer. Anahtar ASLA
loglanmaz; yanıttan yalnız durum kodu ve hata sınıfı yazılır.

MALİYET: ElevenLabs STS'yi ses dakikası başına ücretlendirir (dakikada 1.000
kredi). api_usage'a operation="sts", characters=kredi olarak yazılır; USD
karşılığı services/usage.maliyet_hesapla'da.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests

from api.config import _bayrak
from api.services import mp3

logger = logging.getLogger("tavsan.voice.sts")

BAYRAK = "ANNE_SESI_STS"
MODEL = "eleven_multilingual_sts_v2"
STS_URL = "https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}"
CIKIS_BICIMI = "mp3_44100_128"
REFERANS_KLASOR = (Path(__file__).resolve().parent.parent.parent
                   / "data" / "anne_sesi_referans")
# 4,5 dk: bildirilen 5 dk istek sınırının güvenli altı. Çoğu ninni tek parça.
PARCA_SN = 270
STS_TIMEOUT_S = 180
KREDI_DAKIKA = 1000

_izin_kilidi = threading.Lock()
_izin_yok = False                     # süreç boyunca: anahtarda STS izni yok


class IzinYok(Exception):
    """Anahtarın speech_to_speech izni yok (401/403)."""


def acik_mi() -> bool:
    """Her çağrıda env'den okunur (önbelleksiz)."""
    return _bayrak(BAYRAK)


def izin_yok_mu() -> bool:
    return _izin_yok


def _izin_yok_isaretle(durum: int, sinif: str) -> None:
    global _izin_yok
    with _izin_kilidi:
        ilk = not _izin_yok
        _izin_yok = True
    if ilk:
        logger.error("ANNE_SESI_STS: ElevenLabs anahtarının speech-to-speech İZNİ YOK "
                     "(HTTP %s, %s). Anne Sesi içerikleri TTS ile üretilecek. "
                     "ElevenLabs panelinde anahtara 'Speech to Speech' izni verilip "
                     "servis yeniden başlatılmalı.", durum, sinif or "-")


def referans_yolu(icerik_id: str) -> Path | None:
    """Referans kaydın yolu; yoksa ya da boşsa None. Kimlik dosya adıdır —
    yol kaçışı engellenir."""
    if not icerik_id or "/" in icerik_id or "\\" in icerik_id or ".." in icerik_id:
        return None
    p = REFERANS_KLASOR / f"{icerik_id}.mp3"
    return p if p.is_file() and p.stat().st_size > 0 else None


def kredi(saniye: float) -> int:
    return max(1, round(saniye * KREDI_DAKIKA / 60))


def _hata_sinifi(r) -> str:
    """Yanıttan yalnız hata SINIFI (ör. missing_permissions) — gövde/anahtar değil."""
    try:
        d = r.json().get("detail")
        if isinstance(d, dict):
            return str(d.get("status") or "")[:60]
    except Exception:
        pass
    return ""


def _izin_hatasi_mi(r) -> bool:
    if r.status_code not in (401, 403):
        return False
    sinif = _hata_sinifi(r)
    # 401 "invalid_api_key" izin değil, anahtar sorunu: izin hatası SAYILMAZ
    # (STS'yi kalıcı kapatmaz); içerik TTS'e düşer, TTS de kendi hatasını loglar.
    return sinif in ("missing_permissions", "") or "permission" in sinif


def _parca_donustur(anahtar: str, voice_id: str, parca: bytes, user_id) -> bytes:
    t0 = time.perf_counter()
    r = requests.post(
        STS_URL.format(voice_id=voice_id),
        params={"output_format": CIKIS_BICIMI},
        headers={"xi-api-key": anahtar, "Accept": "audio/mpeg"},
        data={"model_id": MODEL,
              "voice_settings": json.dumps({"stability": 0.5,
                                            "similarity_boost": 0.85}),
              "remove_background_noise": "false"},
        files={"audio": ("referans.mp3", parca, "audio/mpeg")},
        timeout=STS_TIMEOUT_S)
    if _izin_hatasi_mi(r):
        _izin_yok_isaretle(r.status_code, _hata_sinifi(r))
        raise IzinYok()
    if r.status_code != 200 or not r.content:
        raise RuntimeError(f"STS HTTP {r.status_code} {_hata_sinifi(r)}".strip())
    from api.services import usage as usage_svc
    usage_svc.kaydet(usage_svc.SERVIS_ELEVENLABS, usage_svc.OP_STS, model=MODEL,
                     characters=kredi(mp3.sure(parca)), user_id=user_id,
                     duration_ms=int((time.perf_counter() - t0) * 1000))
    return r.content


def donustur(voice_id: str, kaynak: bytes, user_id=None) -> bytes | None:
    """Kaynak sesi annenin sesine dönüştür. Başarısızlıkta None (çağıran TTS'e
    düşer); izin yoksa IzinYok fırlatır. Parçalardan biri bile düşerse None —
    yarım masal sunulmaz."""
    anahtar = os.getenv("ELEVENLABS_API_KEY")
    if not anahtar or not voice_id or not kaynak:
        return None
    if _izin_yok:
        raise IzinYok()
    parcalar = mp3.parcala(kaynak, PARCA_SN)
    cikti: list[bytes] = []
    for i, parca in enumerate(parcalar, 1):
        try:
            cikti.append(_parca_donustur(anahtar, voice_id, parca, user_id))
        except IzinYok:
            raise
        except Exception as e:
            # requests istisnası URL taşır, anahtar taşımaz (başlıkta gider).
            logger.warning("STS parça %d/%d başarısız (voice_id=%s): %s",
                           i, len(parcalar), voice_id, e)
            return None
    return mp3.birlestir(cikti)


def seslendir(icerik: dict, voice_id: str, user_id=None,
              en_fazla_sn: float | None = None) -> bytes | None:
    """İçeriği referans kaydından STS ile üret. Bayrak kapalı, referans yok ya
    da izin yoksa None → çağıran TTS kullanır. `en_fazla_sn` (önizleme):
    referansın yalnız ilk o kadar saniyesi dönüştürülür."""
    if not acik_mi() or _izin_yok:
        return None
    yol = referans_yolu(icerik.get("id", ""))
    if yol is None:
        logger.info("STS: referans kayıt yok (%s) — TTS", icerik.get("id"))
        return None
    kaynak = yol.read_bytes()
    if mp3.sure(kaynak) <= 0:
        logger.warning("STS: referans okunamadı, MP3 değil gibi (%s) — TTS", yol.name)
        return None
    if en_fazla_sn:
        kaynak = mp3.kes(kaynak, en_fazla_sn)
    try:
        return donustur(voice_id, kaynak, user_id)
    except IzinYok:
        return None
