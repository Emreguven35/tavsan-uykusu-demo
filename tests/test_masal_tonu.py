"""
Masal anlatım tonu — ses profilleri + duraklama ritmi.

NEDEN VAR: klonlu sesle üretilen masallar çok hızlı okunuyordu, masal kıvamında
değildi. `/voice/generate` ElevenLabs'e voice_settings HİÇ göndermiyordu, yani
her şey varsayılan hızdaydı.

İki profil tanımlandı (tts.SES_PROFILLERI):
  - 'masal'  : speed 0.85, stability 0.70, similarity_boost 0.75, style düşük
  - 'sohbet' : ElevenLabs varsayılanları (chat TTS'i ileride açılırsa normal hız)

Metin tarafında paragraf/cümle aralarına ElevenLabs `<break time="x.xs" />`
konuyor. Flash v2.5'in ikisini de gerçekten uyguladığı ÖLÇÜLDÜ (2026-08-25):
speed 0.85 → %20 uzama, <break time="2.0s"/> → +2,25 sn.

BU DOSYANIN ASIL İŞİ iki tuzağı sabitlemek:
  1. Ayar değişince cache: profil ve ayar sürümü cache anahtarına girmezse eski
     HIZLI okunmuş MP3 sunulmaya devam eder ve düzeltme kullanıcıya HİÇ ulaşmaz.
  2. Break etiketi enflasyonu: ElevenLabs "tek üretimde çok fazla break etiketi
     kararsızlık yapar" diyor. Korpustaki masallar 81-109 cümle; her cümleye
     etiket koymak hem artefakt riski hem ~%50 karakter maliyeti demek.
     Kural kendi kendini sınırlamalı (paragraf hep, cümle yalnız sığıyorsa).

Çalıştırma: python tests/test_masal_tonu.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "masal_tonu_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from api import tts                                          # noqa: E402
from api.konusma_metni import (                              # noqa: E402
    MASAL_MAX_BREAK, break_etiketi, konusma_metnine_cevir, masal_metni_hazirla,
)
from api.services import voice as voice_svc                  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


_BREAK = re.compile(r'<break time="([\d.]+)s" />')


def breakler(s: str) -> list[float]:
    return [float(x) for x in _BREAK.findall(s)]


# =============================================================================
# 1) SES PROFİLLERİ
# =============================================================================
P = tts.SES_PROFILLERI
check("1a) İki profil tanımlı: masal + sohbet",
      set(P) == {"masal", "sohbet"}, str(sorted(P)))

masal = P["masal"]
check("1b) Masal profili yavaş okuyor (speed 0.85)",
      masal["speed"] == 0.85, str(masal["speed"]))
check("1c) Masal stability 0.65-0.75 bandında (dalgalanmayan ton)",
      0.65 <= masal["stability"] <= 0.75, str(masal["stability"]))
check("1d) Masal similarity_boost 0.75",
      masal["similarity_boost"] == 0.75, str(masal["similarity_boost"]))
check("1e) Masal style DÜŞÜK (abartılı tonlama yok)",
      masal["style"] <= 0.2, str(masal["style"]))

sohbet = P["sohbet"]
check("1f) Sohbet profili normal hızda (speed 1.0)",
      sohbet["speed"] == 1.0, str(sohbet["speed"]))
# Sohbet profili ElevenLabs varsayılanlarıyla AYNI olmalı: aksi hâlde mevcut
# /ask ses cache'i sessizce geçersizleşir ve eski cevaplar yeni tonla çalar.
check("1g) Sohbet profili ElevenLabs varsayılanlarıyla aynı (chat sesi değişmedi)",
      (sohbet["stability"], sohbet["similarity_boost"], sohbet["style"],
       sohbet["use_speaker_boost"]) == (0.5, 0.75, 0.0, True), str(sohbet))

# ElevenLabs speed aralığı 0.7-1.2; dışına çıkan değer API'de hata verir.
_aralik_disi = {ad: p["speed"] for ad, p in P.items() if not 0.7 <= p["speed"] <= 1.2}
check("1h) Tüm profillerin speed'i ElevenLabs aralığında (0.7-1.2)",
      not _aralik_disi, str(_aralik_disi))
_alan_eksik = [ad for ad, p in P.items() if not {
    "stability", "similarity_boost", "style", "use_speaker_boost", "speed"} <= set(p)]
check("1i) Her profil beş voice_settings alanını da taşıyor",
      not _alan_eksik, str(_alan_eksik))

check("1j) Varsayılan profil sohbet, /voice/generate varsayılanı masal",
      tts.VARSAYILAN_PROFIL == "sohbet" and tts.MASAL_PROFILI == "masal", "")
check("1k) Bilinmeyen profil adı çökmez, varsayılana düşer",
      tts.profil_ayarlari("yok-boyle") == P[tts.VARSAYILAN_PROFIL], "")
check("1l) profil_ayarlari KOPYA döndürür (çağıran profili bozamaz)",
      tts.profil_ayarlari("masal") is not P["masal"], "")


# =============================================================================
# 2) voice_settings GERÇEKTEN gönderiliyor mu?
# =============================================================================
# Ayarları tanımlayıp isteğe koymayı unutmak bu işin en sessiz hatası: her şey
# yeşil görünür, ses aynı hızda kalır. İstek gövdesi doğrudan denetlenir.
_GONDERILEN = {}


class _SahteYanit:
    status_code = 200
    content = b"ID3FAKE"

    def raise_for_status(self):
        return None


def _sahte_post(url, headers=None, json=None, timeout=None):
    _GONDERILEN.clear()
    _GONDERILEN.update(json or {})
    _GONDERILEN["_url"] = url
    return _SahteYanit()


_gercek_post = tts.requests.post
tts.requests.post = _sahte_post
try:
    tts.synthesize("Bir varmış bir yokmuş.", voice_id="v1", profil="masal")
    _vs = _GONDERILEN.get("voice_settings", {})
    check("2a) İstek gövdesinde voice_settings VAR", bool(_vs), str(_GONDERILEN)[:200])
    check("2b) Masal ayarları birebir gidiyor (speed 0.85 dahil)",
          _vs == P["masal"], str(_vs))
    check("2c) Model hâlâ flash v2.5",
          _GONDERILEN.get("model_id") == tts.TTS_MODEL, str(_GONDERILEN.get("model_id")))

    tts.synthesize("Merhaba.", voice_id="v1", profil="sohbet")
    check("2d) Sohbet profilinde normal hız gidiyor",
          _GONDERILEN.get("voice_settings", {})["speed"] == 1.0,
          str(_GONDERILEN.get("voice_settings")))
finally:
    tts.requests.post = _gercek_post


# =============================================================================
# 3) DURAKLAMA RİTMİ — masal_metni_hazirla
# =============================================================================
check("3a) break etiketi ElevenLabs biçiminde",
      break_etiketi(0.8) == '<break time="0.8s" />', break_etiketi(0.8))
check("3b) 3 saniye ElevenLabs üst sınırı — aşan değer kırpılıyor",
      break_etiketi(9.0) == '<break time="3.0s" />', break_etiketi(9.0))

IKI_PARAGRAF = "Birinci cümle. İkinci cümle.\n\nYeni paragraf başladı. Son cümle."
_h = masal_metni_hazirla(IKI_PARAGRAF)
check("3c) Paragraf arasına UZUN duraklama girdi",
      break_etiketi(0.8) in _h, _h)
check("3d) Cümle aralarına KISA duraklama girdi (kısa metin)",
      _h.count(break_etiketi(0.3)) == 2, _h)
check("3e) Paragrafın SON cümlesinden sonra çift etiket yok",
      not re.search(r"/>\s*<break", _h), _h)
check("3f) Metnin kendisi korundu (kelimeler kaybolmadı)",
      all(k in _h for k in ("Birinci cümle", "Yeni paragraf başladı", "Son cümle")), _h)
check("3g) Metin duraklamayla BİTMİYOR (sonda boşa etiket yok)",
      not _h.rstrip().endswith("/>"), _h[-40:])

check("3h) Boş girdi çökmez", masal_metni_hazirla("") == "" and
      masal_metni_hazirla("   \n\n  ") == "", "")

# Tek paragraf, tek cümle → hiç etiket yok (etiket için yer yok)
check("3i) Tek cümlelik metne gereksiz etiket eklenmiyor",
      breakler(masal_metni_hazirla("Tek bir cümle.")) == [], "")

# Kapatma anahtarları (kalibrasyon için)
check("3j) cumle_sn=0 → yalnız paragraf duraklaması",
      breakler(masal_metni_hazirla(IKI_PARAGRAF, cumle_sn=0)) == [0.8], "")
check("3k) paragraf_sn=0 → paragraf duraklaması yok",
      0.8 not in breakler(masal_metni_hazirla(IKI_PARAGRAF, paragraf_sn=0)), "")

# Markdown temizliği hâlâ çalışıyor (etiketler EN SONDA ekleniyor)
check("3l) Temizlik kuralları korunuyor (markdown gitti, etiket bozulmadı)",
      "**" not in masal_metni_hazirla("**Kalın** yazı. Devamı var.\n\nİkinci."), "")


# =============================================================================
# 4) BREAK ENFLASYONU FRENİ
# =============================================================================
# Uzun metinde cümle duraklaması KENDİLİĞİNDEN kapanmalı; paragraf duraklaması
# kalmalı. Aksi hâlde tek üretimde ~100 etiket → ElevenLabs'in uyardığı
# kararsızlık + ~%50 fazla karakter maliyeti.
UZUN = "\n\n".join("Cümle bir. Cümle iki. Cümle üç. Cümle dört." for _ in range(30))
_u = masal_metni_hazirla(UZUN)
_ub = breakler(_u)
check("4a) Uzun metinde cümle duraklaması kapandı (yalnız paragraf)",
      set(_ub) == {0.8}, f"{sorted(set(_ub))} adet={len(_ub)}")
check("4b) Etiket sayısı paragraf sayısı - 1",
      len(_ub) == 29, str(len(_ub)))

# Gerçek katalog: hiçbir masal/ninni sınırı aşmamalı, hiçbir etiket 3sn'yi geçmemeli.
cat = voice_svc.load_stories()
_asanlar, _uzun_etiket, _ozet = [], [], []
for tur in ("masallar", "ninniler"):
    for s in cat.get(tur, []):
        h = masal_metni_hazirla(s["text"])
        b = breakler(h)
        _duz = len(konusma_metnine_cevir(s["text"]))
        _ozet.append((s["id"], len(b), len(h) - _duz, _duz))
        if len(b) > MASAL_MAX_BREAK:
            _asanlar.append((s["id"], len(b)))
        if any(x > 3.0 for x in b):
            _uzun_etiket.append(s["id"])

check("4c) Katalogdaki hiçbir masal/ninni etiket sınırını aşmıyor",
      not _asanlar, str(_asanlar))
check("4d) Hiçbir etiket 3 saniyeyi geçmiyor (API sınırı)", not _uzun_etiket,
      str(_uzun_etiket))

# Ninniler kısa: cümle duraklaması ALMALI (masal ritmi asıl orada hissediliyor).
_ninni = masal_metni_hazirla(cat["ninniler"][0]["text"])
check("4e) Ninnilerde cümle duraklaması VAR (kısa metin sınıra sığıyor)",
      0.3 in breakler(_ninni), str(breakler(_ninni)))

# Uzun masallarda yalnız paragraf duraklaması kalıyor.
_masal = masal_metni_hazirla(cat["masallar"][0]["text"])
check("4f) Uzun masalda yalnız paragraf duraklaması var",
      set(breakler(_masal)) == {0.8}, str(sorted(set(breakler(_masal)))))

# Etiketler metnin parçası olarak gönderiliyor, yani karakter başına
# ücretlendirmeye GİRİYOR. İki ayrı ölçüt gerekiyor: uzun metinlerde yüzde
# (mutlak tutar orada birikiyor), kısa metinlerde mutlak artış (250 karakterlik
# bir ninnide %50 artış bile ~1,5 kuruş; yüzdeye bakmak yanıltır).
# Eşikler OPTİMUM değil, REGRESYON FRENİDİR: bugünkü ölçüm masallarda +%7…+%15
# (+242…+682 karakter), ninnilerde +110…+132 karakter (~1,5 kuruş). Duraklama
# kuralı ileride gevşetilirse bu iki kontrol önce patlar.
_uzun_pay = [(sid, ek / duz) for sid, _n, ek, duz in _ozet if duz > 1000]
_sisen = [(sid, f"+%{100*o:.1f}") for sid, o in _uzun_pay if o >= 0.20]
check("4g) Uzun metinlerde etiket maliyeti %20'nin altında (bugün en kötü +%15)",
      not _sisen, str(_sisen))

_mutlak = [(sid, ek) for sid, _n, ek, _d in _ozet if ek > 800]
check("4h) Hiçbir kayıtta etiket yükü 800 karakteri (~$0,09) geçmiyor "
      "(bugün en kötü +682)", not _mutlak, str(_mutlak))


# =============================================================================
# 5) CACHE — ayar değişince eski hızlı kayıt sunulmaya devam etmemeli
# =============================================================================
_AUDIO = Path(tempfile.mkdtemp(prefix="masal_tonu_audio_"))
tts.AUDIO_DIR = _AUDIO
_CAGRI = {"n": 0}


def _sahte_synth(text, model=None, voice_id=None, profil=None):
    _CAGRI["n"] += 1
    return b"ID3FAKEMP3"


_gercek_synth = tts.synthesize
tts.synthesize = _sahte_synth
try:
    METIN = "Bir varmış bir yokmuş. İkinci cümle.\n\nİkinci paragraf burada."
    a = tts.voice_audio("v1", METIN, profil="masal")
    b = tts.voice_audio("v1", METIN, profil="masal")
    check("5a) Aynı ses+metin+profil → 2. istekte TTS yok",
          b["cached"] is True and _CAGRI["n"] == 1, str(_CAGRI["n"]))

    c = tts.voice_audio("v1", METIN, profil="sohbet")
    check("5b) Profil değişince AYRI dosya (eski hızlı kayıt sunulmuyor)",
          c["audio_url"] != a["audio_url"] and _CAGRI["n"] == 2, str(_CAGRI["n"]))

    # Ayar sürümü damgası anahtarda mı? (kalibrasyondan sonra kritik)
    _eski_surum = tts.SES_AYAR_SURUMU
    tts.SES_AYAR_SURUMU = _eski_surum + "-yeni"
    d = tts.voice_audio("v1", METIN, profil="masal")
    tts.SES_AYAR_SURUMU = _eski_surum
    check("5c) Ayar sürümü artınca cache tazeleniyor",
          d["audio_url"] != a["audio_url"] and _CAGRI["n"] == 3, str(_CAGRI["n"]))

    # Masal profilinde metin duraklamalı hazırlanıyor, sohbet profilinde düz.
    check("5d) Masal ve sohbet AYNI metinden farklı TTS girdisi üretiyor",
          masal_metni_hazirla(METIN) != konusma_metnine_cevir(METIN), "")
finally:
    tts.synthesize = _gercek_synth


# =============================================================================
# 6) ENDPOINT SÖZLEŞMESİ
# =============================================================================
from api.schemas.voice import VoiceGenerateReq                # noqa: E402

_r = VoiceGenerateReq(voiceId="v1", storyId="masal_kelogan_sihirli_degnek")
check("6a) profile göndermemek geçerli (varsayılan masal router'da)",
      _r.profile is None, str(_r.profile))

_ok = VoiceGenerateReq(voiceId="v1", text="deneme", profile="sohbet")
check("6b) Geçerli profil kabul ediliyor", _ok.profile == "sohbet", "")

try:
    VoiceGenerateReq(voiceId="v1", text="deneme", profile="ninni")
    _red = False
except Exception:
    _red = True
check("6c) Geçersiz profil adı REDDEDİLİYOR (422)", _red, "")


# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("MASAL ANLATIM TONU TEST SONUÇLARI")
print("=" * 74)
for sid, n, ek, duz in _ozet:
    _pay = 100 * ek / max(duz, 1)
    print(f"    {sid:34} {n:>3} duraklama  {duz:>5} krktr  +{ek:>4} (+%{_pay:.0f})")
print("-" * 74)
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
