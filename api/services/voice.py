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

ELEVENLABS_VOICES_ADD = "https://api.elevenlabs.io/v1/voices/add"
CLONE_TIMEOUT_S = 60


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
        return {"ok": False, "error": f"ElevenLabs'e ulaşılamadı: {e}", "status": 502}

    if not resp.ok:
        detail = _safe_error(resp)
        logger.warning("Voice clone HTTP %s: %s", resp.status_code, detail)
        return {"ok": False, "error": detail, "status": 502}

    try:
        voice_id = resp.json().get("voice_id")
    except ValueError:
        return {"ok": False, "error": "ElevenLabs geçersiz JSON", "status": 502}
    if not voice_id:
        return {"ok": False, "error": "voice_id alınamadı", "status": 502}
    return {"ok": True, "voice_id": voice_id}


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
