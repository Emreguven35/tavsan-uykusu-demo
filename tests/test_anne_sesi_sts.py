"""
Anne Sesi speech-to-speech (ANNE_SESI_STS) — ağ YOK, ElevenLabs MOCK.

  K  Bayrak kapalı: referans olsa bile STS çağrılmaz, TTS aynen.
  A  Bayrak açık: referansı olan içerik STS (doğru model/voice_id/dosya),
     olmayan TTS; maliyet defterine operation=sts + kredi.
  P  Uzun kayıt güvenli parçalara bölünür, çıktı tek MP3'te birleşir;
     parçalardan biri düşerse yarım içerik yerine TTS.
  O  Önizleme: referansın yalnız ilk 30 sn'si dönüştürülür.
  I  İzin yok (401 missing_permissions): açık HATA logu, TTS'e düşüş, STS bir
     daha denenmez; anahtar hiçbir log satırında geçmez.
  G  Geçici hata: yeniden denenir, olmazsa TTS.

Çalıştırma: python tests/test_anne_sesi_sts.py
"""
import logging
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "anne_sesi_sts_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "anne_sesi_sts_medya"
_REF = Path(tempfile.gettempdir()) / "anne_sesi_sts_referans"
shutil.rmtree(_MEDYA, ignore_errors=True)
shutil.rmtree(_REF, ignore_errors=True)
_REF.mkdir(parents=True)
ANAHTAR = "sk_GIZLI_TEST_ANAHTARI_9f3c"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = ANAHTAR
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.pop("ANNE_SESI_STS", None)
os.environ["VOICE_PAKET_NINNILER"] = "ninni_dandini,ninni_ay_isigi"
os.environ["VOICE_PAKET_MASALLAR"] = "masal_aysecik_uyku_perisi"

from api.db import Base, SessionLocal, engine                  # noqa: E402
import api.models                                              # noqa: E402,F401
from api.models import ApiUsage, User, VoiceAudio, VoiceProfile  # noqa: E402
from api.services import mp3, storage, voice_paket, voice_sts, voice_uretim  # noqa: E402
from api.services import usage as usage_svc                    # noqa: E402
from api.services import voice as voice_svc                    # noqa: E402

Base.metadata.create_all(bind=engine)
usage_svc.SENKRON_MOD = True
voice_sts.REFERANS_KLASOR = _REF
voice_svc.delete_voice = lambda v: {"ok": True, "error": None, "status": 200}
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("" if cond else f"   → {detail}"))


# --- Log yakalayıcı (anahtar sızıyor mu?) -----------------------------------
class _Topla(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.kayitlar: list[logging.LogRecord] = []

    def emit(self, record):
        self.kayitlar.append(record)


LOG = _Topla()
logging.getLogger().addHandler(LOG)
logging.getLogger().setLevel(logging.DEBUG)


def mp3_uret(saniye: float, etiket: bytes = b"\x00") -> bytes:
    """MPEG-1 Layer III 128 kbps 44.1 kHz (417 bayt / 26.12 ms çerçeve)."""
    cerceve = b"\xff\xfb\x90\x00" + etiket * (417 - 4)
    n = int(saniye / (1152 / 44100))
    return b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + cerceve * n


# --- Sahte ElevenLabs --------------------------------------------------------
TTS: list[str] = []


def _sahte_tts(voice_id, metin, user_id):
    TTS.append(metin)
    return mp3_uret(20, b"\x11")


voice_uretim._seslendir = _sahte_tts


class Yanit:
    def __init__(self, kod, icerik=b"", govde=None):
        self.status_code, self.content, self._govde = kod, icerik, govde

    def json(self):
        if self._govde is None:
            raise ValueError("json yok")
        return self._govde


STS: list[dict] = []
DAVRANIS = {"mod": "ok"}          # ok | izin | hata | ikinci_parca_hata


def _sahte_post(url, params=None, headers=None, data=None, files=None, timeout=None):
    girdi = files["audio"][1]
    STS.append({"url": url, "params": params, "headers": headers, "data": data,
                "sure": mp3.sure(girdi)})
    mod = DAVRANIS["mod"]
    if mod == "izin":
        return Yanit(401, govde={"detail": {"status": "missing_permissions",
                                            "message": "missing speech_to_speech"}})
    if mod == "hata" or (mod == "ikinci_parca_hata" and len(STS) % 3 == 2):
        return Yanit(500, govde={"detail": {"status": "internal"}})
    # Dönüştürülmüş ses: girdiyle aynı uzunlukta, ayırt edilebilir baytlar.
    return Yanit(200, mp3_uret(mp3.sure(girdi) + 0.01, b"\x22"))


voice_sts.requests.post = _sahte_post


# --- Kurulum yardımcıları ----------------------------------------------------
def profil_ac(amac=None) -> tuple:
    s = SessionLocal()
    try:
        u = User(email=f"{_uuid.uuid4().hex[:8]}@test.com", password_hash="x")
        s.add(u)
        s.flush()
        p = VoiceProfile(user_id=u.id, elevenlabs_voice_id="ANNE_VOICE_1",
                         status="cloning", amac=amac)
        s.add(p)
        s.commit()
        return u.id, p.id
    finally:
        s.close()


def sesler(pid) -> dict:
    s = SessionLocal()
    try:
        return {a.content_id: storage.yerel_dosya(a.storage_path).read_bytes()
                for a in s.query(VoiceAudio).filter(VoiceAudio.voice_profile_id == pid)}
    finally:
        s.close()


def sifirla():
    TTS.clear()
    STS.clear()
    LOG.kayitlar.clear()
    DAVRANIS["mod"] = "ok"
    voice_sts._izin_yok = False


(_REF / "ninni_dandini.mp3").write_bytes(mp3_uret(120))
(_REF / "masal_aysecik_uyku_perisi.mp3").write_bytes(mp3_uret(600))   # 10 dk

# =============================================================================
print("K) Bayrak kapalı")
sifirla()
check("K0) Bayrak varsayılan KAPALI", voice_sts.acik_mi() is False, "")
_, pid = profil_ac()
voice_uretim.uret(pid)
_s = sesler(pid)
check("K1) Referans olsa bile STS çağrılmadı", STS == [], str(len(STS)))
check("K2) Paketteki 3 içerik de TTS ile üretildi",
      len(TTS) == 3 and set(_s) == {"ninni_dandini", "ninni_ay_isigi",
                                    "masal_aysecik_uyku_perisi"}, str(list(_s)))
check("K3) Dosyalar TTS çıktısının birebir aynısı",
      all(v == mp3_uret(20, b"\x11") for v in _s.values()), "")

# =============================================================================
print("A) Bayrak açık")
os.environ["ANNE_SESI_STS"] = "true"
sifirla()
uid, pid = profil_ac()
voice_uretim.uret(pid)
_s = sesler(pid)
check("A1) Referansı olan ninni STS ile üretildi",
      b"\x22" * 50 in _s["ninni_dandini"], "")
check("A2) Referansı OLMAYAN ninni TTS'e düştü",
      _s["ninni_ay_isigi"] == mp3_uret(20, b"\x11") and len(TTS) == 1, str(len(TTS)))
_ilk = STS[0]
check("A3) Doğru uç, voice_id ve model",
      _ilk["url"] == "https://api.elevenlabs.io/v1/speech-to-speech/ANNE_VOICE_1"
      and _ilk["data"]["model_id"] == "eleven_multilingual_sts_v2"
      and _ilk["params"]["output_format"] == "mp3_44100_128", str(_ilk["url"]))
check("A4) Anahtar yalnız başlıkta gidiyor", _ilk["headers"]["xi-api-key"] == ANAHTAR
      and ANAHTAR not in _ilk["url"], "")
s = SessionLocal()
try:
    _u = s.query(ApiUsage).filter(ApiUsage.operation == "sts",
                                  ApiUsage.user_id == uid).all()
finally:
    s.close()
_kredi = sum(x.characters for x in _u)
_beklenen = round(120 * 1000 / 60) + round(600 * 1000 / 60)
check("A5) Maliyet defterinde STS kaydı: parça başına satır, kredi = dakika × 1000",
      len(_u) == 1 + 3 and abs(_kredi - _beklenen) <= 5, f"{len(_u)} {_kredi} ~{_beklenen}")
check("A6) USD = kredi × 0,00022",
      abs(sum(x.estimated_cost_usd for x in _u) - _kredi * 0.00022) < 0.01,
      f"{sum(x.estimated_cost_usd for x in _u):.3f}")
check("A7) Model adı deftere yazıldı",
      all(x.model == "eleven_multilingual_sts_v2" for x in _u), "")

# =============================================================================
print("P) Parçalama")
_masal = STS[1:]                  # ilk çağrı ninninin (120 sn, tek parça)
check("P1) 10 dk'lık masal 3 parçaya bölündü, her parça ≤ 270 sn",
      len(_masal) == 3 and all(x["sure"] <= 270.0 for x in _masal),
      str([round(x["sure"], 1) for x in _masal]))
check("P2) Parçaların toplamı kaynağın tamamı",
      abs(sum(x["sure"] for x in _masal) - mp3.sure(mp3_uret(600))) < 0.1, "")
_m = _s["masal_aysecik_uyku_perisi"]
check("P3) Çıktı tek MP3: ortada ID3 yok, süre parçaların toplamı",
      _m[:2] == b"\xff\xfb" and _m.count(b"ID3") == 0
      and abs(mp3.sure(_m) - 600) < 1.0, f"{mp3.sure(_m):.1f}")
_parca = mp3.parcala(mp3_uret(100), 30)
check("P4) mp3.parcala/birlestir kayıpsız",
      len(_parca) == 4 and abs(mp3.sure(mp3.birlestir(_parca)) - mp3.sure(mp3_uret(100))) < 0.01,
      str(len(_parca)))

sifirla()
DAVRANIS["mod"] = "ikinci_parca_hata"
_, pid = profil_ac()
voice_uretim.uret(pid)
_s = sesler(pid)
check("P5) Parçalardan biri düşünce yarım masal yerine TTS",
      _s["masal_aysecik_uyku_perisi"] == mp3_uret(20, b"\x11"), "")

# =============================================================================
print("O) Önizleme")
sifirla()
_, pid = profil_ac(amac="onizleme")
voice_uretim.uret(pid)
_s = sesler(pid)
check("O1) Önizlemede STS'ye referansın yalnız ilk 30 sn'si gitti",
      len(STS) == 1 and STS[0]["sure"] <= 30.0, str([x["sure"] for x in STS]))
check("O2) Önizleme STS çıktısı ve ≤ 30 sn",
      b"\x22" * 50 in _s[voice_paket.onizleme_icerik_id()]
      and mp3.sure(_s[voice_paket.onizleme_icerik_id()]) <= 30.0, "")
check("O3) Önizlemede TTS hiç çağrılmadı", TTS == [], str(len(TTS)))

# =============================================================================
print("I) İzin yok")
sifirla()
DAVRANIS["mod"] = "izin"
_, pid = profil_ac()
voice_uretim.uret(pid)
_s = sesler(pid)
_hatalar = [r for r in LOG.kayitlar if r.levelno >= logging.ERROR
            and "İZNİ YOK" in r.getMessage()]
check("I1) Açık HATA logu (bir kez)", len(_hatalar) == 1, str(len(_hatalar)))
check("I2) STS yalnız bir kez denendi, sonra hiç", len(STS) == 1, str(len(STS)))
check("I3) Tüm içerikler TTS ile üretildi (paket tamam)",
      len(_s) == 3 and all(v == mp3_uret(20, b"\x11") for v in _s.values()), "")
check("I4) Anahtar HİÇBİR log satırında yok",
      all(ANAHTAR not in r.getMessage() and ANAHTAR not in str(r.args)
          for r in LOG.kayitlar), "")
_, pid2 = profil_ac()
STS.clear()
voice_uretim.uret(pid2)
check("I5) Sonraki paket STS'yi denemiyor (süreç boyunca)", STS == [], str(len(STS)))

# =============================================================================
print("G) Geçici hata")
sifirla()
DAVRANIS["mod"] = "hata"
uidg, pid = profil_ac()
voice_uretim.uret(pid)
_s = sesler(pid)
check("G1) Geçici hata: DENEME_SAYISI kadar denendi, sonra TTS",
      len([x for x in STS if x["sure"] < 130]) == voice_uretim.DENEME_SAYISI
      and _s["ninni_dandini"] == mp3_uret(20, b"\x11"), str(len(STS)))
s = SessionLocal()
try:
    _g = s.query(ApiUsage).filter(ApiUsage.operation == "sts",
                                  ApiUsage.user_id == uidg).count()
finally:
    s.close()
check("G2) Hatalı STS deftere yazılmadı (ücretlendirilmeyen çağrı)", _g == 0, str(_g))
check("G3) Anahtar HİÇBİR log satırında yok",
      all(ANAHTAR not in r.getMessage() and ANAHTAR not in str(r.args)
          for r in LOG.kayitlar), "")

check("Y1) Yol kaçışı: referans_yolu('../x') None",
      voice_sts.referans_yolu("../stories") is None
      and voice_sts.referans_yolu("a/b") is None, "")
os.environ.pop("ANNE_SESI_STS")

print("=" * 78)
gecen = sum(1 for _, ok, _ in results if ok)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
shutil.rmtree(_REF, ignore_errors=True)
shutil.rmtree(_MEDYA, ignore_errors=True)
sys.exit(0 if gecen == len(results) else 1)
