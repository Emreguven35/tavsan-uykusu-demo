"""
LiveAvatar (HeyGen) — LITE mode oturum token'ı üretimi.

Bu modül SADECE görüntü katmanı içindir: LLM/RAG/TTS boru hattımız (engine.chatbot
+ api.tts) AYNEN kalır. Buradaki tek iş, LiveAvatar REST API'sine gizli API key ile
gidip kısa ömürlü bir LITE session token üretmek ve frontend'e dönmektir.

GÜVENLİK: LIVEAVATAR_API_KEY ASLA frontend'e / log'a / hata mesajına yazılmaz.
Frontend'e yalnızca kısa ömürlü session_token gider (Web SDK bununla /sessions/start
çağrısını kendisi yapar).

KREDİ DİSİPLİNİ: LIVEAVATAR_SANDBOX (default "true") → is_sandbox=true. Sandbox
oturumları KREDİ TÜKETMEZ; yalnız "Wayne" sandbox avatarı (sabit ID) kullanılabilir
ve oturum ~1 dk sonra sunucu tarafında otomatik kapanır. Gerçek İlayda avatarıyla
canlı test için: LIVEAVATAR_SANDBOX=false + LIVEAVATAR_AVATAR_ID=<İlayda avatar id>.

Hata (key yok / kota bitti / ağ) → çökmez; {"ok": False, "error", "status"} döner.
Endpoint bunu anlamlı JSON + uygun HTTP kodla iletir (500 ile ham çökme YOK).
"""
import os
import logging

import requests

logger = logging.getLogger("tavsan.avatar")

# LiveAvatar REST tabanı (override edilebilir; testte kullanılmaz — post mock'lanır).
LIVEAVATAR_BASE = os.getenv("LIVEAVATAR_API_URL", "https://api.liveavatar.com").rstrip("/")
TOKEN_PATH = "/v1/sessions/token"
REQ_TIMEOUT_S = 20

# Sandbox (kredi yakmayan) test avatarı. Docs: sandbox'ta YALNIZ bu avatar geçerli
# ve oturum ~1 dk sonra otomatik sonlanır. Kaynak: docs.liveavatar.com/docs/sandbox-mode
SANDBOX_AVATAR_ID = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"  # "Wayne"


def is_sandbox() -> bool:
    """LIVEAVATAR_SANDBOX yorumu. Tanımsız → True (güvenli/ücretsiz varsayılan)."""
    return os.getenv("LIVEAVATAR_SANDBOX", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def _extract_error(resp) -> str:
    """Upstream hata gövdesinden anlamlı mesaj çıkar (API key sızdırmadan)."""
    try:
        j = resp.json()
    except ValueError:
        return f"LiveAvatar HTTP {resp.status_code}"
    if isinstance(j, dict):
        if j.get("message"):
            return str(j["message"])
        d = j.get("data")
        if isinstance(d, list) and d and isinstance(d[0], dict) and d[0].get("message"):
            return str(d[0]["message"])
    return f"LiveAvatar HTTP {resp.status_code}"


def create_session_token() -> dict:
    """LITE mode session token üret.

    Döner:
      başarı → {"ok": True, "session_token", "session_id", "avatar_id",
                "is_sandbox", "mode"}
      hata   → {"ok": False, "error": "<insan-okur mesaj>", "status": <int>}
                status: 500 = yapılandırma (key/avatar yok), 502 = upstream/ağ/kota.
    """
    api_key = os.getenv("LIVEAVATAR_API_KEY")
    if not api_key:
        return {"ok": False,
                "error": "LIVEAVATAR_API_KEY tanımlı değil (.env'e ekleyin)",
                "status": 500}

    sandbox = is_sandbox()
    # Sandbox'ta yalnız Wayne avatarı geçerli; aksi halde env'deki (İlayda) avatar id.
    if sandbox:
        avatar_id = SANDBOX_AVATAR_ID
    else:
        avatar_id = os.getenv("LIVEAVATAR_AVATAR_ID", "").strip()
        if not avatar_id:
            return {"ok": False,
                    "error": "LIVEAVATAR_AVATAR_ID tanımlı değil "
                             "(LIVEAVATAR_SANDBOX=false iken zorunlu)",
                    "status": 500}

    payload = {"mode": "LITE", "avatar_id": avatar_id, "is_sandbox": sandbox}
    try:
        resp = requests.post(
            f"{LIVEAVATAR_BASE}{TOKEN_PATH}",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=REQ_TIMEOUT_S,
        )
    except requests.RequestException as e:
        logger.warning("LiveAvatar token isteği ağ hatası: %s", e)
        return {"ok": False, "error": f"LiveAvatar API'ye ulaşılamadı: {e}",
                "status": 502}

    if not resp.ok:
        detail = _extract_error(resp)               # API key gövdede değil → güvenli
        logger.warning("LiveAvatar token HTTP %s: %s", resp.status_code, detail)
        return {"ok": False, "error": detail, "status": 502}

    try:
        data = resp.json().get("data") or {}
    except ValueError:
        return {"ok": False, "error": "LiveAvatar geçersiz JSON döndürdü",
                "status": 502}

    token = data.get("session_token")
    if not token:
        return {"ok": False, "error": "session_token alınamadı", "status": 502}

    return {
        "ok": True,
        "session_token": token,
        "session_id": data.get("session_id"),
        "avatar_id": avatar_id,
        "is_sandbox": sandbox,
        "mode": "LITE",
    }
