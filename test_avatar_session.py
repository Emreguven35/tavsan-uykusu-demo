"""
/avatar-session endpoint testleri (FastAPI TestClient). LiveAvatar API MOCK'lanır
(requests.post) -> deterministik, ÜCRETSİZ, kredi tüketmez, ağ gerektirmez.

Senaryolar:
  1. Sandbox default (LIVEAVATAR_SANDBOX tanımsız) -> is_sandbox=true, avatar_id=Wayne
  2. Başarı -> session_token + session_id + avatar_id + mode=LITE döner
  3. API key YOK -> 500, anlamlı JSON hata (ham çökme yok)
  4. Upstream kota/hata (HTTP 402) -> 502, anlamlı mesaj, API KEY SIZMAZ
  5. Sandbox=false + avatar id YOK -> 500 yapılandırma hatası
  6. Sandbox=false + avatar id VAR -> gerçek avatar id kullanılır, is_sandbox=false
  7. Gizli API key upstream'e X-API-KEY header'ında gider, response'a ASLA konmaz
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# LLM/embedding'i mock modda tut (import sırasında ağ/anahtar gerekmesin).
os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
# Faz 5R: JWT_SECRET zorunlu + /avatar-session X-API-Key (DEMO_API_KEY) ister.
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
DEMO_KEY = "test-demo-key"
os.environ.setdefault("DEMO_API_KEY", DEMO_KEY)
DEMO_HDR = {"X-API-Key": DEMO_KEY}

from fastapi.testclient import TestClient  # noqa: E402
from api import avatar                      # noqa: E402
from api.main import app                    # noqa: E402

SECRET_KEY = "sk-super-secret-DO-NOT-LEAK"
WAYNE = avatar.SANDBOX_AVATAR_ID


# --- MOCK: LiveAvatar upstream (requests.post) ------------------------------
class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


_LAST = {"url": None, "headers": None, "json": None}


def make_fake_post(status_code, payload):
    def _post(url, headers=None, json=None, timeout=None):
        _LAST.update({"url": url, "headers": headers or {}, "json": json or {}})
        return FakeResp(status_code, payload)
    return _post


def _reset_env(sandbox=None, key=SECRET_KEY, avatar_id=None):
    for k in ("LIVEAVATAR_SANDBOX", "LIVEAVATAR_API_KEY", "LIVEAVATAR_AVATAR_ID"):
        os.environ.pop(k, None)
    if sandbox is not None:
        os.environ["LIVEAVATAR_SANDBOX"] = sandbox
    if key is not None:
        os.environ["LIVEAVATAR_API_KEY"] = key
    if avatar_id is not None:
        os.environ["LIVEAVATAR_AVATAR_ID"] = avatar_id


def main():
    client = TestClient(app)
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    OK_BODY = {"code": 100, "data": {"session_id": "sess-123",
                                     "session_token": "tok-abc"}, "message": "ok"}

    # 1) Sandbox default (SANDBOX tanımsız) -> is_sandbox true, Wayne avatarı
    _reset_env(sandbox=None, avatar_id="ilayda-real-id")
    avatar.requests.post = make_fake_post(200, OK_BODY)
    r1 = client.post("/avatar-session", headers=DEMO_HDR)
    j1 = r1.json()
    check("1) Sandbox default -> is_sandbox=true + Wayne avatar",
          r1.status_code == 200 and j1["is_sandbox"] is True
          and j1["avatar_id"] == WAYNE and _LAST["json"]["is_sandbox"] is True
          and _LAST["json"]["avatar_id"] == WAYNE and _LAST["json"]["mode"] == "LITE",
          f"status={r1.status_code} body={j1}")

    # 2) Başarı -> token + meta alanları
    check("2) session_token + session_id + mode=LITE döner",
          j1.get("session_token") == "tok-abc" and j1.get("session_id") == "sess-123"
          and j1.get("mode") == "LITE",
          f"body={j1}")

    # 3) API key yok -> 500 anlamlı hata (çökme yok)
    _reset_env(sandbox="true", key=None)
    avatar.requests.post = make_fake_post(200, OK_BODY)
    r3 = client.post("/avatar-session", headers=DEMO_HDR)
    check("3) API key yok -> 500 + anlamlı JSON hata",
          r3.status_code == 500 and "LIVEAVATAR_API_KEY" in r3.json().get("detail", ""),
          f"status={r3.status_code} detail={r3.json().get('detail')}")

    # 4) Upstream kota/hata (402) -> 502 + anlamlı mesaj + API KEY SIZMAZ
    _reset_env(sandbox="true")
    avatar.requests.post = make_fake_post(402, {"message": "Insufficient credits"})
    r4 = client.post("/avatar-session", headers=DEMO_HDR)
    detail4 = r4.json().get("detail", "")
    check("4) Upstream kota -> 502 + mesaj + key sızmaz",
          r4.status_code == 502 and "credit" in detail4.lower()
          and SECRET_KEY not in detail4,
          f"status={r4.status_code} detail={detail4}")

    # 5) Sandbox kapalı + avatar id yok -> 500 yapılandırma
    _reset_env(sandbox="false", avatar_id=None)
    avatar.requests.post = make_fake_post(200, OK_BODY)
    r5 = client.post("/avatar-session", headers=DEMO_HDR)
    check("5) Sandbox=false + avatar id yok -> 500",
          r5.status_code == 500 and "LIVEAVATAR_AVATAR_ID" in r5.json().get("detail", ""),
          f"status={r5.status_code} detail={r5.json().get('detail')}")

    # 6) Sandbox kapalı + avatar id var -> gerçek avatar, is_sandbox=false
    _reset_env(sandbox="false", avatar_id="ilayda-real-id")
    avatar.requests.post = make_fake_post(200, OK_BODY)
    r6 = client.post("/avatar-session", headers=DEMO_HDR)
    j6 = r6.json()
    check("6) Sandbox=false + avatar id -> gerçek avatar, is_sandbox=false",
          r6.status_code == 200 and j6["is_sandbox"] is False
          and j6["avatar_id"] == "ilayda-real-id"
          and _LAST["json"]["avatar_id"] == "ilayda-real-id"
          and _LAST["json"]["is_sandbox"] is False,
          f"status={r6.status_code} body={j6}")

    # 7) Gizli key upstream header'ında; response'a ASLA konmaz
    key_in_header = _LAST["headers"].get("X-API-KEY") == SECRET_KEY
    key_absent_in_body = SECRET_KEY not in r6.text
    check("7) API key upstream X-API-KEY header'ında, response'ta yok",
          key_in_header and key_absent_in_body,
          f"header_ok={key_in_header} body_temiz={key_absent_in_body}")

    # --- özet ---
    print("\n" + "=" * 74)
    print("AVATAR SESSION TEST SONUÇLARI")
    print("=" * 74)
    passed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"[{mark}] {name}\n       {detail}")
    print("-" * 74)
    print(f"TOPLAM: {passed}/{len(results)} gecti")
    print("=" * 74)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
