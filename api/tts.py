"""
ElevenLabs TTS + ses cache.

- Türkçe metin → MP3 (TTS_MODEL = eleven_flash_v2_5), doğrudan REST (requests) ile.
- Ses cache: MP3'ler data/audio_cache/ altında, dosya adı = CEVAP CACHE'İYLE AYNI
  hash (chatbot._cevap_uret'in döndürdüğü 'anahtar'). Böylece cevap cache HIT olunca
  aynı hash'li MP3 varsa TTS'e HİÇ gidilmez.
- TTS hatası (kota/ağ/anahtar yok) → None döner; endpoint çökmez, ses_url=null olur.
- LRU: en fazla AUDIO_MAX_FILES dosya veya AUDIO_MAX_BYTES; eskiden (mtime) siler.
"""
import os
import re
import time
import hashlib
import logging
from pathlib import Path

import requests

from api.konusma_metni import konusma_metnine_cevir  # TTS öncesi metin temizliği

logger = logging.getLogger("tavsan.tts")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIO_DIR = DATA_DIR / "audio_cache"

# Aktif TTS modeli (merkezi sabit). Flash v2.5 çok dillidir (Türkçe destekli),
# multilingual_v2'ye göre daha hızlı ve karakter başına YARI kredi tüketir.
TTS_MODEL = "eleven_flash_v2_5"
MULTILINGUAL_MODEL = "eleven_multilingual_v2"   # kıyas/geri-dönüş için
ELEVENLABS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
TTS_TIMEOUT_S = 30

# ElevenLabs karakter (kredi) bazlı ücretlendirir. Flash v2.5 = 0.5 kredi/karakter
# (multilingual_v2 = 1 kredi/karakter). Creator planı $22 / 100.000 kredi →
# ~$0.00022/kredi. Dolayısıyla Flash: 0.5 × 0.00022 = ~0.00011 $/karakter.
# Kaynak: elevenlabs.io/pricing + modeller (Flash v2.5, kredi/krktr), 2026-07-02.
ELEVENLABS_USD_PER_CHAR = 0.00011

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


def synthesize(text: str, model: str | None = None,
               voice_id: str | None = None) -> bytes | None:
    """Metni MP3 byte'larına çevir. Anahtar yok / hata → None (endpoint çökmesin).
    model verilmezse merkezi TTS_MODEL (flash v2.5); voice_id verilmezse env
    ELEVENLABS_VOICE_ID (klonlanmış kullanıcı sesi için voice_id geçilir)."""
    key = os.getenv("ELEVENLABS_API_KEY")
    voice = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        logger.info("TTS atlandı: ELEVENLABS_API_KEY/VOICE_ID tanımlı değil.")
        return None
    try:
        r = requests.post(
            f"{ELEVENLABS_BASE}/{voice}",
            headers={"xi-api-key": key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json={"text": text, "model_id": model or TTS_MODEL},
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

    # TTS öncesi temizlik: yalnız SESE giden metin değişir. Dosya adı (anahtar)
    # cevap-cache anahtarından gelir; bu temizlik hash'i ETKİLEMEZ.
    konusma = konusma_metnine_cevir(text)
    audio = synthesize(konusma)
    if audio is None:                            # graceful: ses yok, cevap kalır
        return {"ses_url": None, "tts_usd": 0.0, "tts_called": False, "cached": False}

    try:
        path.write_bytes(audio)
    except OSError as e:
        logger.warning("MP3 yazılamadı: %s", e)
        return {"ses_url": None, "tts_usd": 0.0, "tts_called": True, "cached": False}

    _enforce_lru()
    return {"ses_url": f"/audio/{anahtar}.mp3", "tts_usd": tts_cost(konusma),
            "tts_called": True, "cached": False}


def voice_audio(voice_id: str, text: str) -> dict:
    """Belirli bir (klonlanmış) voice_id ile metni seslendir + cache'le.

    Cache anahtarı = sha256(voice_id || temizlenmiş_metin) → aynı ses+metin ikinci
    kez TTS'e gitmez. Farklı voice_id aynı metinde ayrı dosya (çakışma yok).
    Döner: {audio_url: str|None, cached: bool, tts_usd: float}.

    Depolama kararı: mevcut data/audio_cache + /audio route yeniden kullanılır
    (LRU'lu). Railway'de kalıcılık için bu klasöre volume mount edilebilir; ses
    metinden ucuza yeniden üretilebildiğinden efemer disk de kabul edilebilir."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    konusma = konusma_metnine_cevir(text)                # mevcut TTS metin temizliği
    anahtar = hashlib.sha256(f"{voice_id}||{konusma}".encode("utf-8")).hexdigest()
    path = audio_path(anahtar)

    if path.exists():
        return {"audio_url": f"/audio/{anahtar}.mp3", "cached": True, "tts_usd": 0.0}

    audio = synthesize(konusma, voice_id=voice_id)
    if audio is None:
        return {"audio_url": None, "cached": False, "tts_usd": 0.0}
    try:
        path.write_bytes(audio)
    except OSError as e:
        logger.warning("Voice MP3 yazılamadı: %s", e)
        return {"audio_url": None, "cached": False, "tts_usd": 0.0}
    _enforce_lru()
    return {"audio_url": f"/audio/{anahtar}.mp3", "cached": False,
            "tts_usd": tts_cost(konusma)}


def is_safe_name(name: str) -> bool:
    """/audio/{dosya} için: yalnız hash.mp3 kalıbı (path traversal engeli)."""
    if not name.endswith(".mp3"):
        return False
    return bool(_SAFE_NAME.match(name[:-4]))
