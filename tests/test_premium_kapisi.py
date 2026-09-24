"""
Premium kapısı + ses klonlama hata yolları — ağ YOK, ElevenLabs MOCK'lanır.

NEDEN VAR: mobil BETA_MODE'da herkesi premium sayıyordu; sunucuda ise premium
kararı YALNIZ GET /subscriptions/status içinde duruyor, hiçbir uç onu
zorlamıyordu. Kapı tek bir yere (deps.require_premium) toplandı; bu dosya
kapının İKİ yönde de doğru çalıştığını ve kapanınca Türkçe konuştuğunu ölçer.

Ayrıca 2026-09-16..18 arası üretim arızasının gerileme testi: POST /voice/clone
başarısızlıklarının TAMAMI ElevenLabs'in hesap geneli "10/10 custom voices"
yanıtıydı ve bize ayırt edilemeyen bir 502 olarak dönüyordu.

Kapsam:
  1. BETA_MODE=true → ücretsiz hesap /voice/generate ve /voice/clone 200
  2. BETA_MODE=false → 403 + Türkçe detail (hem clone hem generate)
  3. BETA_MODE=false + AKTİF ABONELİK → yine 200 (kapı aboneliği tanıyor)
  4. /subscriptions/status ile uçlar AYNI kararı verir (ayrışma yok)
  5. 422 gövdesi: detail tek Türkçe cümle (İngilizce hata listesi değil)
  6. Slot tavanı (10/10) → 503 + Türkçe detail, ham upstream metni SIZMAZ
  7. Slot tavanında KURTARMA: bayat ses silinip klonlama YENİDEN denenir
  8. Anlatıcı sesi (ELEVENLABS_VOICE_ID) ve BAŞKA kullanıcının sesi SİLİNMEZ
  9. Aylık sayaç: BAŞARISIZ denemeler hak YAKMAZ (403/422/503 sonrası
     kullanıcı hâlâ klonlayabilir)

Çalıştırma: python tests/test_premium_kapisi.py
"""
import io
import os
import subprocess
import sys
import tempfile
import uuid as _uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "premium_kapisi_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ELEVENLABS_VOICE_ID"] = "ANLATICI_SESI"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "premium_medya")
os.environ["BETA_MODE"] = "true"                  # bu süreç beta modunda
os.environ.pop("BETA_PREMIUM_ALL", None)

from api import tts                                    # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
from api.models import VoiceProfile                    # noqa: E402
from api.routers import voice as voice_router          # noqa: E402
from api.services import voice as voice_svc            # noqa: E402
from api.services import storage as _storage           # noqa: E402
from api.services import voice_uretim as _vu           # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


# --- MOCK'lar: ağ YOK ---------------------------------------------------------
# KLON_SONUC'u her test kendi senaryosuna göre kurar.
KLON_SONUC: list[dict] = []
KLON_CAGRI = {"n": 0}
SILINEN: list[str] = []
HESAPTAKI: list[str] = []


def fake_clone_voice(name, audio_bytes, filename, content_type):
    KLON_CAGRI["n"] += 1
    sonuc = KLON_SONUC.pop(0) if KLON_SONUC else {"ok": True,
                                                  "voice_id": f"v{KLON_CAGRI['n']}"}
    if sonuc.get("ok"):
        HESAPTAKI.append(sonuc["voice_id"])
    return sonuc


def fake_delete_voice(voice_id):
    SILINEN.append(voice_id)
    if voice_id in HESAPTAKI:
        HESAPTAKI.remove(voice_id)
    return {"ok": True, "error": None, "status": 200}


def fake_list_cloned_voices():
    return {"ok": True, "voices": [{"voice_id": v, "name": "x"} for v in HESAPTAKI]}


def fake_voice_audio(voice_id, text, profil=None, user_id=None):
    return {"audio_url": f"/audio/{voice_id}.mp3", "cached": False, "profile": "masal"}


# v2.3: /voice/clone üretimi arka plana atıyor; bu suite premium KAPISINI
# ölçüyor, üretimi değil.
_vu.kuyruga_al = lambda pid: None
voice_svc.clone_voice = fake_clone_voice
voice_svc.delete_voice = fake_delete_voice
voice_svc.list_cloned_voices = fake_list_cloned_voices
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


HAZIR_ICERIK = "ninni_dandini"


def paketi_hazirla(tok):
    """v2.3 — /voice/generate artık ÜRETMİYOR, hazır dosyayı sunuyor.

    Premium kapısını `generate` üzerinden ölçebilmek için önce o kullanıcının
    paketinde hazır bir içerik olmalı; yoksa kapı geçilse bile 409 döner."""
    from api.models import VoiceAudio
    db = SessionLocal()
    try:
        p = (db.query(VoiceProfile)
             .filter(VoiceProfile.user_id == uid_of(tok),
                     VoiceProfile.status != "replaced").first())
        if p is None:
            return None
        yol = _storage.ses_yolu(p.user_id, p.id, HAZIR_ICERIK)
        _storage.depo().yaz(yol, b"ID3" + b"fake" * 13)
        db.add(VoiceAudio(voice_profile_id=p.id, content_id=HAZIR_ICERIK,
                          storage_path=yol, bytes=55))
        db.commit()
        return yol
    finally:
        db.close()


def uret(tok, voice_id):
    return client.post("/api/v1/voice/generate", headers=H(tok),
                       json={"voiceId": voice_id, "storyId": HAZIR_ICERIK})


def hak_ver(tok):
    """Kullanıcının aylık klonlama hakkını geri ver (31 gün geriye al)."""
    from datetime import datetime, timedelta, timezone
    db = SessionLocal()
    try:
        yeni = datetime.now(timezone.utc) - timedelta(days=31)
        for p in db.query(VoiceProfile).filter(
                VoiceProfile.user_id == uid_of(tok)).all():
            p.last_cloned_at = yeni
        db.commit()
    finally:
        db.close()


PREMIUM_MESAJ = "Bu özellik Premium üyelik gerektiriyor."
SES_422 = "Ses kaydı işlenemedi, lütfen sessiz bir ortamda tekrar deneyin."


# =============================================================================
# 1) BETA_MODE=true → ücretsiz hesap premium uçları KULLANABİLİR
# =============================================================================
t_beta = reg("beta_anne@test.com")            # aboneliği YOK
r = klonla(t_beta)
check("1a) BETA_MODE=true: ücretsiz hesapla /voice/clone → 200",
      r.status_code == 200, f"{r.status_code} {r.text[:160]}")
_vid = r.json().get("voiceId")

paketi_hazirla(t_beta)
rg = uret(t_beta, _vid)
check("1b) BETA_MODE=true: ücretsiz hesapla /voice/generate → 200",
      rg.status_code == 200, f"{rg.status_code} {rg.text[:160]}")
check("1c) audio_url döndü", bool(rg.json().get("audio_url")), rg.text[:120])

st = client.get("/api/v1/subscriptions/status", headers=H(t_beta))
check("4a) /subscriptions/status ile uçlar aynı kararı veriyor (beta)",
      (st.json()["premium"], st.json()["source"]) == (True, "beta"), str(st.json()))


# =============================================================================
# 5) 422 gövdesi Türkçe TEK cümle (İngilizce hata listesi DEĞİL)
# =============================================================================
r422 = client.post("/api/v1/voice/clone", headers=H(t_beta))   # 'audio' alanı YOK
check("5a) Eksik alan → 422", r422.status_code == 422,
      f"{r422.status_code} {r422.text[:160]}")
_d = r422.json().get("detail")
check("5b) 422 detail TEK Türkçe cümle (liste değil)",
      isinstance(_d, str) and _d == SES_422, repr(_d)[:200])
check("5c) Ayrıntı kaybolmadı (errors alanı duruyor)",
      isinstance(r422.json().get("errors"), list), str(r422.json().keys()))

r422g = client.post("/api/v1/voice/generate", headers=H(t_beta),
                    json={"voiceId": "x"})       # text de storyId de yok
check("5d) /voice/generate doğrulama hatası da Türkçe",
      r422g.status_code == 422 and r422g.json().get("detail") == SES_422,
      f"{r422g.status_code} {r422g.text[:160]}")

# Ses DIŞI uçta ses metni GÖSTERİLMEZ (mesaj uca göre seçiliyor)
r422b = client.post("/api/v1/auth/register", json={"email": "bozuk"})
check("5e) Ses dışı uçta genel Türkçe mesaj (ses metni sızmıyor)",
      r422b.status_code == 422
      and isinstance(r422b.json().get("detail"), str)
      and r422b.json()["detail"] != SES_422,
      f"{r422b.status_code} {str(r422b.json().get('detail'))[:120]}")


# =============================================================================
# 6-7) SLOT TAVANI (10/10) — 503 + Türkçe + kurtarma
# =============================================================================
HAM_UPSTREAM = ("You have reached your maximum amount of custom voices (10 / 10). "
                "You can upgrade your subscription to increase your custom voice limit.")

# 6) Kurtarılacak hiçbir şey yokken: 503, Türkçe, ham metin SIZMAZ
t_dolu = reg("slot_dolu@test.com")
KLON_SONUC.clear()
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})
_silinen_once = len(SILINEN)
r6 = klonla(t_dolu)
check("6a) Slot tavanı → 502 değil 503 (bizim kapasitemiz, annenin hatası değil)",
      r6.status_code == 503, f"{r6.status_code} {r6.text[:160]}")
check("6b) Slot tavanı mesajı Türkçe",
      r6.json().get("detail") == voice_router.SES_KAPASITE_MESAJ,
      str(r6.json().get("detail")))
check("6c) Ham ElevenLabs metni kullanıcıya SIZMIYOR",
      "custom voices" not in r6.text and "upgrade" not in r6.text, r6.text[:160])
check("6d) Kurtarılacak bayat ses yokken BAŞKASININ sesi silinmedi",
      len(SILINEN) == _silinen_once, str(SILINEN[_silinen_once:]))

# 7) Bayat (yalnız 'replaced' satırlarla anılan) ses varsa: silinip YENİDEN denenir
_db = SessionLocal()
try:
    _db.add(VoiceProfile(user_id=uid_of(t_dolu), elevenlabs_voice_id="BAYAT_SES",
                         status="replaced"))
    _db.commit()
finally:
    _db.close()
HESAPTAKI.append("BAYAT_SES")
HESAPTAKI.append("ANLATICI_SESI")             # env'deki anlatıcı sesi de hesapta

KLON_SONUC.clear()
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})
KLON_SONUC.append({"ok": True, "voice_id": "YENI_SES"})      # kurtarma sonrası
_cagri_once = KLON_CAGRI["n"]
hak_ver(t_dolu)
r7 = klonla(t_dolu)
check("7a) Bayat slot kurtarılınca klonlama BAŞARILI (200)",
      r7.status_code == 200, f"{r7.status_code} {r7.text[:160]}")
check("7b) Klonlama tam 2 kez denendi (tavan + kurtarma sonrası tek retry)",
      KLON_CAGRI["n"] - _cagri_once == 2, f"{KLON_CAGRI['n'] - _cagri_once}")
check("7c) Silinen ses BAYAT olan", "BAYAT_SES" in SILINEN, str(SILINEN[-4:]))
check("8a) Anlatıcı sesi (ELEVENLABS_VOICE_ID) SİLİNMEDİ",
      "ANLATICI_SESI" not in SILINEN, str(SILINEN))
check("8b) Başka kullanıcının geçerli sesi SİLİNMEDİ",
      _vid not in SILINEN, f"vid={_vid} silinen={SILINEN}")


# =============================================================================
# 9) BAŞARISIZ DENEME AYLIK HAK YAKMIYOR
# =============================================================================
t_yakma = reg("hak_yakma@test.com")
KLON_SONUC.clear()
KLON_SONUC.append({"ok": False, "error": "bozuk kayıt", "status": 422,
                   "reason": voice_svc.HATA_SES_GECERSIZ})
r9a = klonla(t_yakma)
check("9a) Bozuk ses kaydı → 422 + Türkçe detail",
      r9a.status_code == 422 and r9a.json().get("detail") == SES_422,
      f"{r9a.status_code} {r9a.text[:160]}")

_db = SessionLocal()
try:
    _n = (_db.query(VoiceProfile)
          .filter(VoiceProfile.user_id == uid_of(t_yakma)).count())
finally:
    _db.close()
check("9b) Başarısız denemede voice_profiles satırı AÇILMADI (sayaç artmadı)",
      _n == 0, f"satır={_n}")

_vs = client.get("/api/v1/voice/voice-status", headers=H(t_yakma)).json()
check("9c) Başarısız denemeden sonra can_clone HÂLÂ true",
      _vs.get("can_clone") is True, str(_vs))

KLON_SONUC.clear()
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})
klonla(t_yakma)
KLON_SONUC.clear()
r9d = klonla(t_yakma)
check("9d) 422 + 503'ten sonra klonlama hâlâ mümkün (429 DEĞİL)",
      r9d.status_code == 200, f"{r9d.status_code} {r9d.text[:160]}")


# =============================================================================
# 10) KENDİ SESİ FEDA EDİLDİ AMA KLONLAMA YİNE TUTMADI → HAK İADE
# =============================================================================
# Tıkanıklıkta kurtarma kullanıcının KENDİ sesini silebiliyor. Klonlama yine
# tutmazsa hem sesi gider hem de 30 günlük bekleme yüzünden yenisini
# kaydedemez; kayıp kendi davranışından değil bizim denememizden kaynaklandığı
# için hak İADE edilmeli.
t_feda = reg("feda@test.com")
KLON_SONUC.clear()
r10 = klonla(t_feda)                       # başarılı ilk klon → kendi sesi oluşur
_kendi_ses = r10.json()["voiceId"]
check("10a) Hazırlık: ilk klon başarılı", r10.status_code == 200, r10.text[:120])

# Bayat aday YOK; tek kurtarılabilir şey kullanıcının kendi sesi.
KLON_SONUC.clear()
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})     # retry de tutmuyor
hak_ver(t_feda)
r10b = klonla(t_feda)
check("10b) Kurtarma sonrası da tutmazsa 503", r10b.status_code == 503,
      f"{r10b.status_code} {r10b.text[:160]}")
check("10c) Kullanıcının kendi sesi feda edildi", _kendi_ses in SILINEN,
      f"kendi={_kendi_ses} silinen={SILINEN[-4:]}")

_vs10 = client.get("/api/v1/voice/voice-status", headers=H(t_feda)).json()
check("10d) Hak İADE edildi: can_clone yeniden true (30 gün kilit YOK)",
      _vs10.get("can_clone") is True, str(_vs10))

KLON_SONUC.clear()
r10c = klonla(t_feda)
check("10e) İade sonrası hemen yeniden klonlayabiliyor (429 değil)",
      r10c.status_code == 200, f"{r10c.status_code} {r10c.text[:160]}")

# Bayat aşamasıyla kurtarıldığında hak İADE EDİLMEZ (kendi sesi kaybolmadı)
t_bayat = reg("bayat_iade@test.com")
KLON_SONUC.clear()
_bayat_kendi = klonla(t_bayat).json()["voiceId"]
_db = SessionLocal()
try:
    _db.add(VoiceProfile(user_id=uid_of(t_bayat), elevenlabs_voice_id="BAYAT2",
                         status="replaced"))
    _db.commit()
finally:
    _db.close()
HESAPTAKI.append("BAYAT2")
hak_ver(t_bayat)                           # aylık kapı 429'a takılmasın
_db = SessionLocal()
try:
    _once = {p.elevenlabs_voice_id: p.last_cloned_at
             for p in _db.query(VoiceProfile)
             .filter(VoiceProfile.user_id == uid_of(t_bayat)).all()}
finally:
    _db.close()

KLON_SONUC.clear()
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})
KLON_SONUC.append({"ok": False, "error": HAM_UPSTREAM, "status": 503,
                   "reason": voice_svc.HATA_SLOT_DOLU})
r10d = klonla(t_bayat)
check("10f) Bayat kurtarma başarısız olsa da 503",
      r10d.status_code == 503, f"{r10d.status_code} {r10d.text[:160]}")
check("10g) Bayat aşamasında BAYAT ses silindi, kullanıcının kendi sesi DURUYOR",
      "BAYAT2" in SILINEN and _bayat_kendi not in SILINEN,
      f"kendi={_bayat_kendi} silinen={SILINEN[-3:]}")

_db = SessionLocal()
try:
    _sonra = {p.elevenlabs_voice_id: p.last_cloned_at
              for p in _db.query(VoiceProfile)
              .filter(VoiceProfile.user_id == uid_of(t_bayat)).all()}
finally:
    _db.close()
# İade son_klon damgasını "şimdi - 30 gün"e taşır; bayat aşamasında hiç
# dokunulmamalı (kullanıcı kendi sesini kaybetmedi, telafi edilecek bir şey yok).
check("10h) Bayat aşamasında hak İADE EDİLMEZ (son_klon damgası değişmedi)",
      _sonra == _once, f"once={_once} sonra={_sonra}")


# =============================================================================
# 2-3) BETA_MODE=false — AYRI SÜREÇ (config lru_cache'li, env sonradan değişmez)
# =============================================================================
_ALT_SUREC = r'''
import io, os, sys, tempfile, uuid
from pathlib import Path
ROOT = Path(sys.argv[1]); sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
db = Path(tempfile.gettempdir()) / "premium_kapisi_kapali.db"
if db.exists(): db.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ.pop("BETA_MODE", None)               # KAPALI
os.environ.pop("BETA_PREMIUM_ALL", None)
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "premium_medya_alt")

from api import tts
from api.db import Base, SessionLocal, engine
from api.models import Subscription, VoiceProfile
from api.routers import voice as voice_router
from api.services import voice as voice_svc

voice_svc.clone_voice = lambda *a, **k: {"ok": True, "voice_id": "VS1"}
from api.services import voice_uretim as _vu2
_vu2.kuyruga_al = lambda pid: None
voice_svc.delete_voice = lambda v: {"ok": True, "error": None, "status": 200}
voice_svc.list_cloned_voices = lambda: {"ok": True, "voices": []}
voice_router.voice_svc = voice_svc
tts.voice_audio = lambda vid, t, profil=None, user_id=None: {
    "audio_url": "/audio/x.mp3", "cached": False, "profile": "masal"}
voice_router.tts = tts
Base.metadata.create_all(bind=engine)

from fastapi.testclient import TestClient
from api.main import app
from api.services.security import decode_access_token
c = TestClient(app)

def reg(e):
    return c.post("/api/v1/auth/register",
                  json={"email": e, "password": "TestPass123!"}).json()["access_token"]
def H(t): return {"Authorization": f"Bearer {t}"}

tok = reg("ucretsiz@test.com")
rc = c.post("/api/v1/voice/clone", headers=H(tok),
            files={"audio": ("s.mp3", io.BytesIO(b"A" * 900), "audio/mpeg")},
            data={"name": "Ses"})
rg = c.post("/api/v1/voice/generate", headers=H(tok),
            json={"voiceId": "VS1", "storyId": "ninni_dandini"})
st = c.get("/api/v1/subscriptions/status", headers=H(tok)).json()

# Aktif abonelikli kullanıcı: kapı aboneliği de tanımalı
tok2 = reg("aboneli@test.com")
uid = uuid.UUID(decode_access_token(tok2)["sub"])
s = SessionLocal()
try:
    s.add(Subscription(user_id=uid, platform="ios", product_id="premium_monthly",
                       status="active", receipt_data="A1b2C3real=="))
    prof = VoiceProfile(user_id=uid, elevenlabs_voice_id="VS1", status="ready")
    s.add(prof)
    s.commit()
    # v2.3 — generate hazır dosyayı sunuyor; kapıyı ölçmek için paket hazır olmalı.
    from api.models import VoiceAudio
    from api.services import storage as _st_depo
    yol = _st_depo.ses_yolu(uid, prof.id, "ninni_dandini")
    _st_depo.depo().yaz(yol, b"ID3" + b"fake" * 13)
    s.add(VoiceAudio(voice_profile_id=prof.id, content_id="ninni_dandini",
                     storage_path=yol, bytes=55))
    s.commit()
finally:
    s.close()
rg2 = c.post("/api/v1/voice/generate", headers=H(tok2),
             json={"voiceId": "VS1", "storyId": "ninni_dandini"})
st2 = c.get("/api/v1/subscriptions/status", headers=H(tok2)).json()

import json
print("SONUC" + json.dumps({
    "clone_status": rc.status_code, "clone_detail": rc.json().get("detail"),
    "gen_status": rg.status_code, "gen_detail": rg.json().get("detail"),
    "status_body": st,
    "abone_gen_status": rg2.status_code, "abone_status_body": st2,
}, ensure_ascii=False))
'''

# python -c ile çalıştırılıyor (geçici DOSYA değil): script dizini sys.path'in
# başına geçtiği için Temp'te unutulmuş bir modül (örn. inspect.py) standart
# kütüphaneyi gölgeleyip alt süreci alakasız bir hatayla düşürebiliyordu.
_p = subprocess.run([sys.executable, "-c", _ALT_SUREC, str(ROOT)], cwd=str(ROOT),
                    capture_output=True, text=True, encoding="utf-8", timeout=300)
_satir = next((l for l in (_p.stdout or "").splitlines() if l.startswith("SONUC")), None)
if _satir is None:
    check("2a) BETA_MODE=false alt süreci çalıştı", False,
          (_p.stderr or "")[-400:])
else:
    import json as _json
    _r = _json.loads(_satir[len("SONUC"):])
    check("2a) BETA_MODE=false: ücretsiz hesapla /voice/clone → 403",
          _r["clone_status"] == 403, str(_r["clone_status"]))
    check("2b) 403 detail Türkçe ve TAM METİN",
          _r["clone_detail"] == PREMIUM_MESAJ, repr(_r["clone_detail"]))
    check("2c) BETA_MODE=false: ücretsiz hesapla /voice/generate → 403",
          _r["gen_status"] == 403, str(_r["gen_status"]))
    check("2d) /voice/generate 403 detail de Türkçe (gövde boş değil)",
          _r["gen_detail"] == PREMIUM_MESAJ, repr(_r["gen_detail"]))
    check("4b) /subscriptions/status uçlarla aynı kararı veriyor (none)",
          (_r["status_body"]["premium"], _r["status_body"]["source"]) == (False, "none"),
          str(_r["status_body"]))
    check("3a) BETA_MODE=false + aktif abonelik → /voice/generate 200",
          _r["abone_gen_status"] == 200, str(_r["abone_gen_status"]))
    check("3b) Abonelikli kullanıcıda source=store (B4 adı)",
          (_r["abone_status_body"]["premium"], _r["abone_status_body"]["source"])
          == (True, "store"),
          str(_r["abone_status_body"]))


# =============================================================================
# RAPOR
# =============================================================================
print("\n" + "=" * 78)
print("PREMIUM KAPISI + SES KLONLAMA HATA YOLLARI")
print("=" * 78)
_gecen = 0
for ad, ok, detay in results:
    print(f"  {'[OK]  ' if ok else '[HATA]'} {ad}" + (f"   → {detay}" if not ok else ""))
    _gecen += int(ok)
print("-" * 78)
print(f"  {_gecen}/{len(results)} kontrol geçti")
sys.exit(0 if _gecen == len(results) else 1)
