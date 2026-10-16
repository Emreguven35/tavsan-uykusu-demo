"""
Eski tam paket + premium olmayan anne — önizleme (2026-10-16). Ağ YOK.

Sorun: önizleme sisteminden (0029) önce kaydedilmiş tam paket (amac NULL)
sahibi premium değilse mobil 30 sn önizleme ekranını açıp `onizleme` işaretli
masal bekliyordu; sunucu hiç vermiyordu → "Masalın hazırlanıyor"da sonsuz
bekleme (BETA_HARIC hesabı, beta kapanınca tüm eski paketliler).

  E  Eski paketli ücretsiz: önizleme masalı işaretli + 30 sn KESİK kopyanın
     bağlantısı; tam dosya bağlantısı HİÇ verilmez; voice-status onizleme=true;
     kesik kopya yazılamazsa içerik kilitli kalır.
  P  Premium: aynı eski paketin tam dosyaları, onizleme yok.
  N  Ninni: yalnız paketteyse ve dosyası STS ile üretildiyse (ya da dosyası
     yoksa) listelenir; eski TTS ninni dosyası listelenmez, SİLİNMEZ.
  M  Masalsız eski paket (ücretsiz): status "none" + bir yeniden kayıt hakkı;
     POST /clone beklemeyi atlar, yeni kayıt önizleme profili; hak tek sefer.
  U  Üretim `yontem` yazar (tts).

Çalıştırma: python tests/test_ses_eski_paket_onizleme.py
"""
import io
import os
import shutil
import sys
import tempfile
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

_DB = Path(tempfile.gettempdir()) / "ses_eski_paket_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "ses_eski_paket_medya"
shutil.rmtree(_MEDYA, ignore_errors=True)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ.pop("ANNE_SESI_STS", None)
os.environ.pop("VOICE_PAKET_NINNILER", None)
os.environ.pop("VOICE_PAKET_MASALLAR", None)
os.environ.pop("VOICE_ONIZLEME_SN", None)
os.environ.pop("PUBLIC_BASE_URL", None)
# Beta açık; ücretsiz hesaplar BETA_HARIC ile gerçek kademeyi görür (canlıdaki
# mrguven35+apple durumu).
os.environ["BETA_MODE"] = "true"
os.environ["BETA_HARIC_EMAILS"] = "ucretsiz1@test.com,ucretsiz2@test.com,masalsiz@test.com"

from api.db import Base, SessionLocal, engine                  # noqa: E402
import api.models                                              # noqa: E402,F401
from api.models import User, VoiceAudio, VoiceProfile          # noqa: E402
from api.routers import voice as voice_router                  # noqa: E402
from api.services import mp3, storage, voice_paket, voice_uretim  # noqa: E402
from api.services import voice as voice_svc                    # noqa: E402

Base.metadata.create_all(bind=engine)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def mp3_uret(saniye: float, etiket: bytes = b"\x00") -> bytes:
    """MPEG-1 Layer III 128 kbps 44.1 kHz (417 bayt / 26.12 ms çerçeve)."""
    cerceve = b"\xff\xfb\x90\x00" + etiket * (417 - 4)
    n = int(saniye / (1152 / 44100))
    return b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + cerceve * n


# --- Sahte ElevenLabs / kuyruk ------------------------------------------------
KLON = {"n": 0}
KUYRUK: list[str] = []


def _sahte_klon(name, audio_bytes, filename, content_type):
    KLON["n"] += 1
    return {"ok": True, "voice_id": f"VOICE{KLON['n']}"}


voice_svc.clone_voice = _sahte_klon
voice_svc.delete_voice = lambda v: {"ok": True, "error": None, "status": 200}
_gercek_kuyruga_al = voice_uretim.kuyruga_al
voice_uretim.kuyruga_al = lambda pid: KUYRUK.append(str(pid)) or True

from fastapi.testclient import TestClient                     # noqa: E402
from api.main import app                                      # noqa: E402

client = TestClient(app)
MASALLAR = [x["id"] for x in voice_paket.paket_icerikleri()]
ONIZLEME = voice_paket.onizleme_icerik_id()
ESKI_NINNILER = ["ninni_dandini", "ninni_uyu_yavrum", "ninni_ay_isigi"]


def reg(email):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "TestPass123!"})
    return r.json()["access_token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def uid_of(tok):
    from api.services.security import decode_access_token
    return _uuid.UUID(decode_access_token(tok)["sub"])


def eski_paket(tok, icerikler, gun_once=8, yontem=None):
    """0029 öncesi tam paket: amac NULL, released, dosyalar depoda."""
    uid = uid_of(tok)
    db = SessionLocal()
    try:
        an = datetime.now(timezone.utc) - timedelta(days=gun_once)
        p = VoiceProfile(user_id=uid, elevenlabs_voice_id=None, status="released",
                         progress_done=len(icerikler), progress_total=len(icerikler),
                         last_cloned_at=an, released_at=an, amac=None)
        db.add(p)
        db.commit()
        db.refresh(p)
        for cid in icerikler:
            yol = storage.ses_yolu(uid, p.id, cid)
            veri = mp3_uret(120 if cid.startswith("masal_") else 60)
            storage.depo().yaz(yol, veri)
            y = yontem.get(cid) if isinstance(yontem, dict) else yontem
            db.add(VoiceAudio(voice_profile_id=p.id, content_id=cid, storage_path=yol,
                              bytes=len(veri), duration_sec=120, yontem=y))
        db.commit()
        return p.id
    finally:
        db.close()


def stories(tok):
    r = client.get("/api/v1/voice/stories", headers=H(tok))
    assert r.status_code == 200, r.text
    return r.json()


def status_of(tok):
    r = client.get("/api/v1/voice/voice-status", headers=H(tok))
    assert r.status_code == 200, r.text
    return r.json()


def indir(url):
    return client.get(url)


TAM_SN = mp3.sure(mp3_uret(120))

# =============================================================================
# E — ESKİ PAKETLİ ÜCRETSİZ KULLANICI ÖNİZLEME ALIYOR
# =============================================================================
t1 = reg("ucretsiz1@test.com")
pid1 = eski_paket(t1, ESKI_NINNILER + [ONIZLEME])
s1 = stories(t1)
m1 = {x["id"]: x for x in s1["masallar"]}
on = m1.get(ONIZLEME) or {}
check("E1) Önizleme masalı işaretli, kilitsiz, bağlantılı",
      on.get("onizleme") is True and on.get("locked") is False and on.get("audio_url"),
      str(on))
check("E2) Bağlantı KESİK kopyaya (…onizleme30.mp3), tam dosyaya değil",
      ".onizleme30.mp3?" in (on.get("audio_url") or "")
      and f"/{ONIZLEME}.mp3?" not in (on.get("audio_url") or ""), on.get("audio_url"))
r = indir(on.get("audio_url") or "/yok")
_sn = mp3.sure(r.content) if r.status_code in (200, 206) else -1
check("E3) İndirilen dosya ≤ 30 sn (tam dosya 120 sn)",
      r.status_code in (200, 206) and 0 < _sn <= 30.5 and TAM_SN > 100,
      f"HTTP {r.status_code} sure={_sn} tam={TAM_SN}")
_urlli = [x for x in s1["masallar"] + s1["ninniler"] if x.get("audio_url")]
check("E4) Başka HİÇBİR içerik bağlantı taşımıyor (tam dosya sızmıyor)",
      [x["id"] for x in _urlli] == [ONIZLEME], str([x["id"] for x in _urlli]))
check("E5) Diğer masallar kilitli",
      all(x["locked"] for x in s1["masallar"] if x["id"] != ONIZLEME),
      str([(x["id"], x["locked"]) for x in s1["masallar"]]))
check("E6) Ninni listesi boş (eski TTS ninnileri listelenmez)",
      s1["ninniler"] == [], str(s1["ninniler"]))
v1 = status_of(t1)
check("E7) voice-status onizleme=true, durum released",
      v1["onizleme"] is True and v1["status"] == "released", str(v1))
_kopya = _MEDYA / storage.ses_yolu(uid_of(t1), pid1, f"{ONIZLEME}.onizleme30")
_mtime = _kopya.stat().st_mtime_ns if _kopya.exists() else None
s1b = stories(t1)
check("E8) Kopya bir kez yazılır, ikinci istekte yeniden kullanılır",
      _mtime is not None and _kopya.stat().st_mtime_ns == _mtime
      and ".onizleme30.mp3?" in ({x["id"]: x for x in s1b["masallar"]}[ONIZLEME]
                                 .get("audio_url") or ""), str(_kopya))
db = SessionLocal()
try:
    _ninni_dosya = all((_MEDYA / a.storage_path).exists() for a in db.query(VoiceAudio)
                       .filter(VoiceAudio.voice_profile_id == pid1).all())
    _ninni_satir = db.query(VoiceAudio).filter(
        VoiceAudio.voice_profile_id == pid1,
        VoiceAudio.content_id.in_(ESKI_NINNILER)).count()
finally:
    db.close()
check("E9) Eski ninni dosyaları ve satırları SİLİNMEDİ",
      _ninni_dosya and _ninni_satir == 3, f"{_ninni_dosya} {_ninni_satir}")

# Ayardaki önizleme masalı yoksa katalog sırasıyla ilk hazır masal.
t1c = reg("ucretsiz2@test.com")
_baska = [m for m in MASALLAR if m != ONIZLEME][-1]
eski_paket(t1c, [_baska])
s1c = {x["id"]: x for x in stories(t1c)["masallar"]}
check("E10) Önizleme masalı yoksa elindeki masal önizlenir (kesik)",
      s1c[_baska].get("onizleme") is True
      and ".onizleme30.mp3?" in (s1c[_baska].get("audio_url") or ""), str(s1c[_baska]))

# Kesik kopya yazılamazsa (kaynak okunamıyor) → kilitli, bağlantı yok.
_gercek_oku = storage.YerelDepo.oku
storage.YerelDepo.oku = lambda self, yol: None
os.environ["VOICE_ONIZLEME_SN"] = "20"          # yeni kopya adı → yeniden üretim dener
try:
    s1d = {x["id"]: x for x in stories(t1)["masallar"]}
finally:
    storage.YerelDepo.oku = _gercek_oku
    os.environ.pop("VOICE_ONIZLEME_SN", None)
check("E11) Kopya üretilemezse içerik kilitli, bağlantı YOK (tam dosya verilmez)",
      s1d[ONIZLEME]["locked"] is True and s1d[ONIZLEME]["audio_url"] is None
      and s1d[ONIZLEME]["onizleme"] is False, str(s1d[ONIZLEME]))

# =============================================================================
# P — PREMIUM KULLANICI TAM PAKETİ GÖRÜYOR
# =============================================================================
t2 = reg("premium1@test.com")                 # beta: premium
pid2 = eski_paket(t2, ESKI_NINNILER + [ONIZLEME])
s2 = stories(t2)
m2 = {x["id"]: x for x in s2["masallar"]}
check("P1) Premium: masal listesi tam paket (5 masal, hepsi pakette)",
      sorted(m2) == sorted(MASALLAR) and all(x["in_package"] for x in m2.values()),
      str(sorted(m2)))
check("P2) Premium: hazır masal TAM dosya bağlantısı, onizleme yok, kilitsiz",
      m2[ONIZLEME]["locked"] is False and m2[ONIZLEME]["onizleme"] is False
      and f"/{ONIZLEME}.mp3?" in (m2[ONIZLEME]["audio_url"] or ""), str(m2[ONIZLEME]))
r = indir(m2[ONIZLEME]["audio_url"])
check("P3) Premium: indirilen dosya tam süre",
      r.status_code in (200, 206) and mp3.sure(r.content) > 100, f"HTTP {r.status_code}")
v2 = status_of(t2)
check("P4) Premium: voice-status onizleme=false, kayıt hakkı açılmadı",
      v2["onizleme"] is False and v2["can_clone"] is False
      and v2["status"] == "released", str(v2))
check("P5) Premium: eski TTS ninnileri listelenmez",
      s2["ninniler"] == [], str(s2["ninniler"]))
check("P6) Premium için kesik kopya üretilmedi",
      not (_MEDYA / storage.ses_yolu(uid_of(t2), pid2, f"{ONIZLEME}.onizleme30")).exists())

# =============================================================================
# N — NİNNİ YALNIZ STS + PAKETTE
# =============================================================================
os.environ["VOICE_PAKET_NINNILER"] = "ninni_dandini,ninni_uyu_yavrum,ninni_e_bebegim"
_gercek_uretilebilir = voice_paket.ninni_uretilebilir
voice_paket.ninni_uretilebilir = lambda cid: True     # referans + STS varmış gibi
try:
    t3 = reg("premium2@test.com")
    eski_paket(t3, ["ninni_dandini", "ninni_uyu_yavrum", "ninni_ay_isigi", ONIZLEME],
               yontem={"ninni_dandini": "sts", "ninni_uyu_yavrum": None,
                       "ninni_ay_isigi": "sts", ONIZLEME: "tts"})
    n3 = {x["id"]: x for x in stories(t3)["ninniler"]}
finally:
    voice_paket.ninni_uretilebilir = _gercek_uretilebilir
    os.environ.pop("VOICE_PAKET_NINNILER", None)
check("N1) Pakette + STS dosyası → listelenir, hazır",
      "ninni_dandini" in n3 and n3["ninni_dandini"]["hazir"] is True, str(list(n3)))
check("N2) Pakette ama dosyası TTS (eski, yontem NULL) → listelenmez",
      "ninni_uyu_yavrum" not in n3, str(list(n3)))
check("N3) STS dosyası var ama pakette değil → listelenmez",
      "ninni_ay_isigi" not in n3, str(list(n3)))
check("N4) Pakette, dosyası henüz yok → listelenir (hazırlanıyor)",
      "ninni_e_bebegim" in n3 and n3["ninni_e_bebegim"]["hazir"] is False, str(list(n3)))
check("N5) STS kapalı (canlı): paket ninnisiz, ninni listesi boş",
      stories(t3)["ninniler"] == [], "")

# =============================================================================
# M — MASALSIZ ESKİ PAKET: BİR YENİDEN KAYIT HAKKI
# =============================================================================
t4 = reg("masalsiz@test.com")
eski_paket(t4, ESKI_NINNILER)
v4 = status_of(t4)
check("M1) voice-status 'none' + can_clone + kayıt hakkı 1 (kayıt ekranı)",
      v4["status"] == "none" and v4["can_clone"] is True
      and v4["kayit_hakki"] == {"kalan": 1, "toplam": 1}, str(v4))
s4 = stories(t4)
check("M2) Masalsız: hiçbir bağlantı yok, ninni listesi boş",
      not any(x.get("audio_url") for x in s4["masallar"] + s4["ninniler"])
      and s4["ninniler"] == [], "")
r = client.post("/api/v1/voice/clone", headers=H(t4),
                files={"audio": ("s.mp3", io.BytesIO(b"A" * 900), "audio/mpeg")},
                data={"name": "Ses"})
check("M3) POST /clone 30 gün beklemeden 200, önizleme kaydı",
      r.status_code == 200 and r.json().get("onizleme") is True, r.text[:200])
db = SessionLocal()
try:
    # Sunucunun kendi seçimi (created_at saniye hassasiyetinde berabere kalabilir).
    _son = voice_router._son_profil(db, db.get(User, uid_of(t4)))
    _amac = _son.amac
finally:
    db.close()
check("M4) Yeni profil önizleme amaçlı ve üretime alındı",
      _amac == voice_paket.AMAC_ONIZLEME and str(_son.id) in KUYRUK, str(_amac))
v4b = status_of(t4)
check("M5) Hak tek sefer: yeni kayıttan sonra can_clone=false",
      v4b["can_clone"] is False and v4b["onizleme"] is True, str(v4b))
r = client.post("/api/v1/voice/clone", headers=H(t4),
                files={"audio": ("s.mp3", io.BytesIO(b"A" * 900), "audio/mpeg")},
                data={"name": "Ses"})
check("M6) İkinci kayıt 429", r.status_code == 429, str(r.status_code))

t5 = reg("premium3@test.com")                 # premium, masalsız → hak YOK
eski_paket(t5, ESKI_NINNILER)
v5 = status_of(t5)
check("M7) Premium masalsız eski paket: hak açılmaz (durum released)",
      v5["status"] == "released" and v5["can_clone"] is False, str(v5))

# =============================================================================
# U — ÜRETİM YÖNTEMİ YAZILIR
# =============================================================================
voice_uretim._seslendir = lambda voice_id, metin, user_id: mp3_uret(90)
voice_uretim._sesi_birak = lambda voice_id: True
db = SessionLocal()
try:
    _p = db.get(VoiceProfile, _son.id)
    _p.elevenlabs_voice_id = "VOICE_U"
    db.commit()
finally:
    db.close()
_s = voice_uretim.uret(_son.id)
db = SessionLocal()
try:
    _a = db.query(VoiceAudio).filter(VoiceAudio.voice_profile_id == _son.id).all()
finally:
    db.close()
check("U1) Önizleme üretimi: tek dosya, yontem='tts', ≤ 30 sn",
      len(_a) == 1 and _a[0].yontem == "tts"
      and mp3.sure((_MEDYA / _a[0].storage_path).read_bytes()) <= 30.5,
      f"{_s} {[(a.content_id, a.yontem) for a in _a]}")
s4c = {x["id"]: x for x in stories(t4)["masallar"]}
check("U2) Yeni önizleme profili: masal onizleme + bağlantı (mevcut yol)",
      s4c[ONIZLEME]["onizleme"] is True and s4c[ONIZLEME]["audio_url"], str(s4c[ONIZLEME]))

# =============================================================================
print("\n" + "=" * 74)
print("ESKİ PAKET + ÜCRETSİZ — ÖNİZLEME / NİNNİ / KAYIT HAKKI")
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
shutil.rmtree(_MEDYA, ignore_errors=True)
sys.exit(0 if _gecen == len(results) else 1)
