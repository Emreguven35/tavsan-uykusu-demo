"""
Ses (voice) servis katmanı — ElevenLabs voice clone + masal/ninni kataloğu.

GÜVENLİK: ELEVENLABS_API_KEY yalnız burada okunur, asla yanıta/loga yazılmaz.
Hata (key yok / kota / ağ) → çökme yok; {"ok": False, "error", "status"} döner.
"""
import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger("tavsan.voice")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
STORIES_PATH = DATA_DIR / "stories.json"

ELEVENLABS_VOICES = "https://api.elevenlabs.io/v1/voices"
ELEVENLABS_VOICES_ADD = "https://api.elevenlabs.io/v1/voices/add"
ELEVENLABS_VOICE = "https://api.elevenlabs.io/v1/voices/{voice_id}"
CLONE_TIMEOUT_S = 60
DELETE_TIMEOUT_S = 20
LIST_TIMEOUT_S = 20

# --- Hata sınıfları ----------------------------------------------------------
# Eskiden upstream'in HER hatası tek tip 502'ye düşüyordu. 2026-09-16..18 arası
# POST /voice/clone'un 5 başarısızlığının TAMAMI ElevenLabs'in
# "maximum amount of custom voices (10 / 10)" yanıtıydı; bizim tarafta ise
# ayırt edilemeyen bir 502 görünüyordu — ne kurtarma denenebiliyor ne de
# kullanıcıya doğru şey söylenebiliyordu. Sınıf artık çağırana taşınıyor.
HATA_SLOT_DOLU = "slot_dolu"        # hesap genelindeki klon slotu tükendi
HATA_SES_GECERSIZ = "ses_gecersiz"  # kayıt işlenemedi (gürültü/format/süre)
HATA_YETKI = "yetki"                # anahtar/plan sorunu — kullanıcının suçu değil
HATA_AG = "ag"                      # ağ/zaman aşımı
HATA_BILINMEYEN = "bilinmeyen"


def load_stories() -> dict:
    """Masal/ninni kataloğu (5 masal + 3 ninni). Metinler seslendirme için kullanılır."""
    return json.loads(STORIES_PATH.read_text(encoding="utf-8"))


def find_story(story_id: str) -> dict | None:
    cat = load_stories()
    for item in cat.get("masallar", []) + cat.get("ninniler", []):
        if item["id"] == story_id:
            return item
    return None


def clone_voice(name: str, audio_bytes: bytes, filename: str,
                content_type: str) -> dict:
    """ElevenLabs Instant Voice Clone. 30 sn'lik örnek → voice_id.

    Döner: başarı → {"ok": True, "voice_id": "..."}
           hata   → {"ok": False, "error": "<mesaj>", "status": <int>}
    """
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return {"ok": False, "error": "ELEVENLABS_API_KEY tanımlı değil", "status": 503}

    try:
        resp = requests.post(
            ELEVENLABS_VOICES_ADD,
            headers={"xi-api-key": key},
            data={"name": name},
            files={"files": (filename or "sample.mp3", audio_bytes,
                             content_type or "audio/mpeg")},
            timeout=CLONE_TIMEOUT_S,
        )
    except requests.RequestException as e:
        logger.warning("Voice clone ağ hatası: %s", e)
        return {"ok": False, "error": f"ElevenLabs'e ulaşılamadı: {e}",
                "status": 502, "reason": HATA_AG}

    if not resp.ok:
        detail = _safe_error(resp)
        reason = _hata_turu(resp.status_code, detail)
        logger.warning("Voice clone HTTP %s (%s): %s",
                       resp.status_code, reason, detail)
        return {"ok": False, "error": detail,
                "status": _durum_kodu(reason), "reason": reason}

    try:
        voice_id = resp.json().get("voice_id")
    except ValueError:
        return {"ok": False, "error": "ElevenLabs geçersiz JSON", "status": 502,
                "reason": HATA_BILINMEYEN}
    if not voice_id:
        return {"ok": False, "error": "voice_id alınamadı", "status": 502,
                "reason": HATA_BILINMEYEN}
    return {"ok": True, "voice_id": voice_id}


def delete_voice(voice_id: str) -> dict:
    """ElevenLabs'teki bir klon sesi SİL (slot + ücret + biyometrik veri birikmesin).

    BEST-EFFORT, ASLA YÜKSELTMEZ: kullanıcının YENİ sesi başarıyla oluşturulduktan
    SONRA çağrılır. Silme başarısız olursa yeni klon geçerli kalmalıdır —
    temizlik hatası yüzünden kullanıcının az önce kaydettiği ses çöpe gitmez.
    Çağıran sonucu loglar, akışını değiştirmez.

    Döner: {"ok": bool, "error": str | None, "status": int | None}
    404 = ses zaten yok → BAŞARI sayılır (idempotent temizlik)."""
    if not voice_id:
        return {"ok": False, "error": "voice_id boş", "status": None}
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return {"ok": False, "error": "ELEVENLABS_API_KEY tanımlı değil", "status": 503}

    try:
        resp = requests.delete(
            ELEVENLABS_VOICE.format(voice_id=voice_id),
            headers={"xi-api-key": key},
            timeout=DELETE_TIMEOUT_S,
        )
    except requests.RequestException as e:
        logger.warning("Voice delete ağ hatası (voice_id=%s): %s", voice_id, e)
        return {"ok": False, "error": str(e), "status": 502}

    if resp.status_code == 404:
        # Zaten silinmiş: temizliğin amacı gerçekleşmiş durumda.
        logger.info("Voice zaten yok (404), temiz sayıldı: %s", voice_id)
        return {"ok": True, "error": None, "status": 404}
    if not resp.ok:
        detail = _safe_error(resp)
        logger.warning("Voice delete HTTP %s (voice_id=%s): %s",
                       resp.status_code, voice_id, detail)
        return {"ok": False, "error": detail, "status": resp.status_code}
    return {"ok": True, "error": None, "status": resp.status_code}


def _safe_error(resp) -> str:
    """Upstream hata gövdesinden anlamlı mesaj (API key sızdırmadan)."""
    try:
        j = resp.json()
    except ValueError:
        return f"ElevenLabs HTTP {resp.status_code}"
    if isinstance(j, dict):
        d = j.get("detail")
        if isinstance(d, dict) and d.get("message"):
            return str(d["message"])
        if isinstance(d, str):
            return d
    return f"ElevenLabs HTTP {resp.status_code}"
def _hata_turu(status_code: int, mesaj: str) -> str:
    """Upstream yanıtını bizim için ANLAMLI bir sınıfa indirge.

    ElevenLabs slot tavanını HTTP 400 ile bildiriyor — yani "istemci hatası"
    gibi görünen bir yanıt aslında HESAP KAPASİTESİ sorunu. Mesaja bakmadan
    ayırt etmek mümkün değil; metin eşleşmesi bu yüzden bilinçli."""
    m = (mesaj or "").lower()
    if "maximum amount of custom voices" in m or "voice_limit" in m:
        return HATA_SLOT_DOLU
    if status_code in (401, 403):
        return HATA_YETKI
    # Kaydın kendisi işlenemedi: süre/format/gürültü. 400 + "audio/file/duration"
    # ya da doğrudan 422.
    if status_code == 422 or (status_code == 400 and any(
            k in m for k in ("audio", "file", "duration", "too short", "sample"))):
        return HATA_SES_GECERSIZ
    return HATA_BILINMEYEN


def _durum_kodu(reason: str) -> int:
    """Hata sınıfı → dışarı verilecek HTTP kodu.

    Slot tavanı ve anahtar/plan sorunu 503'tür: kullanıcı yanlış bir şey
    yapmadı, servis şu an veremiyor. Eskiden ikisi de 502 dönüyordu ve
    "geçici mi, bizden mi" ayrımı kaybolduğu için izlemede de görünmüyordu."""
    return {
        HATA_SLOT_DOLU: 503,
        HATA_YETKI: 503,
        HATA_SES_GECERSIZ: 422,
        HATA_AG: 502,
    }.get(reason, 502)


def list_cloned_voices() -> dict:
    """Hesaptaki KLON sesleri listele (premade sesler hariç).

    Slot tavanı HESAP GENELİNDEdir; kullanıcı başına aylık limitle ilgisi yoktur.
    Tavana çarpıldığında hangi slotun sahipsiz kaldığını ancak bu liste ile
    DB'yi karşılaştırarak anlayabiliyoruz.

    Döner: {"ok": True, "voices": [{"voice_id", "name"}]} | {"ok": False, ...}"""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return {"ok": False, "error": "ELEVENLABS_API_KEY tanımlı değil",
                "status": 503, "voices": []}
    try:
        resp = requests.get(ELEVENLABS_VOICES, headers={"xi-api-key": key},
                            timeout=LIST_TIMEOUT_S)
    except requests.RequestException as e:
        logger.warning("Voice list ağ hatası: %s", e)
        return {"ok": False, "error": str(e), "status": 502, "voices": []}
    if not resp.ok:
        return {"ok": False, "error": _safe_error(resp),
                "status": resp.status_code, "voices": []}
    try:
        veri = resp.json().get("voices", [])
    except ValueError:
        return {"ok": False, "error": "ElevenLabs geçersiz JSON", "status": 502,
                "voices": []}
    return {"ok": True, "voices": [
        {"voice_id": v.get("voice_id"), "name": v.get("name")}
        for v in veri if v.get("category") == "cloned" and v.get("voice_id")]}
