"""
ElevenLabs TTS + ses cache.

- Türkçe metin → MP3 (TTS_MODEL = eleven_flash_v2_5), doğrudan REST (requests) ile.
- Ses cache: MP3'ler data/audio_cache/ altında, dosya adı = CEVAP CACHE'İYLE AYNI
  hash (chatbot._cevap_uret'in döndürdüğü 'anahtar'). Böylece cevap cache HIT olunca
  aynı hash'li MP3 varsa TTS'e HİÇ gidilmez.
- TTS hatası (kota/ağ/anahtar yok) → None döner; endpoint çökmez, ses_url=null olur.
- LRU: en fazla AUDIO_MAX_FILES dosya veya AUDIO_MAX_BYTES; eskiden (mtime) siler.
- İKİ SES PROFİLİ (bkz. SES_PROFILLERI): 'masal' yavaş/sakin anlatım (masal ve
  ninniler), 'sohbet' normal hız (chat TTS'i). Profil adı voice cache anahtarına
  girer, böylece ayar değişince eski hızlı kayıt sunulmaya devam etmez.
"""
import os
import re
import time
import hashlib
import logging
from pathlib import Path

import requests

from api.konusma_metni import (                      # TTS öncesi metin işleme
    konusma_metnine_cevir, masal_metni_hazirla,
)

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

# ---------------------------------------------------------------------------
# SES PROFİLLERİ — ElevenLabs voice_settings
# ---------------------------------------------------------------------------
# İki profil: masal/ninni anlatımı yavaş ve sakin olmalı, sohbet TTS'i (ileride
# açılırsa) normal hızda kalmalı. Profil adı cache anahtarına girer.
#
# ÖLÇÜM (flash v2.5, canlı anahtarla, 2026-08-25) — varsayılmadı, denendi:
#   speed 1.0 → 0.85 : aynı cümle %20 uzadı (3,22 sn → 3,87 sn)
#   <break time="2.0s" /> : +2,25 sn
# Yani Flash v2.5 ikisini de uyguluyor; ayrı/pahalı bir modele geçmek gerekmedi.
#
# speed'in geçerli aralığı 0.7-1.2'dir (ElevenLabs); dışına çıkan değer API'de
# hata verir, bu yüzden profil doğrulaması aralığı da kontrol eder.
SES_PROFILLERI: dict[str, dict] = {
    # Masal/ninni: yavaş, tutarlı, abartısız tonlama.
    "masal": {
        "stability": 0.70,          # 0.65-0.75 bandının ortası: dalgalanmayan ton
        "similarity_boost": 0.75,
        "style": 0.05,              # düşük: abartılı tonlama yok
        "use_speaker_boost": True,
        "speed": 0.85,
    },
    # Sohbet: ElevenLabs varsayılanlarıyla aynı → mevcut /ask sesi DEĞİŞMEZ,
    # dolayısıyla eski chat MP3 cache'i geçersizleşmez.
    "sohbet": {
        "stability": 0.50,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": True,
        "speed": 1.0,
    },
}
VARSAYILAN_PROFIL = "sohbet"
MASAL_PROFILI = "masal"

# Profil değerleri değişince ESKİ cache'lenmiş MP3'ler yanlıştır (hızlı okunmuş
# masal sunulmaya devam eder). Bu damga voice cache anahtarına girer; kalibrasyon
# sonrası ayar değiştirilirse BURASI DA artırılmalı.
#
# masal-v2 (2026-08-25): kalibrasyon dinlemesinde "B" onaylandı — yani yukarıdaki
# değerler DEĞİŞMEDİ. Damga yine de artırıldı: üretimde bu profilden önce üretilmiş
# ne varsa kesin olarak tazelensin (anahtar biçimi zaten değiştiği için eski
# kayıtlar erişilemez durumdaydı; bu ikinci emniyet kemeri).
SES_AYAR_SURUMU = "masal-v2"

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


def profil_ayarlari(profil: str | None) -> dict:
    """Profil adını voice_settings sözlüğüne çevir. Bilinmeyen ad → varsayılan."""
    if profil not in SES_PROFILLERI:
        if profil:
            logger.warning("Bilinmeyen ses profili %r — %s kullanılıyor.",
                           profil, VARSAYILAN_PROFIL)
        profil = VARSAYILAN_PROFIL
    return dict(SES_PROFILLERI[profil])


def synthesize(text: str, model: str | None = None,
               voice_id: str | None = None,
               profil: str = VARSAYILAN_PROFIL,
               usage_op: str | None = None,
               user_id=None) -> bytes | None:
    """Metni MP3 byte'larına çevir. Anahtar yok / hata → None (endpoint çökmesin).
    model verilmezse merkezi TTS_MODEL (flash v2.5); voice_id verilmezse env
    ELEVENLABS_VOICE_ID (klonlanmış kullanıcı sesi için voice_id geçilir).
    profil: 'masal' (yavaş anlatım) veya 'sohbet' (normal hız).
    usage_op verilirse BAŞARILI çağrı api_usage'a kaydedilir; metnin kendisi değil
    yalnız UZUNLUĞU yazılır (KVKK). Hata yolunda kayıt açılmaz — ücretlendirilmeyen
    çağrıyı maliyet defterine yazmayalım."""
    key = os.getenv("ELEVENLABS_API_KEY")
    voice = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        logger.info("TTS atlandı: ELEVENLABS_API_KEY/VOICE_ID tanımlı değil.")
        return None
    _t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{ELEVENLABS_BASE}/{voice}",
            headers={"xi-api-key": key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json={"text": text, "model_id": model or TTS_MODEL,
                  "voice_settings": profil_ayarlari(profil)},
            timeout=TTS_TIMEOUT_S,
        )
        r.raise_for_status()
        if not r.content:
            logger.warning("TTS boş içerik döndü.")
            return None
        if usage_op:
            from api.services import usage as _usage    # döngüsel import önleme
            _usage.kaydet(_usage.SERVIS_ELEVENLABS, usage_op,
                          model=model or TTS_MODEL, characters=len(text),
                          user_id=user_id,
                          duration_ms=int((time.perf_counter() - _t0) * 1000))
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
        return {"ses_url": audio_url(anahtar), "tts_usd": 0.0,
                "tts_called": False, "cached": True}

    # TTS öncesi temizlik: yalnız SESE giden metin değişir. Dosya adı (anahtar)
    # cevap-cache anahtarından gelir; bu temizlik hash'i ETKİLEMEZ.
    konusma = konusma_metnine_cevir(text)
    from api.services import usage as _usage                 # döngüsel import önleme
    audio = synthesize(konusma, profil=VARSAYILAN_PROFIL,    # chat: normal hız
                       usage_op=_usage.OP_TTS)
    if audio is None:                            # graceful: ses yok, cevap kalır
        return {"ses_url": None, "tts_usd": 0.0, "tts_called": False, "cached": False}

    try:
        path.write_bytes(audio)
    except OSError as e:
        logger.warning("MP3 yazılamadı: %s", e)
        return {"ses_url": None, "tts_usd": 0.0, "tts_called": True, "cached": False}

    _enforce_lru()
    return {"ses_url": audio_url(anahtar), "tts_usd": tts_cost(konusma),
            "tts_called": True, "cached": False}


def voice_audio(voice_id: str, text: str,
                profil: str = MASAL_PROFILI, user_id=None) -> dict:
    """Belirli bir (klonlanmış) voice_id ile metni seslendir + cache'le.

    Cache anahtarı = sha256(voice_id || profil || ayar_sürümü || hazır_metin) →
    aynı ses+metin+ayar ikinci kez TTS'e gitmez. Profil ve ayar sürümü anahtara
    GİRER: kalibrasyonla ayar değişince eski (hızlı okunmuş) MP3 sunulmaya devam
    etmez, yeni dosya üretilir. Farklı voice_id aynı metinde ayrı dosya.
    Döner: {audio_url: str|None, cached: bool, tts_usd: float}.

    Masal profilinde metin `masal_metni_hazirla` ile paragraf/cümle duraklamalı
    hazırlanır; sohbet profilinde düz temizlik uygulanır.

    Depolama kararı: mevcut data/audio_cache + /audio route yeniden kullanılır
    (LRU'lu). Railway'de kalıcılık için bu klasöre volume mount edilebilir; ses
    metinden ucuza yeniden üretilebildiğinden efemer disk de kabul edilebilir."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    konusma = (masal_metni_hazirla(text) if profil == MASAL_PROFILI
               else konusma_metnine_cevir(text))
    anahtar = hashlib.sha256(
        f"{voice_id}||{profil}||{SES_AYAR_SURUMU}||{konusma}".encode("utf-8")
    ).hexdigest()
    path = audio_path(anahtar)

    if path.exists():
        return {"audio_url": audio_url(anahtar), "cached": True, "tts_usd": 0.0}

    from api.services import usage as _usage                 # döngüsel import önleme
    audio = synthesize(konusma, voice_id=voice_id, profil=profil,
                       usage_op=_usage.OP_TTS, user_id=user_id)
    if audio is None:
        return {"audio_url": None, "cached": False, "tts_usd": 0.0}
    try:
        path.write_bytes(audio)
    except OSError as e:
        logger.warning("Voice MP3 yazılamadı: %s", e)
        return {"audio_url": None, "cached": False, "tts_usd": 0.0}
    _enforce_lru()
    return {"audio_url": audio_url(anahtar), "cached": False,
            "tts_usd": tts_cost(konusma)}


def audio_url(anahtar: str) -> str:
    """Ses dosyasının istemciye verilecek adresi.

    PUBLIC_BASE_URL tanımlıysa MUTLAK URL döner
    (https://tavsan-api-production.up.railway.app/audio/<hash>.mp3) — mobilin
    göreli path'i yanlış tabanla birleştirme riski kökten kalkar.
    Tanımsızsa göreli path (/audio/<hash>.mp3) — lokal geliştirme davranışı."""
    from api.config import get_settings          # döngüsel import önleme
    base = get_settings().public_base_url
    yol = f"/audio/{anahtar}.mp3"
    return f"{base}{yol}" if base else yol


def is_safe_name(name: str) -> bool:
    """/audio/{dosya} için: yalnız hash.mp3 kalıbı (path traversal engeli)."""
    if not name.endswith(".mp3"):
        return False
    return bool(_SAFE_NAME.match(name[:-4]))
