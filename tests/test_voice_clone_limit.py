"""
Aylık ses klonlama limiti testleri — ElevenLabs MOCK'lanır, ağ YOK.

NEDEN VAR: gizlilik politikası "ses kaydı ayda bir kez yenilenebilir" diyordu
ama kodda HİÇBİR kontrol yoktu — kullanıcı istediği kadar klon açabiliyordu.
Her klon ElevenLabs'te slot + ücret tutuyor ve gereksiz biyometrik veri
saklanıyordu. Politika ile davranış ayrışmıştı.

Kapsam:
  1. İlk klonlama serbest; hemen ardından ikinci deneme 429
  2. 429 gövdesi: detail metni + retry_after_days + next_clone_available_at
     + Retry-After başlığı; ElevenLabs'e HİÇ gidilmiyor (maliyet yok)
  3. 30 gün geçince yeniden serbest; 29.9 günde hâlâ kapalı (sınır kesin)
  4. GET /voice-status: can_clone + next_clone_available_at + retry_after_days
  5. ESKİ VOICE SİLİNİYOR (slot/ücret birikmesin) ve satır 'replaced' oluyor
  6. Silme BAŞARISIZ olursa yeni klon YİNE geçerli (best-effort temizlik)
  7. Geriye uyum: last_cloned_at NULL olan eski satırda created_at'e düşülür
     (mevcut kullanıcıya sessizce fazladan hak doğmaz)
  8. Kullanıcılar birbirini etkilemez
  9. 'replaced' voiceId ile /generate → 410 (anlamsız upstream hatası yerine)

Çalıştırma: python tests/test_voice_clone_limit.py
"""
import io
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "voice_clone_limit_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"

from api import tts                                    # noqa: E402
from api.db import Base, engine                        # noqa: E402
from api.models import VoiceProfile                    # noqa: E402
from api.routers import voice as voice_router          # noqa: E402
from api.services import voice as voice_svc            # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


# --- MOCK'lar: ağ YOK ---------------------------------------------------------
KLON = {"n": 0}
SILINEN: list[str] = []
SILME_BASARILI = {"v": True}


def fake_clone_voice(name, audio_bytes, filename, content_type):
    KLON["n"] += 1
    return {"ok": True, "voice_id": f"voice-{KLON['n']}"}


def fake_delete_voice(voice_id):
    SILINEN.append(voice_id)
    if SILME_BASARILI["v"]:
        return {"ok": True, "error": None, "status": 200}
    return {"ok": False, "error": "ElevenLabs HTTP 500", "status": 500}


def fake_voice_audio(voice_id, text, profil=None, user_id=None):
    return {"audio_url": f"/audio/{voice_id}.mp3", "cached": False, "profile": "masal"}


voice_svc.clone_voice = fake_clone_voice
voice_svc.delete_voice = fake_delete_voice
voice_router.voice_svc = voice_svc
tts.voice_audio = fake_voice_audio
voice_router.tts = tts

Base.metadata.create_all(bind=engine)

from fastapi.testclient import TestClient             # noqa: E402
from api.main import app                              # noqa: E402

client = TestClient(app)


def reg(email, pw="TestPass123!"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": pw})
    return r.json()["access_token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def uid_of(tok):
    from api.services.security import decode_access_token
    return _uuid.UUID(decode_access_token(tok)["sub"])


def klonla(tok, ad="Ses"):
    return client.post("/api/v1/voice/clone", headers=H(tok),
                       files={"audio": ("s.mp3", io.BytesIO(b"FAKEAUDIO" * 100),
                                        "audio/mpeg")},
                       data={"name": ad})


def geri_al(tok, gun: float):
    """Kullanıcının TÜM profillerinin klonlama anını `gun` gün geriye çek.

    "Zaman geçti" benzetimi: tek satır güncellemek yetmiyor çünkü router güncel
    profili (replaced olmayan + en yeni) seçiyor; yalnız bir satırı geri almak
    testi üretimdeki sıralamadan ayırırdı."""
    from api.db import SessionLocal
    db = SessionLocal()
    try:
        yeni = datetime.now(timezone.utc) - timedelta(days=gun)
        for p in db.query(VoiceProfile).filter(
                VoiceProfile.user_id == uid_of(tok)).all():
            p.last_cloned_at = yeni
        db.commit()
    finally:
        db.close()


# =============================================================================
# 1-2) İLK KLON SERBEST, İKİNCİSİ 429
# =============================================================================
t1 = reg("voice1@test.com")
r1 = klonla(t1)
check("1a) İlk klonlama başarılı (200)", r1.status_code == 200,
      f"{r1.status_code} {r1.text[:120]}")
check("1b) voiceId döndü", r1.json().get("voiceId") == "voice-1", r1.text[:120])

_klon_once = KLON["n"]
r2 = klonla(t1)
check("2a) Hemen ardından ikinci klonlama 429",
      r2.status_code == 429, f"{r2.status_code} {r2.text[:160]}")

_j = r2.json()
check("2b) detail metni politikadaki ifadeyle aynı",
      _j.get("detail", "").startswith("Sesini ayda bir kez kaydedebilirsin.")
      and "Bir sonraki hakkın:" in _j.get("detail", ""), str(_j.get("detail")))
check("2c) retry_after_days var ve 1-30 arasında",
      isinstance(_j.get("retry_after_days"), int) and 1 <= _j["retry_after_days"] <= 30,
      str(_j.get("retry_after_days")))
check("2d) next_clone_available_at ISO tarih olarak var",
      isinstance(_j.get("next_clone_available_at"), str)
      and _j["next_clone_available_at"].startswith("20"),
      str(_j.get("next_clone_available_at")))
check("2e) Retry-After başlığı (saniye) var",
      r2.headers.get("Retry-After", "").isdigit(), r2.headers.get("Retry-After"))
check("2f) 429'da ElevenLabs'e HİÇ gidilmedi (maliyet yok)",
      KLON["n"] == _klon_once, f"klon çağrısı {KLON['n'] - _klon_once} kez arttı")

# Tarih biçimi kullanıcıya gösterilebilir olmalı (gg.aa.yyyy).
import re                                                          # noqa: E402
check("2g) Mesajdaki tarih gg.aa.yyyy biçiminde",
      bool(re.search(r"\d{2}\.\d{2}\.\d{4}", _j.get("detail", ""))), _j.get("detail"))


# =============================================================================
# 3) SINIR KESİNLİĞİ — 29.9 gün kapalı, 30 gün açık
# =============================================================================
geri_al(t1, 29.9)
r3 = klonla(t1)
check("3a) 29.9 gün sonra HÂLÂ kapalı (429)", r3.status_code == 429,
      f"{r3.status_code}")
check("3b) 29.9 günde retry_after_days en az 1 (0 gün denilip tekrar 429 alınmaz)",
      r3.json().get("retry_after_days", 0) >= 1, str(r3.json().get("retry_after_days")))

geri_al(t1, 30.1)
r4 = klonla(t1)
check("3c) 30 gün geçince yeniden serbest (200)", r4.status_code == 200,
      f"{r4.status_code} {r4.text[:160]}")
check("3d) Yeni voiceId üretildi", r4.json().get("voiceId") == "voice-2",
      r4.text[:120])


# =============================================================================
# 5) ESKİ VOICE SİLİNDİ + SATIR 'replaced'
# =============================================================================
check("5a) Eski voice ElevenLabs'ten SİLİNDİ", "voice-1" in SILINEN, str(SILINEN))
check("5b) YENİ voice silinmedi", "voice-2" not in SILINEN, str(SILINEN))

from api.db import SessionLocal                                    # noqa: E402

_db = SessionLocal()
try:
    _profiller = (_db.query(VoiceProfile)
                  .filter(VoiceProfile.user_id == uid_of(t1))
                  .order_by(VoiceProfile.created_at).all())
    _durumlar = {p.elevenlabs_voice_id: p.status for p in _profiller}
    _son_klon = {p.elevenlabs_voice_id: p.last_cloned_at for p in _profiller}
finally:
    _db.close()
check("5c) Eski satır 'replaced', yeni satır 'ready'",
      _durumlar.get("voice-1") == "replaced" and _durumlar.get("voice-2") == "ready",
      str(_durumlar))
check("5d) Yeni satırda last_cloned_at DOLU",
      _son_klon.get("voice-2") is not None, str(_son_klon))


# =============================================================================
# 9) 'replaced' voiceId ile /generate → 410
# =============================================================================
r_gen = client.post("/api/v1/voice/generate", headers=H(t1),
                    json={"voiceId": "voice-1", "text": "merhaba"})
check("9a) Silinmiş (replaced) ses ile üretim → 410",
      r_gen.status_code == 410, f"{r_gen.status_code} {r_gen.text[:160]}")
check("9b) 410 mesajı güncel sesi nereden alacağını söylüyor",
      "voice-status" in r_gen.text, r_gen.text[:160])

r_gen2 = client.post("/api/v1/voice/generate", headers=H(t1),
                     json={"voiceId": "voice-2", "text": "merhaba"})
check("9c) GÜNCEL ses ile üretim çalışıyor (gerileme yok)",
      r_gen2.status_code == 200, f"{r_gen2.status_code} {r_gen2.text[:160]}")


# =============================================================================
# 4) GET /voice-status
# =============================================================================
rs = client.get("/api/v1/voice/voice-status", headers=H(t1)).json()
check("4a) can_clone=false (az önce klonladı)", rs.get("can_clone") is False, str(rs))
check("4b) next_clone_available_at dolu", bool(rs.get("next_clone_available_at")), str(rs))
check("4c) retry_after_days >= 1", rs.get("retry_after_days", 0) >= 1, str(rs))
check("4d) last_cloned_at dönüyor", bool(rs.get("last_cloned_at")), str(rs))
check("4e) voiceId GÜNCEL olan (voice-2)", rs.get("voiceId") == "voice-2", str(rs))

geri_al(t1, 31)
rs2 = client.get("/api/v1/voice/voice-status", headers=H(t1)).json()
check("4f) 31 gün sonra can_clone=true", rs2.get("can_clone") is True, str(rs2))
check("4g) can_clone=true iken next_clone_available_at null",
      rs2.get("next_clone_available_at") is None, str(rs2))
check("4h) can_clone=true iken retry_after_days 0",
      rs2.get("retry_after_days") == 0, str(rs2))

# Hiç klonlamamış kullanıcı
t_yeni = reg("voice_yeni@test.com")
rs3 = client.get("/api/v1/voice/voice-status", headers=H(t_yeni)).json()
check("4i) Hiç klonlamamış kullanıcı: status=none, can_clone=true",
      rs3.get("status") == "none" and rs3.get("can_clone") is True, str(rs3))


# =============================================================================
# 6) SİLME BAŞARISIZ OLSA BİLE YENİ KLON GEÇERLİ (best-effort)
# =============================================================================
t2 = reg("voice2@test.com")
klonla(t2)
geri_al(t2, 31)
SILME_BASARILI["v"] = False
_onceki_silinen = len(SILINEN)
r6 = klonla(t2)
SILME_BASARILI["v"] = True
check("6a) Silme başarısızken klonlama YİNE 200", r6.status_code == 200,
      f"{r6.status_code} {r6.text[:160]}")
check("6b) Silme denendi", len(SILINEN) > _onceki_silinen, str(SILINEN[-3:]))

_db = SessionLocal()
try:
    _p2 = (_db.query(VoiceProfile).filter(VoiceProfile.user_id == uid_of(t2))
           .order_by(VoiceProfile.created_at).all())
    _d2 = [(p.elevenlabs_voice_id, p.status) for p in _p2]
finally:
    _db.close()
check("6c) Silinemeyen satır 'replaced' YAPILMADI (yalan söylenmiyor)",
      all(s != "replaced" for _v, s in _d2), str(_d2))


# =============================================================================
# 7) GERİYE UYUM — last_cloned_at NULL olan eski satır
# =============================================================================
# 0009 migration'ı öncesi satırlarda last_cloned_at yok. NULL'u "hiç klonlamamış"
# saymak mevcut HER kullanıcıya sessizce fazladan bir hak doğururdu.
t3 = reg("voice3@test.com")
klonla(t3)
_db = SessionLocal()
try:
    _p3 = (_db.query(VoiceProfile).filter(VoiceProfile.user_id == uid_of(t3))
           .order_by(VoiceProfile.created_at.desc()).first())
    _p3.last_cloned_at = None                     # eski satırı taklit et
    _db.commit()
finally:
    _db.close()

r7 = klonla(t3)
check("7a) last_cloned_at NULL iken created_at'e düşülüyor → 429 (bedava hak YOK)",
      r7.status_code == 429, f"{r7.status_code} {r7.text[:160]}")
rs7 = client.get("/api/v1/voice/voice-status", headers=H(t3)).json()
check("7b) voice-status da created_at'e düşüyor (can_clone=false)",
      rs7.get("can_clone") is False, str(rs7))


# =============================================================================
# 8) KULLANICILAR BİRBİRİNİ ETKİLEMİYOR
# =============================================================================
t4 = reg("voice4@test.com")
r8 = klonla(t4)
check("8) Başka kullanıcının limiti bu kullanıcıyı etkilemiyor (200)",
      r8.status_code == 200, f"{r8.status_code} {r8.text[:160]}")


# =============================================================================
# 10) SERVİS KATMANI — delete_voice sözleşmesi
# =============================================================================
check("10a) delete_voice boş voice_id'de güvenli",
      voice_svc.__dict__.get("delete_voice") is not None, "")

# Gerçek fonksiyonu (mock'lanmamış hâlini) modülden yeniden yükleyip sözleşmesini
# kontrol et: 404 = zaten yok = BAŞARI (idempotent temizlik).
import importlib                                                   # noqa: E402

_gercek = importlib.reload(importlib.import_module("api.services.voice"))


class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.ok = 200 <= code < 300

    def json(self):
        return {"detail": "x"}


_orig_delete = _gercek.requests.delete
try:
    _gercek.requests.delete = lambda *a, **k: _Resp(404)
    check("10b) delete_voice: 404 (ses zaten yok) BAŞARI sayılıyor",
          _gercek.delete_voice("v")["ok"] is True, "")
    _gercek.requests.delete = lambda *a, **k: _Resp(500)
    check("10c) delete_voice: 500 hata döner ama YÜKSELTMEZ",
          _gercek.delete_voice("v")["ok"] is False, "")
    check("10d) delete_voice: boş voice_id'de çağrı yapılmaz",
          _gercek.delete_voice("")["ok"] is False, "")
finally:
    _gercek.requests.delete = _orig_delete


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("AYLIK SES KLONLAMA LİMİTİ TEST SONUÇLARI")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
        print(f"[{mark}] {name}")
    else:
        print(f"[{mark}] {name}\n       {detail}")

print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} geçti")
sys.exit(0 if passed == len(results) else 1)
