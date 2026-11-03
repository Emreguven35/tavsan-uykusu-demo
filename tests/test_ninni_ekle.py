"""
"Ninnilerini al" (ANNE_SESI_NINNI_EKLE, 2026-11-03). Ağ YOK (ElevenLabs sahte).

  F  Bayrak kapalı: uygun=false/0/None (stories + voice-status), amac=ninni_ekle 422.
  U  Uygunluk: released paket + premium + hak + eksik STS ninni; önizleme,
     premium değil, üretim sürüyor, profil yok, eksik yok → uygun değil.
  R  Açık rıza yine zorunlu (build 28+ onaysız 403, klon açılmaz, hak yanmaz).
  A  Mutlu yol: aylık bekleme muaf; yalnız eksik ninniler STS; eski TTS
     ninninin ÜZERİNE yazılır; masallar BAYT BAYT aynı; status/progress/error
     aynı; klon silinir; hak bir kez.
  P  Kısmi: üretilemeyen ninni listelenmez, hak iade edilmez.
  H  Hata: hiç ninni yok → uretilemedi + hak İADE + klon silinir + status
     released (%50 kuralı yok); döngüde istisna → klon yine silinir; klon
     açılamazsa hak iade; klon silinemezse alan kalır, günlük temizlik 2 sa
     korur sonra siler; iş sürerken Sesimi sil / yeni kayıt 409; yeniden
     başlatmada devam; hesap silmede geçici klon silinir; eşzamanlı hak tek.
  K  Kredi: saniye × 11 (ölçüm); kuru çalıştırma çıktısı (hash, kişisel bilgi yok).

Çalıştırma: python tests/test_ninni_ekle.py
"""
import hashlib
import io
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "ninni_ekle_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "ninni_ekle_medya"
_REF = Path(tempfile.gettempdir()) / "ninni_ekle_referans"
shutil.rmtree(_MEDYA, ignore_errors=True)
shutil.rmtree(_REF, ignore_errors=True)
_REF.mkdir()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ANNE_SESI_STS"] = "true"
for _k in ("VOICE_PAKET_NINNILER", "VOICE_PAKET_MASALLAR", "VOICE_ONIZLEME_SN",
           "PUBLIC_BASE_URL", "ANNE_SESI_NINNI_EKLE"):
    os.environ.pop(_k, None)
os.environ["BETA_MODE"] = "true"
os.environ["BETA_HARIC_EMAILS"] = "ucretsiz@test.com"

from api.db import Base, SessionLocal, engine                  # noqa: E402
import api.models                                              # noqa: E402,F401
from api.models import ApiUsage, User, VoiceAudio, VoiceProfile  # noqa: E402
from api.services import (mp3, ninni_ekle, storage, usage,     # noqa: E402
                          voice_sts, voice_temizlik, voice_uretim)
from api.services import voice as voice_svc                    # noqa: E402

voice_sts.REFERANS_KLASOR = _REF
usage.SENKRON_MOD = True
Base.metadata.create_all(bind=engine)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def mp3_uret(saniye: float, etiket: bytes = b"\x00") -> bytes:
    cerceve = b"\xff\xfb\x90\x00" + etiket * 413
    return b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + cerceve * int(saniye / (1152 / 44100))


NINNILER = ["ninni_dandini", "ninni_uyusun_da_buyusun", "ninni_uykudan_uyanmis"]
MASALLAR = ["masal_aysecik_uyku_perisi", "masal_kelogan_sihirli_degnek",
            "masal_cirkin_ordek_yavrusu", "masal_uc_kucuk_domuzcuk",
            "masal_kirmizi_baslikli_kiz"]
for _cid, _sn in zip(NINNILER, (90, 60, 120)):
    (_REF / f"{_cid}.mp3").write_bytes(mp3_uret(_sn))

# --- Sahte ElevenLabs ---------------------------------------------------------
EL = {"klon": [], "silinen": [], "sts": [], "klon_hata": False, "silme_hata": False,
      "sts_hata": set(), "sts_istisna": None, "liste": []}


def _klon(name, audio_bytes, filename, content_type):
    if EL["klon_hata"]:
        return {"ok": False, "reason": "bilinmeyen", "error": "x", "status": 502}
    vid = f"GECICI{len(EL['klon']) + 1}"
    EL["klon"].append(vid)
    return {"ok": True, "voice_id": vid}


def _sil(vid):
    if EL["silme_hata"]:
        return {"ok": False, "error": "upstream 500", "status": 500}
    EL["silinen"].append(vid)
    return {"ok": True, "error": None, "status": 200}


def _sts(icerik, voice_id, user_id=None, en_fazla_sn=None):
    EL["sts"].append((icerik["id"], voice_id))
    if EL["sts_istisna"] == icerik["id"]:
        raise RuntimeError("beklenmedik")
    if icerik["id"] in EL["sts_hata"]:
        return None
    return mp3_uret(60, b"\x11")                  # STS çıktısı (eski TTS'ten farklı bayt)


voice_svc.clone_voice = _klon
voice_svc.delete_voice = _sil
voice_svc.list_cloned_voices = lambda: {"ok": True, "voices": [{"voice_id": v} for v in EL["liste"]]}
voice_sts.seslendir = _sts
# Üretim senkron: kuyruğa alınan iş hemen çalışır (normal paket kuyruğu kapalı).
KUYRUK = []
ninni_ekle.kuyruga_al = lambda pid: KUYRUK.append(str(pid)) or ninni_ekle.uret(pid)
voice_uretim.kuyruga_al = lambda pid: True

from fastapi.testclient import TestClient                     # noqa: E402
from api.main import app                                      # noqa: E402

client = TestClient(app)
SES_ONAY = [{"tur": "acik_riza_ses", "onay": True, "metin_surumu": "kvkk-2026-10"}]


def anne(email, onay=True):
    govde = {"email": email, "password": "TestPass123!"}
    if onay:
        govde["consents"] = SES_ONAY
    tok = client.post("/api/v1/auth/register", json=govde).json()["access_token"]
    db = SessionLocal()
    uid = db.query(User).filter(User.email == email).one().id
    db.close()
    return {"Authorization": f"Bearer {tok}"}, uid


def eski_paket(uid, *, amac=None, status="released", klon_gun_once=3):
    """STS öncesi paket: Dandini TTS (eski) + 5 TTS masal."""
    db = SessionLocal()
    try:
        an = datetime.now(timezone.utc) - timedelta(days=klon_gun_once)
        p = VoiceProfile(user_id=uid, status=status, amac=amac, last_cloned_at=an,
                         released_at=an, progress_done=6, progress_total=6,
                         error="eski hata metni")
        db.add(p)
        db.commit()
        for cid in ["ninni_dandini"] + MASALLAR:
            yol = storage.ses_yolu(uid, p.id, cid)
            veri = mp3_uret(30 if cid.startswith("ninni") else 200, b"\x22")
            storage.depo().yaz(yol, veri)
            db.add(VoiceAudio(voice_profile_id=p.id, content_id=cid, storage_path=yol,
                              bytes=len(veri), duration_sec=30, yontem=None if cid.startswith("ninni") else "tts"))
        db.commit()
        return p.id
    finally:
        db.close()


def kaydet(h, amac="ninni_ekle", build=30):
    hh = dict(h)
    if build is not None:
        hh["X-App-Version"] = f"1.0.0+{build}"
    return client.post("/api/v1/voice/clone", headers=hh, data={"amac": amac},
                       files={"audio": ("s.m4a", io.BytesIO(b"SES" * 200), "audio/mp4")})


def stories(h):
    return client.get("/api/v1/voice/stories", headers=h).json()


def vstatus(h):
    return client.get("/api/v1/voice/voice-status", headers=h).json()


def profil(pid):
    db = SessionLocal()
    try:
        p = db.get(VoiceProfile, pid)
        db.expunge(p)
        return p
    finally:
        db.close()


def kullanici(uid):
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        db.expunge(u)
        return u
    finally:
        db.close()


def dosyalar(uid, pid):
    """content_id → (sha1 bayt, yontem, satır yolu)."""
    db = SessionLocal()
    try:
        out = {}
        for a in db.query(VoiceAudio).filter(VoiceAudio.voice_profile_id == pid).all():
            veri = storage.depo().oku(a.storage_path) or b""
            out[a.content_id] = (hashlib.sha1(veri).hexdigest(), a.yontem, a.storage_path)
        return out
    finally:
        db.close()


def ac():
    os.environ["ANNE_SESI_NINNI_EKLE"] = "true"


def kapat():
    os.environ.pop("ANNE_SESI_NINNI_EKLE", None)


# =============================================================================
# F — bayrak kapalı
# =============================================================================
kapat()
hA, uA = anne("mutlu@test.com")
pA = eski_paket(uA)
sA, vA = stories(hA), vstatus(hA)
check("F1) Kapalı: stories ninni_ekle_uygun=false, sayisi 0, durumu None",
      sA["ninni_ekle_uygun"] is False and sA["ninni_ekle_sayisi"] == 0
      and sA["ninni_ekle_durumu"] is None, str({k: sA[k] for k in sA if k.startswith("ninni_ekle")}))
check("F2) Kapalı: voice-status aynı alanlar false",
      vA["ninni_ekle_uygun"] is False and vA["ninni_ekle_durumu"] is None, str(vA))
_n = len(EL["klon"])
r = kaydet(hA)
check("F3) Kapalı: amac=ninni_ekle → 422, klon açılmadı",
      r.status_code == 422 and len(EL["klon"]) == _n, r.text[:200])

# =============================================================================
# U — uygunluk
# =============================================================================
ac()
sA, vA = stories(hA), vstatus(hA)
check("U1) Uygun: stories uygun=true, sayisi=3; voice-status aynı",
      sA["ninni_ekle_uygun"] is True and sA["ninni_ekle_sayisi"] == 3
      and vA["ninni_ekle_uygun"] is True and vA["ninni_ekle_sayisi"] == 3,
      str((sA["ninni_ekle_sayisi"], vA["ninni_ekle_uygun"])))
check("U2) Eski TTS Dandini listelenmiyor (ninni listesi boş)", sA["ninniler"] == [],
      str([x["id"] for x in sA["ninniler"]]))
hO, uO = anne("onizleme@test.com")
eski_paket(uO, amac="onizleme")
hF, uF = anne("ucretsiz@test.com")
eski_paket(uF)
hG, uG = anne("suruyor@test.com")
eski_paket(uG, status="generating")
hY, uY = anne("profilsiz@test.com")
check("U3) Önizleme profili / premium değil / üretim sürüyor / profil yok → uygun değil",
      all(stories(h)["ninni_ekle_uygun"] is False for h in (hO, hF, hG, hY)), "")
check("U4) Uygun değilse kayıt 409 ninni_ekle_uygun_degil + sebep, klon yok",
      kaydet(hF).json().get("sebep") == "premium_degil"
      and kaydet(hO).json().get("code") == "ninni_ekle_uygun_degil"
      and len(EL["klon"]) == _n, "")

# =============================================================================
# R — rıza
# =============================================================================
hR, uR = anne("onaysiz@test.com", onay=False)
eski_paket(uR)
r = kaydet(hR, build=30)
check("R1) Build 30 onaysız → 403 riza_gerekli; klon yok, hak yanmadı",
      r.status_code == 403 and r.json().get("code") == "riza_gerekli"
      and len(EL["klon"]) == _n and kullanici(uR).ses_ninni_ekle_at is None, r.text[:200])

# =============================================================================
# A — mutlu yol
# =============================================================================
once = dosyalar(uA, pA)
pA_once = profil(pA)
r = kaydet(hA)
pA_sonra = profil(pA)
sonra = dosyalar(uA, pA)
check("A1) 200, ninni_ekle=true, onizleme=false, tek klon",
      r.status_code == 200 and r.json()["ninni_ekle"] is True and r.json()["onizleme"] is False
      and len(EL["klon"]) == _n + 1, r.text[:200])
_vid = EL["klon"][-1]
check("A2) Aylık bekleme muaf: 3 gün önce kayıt yapmış anne kabul edildi, last_cloned_at değişmedi",
      pA_sonra.last_cloned_at == pA_once.last_cloned_at, "")
check("A3) Yalnız 3 eksik ninni STS'e gitti, geçici klonla; masal STS'e gitmedi",
      sorted(c for c, v in EL["sts"]) == sorted(NINNILER)
      and all(v == _vid for _, v in EL["sts"]), str(EL["sts"]))
check("A4) 3 ninni yontem=sts, depoda",
      all(sonra[c][1] == "sts" and sonra[c][0] for c in NINNILER), str(sonra))
check("A5) Eski TTS Dandini'nin ÜZERİNE yazıldı (aynı yol, farklı bayt, tek satır)",
      sonra["ninni_dandini"][2] == once["ninni_dandini"][2]
      and sonra["ninni_dandini"][0] != once["ninni_dandini"][0], "")
check("A6) Masallar BAYT BAYT aynı (yol, içerik, yöntem)",
      all(sonra[m] == once[m] for m in MASALLAR), "")
check("A7) Profil status released, progress ve error AYNI",
      pA_sonra.status == "released" and pA_sonra.progress_done == pA_once.progress_done
      and pA_sonra.progress_total == pA_once.progress_total
      and pA_sonra.error == pA_once.error, f"{pA_sonra.status} {pA_sonra.error}")
check("A8) Klon silindi, alan boş, durum tamam, hak kullanıldı",
      _vid in EL["silinen"] and pA_sonra.ninni_ekle_voice_id is None
      and pA_sonra.ninni_ekle_durum == "tamam" and kullanici(uA).ses_ninni_ekle_at is not None, "")
sA = stories(hA)
check("A9) Stories: 3 ninni hazır; uygun=false; durumu tamam",
      [x["id"] for x in sA["ninniler"]] == NINNILER and all(x["hazir"] for x in sA["ninniler"])
      and sA["ninni_ekle_uygun"] is False and sA["ninni_ekle_durumu"] == "tamam", str(sA["ninniler"])[:300])
r = kaydet(hA)
check("A10) İkinci deneme 409 (tek seferlik)", r.status_code == 409, r.text[:200])
db = SessionLocal()
_kl = db.query(ApiUsage).filter(ApiUsage.user_id == uA, ApiUsage.operation == "voice_clone").count()
db.close()
check("A11) Klon kullanım defterine yazıldı", _kl == 1, str(_kl))

# =============================================================================
# P — kısmi
# =============================================================================
hP, uP = anne("kismi@test.com")
pP = eski_paket(uP)
onceP = dosyalar(uP, pP)
EL["sts_hata"] = {"ninni_dandini"}
kaydet(hP)
EL["sts_hata"] = set()
sP, pPs = stories(hP), profil(pP)
check("P1) Dandini üretilemedi: listelenmez, eski TTS dosyası olduğu gibi",
      [x["id"] for x in sP["ninniler"]] == ["ninni_uyusun_da_buyusun", "ninni_uykudan_uyanmis"]
      and dosyalar(uP, pP)["ninni_dandini"] == onceP["ninni_dandini"],
      str([x["id"] for x in sP["ninniler"]]))
check("P2) Kısmi: durum tamam, hak iade EDİLMEDİ, klon silindi, status released",
      pPs.ninni_ekle_durum == "tamam" and kullanici(uP).ses_ninni_ekle_at is not None
      and EL["klon"][-1] in EL["silinen"] and pPs.status == "released", "")

# =============================================================================
# H — hatalar
# =============================================================================
hH, uH = anne("hicbiri@test.com")
pH = eski_paket(uH)
onceH = dosyalar(uH, pH)
EL["sts_hata"] = set(NINNILER)
kaydet(hH)
EL["sts_hata"] = set()
pHs, sH = profil(pH), stories(hH)
check("H1) Hiç ninni yok → uretilemedi + hak İADE; status released (failed değil)",
      pHs.ninni_ekle_durum == "uretilemedi" and kullanici(uH).ses_ninni_ekle_at is None
      and pHs.status == "released", f"{pHs.ninni_ekle_durum} {pHs.status}")
check("H2) Klon silindi; dosyalar aynı; kart yeniden uygun + durumu uretilemedi",
      EL["klon"][-1] in EL["silinen"] and dosyalar(uH, pH) == onceH
      and sH["ninni_ekle_uygun"] is True and sH["ninni_ekle_durumu"] == "uretilemedi", "")

hX, uX = anne("istisna@test.com")
pX = eski_paket(uX)
EL["sts_istisna"] = "ninni_uyusun_da_buyusun"
kaydet(hX)
EL["sts_istisna"] = None
pXs = profil(pX)
check("H3) Döngüde istisna: klon yine silindi, ilk ninni yazıldı, durum tamam",
      EL["klon"][-1] in EL["silinen"] and pXs.ninni_ekle_voice_id is None
      and dosyalar(uX, pX)["ninni_dandini"][1] == "sts" and pXs.ninni_ekle_durum == "tamam", "")

hK, uK = anne("klonhata@test.com")
pK = eski_paket(uK)
EL["klon_hata"] = True
r = kaydet(hK)
EL["klon_hata"] = False
check("H4) Klon açılamadı → hata kodu, hak iade, profil dokunulmadı",
      r.status_code >= 400 and kullanici(uK).ses_ninni_ekle_at is None
      and profil(pK).ninni_ekle_durum is None and stories(hK)["ninni_ekle_uygun"] is True,
      f"{r.status_code} {r.text[:150]}")

hS, uS = anne("silinemedi@test.com")
pS = eski_paket(uS)
EL["silme_hata"] = True
kaydet(hS)
EL["silme_hata"] = False
_kalan = profil(pS).ninni_ekle_voice_id
check("H5) Klon silinemedi → ninni_ekle_voice_id dolu kalır (temizlik için)",
      _kalan == EL["klon"][-1], str(_kalan))
# Günlük temizlik: iş bitmiş (tamam) → korunmaz, silinir ve alan boşalır.
EL["liste"] = [_kalan]
voice_temizlik.gunluk_temizlik()
check("H6) Günlük temizlik bitmiş işin artık klonunu sildi, alan boşaldı",
      _kalan in EL["silinen"] and profil(pS).ninni_ekle_voice_id is None, "")

# Süren iş: 2 saatten genç korunur, yaşlı korunmaz.
hZ, uZ = anne("suren@test.com")
pZ = eski_paket(uZ)
db = SessionLocal()
p = db.get(VoiceProfile, pZ)
p.ninni_ekle_durum, p.ninni_ekle_voice_id = "hazirlaniyor", "SUREN1"
p.ninni_ekle_at = datetime.now(timezone.utc) - timedelta(minutes=10)
db.commit()
db.close()
db = SessionLocal()
_korunan = ninni_ekle.korunan_klonlar(db)
_yasli = ninni_ekle.korunan_klonlar(db, simdi=datetime.now(timezone.utc) + timedelta(hours=3))
db.close()
check("H7) Süren işin klonu 2 sa korunur, sonrası korunmaz",
      "SUREN1" in _korunan and "SUREN1" not in _yasli, f"{_korunan} {_yasli}")
EL["liste"] = ["SUREN1"]
voice_temizlik.gunluk_temizlik()
check("H8) Günlük temizlik süren işin klonunu SİLMEDİ", "SUREN1" not in EL["silinen"], "")
r1 = client.delete("/api/v1/voice/me", headers=hZ)
r2 = kaydet(hZ, amac="paket")
check("H9) İş sürerken Sesimi sil 409, yeni kayıt 409 ninni_ekle_suruyor",
      r1.status_code == 409 and r2.status_code == 409
      and r2.json().get("code") == "ninni_ekle_suruyor", f"{r1.status_code} {r2.text[:150]}")
check("H10) İş sürerken stories: uygun=false, durumu hazirlaniyor",
      stories(hZ)["ninni_ekle_uygun"] is False
      and stories(hZ)["ninni_ekle_durumu"] == "hazirlaniyor", "")
KUYRUK.clear()
_n_dev = voice_uretim.bekleyenleri_devam_ettir()
check("H11) Yeniden başlatmada süren iş kuyruğa alındı ve bitti (klon silindi)",
      str(pZ) in KUYRUK and "SUREN1" in EL["silinen"]
      and profil(pZ).ninni_ekle_durum == "tamam", f"{KUYRUK} {_n_dev}")

# Hesap silme: geçici klon da silinir
hD, uD = anne("hesapsil@test.com")
pD = eski_paket(uD)
db = SessionLocal()
p = db.get(VoiceProfile, pD)
p.ninni_ekle_voice_id = "HESAPSIL1"
db.commit()
voice_temizlik.kullanici_seslerini_sil(db, db.get(User, uD))
db.rollback()
db.close()
check("H12) Hesap silmede geçici klon silindi", "HESAPSIL1" in EL["silinen"], "")

# Eşzamanlı hak: koşullu UPDATE tek kazanan
hE, uE = anne("eszaman@test.com")
db = SessionLocal()
u1 = db.get(User, uE)
db2 = SessionLocal()
u2 = db2.get(User, uE)
_a, _b = ninni_ekle.hakki_al(db, u1), ninni_ekle.hakki_al(db2, u2)
db.close()
db2.close()
check("H13) Aynı anda iki hak alma: yalnız biri True", (_a, _b) == (True, False), f"{_a} {_b}")

# =============================================================================
# K — kredi + kuru çalıştırma
# =============================================================================
check("K1) STS kredisi saniye × 11 (60 sn → 660)", voice_sts.kredi(60) == 660
      and voice_sts.KREDI_SANIYE == 11 and not hasattr(voice_sts, "KREDI_DAKIKA"), "")
check("K2) Kredi tahmini = referans süreleri × 11",
      abs(ninni_ekle.kredi_tahmini(NINNILER)
          - round(sum(mp3.sure((_REF / f"{c}.mp3").read_bytes()) for c in NINNILER) * 11)) <= 1,
      str(ninni_ekle.kredi_tahmini(NINNILER)))
from tools import ninni_ekle as arac                           # noqa: E402
db = SessionLocal()
_metin, _ozet = arac.kuru_rapor(db)
db.rollback()
db.close()
_uygunlar = [l for l in _metin.splitlines() if " UYGUN " in f" {l} " and "·" not in l]
check("K3) Kuru çalıştırma: uygunlar listelenir (H1/H4 iadeli anneler), sebepler yazılır",
      _ozet["uygun"] == len(_uygunlar) >= 2 and "premium_degil" in _metin
      and "onizleme_profili" in _metin and "hak_kullanildi" in _metin, _metin)
_epostalar = ["mutlu@test.com", "ucretsiz@test.com", "hicbiri@test.com"]
check("K4) Kuru çalıştırma: e-posta/UUID yok, hash var",
      not any(e in _metin for e in _epostalar) and str(uA) not in _metin
      and arac.kisa_hash(uH) in _metin, "")
_b = arac.uzak_betik("from __future__ import annotations\nprint(1)\n", "X = 1\n", 11)
check("K5) Uzak betik: modül kaydı + kredi oranı + __future__ düşer",
      "_s.modules['api.services.ninni_ekle']" in _b and "_vs.KREDI_SANIYE = 11" in _b
      and "from __future__" not in _b, _b)

# --- Özet --------------------------------------------------------------------
kapat()
print("\n" + "=" * 74)
print("NİNNİLERİNİ AL (ANNE_SESI_NINNI_EKLE)")
print("=" * 74)
_gecen = 0
for ad, ok, detay in results:
    print(f"  {'[PASS]' if ok else '[FAIL]'} {ad}")
    if not ok:
        print(f"         → {detay[:1500]}")
    _gecen += ok
print("-" * 74)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
print("=" * 74)
shutil.rmtree(_MEDYA, ignore_errors=True)
shutil.rmtree(_REF, ignore_errors=True)
sys.exit(0 if _gecen == len(results) else 1)
