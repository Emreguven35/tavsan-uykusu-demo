"""
SES PAKETİ v2.3 — "üret ve bırak" testleri.

AĞ YOK: ElevenLabs (klonlama, TTS, silme, listeleme) MOCK'lanır; depo geçici bir
klasördür. Gerçek bir kredi harcanmaz.

NEDEN VAR: ElevenLabs hesabının 10 klon slotu var, 94+ kullanıcı. Artık klon
sesi kalıcı tutulmuyor — klonlama biter bitmez masal/ninni paketi üretilip
kendi depomuza yazılıyor, ardından ElevenLabs'teki ses SİLİNİYOR. Bu dosya o
akışın her adımını ve başarısızlık yollarını kilitler.

Kapsam:
  P1  Tam akış: klon → üretim → ses SİLİNDİ → stories hazır + imzalı bağlantı
  P2  Rollback: DB yazımı patlarsa ElevenLabs sesi HEMEN silinir
  P3  Kısmi başarı: >%50 → ready (eksikler error'da); <%50 → failed + hak iadesi
  P4  Restart: yarım kalan üretim kaldığı yerden devam eder, tamamı yeniden
      üretilmez
  P5  Hesap silme: depo klasörü + satırlar + ElevenLabs sesi gider (KVKK)
  P6  /voice/generate üretim YAPMAZ: hazırsa imzalı URL, değilse 409
  P7  İmzalı bağlantı: geçerli imza çalışır, bozuk/süresi dolmuş imza 403
  P8  Günlük temizlik: artık sesler silinir, anlatıcı ve süren üretim KORUNUR

Çalıştırma: python tests/test_ses_paketi.py
"""
import io
import os
import shutil
import sys
import tempfile
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "ses_paketi_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "ses_paketi_medya"
if _MEDYA.exists():
    shutil.rmtree(_MEDYA, ignore_errors=True)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ["VOICE_PACKAGE"] = "starter"
os.environ["BETA_MODE"] = "true"                 # premium kapısı açık
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ELEVENLABS_VOICE_ID"] = "ANLATICI"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from api import tts                                       # noqa: E402
from api.db import Base, SessionLocal, engine             # noqa: E402
from api.models import User, VoiceAudio, VoiceProfile     # noqa: E402
from api.routers import voice as voice_router             # noqa: E402
from api.services import storage, voice_paket, voice_temizlik, voice_uretim  # noqa: E402
from api.services import voice as voice_svc               # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


# --- MOCK'lar -----------------------------------------------------------------
MP3 = b"ID3\x04\x00" + b"\x00" * 4000          # sahte ama geçerli imzalı MP3
KLON = {"n": 0, "hata": None}
SILINEN: list[str] = []
HESAPTAKI: list[str] = ["ANLATICI"]
TTS_CAGRI = {"n": 0}
TTS_BASARISIZ: set[str] = set()                # bu içeriklerde TTS None döner


def fake_clone_voice(name, audio_bytes, filename, content_type):
    if KLON["hata"]:
        return KLON["hata"]
    KLON["n"] += 1
    vid = f"VOICE{KLON['n']}"
    HESAPTAKI.append(vid)
    return {"ok": True, "voice_id": vid}


def fake_delete_voice(voice_id):
    SILINEN.append(voice_id)
    if voice_id in HESAPTAKI:
        HESAPTAKI.remove(voice_id)
    return {"ok": True, "error": None, "status": 200}


def fake_list_cloned_voices():
    return {"ok": True, "voices": [{"voice_id": v, "name": "x"} for v in HESAPTAKI]}


def fake_synthesize(text, model=None, voice_id=None, profil=None,
                    usage_op=None, user_id=None):
    TTS_CAGRI["n"] += 1
    for cid in TTS_BASARISIZ:
        icerik = voice_paket.icerik_bul(cid)
        if icerik and icerik["text"][:40] in text:
            return None
    return MP3


voice_svc.clone_voice = fake_clone_voice
voice_svc.delete_voice = fake_delete_voice
voice_svc.list_cloned_voices = fake_list_cloned_voices
voice_router.voice_svc = voice_svc
voice_uretim.voice_svc = voice_svc
voice_temizlik.voice_svc = voice_svc
tts.synthesize = fake_synthesize

# Router'ın kuyruğa atmasını KAPAT: üretimi testler kendi zamanlamasıyla çağırır.
KUYRUK: list[str] = []
voice_router.voice_uretim = type("K", (), {
    "kuyruga_al": staticmethod(lambda pid: KUYRUK.append(str(pid))),
    "paket_boyutu": staticmethod(voice_paket.paket_boyutu),
})()

Base.metadata.create_all(bind=engine)

from fastapi.testclient import TestClient                  # noqa: E402
from api.main import app                                   # noqa: E402

client = TestClient(app)
PAKET = [x["id"] for x in voice_paket.paket_icerikleri()]
N = len(PAKET)


def reg(email):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "TestPass123!"})
    return r.json()["access_token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def klonla(tok):
    return client.post("/api/v1/voice/clone", headers=H(tok),
                       files={"audio": ("s.mp3", io.BytesIO(b"A" * 900),
                                        "audio/mpeg")},
                       data={"name": "Ses"})


def profil_of(tok):
    from api.services.security import decode_access_token
    uid = _uuid.UUID(decode_access_token(tok)["sub"])
    db = SessionLocal()
    try:
        return (db.query(VoiceProfile).filter(VoiceProfile.user_id == uid)
                .order_by(VoiceProfile.created_at.desc()).first())
    finally:
        db.close()


# =============================================================================
# P1 — TAM AKIŞ
# =============================================================================
check("P0) Paket 4 içerik (3 ninni + 1 masal)",
      N == 4 and PAKET[-1] == "masal_aysecik_uyku_perisi", str(PAKET))

t1 = reg("paket1@test.com")
r1 = klonla(t1)
check("P1a) POST /voice/clone → 200", r1.status_code == 200, r1.text[:160])
check("P1b) Yanıtta voiceId var, sampleUrl YOK (artık üretilmiyor)",
      r1.json().get("voiceId") and r1.json().get("sampleUrl") is None,
      str(r1.json()))

p1 = profil_of(t1)
check("P1c) Profil status=cloning, progress 0/N",
      p1.status == "cloning" and p1.progress_done == 0 and p1.progress_total == N,
      f"{p1.status} {p1.progress_done}/{p1.progress_total}")
check("P1d) Üretim kuyruğa alındı", str(p1.id) in KUYRUK, str(KUYRUK))

_vs = client.get("/api/v1/voice/voice-status", headers=H(t1)).json()
check("P1e) voice-status cloning + progress nesnesi",
      _vs["status"] == "cloning" and _vs["progress"] == {"done": 0, "total": N},
      str(_vs)[:200])

_voice_id_1 = p1.elevenlabs_voice_id
sonuc = voice_uretim.uret(p1.id)
check("P1f) Üretim tamamlandı: N/N", sonuc["uretilen"] == N, str(sonuc))

p1 = profil_of(t1)
check("P1g) status=released (ses serbest bırakıldı)",
      p1.status == "released", p1.status)
check("P1h) elevenlabs_voice_id NULL, released_at dolu",
      p1.elevenlabs_voice_id is None and p1.released_at is not None,
      f"{p1.elevenlabs_voice_id} {p1.released_at}")
check("P1i) ElevenLabs'te ses KALMADI",
      _voice_id_1 in SILINEN and _voice_id_1 not in HESAPTAKI,
      f"silinen={SILINEN} hesapta={HESAPTAKI}")
check("P1j) Anlatıcı sesi korundu", "ANLATICI" in HESAPTAKI, str(HESAPTAKI))

db = SessionLocal()
try:
    _sesler = db.query(VoiceAudio).filter(
        VoiceAudio.voice_profile_id == p1.id).all()
    _yollar = [a.storage_path for a in _sesler]
finally:
    db.close()
check("P1k) voice_audios'ta N satır", len(_sesler) == N, f"{len(_sesler)}")
check("P1l) Dosyalar depoda gerçekten var",
      all(storage.depo().var_mi(y) for y in _yollar), str(_yollar[:2]))
check("P1m) Yol sözleşmesi: voice-audio/{user}/{profil}/{icerik}.mp3",
      all(y.startswith("voice-audio/") and y.endswith(".mp3") for y in _yollar),
      str(_yollar[:1]))

_st = client.get("/api/v1/voice/stories", headers=H(t1)).json()
_hepsi = _st["masallar"] + _st["ninniler"]
_hazirlar = [x for x in _hepsi if x["hazir"]]
check("P1n) /voice/stories: paketteki N içerik hazır",
      len(_hazirlar) == N, f"{len(_hazirlar)}/{N}")
check("P1o) Hazır içerikte imzalı audio_url + durum=hazir",
      all("/media/" in (x["audio_url"] or "") and "sig=" in (x["audio_url"] or "")
          and x["durum"] == "hazir" for x in _hazirlar),
      str(_hazirlar[0]) if _hazirlar else "")
check("P1p) Pakette OLMAYAN masallar 'hazirlaniyor' değil listede duruyor",
      len(_hepsi) == 8 and any(not x["hazir"] for x in _hepsi), str(len(_hepsi)))
check("P1q) Yanıt paket adını ve içerik listesini taşıyor",
      _st["paket"] == "starter" and set(_st["paket_icerikleri"]) == set(PAKET),
      str(_st.get("paket_icerikleri")))

_vs = client.get("/api/v1/voice/voice-status", headers=H(t1)).json()
check("P1r) voice-status: released + N/N + hazir_icerik=N",
      _vs["status"] == "released" and _vs["progress"]["done"] == N
      and _vs["hazir_icerik"] == N, str(_vs)[:220])


# =============================================================================
# P7 — İMZALI BAĞLANTI
# =============================================================================
_url = _hazirlar[0]["audio_url"]
_r = client.get(_url)
check("P7a) İmzalı bağlantıdan dosya iniyor (200 + audio/mpeg)",
      _r.status_code == 200
      and _r.headers.get("content-type", "").startswith("audio/mpeg"),
      f"{_r.status_code} {_r.headers.get('content-type')}")
check("P7b) İnen içerik üretilen MP3", _r.content == MP3, str(len(_r.content)))
check("P7c) Bozuk imza → 403",
      client.get(_url.split("&sig=")[0] + "&sig=" + "0" * 64).status_code == 403, "")
check("P7d) İmzasız erişim → 403",
      client.get(_url.split("?")[0]).status_code == 403, "")

_yol = _yollar[0]
_bitis, _imza = storage.imzala(_yol, omur_sn=-10)          # süresi DOLMUŞ
check("P7e) Süresi dolmuş imza → geçersiz",
      not storage.imza_gecerli_mi(_yol, _bitis, _imza), "")
check("P7f) Path traversal reddediliyor",
      not storage.yol_guvenli_mi("voice-audio/../../etc/passwd"), "")


# =============================================================================
# P6 — /voice/generate ÜRETİM YAPMAZ
# =============================================================================
_g = client.post("/api/v1/voice/generate", headers=H(t1),
                 json={"voiceId": "x", "storyId": PAKET[0]})
check("P6a) Hazır içerik → 200 + imzalı URL",
      _g.status_code == 200 and "/media/" in _g.json()["audio_url"],
      f"{_g.status_code} {_g.text[:140]}")
check("P6b) cached=True (üretim yapılmadı)",
      _g.json().get("cached") is True, str(_g.json()))
_tts_once = TTS_CAGRI["n"]
client.post("/api/v1/voice/generate", headers=H(t1),
            json={"voiceId": "x", "storyId": PAKET[0]})
check("P6c) Çağrı TTS'e HİÇ gitmedi", TTS_CAGRI["n"] == _tts_once,
      f"{TTS_CAGRI['n']} vs {_tts_once}")

_g2 = client.post("/api/v1/voice/generate", headers=H(t1),
                  json={"voiceId": "x", "storyId": "masal_kirmizi_baslikli_kiz"})
check("P6d) Pakette olmayan içerik → 409 + Türkçe detail",
      _g2.status_code == 409 and "hazır değil" in _g2.json().get("detail", ""),
      f"{_g2.status_code} {_g2.text[:140]}")
_g3 = client.post("/api/v1/voice/generate", headers=H(t1),
                  json={"voiceId": "x", "text": "serbest metin"})
check("P6e) Düz metin isteği → 409 (artık üretim yok)",
      _g3.status_code == 409, f"{_g3.status_code} {_g3.text[:140]}")


# =============================================================================
# P2 — ROLLBACK: DB yazımı patlarsa ElevenLabs sesi silinir
# =============================================================================
t2 = reg("paket2@test.com")
_once = len(SILINEN)
# Satır oluşturma bloğunun İÇİNDE patlat: `paket_boyutu()` tam orada çağrılıyor.
# (VoiceProfile sınıfını değiştirmek olmaz — aynı isim sorgularda da kullanılıyor.)
_gercek_paket = voice_router.voice_paket


class PatlayanPaket:
    @staticmethod
    def paket_boyutu():
        raise RuntimeError("DB yazımı simüle edilen hata")


voice_router.voice_paket = PatlayanPaket
try:
    r2 = klonla(t2)
finally:
    voice_router.voice_paket = _gercek_paket
check("P2a) DB yazımı patlayınca 503 dönüyor", r2.status_code == 503,
      f"{r2.status_code} {r2.text[:140]}")
check("P2b) ElevenLabs sesi HEMEN silindi (öksüz slot kalmadı)",
      len(SILINEN) > _once, f"silinen={SILINEN[_once:]}")
check("P2c) Öksüz ses hesapta kalmadı",
      all(v.startswith("ANLATICI") or v not in SILINEN for v in HESAPTAKI),
      str(HESAPTAKI))


# =============================================================================
# P3 — KISMİ BAŞARI
# =============================================================================
# 3a) 4 içerikten 1'i başarısız → %75 > %50 → ready/released
t3 = reg("paket3@test.com")
klonla(t3)
p3 = profil_of(t3)
TTS_BASARISIZ.clear()
TTS_BASARISIZ.add(PAKET[0])
s3 = voice_uretim.uret(p3.id)
TTS_BASARISIZ.clear()
p3 = profil_of(t3)
check("P3a) 3/4 üretildi → released (yarıdan fazlası)",
      s3["uretilen"] == N - 1 and p3.status == "released",
      f"{s3} status={p3.status}")
check("P3b) Eksik içerik error'a yazıldı",
      (p3.error or "").startswith("Üretilemedi:") and PAKET[0] in (p3.error or ""),
      str(p3.error))
_st3 = client.get("/api/v1/voice/stories", headers=H(t3)).json()
_uretilemeyen = [x for x in _st3["masallar"] + _st3["ninniler"]
                 if x["durum"] == "uretilemedi"]
check("P3c) stories'te o içerik 'uretilemedi' olarak işaretli",
      len(_uretilemeyen) == 1 and _uretilemeyen[0]["id"] == PAKET[0],
      str([x["id"] for x in _uretilemeyen]))

# 3b) 4 içerikten 3'ü başarısız → %25 < %50 → failed + hak iadesi
t4 = reg("paket4@test.com")
klonla(t4)
p4 = profil_of(t4)
_voice4 = p4.elevenlabs_voice_id
TTS_BASARISIZ.update(PAKET[:3])
s4 = voice_uretim.uret(p4.id)
TTS_BASARISIZ.clear()
p4 = profil_of(t4)
check("P3d) 1/4 üretildi → failed", p4.status == "failed", f"{s4} {p4.status}")
check("P3e) Hak İADE edildi (can_clone yeniden true)",
      client.get("/api/v1/voice/voice-status",
                 headers=H(t4)).json()["can_clone"] is True,
      str(client.get("/api/v1/voice/voice-status", headers=H(t4)).json()))
check("P3f) Başarısız üretimde de ses silindi (slot tutulmuyor)",
      _voice4 in SILINEN and _voice4 not in HESAPTAKI, str(HESAPTAKI))
check("P3g) error kullanıcıya ne olduğunu söylüyor",
      "iade" in (p4.error or "").lower(), str(p4.error))


# =============================================================================
# P4 — RESTART: yarım kalan üretim devam eder
# =============================================================================
t5 = reg("paket5@test.com")
klonla(t5)
p5 = profil_of(t5)
# İlk iki içeriği üretilmiş gibi göster (yarım kalmış üretim simülasyonu).
db = SessionLocal()
try:
    prof = db.get(VoiceProfile, p5.id)
    prof.status = "generating"
    prof.progress_total = N
    prof.progress_done = 2
    for cid in PAKET[:2]:
        yol = storage.ses_yolu(prof.user_id, prof.id, cid)
        storage.depo().yaz(yol, MP3)
        db.add(VoiceAudio(voice_profile_id=prof.id, content_id=cid,
                          storage_path=yol, bytes=len(MP3)))
    db.commit()
finally:
    db.close()

_tts_once = TTS_CAGRI["n"]
_devam = voice_uretim.bekleyenleri_devam_ettir()
for _ in range(60):                       # havuz iş parçacığını bekle
    if profil_of(t5).status in ("released", "ready", "failed"):
        break
    time.sleep(0.1)
p5 = profil_of(t5)
check("P4a) Yarım kalan üretim kuyruğa alındı", _devam >= 1, str(_devam))
check("P4b) Üretim tamamlandı (released)", p5.status == "released", p5.status)
check("P4c) Tamamlanmış içerikler YENİDEN üretilmedi (2 TTS çağrısı)",
      TTS_CAGRI["n"] - _tts_once == N - 2,
      f"{TTS_CAGRI['n'] - _tts_once} çağrı, beklenen {N - 2}")
db = SessionLocal()
try:
    _n5 = db.query(VoiceAudio).filter(
        VoiceAudio.voice_profile_id == p5.id).count()
finally:
    db.close()
check("P4d) Toplam N ses kaydı (kopya satır yok)", _n5 == N, str(_n5))


# =============================================================================
# P8 — GÜNLÜK TEMİZLİK
# =============================================================================
HESAPTAKI.extend(["ARTIK1", "ARTIK2"])
t6 = reg("paket6@test.com")
klonla(t6)
p6 = profil_of(t6)                         # status=cloning → KORUNMALI
_koru = p6.elevenlabs_voice_id
_sil_once = len(SILINEN)
tem = voice_temizlik.gunluk_temizlik()
check("P8a) Artık sesler silindi", "ARTIK1" in SILINEN and "ARTIK2" in SILINEN,
      str(SILINEN[_sil_once:]))
check("P8b) Anlatıcı sesi KORUNDU (silinmedi)",
      "ANLATICI" in HESAPTAKI and "ANLATICI" not in SILINEN, str(HESAPTAKI))
check("P8c) Üretimi SÜREN profilin sesi korundu",
      _koru in HESAPTAKI, f"{_koru} / {HESAPTAKI}")
check("P8d) Sayım raporlanıyor", tem["silinen"] >= 2, str(tem))


# =============================================================================
# P5 — HESAP SİLME (KVKK)
# =============================================================================
t7 = reg("paket7@test.com")
klonla(t7)
p7 = profil_of(t7)
voice_uretim.uret(p7.id)
_klasor = storage.ses_klasoru(p7.user_id)
check("P5a) Hazırlık: paket depoda", storage.depo().var_mi(
    storage.ses_yolu(p7.user_id, p7.id, PAKET[0])), "")

_sil = client.delete("/api/v1/auth/account", headers=H(t7))
check("P5b) Hesap silme 200", _sil.status_code == 200, _sil.text[:120])
check("P5c) Depo klasörü silindi",
      not (_MEDYA / _klasor).exists(), str(_MEDYA / _klasor))
db = SessionLocal()
try:
    _kalan_profil = db.query(VoiceProfile).filter(
        VoiceProfile.id == p7.id).count()
    _kalan_ses = db.query(VoiceAudio).filter(
        VoiceAudio.voice_profile_id == p7.id).count()
finally:
    db.close()
check("P5d) voice_profiles satırı gitti", _kalan_profil == 0, str(_kalan_profil))
check("P5e) voice_audios satırları gitti", _kalan_ses == 0, str(_kalan_ses))


# =============================================================================
# RAPOR
# =============================================================================
print("\n" + "=" * 74)
print("SES PAKETİ v2.3 — ÜRET VE BIRAK")
print("=" * 74)
_gecen = 0
for ad, ok, detay in results:
    print(f"  {'[PASS]' if ok else '[FAIL]'} {ad}")
    if not ok:
        print(f"         → {detay}")
    _gecen += int(ok)
print("-" * 74)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
print("=" * 74)
sys.exit(0 if _gecen == len(results) else 1)
