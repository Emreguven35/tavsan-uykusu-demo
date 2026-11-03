"""
tools/sts_kontrol.py — Anne Sesi STS üretim kontrol aracı (2026-11-03). Ağ YOK.

  K  Profil özeti: içerik başına tür/yöntem/durum/gerçek süre/dosya; sıra ve --son.
  U  Uyarılar: STS dönemi + referans var + tts → uyarı (STS öncesi değil);
     üretim sürerken > 15 dk hazırlanıyor (5 dk değil); STS çıktısı referansın
     %80–%120 dışında; önizlemede beklenen min(ref, VOICE_ONIZLEME_SN);
     satır var dosya yok.
  E  ElevenLabs kaydı: profil penceresindeki api_usage satırları toplanır;
     başka kullanıcı ve pencere dışı sayılmaz.
  G  Gizlilik: çıktıda kullanıcı/profil UUID'si, e-posta, voice_id, depo yolu yok;
     --profil kısa hash'le süzer.
  C  --canli komutu: railway ssh + python stdin, --canli düşer, LD_LIBRARY_PATH
     yalnız doluysa export.
  S  Salt okunur: araç çalıştıktan sonra tablo sayıları aynı.

Çalıştırma: python tests/test_sts_kontrol.py
"""
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

_DB = Path(tempfile.gettempdir()) / "sts_kontrol_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "sts_kontrol_medya"
_REF = Path(tempfile.gettempdir()) / "sts_kontrol_referans"
shutil.rmtree(_MEDYA, ignore_errors=True)
shutil.rmtree(_REF, ignore_errors=True)
_REF.mkdir()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ANNE_SESI_STS"] = "true"
os.environ["VOICE_ONIZLEME_SN"] = "31"
for _k in ("VOICE_PAKET_NINNILER", "VOICE_PAKET_MASALLAR", "VOICE_ONIZLEME_ICERIK"):
    os.environ.pop(_k, None)

from api.db import Base, SessionLocal, engine                  # noqa: E402
import api.models                                              # noqa: E402,F401
from api.models import ApiUsage, User, VoiceAudio, VoiceProfile  # noqa: E402
from api.services import storage, voice_sts                    # noqa: E402
from tools import sts_kontrol as sk                            # noqa: E402

voice_sts.REFERANS_KLASOR = _REF
Base.metadata.create_all(bind=engine)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def mp3_uret(saniye: float) -> bytes:
    """MPEG-1 Layer III 128 kbps 44.1 kHz (417 bayt / 26.12 ms çerçeve)."""
    cerceve = b"\xff\xfb\x90\x00" + b"\x00" * 413
    return b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + cerceve * int(saniye / (1152 / 44100))


# Referanslar: 2 ninni + 5 masal (Ayşecik 200 sn)
REF = {"ninni_dandini": 90, "ninni_uyusun_da_buyusun": 60, "ninni_uykudan_uyanmis": 120,
       "masal_aysecik_uyku_perisi": 200, "masal_kelogan_sihirli_degnek": 180,
       "masal_cirkin_ordek_yavrusu": 190, "masal_uc_kucuk_domuzcuk": 160,
       "masal_kirmizi_baslikli_kiz": 195}
for _cid, _sn in REF.items():
    (_REF / f"{_cid}.mp3").write_bytes(mp3_uret(_sn))

SIMDI = datetime.now(timezone.utc)
STS_BAS = SIMDI - timedelta(days=1)
EPOSTALAR = []


def kullanici(ad):
    db = SessionLocal()
    try:
        e = f"{ad}@test.com"
        EPOSTALAR.append(e)
        u = User(email=e, password_hash="x")
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def profil(uid, *, status="released", amac="paket", once_dk=60, icerik=None,
           voice_id=None, error=None, dosyasiz=()):
    """icerik: {cid: (yontem, saniye)}; dosyasiz: satırı var dosyası yok."""
    db = SessionLocal()
    try:
        bas = SIMDI - timedelta(minutes=once_dk)
        p = VoiceProfile(user_id=uid, status=status, amac=amac, last_cloned_at=bas,
                         created_at=bas, elevenlabs_voice_id=voice_id, error=error,
                         released_at=None if status in sk.SURUYOR else bas + timedelta(minutes=10),
                         progress_done=len(icerik or {}), progress_total=8)
        db.add(p)
        db.commit()
        for cid, (yontem, sn) in (icerik or {}).items():
            yol = storage.ses_yolu(uid, p.id, cid)
            veri = mp3_uret(sn)
            if cid not in dosyasiz:
                storage.depo().yaz(yol, veri)
            db.add(VoiceAudio(voice_profile_id=p.id, content_id=cid, storage_path=yol,
                              bytes=len(veri), duration_sec=int(sn), yontem=yontem))
        db.commit()
        return p.id
    finally:
        db.close()


def kullanim(uid, op, kredi, dk_once):
    db = SessionLocal()
    try:
        db.add(ApiUsage(service="elevenlabs", operation=op, characters=kredi, user_id=uid,
                        created_at=SIMDI - timedelta(minutes=dk_once)))
        db.commit()
    finally:
        db.close()


def rapor(son=20, profil_hash=None):
    db = SessionLocal()
    try:
        return sk.calistir(db, son, profil_hash, simdi=SIMDI, sts_baslangic=STS_BAS)
    finally:
        db.rollback()
        db.close()


def blok(metin, phash):
    for b in metin.split("=" * 78):
        if f"profil {phash}" in b:
            return b
    return ""


TAM = {cid: ("sts", sn) for cid, sn in REF.items()}

# A) STS döneminde temiz tam paket
uA = kullanici("temiz")
pA = profil(uA, once_dk=120, icerik=dict(TAM))
kullanim(uA, "voice_clone", 0, 119)
kullanim(uA, "sts", 1500, 118)
kullanim(uA, "sts", 3000, 115)
kullanim(uA, "sts", 9999, 60 * 24 * 3)            # pencere dışı (3 gün önce)
# B) STS döneminde bir masal tts'e düşmüş + süre sapmaları + dosyasız satır
uB = kullanici("sorunlu")
icB = dict(TAM)
icB["masal_kelogan_sihirli_degnek"] = ("tts", 180)
icB["ninni_dandini"] = ("sts", 45)                 # %50 → kısa
icB["masal_uc_kucuk_domuzcuk"] = ("sts", 210)      # %131 → uzun
icB["ninni_uyusun_da_buyusun"] = ("sts", 52)       # %87 → normal
pB = profil(uB, once_dk=100, icerik=icB, dosyasiz=("masal_kirmizi_baslikli_kiz",))
kullanim(uA, "sts", 777, 99)                       # BAŞKA kullanıcının satırı (uA)
# C) STS öncesi eski paket: tts uyarı DEĞİL
uC = kullanici("eski")
pC = profil(uC, once_dk=60 * 24 * 5, amac=None,
            icerik={"masal_aysecik_uyku_perisi": ("tts", 400), "ninni_dandini": (None, 22)})
# D) Üretim 20 dk'dır sürüyor; E) 5 dk'dır sürüyor
uD = kullanici("uzun")
pD = profil(uD, status="generating", once_dk=20, voice_id="voice_gizli_D",
            icerik={"ninni_dandini": ("sts", 90)})
uE = kullanici("kisa")
pE = profil(uE, status="generating", once_dk=5, voice_id="voice_gizli_E",
            icerik={"ninni_dandini": ("sts", 90)})
# F) Önizleme 31 sn (STS); G) önizleme 20 sn (STS, kısa)
uF = kullanici("onizleme")
pF = profil(uF, amac="onizleme", once_dk=30,
            icerik={"masal_aysecik_uyku_perisi": ("sts", 31)})
uG = kullanici("onizleme_kisa")
pG = profil(uG, amac="onizleme", once_dk=25,
            icerik={"masal_aysecik_uyku_perisi": ("sts", 20)})
# H) Bitmiş, kısmi: 1 ninni üretilemedi
uH = kullanici("kismi")
icH = {k: v for k, v in TAM.items() if k != "ninni_uykudan_uyanmis"}
pH = profil(uH, once_dk=40, icerik=icH, error="Üretilemedi: ninni_uykudan_uyanmis")

db = SessionLocal()
_once = (db.query(VoiceProfile).count(), db.query(VoiceAudio).count(), db.query(ApiUsage).count())
db.close()

metin, n_uyari = rapor()
H = {k: sk.kisa_hash(v) for k, v in dict(A=pA, B=pB, C=pC, D=pD, E=pE, F=pF, G=pG, H=pH).items()}
bA, bB, bC, bD, bE, bF, bG, bH = (blok(metin, H[k]) for k in "ABCDEFGH")

# --- K ---------------------------------------------------------------------
check("K1) 8 profil, en yeni üstte (E 5 dk önce, C 5 gün önce en altta)",
      metin.index(H["E"]) < metin.index(H["D"]) < metin.index(H["G"])
      < metin.index(H["A"]) < metin.index(H["C"]) and "8 profil" in metin, metin[:200])
check("K2) --son 2 yalnız en yeni iki profil",
      all(H[k] in rapor(son=2)[0] for k in "ED")
      and H["A"] not in rapor(son=2)[0], "")
check("K3) Satır: içerik, tür, yöntem, durum, gerçek süre, referans süresi, dosya",
      all(c in bA for c in REF)
      and "ninni_dandini                  ninni  sts     hazir            90.0    90.0 var" in bA,
      bA)
check("K4) Başlıkta durum, amaç, oluşturma zamanı, klon durumu",
      "durum released" in bA and "amaç paket" in bA and "oluşturma " in bA
      and "ElevenLabs klonu silinmiş" in bA and "ElevenLabs klonu DURUYOR" in bD, bD)
check("K5) Temiz pakette uyarı yok", "uyarı yok" in bA and "UYARI" not in bA, bA)

# --- U ---------------------------------------------------------------------
check("U1) STS döneminde tts → uyarı",
      "masal_kelogan_sihirli_degnek: STS bekleniyordu, yontem=tts" in bB, bB)
check("U2) STS öncesi profilde tts/NULL uyarı değil",
      "STS öncesi" in bC and "uyarı yok" in bC, bC)
check("U3) Süre %50 → uyarı, %131 → uyarı, %87 → uyarı yok",
      "ninni_dandini: süre 45.0 sn" in bB and "masal_uc_kucuk_domuzcuk: süre 210.0 sn" in bB
      and "ninni_uyusun_da_buyusun: süre" not in bB, bB)
check("U4) Satır var dosya yok → durum dosya_yok + uyarı",
      "masal_kirmizi_baslikli_kiz: voice_audios satırı var, depoda dosya YOK" in bB
      and "dosya_yok" in bB, bB)
check("U5) 20 dk süren üretimde dosyasız içerik hazırlanıyor + > 15 dk uyarısı",
      "masal_aysecik_uyku_perisi      masal  -       hazirlaniyor" in bD
      and "dk'dır hazırlanıyor (> 15 dk)" in bD, bD)
check("U6) 5 dk süren üretimde hazırlanıyor ama uyarı yok",
      "hazirlaniyor" in bE and "uyarı yok" in bE, bE)
check("U7) Önizleme 31 sn: satır yalnız Ayşecik, önizleme süresi gösterilir, uyarı yok",
      "önizleme: 31.0 sn (beklenen ~31)" in bF and "uyarı yok" in bF
      and "ninni_dandini" not in bF, bF)
check("U8) Önizleme 20 sn → beklenen min(ref 200, 31)=31 ile sapma uyarısı",
      "masal_aysecik_uyku_perisi: süre 20.0 sn, beklenen ~31.0 sn" in bG, bG)
check("U9) Bitmiş pakette üretilemeyen içerik 'uretilemedi', dosyasız diğerleri listelenmez",
      "ninni_uykudan_uyanmis          ninni  -       uretilemedi" in bH
      and "hazirlaniyor" not in bH, bH)
check("U10) Toplam uyarı sayısı (B: 4, D: 7 hazırlanıyor, G: 1)",
      n_uyari == 4 + 7 + 1 and metin.rstrip().endswith(f"TOPLAM UYARI: {n_uyari}"),
      f"{n_uyari}")

# --- E ---------------------------------------------------------------------
check("E1) Kredi: pencere içi sts toplanır (1500+3000), klon sayılır; pencere dışı yok",
      "sts 2 çağrı / 4.500 kredi, klon 1" in bA, bA)
check("E2) Başka kullanıcının satırı sayılmaz; kaydı olmayan profil 'yok'",
      "ElevenLabs kaydı: yok" in bB, bB)

# --- G ---------------------------------------------------------------------
_sizinti = [str(x) for x in (uA, uB, uC, uD, uE, uF, uG, uH, pA, pB, pD)] + EPOSTALAR \
    + ["voice_gizli_D", "voice_gizli_E", "voice-audio/"]
check("G1) Çıktıda UUID, e-posta, voice_id, depo yolu yok",
      not any(s in metin for s in _sizinti),
      [s for s in _sizinti if s in metin])
_tek = rapor(profil_hash=H["B"])[0]
check("G2) --profil <profil hash> yalnız o profil",
      "1 profil" in _tek and H["B"] in _tek and H["A"] not in _tek, _tek[:200])
check("G3) --profil <kullanıcı hash> da bulur",
      H["F"] in rapor(profil_hash=sk.kisa_hash(uF))[0], "")
check("G4) Hash 8 hane, aynı girdiye aynı", len(H["A"]) == 8
      and sk.kisa_hash(pA) == H["A"], H["A"])

# --- C ---------------------------------------------------------------------
_k = sk.uzak_komut(["--son", "3", "--canli", "--profil", "ab'c"], railway="RW")
check("C1) Uzak komut: railway ssh -- sh -c, python stdin, --canli düşer, argüman kaçışlı",
      _k[:5] == ["RW", "ssh", "--", "sh", "-c"]
      and "/opt/venv/bin/python - --son 3 --profil 'ab'\"'\"'c'" in _k[5]
      and "--canli" not in _k[5], _k[5])
check("C2) LD_LIBRARY_PATH yalnız doluysa export (boş export ortamı basar)",
      '[ -n "$L" ] && export "$L"' in _k[5] and "export $(" not in _k[5], _k[5])

# --- S ---------------------------------------------------------------------
db = SessionLocal()
_sonra = (db.query(VoiceProfile).count(), db.query(VoiceAudio).count(), db.query(ApiUsage).count())
db.close()
check("S1) Salt okunur: tablo sayıları aynı", _once == _sonra, f"{_once} → {_sonra}")

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("STS KONTROL ARACI (tools/sts_kontrol.py)")
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
