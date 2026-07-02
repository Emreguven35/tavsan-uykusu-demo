"""
ElevenLabs TTS + ses cache.

- Türkçe metin → MP3 (eleven_multilingual_v2), doğrudan REST (requests) ile.
- Ses cache: MP3'ler data/audio_cache/ altında, dosya adı = CEVAP CACHE'İYLE AYNI
  hash (chatbot._cevap_uret'in döndürdüğü 'anahtar'). Böylece cevap cache HIT olunca
  aynı hash'li MP3 varsa TTS'e HİÇ gidilmez.
- TTS hatası (kota/ağ/anahtar yok) → None döner; endpoint çökmez, ses_url=null olur.
- LRU: en fazla AUDIO_MAX_FILES dosya veya AUDIO_MAX_BYTES; eskiden (mtime) siler.
"""
import os
import re
import time
import logging
from pathlib import Path

import requests

logger = logging.getLogger("tavsan.tts")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIO_DIR = DATA_DIR / "audio_cache"

ELEVENLABS_MODEL = "eleven_multilingual_v2"     # Türkçe destekli çok dilli model
ELEVENLABS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
TTS_TIMEOUT_S = 30

# ElevenLabs karakter (kredi) bazlı ücretlendirir; ~1 karakter ≈ 1 kredi.
# Creator planı: $22 / 100.000 kredi → ~$0.00022/karakter. Plan değişirse güncelle.
# Kaynak: elevenlabs.io/pricing (Creator tier). Bu SABİT bir yaklaşık maliyettir.
ELEVENLABS_USD_PER_CHAR = 0.00022

AUDIO_MAX_FILES = 500
AUDIO_MAX_BYTES = 100 * 1024 * 1024             # 100 MB

_SAFE_NAME = re.compile(r"^[a-f0-9]{16,64}$")   # yalnız hash adları (path traversal engeli)


def tts_cost(text: str) -> float:
    """Metin karakter sayısından yaklaşık ElevenLabs maliyeti ($)."""
    return round(len(text) * ELEVENLABS_USD_PER_CHAR, 6)


def audio_path(anahtar: str) -> Path:
    return AUDIO_DIR / f"{anahtar}.mp3"


def _enforce_lru() -> None:
    """Dosya sayısı/boyut sınırını aş: en eski (mtime) dosyaları sil."""
    try:
        files = sorted(AUDIO_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    except FileNotFoundError:
        return
    total = sum(p.stat().st_size for p in files)
    while files and (len(files) > AUDIO_MAX_FILES or total > AUDIO_MAX_BYTES):
        victim = files.pop(0)
        try:
            total -= victim.stat().st_size
            victim.unlink()
            logger.info("Ses cache LRU: silindi %s", victim.name)
        except OSError as e:
            logger.warning("Ses cache LRU silme hatası: %s", e)


def synthesize(text: str) -> bytes | None:
    """Metni MP3 byte'larına çevir. Anahtar yok / hata → None (endpoint çökmesin)."""
    key = os.getenv("ELEVENLABS_API_KEY")
    voice = os.getenv("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        logger.info("TTS atlandı: ELEVENLABS_API_KEY/VOICE_ID tanımlı değil.")
        return None
    try:
        r = requests.post(
            f"{ELEVENLABS_BASE}/{voice}",
            headers={"xi-api-key": key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json={"text": text, "model_id": ELEVENLABS_MODEL},
            timeout=TTS_TIMEOUT_S,
        )
        r.raise_for_status()
        if not r.content:
            logger.warning("TTS boş içerik döndü.")
            return None
        return r.content
    except Exception as e:                       # kota/ağ/HTTP — çökme, logla
        logger.warning("TTS hatası (ses_url=null döndürülecek): %s", e)
        return None


def ensure_audio(anahtar: str, text: str) -> dict:
    """anahtar.mp3 hazırsa TTS'e gitme; yoksa üret+kaydet. Döner:
    {ses_url: str|None, tts_usd: float, tts_called: bool, cached: bool}."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = audio_path(anahtar)

    if path.exists():                            # ses cache HIT → TTS yok
        return {"ses_url": f"/audio/{anahtar}.mp3", "tts_usd": 0.0,
                "tts_called": False, "cached": True}

    audio = synthesize(text)
    if audio is None:                            # graceful: ses yok, cevap kalır
        return {"ses_url": None, "tts_usd": 0.0, "tts_called": False, "cached": False}

    try:
        path.write_bytes(audio)
    except OSError as e:
        logger.warning("MP3 yazılamadı: %s", e)
        return {"ses_url": None, "tts_usd": 0.0, "tts_called": True, "cached": False}

    _enforce_lru()
    return {"ses_url": f"/audio/{anahtar}.mp3", "tts_usd": tts_cost(text),
            "tts_called": True, "cached": False}


def is_safe_name(name: str) -> bool:
    """/audio/{dosya} için: yalnız hash.mp3 kalıbı (path traversal engeli)."""
    if not name.endswith(".mp3"):
        return False
    return bool(_SAFE_NAME.match(name[:-4]))
