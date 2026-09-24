"""
Genel (anlatıcı sesli) içerikler — B5: ücretsiz ninni.

Anne Sesi premium; ücretsiz kullanıcının da dinleyebileceği TEK ninni
(config.UCRETSIZ_NINNI) anlatıcı sesiyle (env ELEVENLABS_VOICE_ID) BİR KEZ
üretilir ve depoda `genel-sesler/{icerik_id}.mp3` olarak durur. Herkese aynı
dosya; kişisel veri yok. Sunum yine imzalı bağlantıyla (/media/{yol}).

/voice/stories kuralı: kullanıcının KENDİ sesiyle hazır sürümü varsa o;
yoksa ve içerik genel sürümü olan ücretsiz ninniyse genel sürüm.

Üretim: scripts/genel_ninni_uret.py (konteyner içinde; ElevenLabs anahtarı orada).
"""
from __future__ import annotations

import logging
import os

from api.config import UCRETSIZ_NINNI
from api.services import storage

logger = logging.getLogger("tavsan.genel_ses")

KOVA_GENEL = "genel-sesler"
GENEL_ICERIKLER = (UCRETSIZ_NINNI,)


def genel_yol(icerik_id: str) -> str:
    return f"{KOVA_GENEL}/{icerik_id}.mp3"


def genel_surum_var_mi(icerik_id: str) -> bool:
    return icerik_id in GENEL_ICERIKLER and storage.depo().var_mi(genel_yol(icerik_id))


def uret(icerik_id: str = UCRETSIZ_NINNI, zorla: bool = False) -> dict:
    """Anlatıcı sesiyle üret ve depoya yaz. Dönen: {durum, yol, bytes}.
    Depoda varsa (zorla değilse) YENİDEN ÜRETİLMEZ — kredi bir kez harcanır."""
    from api.services import voice as voice_svc
    from api.services.voice_uretim import _seslendir

    if icerik_id not in GENEL_ICERIKLER:
        raise ValueError(f"genel sürümü tanımlı değil: {icerik_id}")
    yol = genel_yol(icerik_id)
    depo = storage.depo()
    if depo.var_mi(yol) and not zorla:
        return {"durum": "zaten_var", "yol": yol, "bytes": depo.boyut(yol)}
    ses = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    if not ses:
        raise RuntimeError("ELEVENLABS_VOICE_ID tanımlı değil")
    cat = voice_svc.load_stories()
    icerik = next((x for x in cat.get("ninniler", []) + cat.get("masallar", [])
                   if x["id"] == icerik_id), None)
    if icerik is None:
        raise RuntimeError(f"katalogda yok: {icerik_id}")
    mp3 = _seslendir(ses, icerik["text"], None)
    if not mp3:
        raise RuntimeError("seslendirme başarısız (ElevenLabs)")
    boyut = depo.yaz(yol, mp3)
    logger.info("Genel sürüm üretildi: %s (%d bayt)", yol, boyut)
    return {"durum": "uretildi", "yol": yol, "bytes": boyut}
