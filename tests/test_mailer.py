"""
E-posta servisi testleri (Faz 6.3) — Resend HTTP çağrısı MOCK'lanır, ağ YOK.

Kapsam:
  1. RESEND_API_KEY yoksa console fallback (ok=True, akış kırılmaz)
  2. Anahtar varsa Resend'e doğru payload gider (from/to/subject/text/html)
  3. Authorization başlığı Bearer <key>
  4. Resend HTTP hatası → ok=False ama EXCEPTION YOK
  5. Ağ hatası (exception) → ok=False ama EXCEPTION YOK
  6. Sıfırlama e-postası: derin bağlantı + düz metin token, 6 haneli kod YOK
  7. Hoş geldin: console modunda gönderilmez (skipped)
  8. reset-password-request endpoint'i e-postayı tetikler ve token'ı YANITTA DÖNMEZ

Çalıştırma: python tests/test_mailer.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "faz63_mailer_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ.pop("RESEND_API_KEY", None)         # önce console modu
os.environ.pop("MAIL_PROVIDER", None)

from api.config import get_settings            # noqa: E402
from api.services import mailer                # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --- requests.post MOCK ------------------------------------------------------
CALLS: list[dict] = []
MODE = {"kind": "ok"}                          # ok | http_error | raise


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {"id": "resend-msg-1"}
        self.text = text or "{}"

    def json(self):
        return self._payload


def fake_post(url, json=None, timeout=None, headers=None):
    CALLS.append({"url": url, "json": json, "headers": headers})
    if MODE["kind"] == "raise":
        raise ConnectionError("ağ yok")
    if MODE["kind"] == "http_error":
        return FakeResp(422, text='{"message":"domain not verified"}')
    return FakeResp()


mailer.requests.post = fake_post                # ağ ÇAĞRILMAZ


def reload_settings(**env):
    """Settings lru_cache'li — env değişince temizle."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


# =============================================================================
# 1) Console fallback
# =============================================================================
reload_settings(RESEND_API_KEY=None, MAIL_PROVIDER=None)
CALLS.clear()
r = mailer.send_email("a@b.com", "Konu", "Gövde")
check("1) Anahtar yok → console fallback, ok=True, HTTP çağrısı YOK",
      r["ok"] is True and r["provider"] == "console" and not CALLS, f"{r} calls={len(CALLS)}")

# =============================================================================
# 2-3) Resend payload + başlık
# =============================================================================
reload_settings(RESEND_API_KEY="re_test_key_123",
                MAIL_FROM="Tavşan Uykusu <no-reply@tavsanuykusu.app>")
MODE["kind"] = "ok"
CALLS.clear()
r = mailer.send_email("anne@example.com", "Konu", "Düz metin", html="<p>HTML</p>")
_c = CALLS[0] if CALLS else {}
_p = _c.get("json", {})
check("2) Resend'e doğru payload gider",
      r["ok"] is True and r["provider"] == "resend" and r["id"] == "resend-msg-1"
      and _p.get("to") == ["anne@example.com"] and _p.get("subject") == "Konu"
      and _p.get("text") == "Düz metin" and _p.get("html") == "<p>HTML</p>"
      and "tavsanuykusu.app" in _p.get("from", ""),
      f"r={r} payload={_p}")
check("3) Authorization: Bearer <key>",
      (_c.get("headers") or {}).get("Authorization") == "Bearer re_test_key_123",
      str(_c.get("headers")))

# =============================================================================
# 4-5) Hata yolları — exception YOK
# =============================================================================
MODE["kind"] = "http_error"
try:
    r = mailer.send_email("a@b.com", "K", "G")
    _crash = False
except Exception as e:
    _crash, r = True, str(e)
check("4) Resend HTTP hatası → ok=False, exception YOK",
      not _crash and r["ok"] is False and "422" in str(r.get("error")), str(r))

MODE["kind"] = "raise"
try:
    r = mailer.send_email("a@b.com", "K", "G")
    _crash = False
except Exception as e:
    _crash, r = True, str(e)
check("5) Ağ hatası → ok=False, exception YOK",
      not _crash and r["ok"] is False, str(r))

# =============================================================================
# 6) Sıfırlama e-postası içeriği
# =============================================================================
MODE["kind"] = "ok"
CALLS.clear()
mailer.send_password_reset("anne@example.com", "TOKEN_ABC123")
_p = CALLS[0]["json"]
_text = _p["text"]
check("6) Konu doğru",
      _p["subject"] == "Tavşan Uykusu — Şifre Sıfırlama", _p["subject"])
check("6b) Derin bağlantı içerir (tavsan-uykusu://reset-password?token=)",
      "tavsan-uykusu://reset-password?token=TOKEN_ABC123" in _text, _text[:200])
check("6c) Düz metin token da var (elle girme alternatifi)",
      "TOKEN_ABC123" in _text.split("elle girebilirsiniz")[-1], _text[-200:])
check("6d) HTML sürümünde de bağlantı var",
      "tavsan-uykusu://reset-password?token=TOKEN_ABC123" in _p["html"], _p["html"][:200])

# =============================================================================
# 7) Hoş geldin — console modunda gönderilmez
# =============================================================================
reload_settings(RESEND_API_KEY=None, MAIL_PROVIDER=None)
CALLS.clear()
r = mailer.send_welcome("anne@example.com")
check("7) Console modunda hoş geldin gönderilmez (skipped)",
      r["provider"] == "skipped" and not CALLS, f"{r} calls={len(CALLS)}")

reload_settings(RESEND_API_KEY="re_test_key_123")
CALLS.clear()
r = mailer.send_welcome("anne@example.com")
check("7b) Resend aktifken hoş geldin gönderilir",
      r["ok"] is True and len(CALLS) == 1
      and "hoş geldiniz" in CALLS[0]["json"]["subject"].lower(),
      f"{r} subject={CALLS[0]['json']['subject'] if CALLS else None}")

# =============================================================================
# 7c-7f) DISABLED modu — token NE e-postaya NE loga gider
# =============================================================================
import logging as _logging   # noqa: E402


class _Capture(_logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


_cap = _Capture()
_ml = _logging.getLogger("tavsan.mailer")
_ml.addHandler(_cap)
_ml.setLevel(_logging.DEBUG)

reload_settings(RESEND_API_KEY=None, MAIL_PROVIDER="disabled")
CALLS.clear()
_cap.lines.clear()
r = mailer.send_password_reset("anne@example.com", "GIZLI_TOKEN_XYZ")
check("7c) disabled: ok=True (akış kırılmaz), HTTP çağrısı YOK",
      r["ok"] is True and r["provider"] == "disabled" and not CALLS, f"{r}")
_all_logs = "\n".join(_cap.lines)
check("7d) disabled: TOKEN loglanmaz",
      "GIZLI_TOKEN_XYZ" not in _all_logs, f"loglar={_all_logs[:300]}")
check("7e) disabled: alıcı e-posta loglanmaz (KVKK)",
      "anne@example.com" not in _all_logs, f"loglar={_all_logs[:300]}")

# Anahtar VARSA disabled yine de gönderimi engeller (açık irade kazanır)
reload_settings(RESEND_API_KEY="re_test_key_123", MAIL_PROVIDER="disabled")
CALLS.clear()
r = mailer.send_email("a@b.com", "K", "G")
check("7f) disabled, anahtar olsa bile gönderim YOK",
      r["provider"] == "disabled" and not CALLS, f"{r} calls={len(CALLS)}")

# Production'da anahtar yoksa varsayılan DISABLED olmalı (console DEĞİL)
reload_settings(RESEND_API_KEY=None, MAIL_PROVIDER=None, ENVIRONMENT="production")
check("7g) production + anahtar yok → varsayılan 'disabled'",
      get_settings().mail_provider == "disabled", get_settings().mail_provider)
reload_settings(ENVIRONMENT="development")
check("7h) development + anahtar yok → varsayılan 'console'",
      get_settings().mail_provider == "console", get_settings().mail_provider)
_ml.removeHandler(_cap)

# =============================================================================
# 8) Endpoint entegrasyonu
# =============================================================================
from api.db import engine                       # noqa: E402
from api.db.base import Base                    # noqa: E402
from api import models                          # noqa: E402,F401
from fastapi.testclient import TestClient       # noqa: E402
from api.main import app                        # noqa: E402

Base.metadata.create_all(engine)
c = TestClient(app)

EMAIL = "reset@tavsansmoke.com"
c.post("/api/v1/auth/register", json={"email": EMAIL, "password": "GucluParola123!"})

reload_settings(RESEND_API_KEY="re_test_key_123")
MODE["kind"] = "ok"
CALLS.clear()
r = c.post("/api/v1/auth/reset-password-request", json={"email": EMAIL})
_body = r.json()
check("8) reset-password-request -> 200 + e-posta tetiklendi",
      r.status_code == 200 and len(CALLS) == 1, f"{r.status_code} calls={len(CALLS)}")
check("8b) Token YANITTA DÖNMEZ",
      "reset_token" not in _body and set(_body.keys()) == {"detail"}, str(_body))

# Gönderilen token'la gerçekten sıfırlama yapılabiliyor mu (uçtan uca)?
_sent_text = CALLS[0]["json"]["text"]
_token = _sent_text.split("token=")[1].split("\n")[0].strip()
r = c.post("/api/v1/auth/reset-password",
           json={"token": _token, "new_password": "YeniGucluParola456!"})
check("8c) E-postadaki token ile sıfırlama çalışır",
      r.status_code == 200, f"{r.status_code} {r.text[:160]}")
r = c.post("/api/v1/auth/login",
           json={"email": EMAIL, "password": "YeniGucluParola456!"})
check("8d) Yeni parola ile giriş yapılır", r.status_code == 200, r.text[:160])

# Kayıtsız e-posta → aynı yanıt, gönderim YOK (kullanıcı sayımı sızmaz)
CALLS.clear()
r = c.post("/api/v1/auth/reset-password-request",
           json={"email": "yok@tavsansmoke.com"})
check("8e) Kayıtsız e-posta → aynı yanıt, gönderim YOK",
      r.status_code == 200 and not CALLS and r.json() == _body, f"calls={len(CALLS)}")

# 8f) DISABLED modunda endpoint yine 200 döner (enumeration önlenir) ama
# hiçbir kanala token gitmez — production'ın Resend öncesi davranışı.
reload_settings(RESEND_API_KEY=None, MAIL_PROVIDER="disabled")
CALLS.clear()
r = c.post("/api/v1/auth/reset-password-request", json={"email": EMAIL})
check("8f) disabled modunda reset-password-request -> 200, gönderim YOK",
      r.status_code == 200 and not CALLS and r.json() == {"detail": _body["detail"]},
      f"{r.status_code} calls={len(CALLS)} body={r.json()}")

# 8g) Kayıt akışı disabled modunda da çalışır (hoş geldin atlanır)
r = c.post("/api/v1/auth/register",
           json={"email": "disabled-reg@tavsansmoke.com", "password": "GucluParola123!"})
check("8g) disabled modunda kayıt çalışır (hoş geldin atlanır)",
      r.status_code == 201 and "access_token" in r.json() and not CALLS,
      f"{r.status_code} calls={len(CALLS)}")

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("E-POSTA SERVİSİ TEST SONUÇLARI (Faz 6.3)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    print(f"[{mark}] {name}")
    if detail and not ok:
        print(f"       {detail}")
print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} gecti")
print("=" * 74)
sys.exit(0 if passed == len(results) else 1)
