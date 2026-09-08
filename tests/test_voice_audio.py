"""
Ses URL'leri + ses cache testleri (Faz 6.7) — ElevenLabs MOCK'lanır, ağ YOK.

Kapsam:
  1. PUBLIC_BASE_URL yoksa göreli path (lokal geliştirme davranışı korunur)
  2. PUBLIC_BASE_URL varsa MUTLAK URL (mobil göreli birleştirme riski biter)
  3. SES CACHE: aynı voice_id + aynı metin → 2. istekte TTS ÇAĞRILMAZ (maliyet!)
  4. Farklı voice_id aynı metinde ayrı dosya (çakışma yok)
  5. Metin temizliği cache anahtarını etkiler ama aynı metin hep aynı hash
  6. /audio route auth'suz erişilebilir (hash'li dosya adı yeterli koruma)
  7. /audio path-traversal engeli korunuyor
  8. Masal kataloğu: 5 masal + 3 ninni, süre ipucu "5 dk", metinler yeterince uzun

Çalıştırma: python tests/test_voice_audio.py
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "faz67_voice_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"
os.environ.pop("PUBLIC_BASE_URL", None)

from api import tts                              # noqa: E402
from api.config import get_settings              # noqa: E402
from api.services import voice as voice_svc      # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def reload_settings(**env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


# --- ElevenLabs MOCK: gerçek üretim sayısını say ------------------------------
TTS_CALLS = {"n": 0, "son_profil": None, "son_metin": None}


def fake_synthesize(text, model=None, voice_id=None, profil=None,
                    usage_op=None, user_id=None):
    TTS_CALLS["n"] += 1
    TTS_CALLS["son_profil"] = profil
    TTS_CALLS["son_metin"] = text
    return b"ID3FAKEMP3" + text.encode("utf-8")[:32]


tts.synthesize = fake_synthesize                  # ağ ÇAĞRILMAZ

# Test ses dosyaları geçici klasöre yazılsın (gerçek cache kirlenmesin).
_AUDIO = Path(tempfile.mkdtemp(prefix="faz67_audio_"))
tts.AUDIO_DIR = _AUDIO

# =============================================================================
# 1-2) Göreli vs MUTLAK URL
# =============================================================================
reload_settings(PUBLIC_BASE_URL=None)
_rel = tts.audio_url("abc123")
check("1) PUBLIC_BASE_URL yok → göreli path",
      _rel == "/audio/abc123.mp3", _rel)

reload_settings(PUBLIC_BASE_URL="https://tavsan-api-production.up.railway.app")
_abs = tts.audio_url("abc123")
check("2) PUBLIC_BASE_URL var → MUTLAK URL",
      _abs == "https://tavsan-api-production.up.railway.app/audio/abc123.mp3", _abs)

reload_settings(PUBLIC_BASE_URL="https://example.com/")   # sondaki / temizlenmeli
check("2b) Sondaki eğik çizgi çift // üretmez",
      tts.audio_url("x") == "https://example.com/audio/x.mp3", tts.audio_url("x"))

# =============================================================================
# 3-5) SES CACHE — aynı metin ikinci kez ÜRETİLMEZ
# =============================================================================
reload_settings(PUBLIC_BASE_URL="https://tavsan-api-production.up.railway.app")
METIN = "Bir varmış bir yokmuş, uzak bir ormanda küçük bir tavşan yaşarmış."

TTS_CALLS["n"] = 0
r1 = tts.voice_audio("voice_A", METIN)
r2 = tts.voice_audio("voice_A", METIN)
check("3) Aynı ses+metin 2. kez → TTS ÇAĞRILMADI (cached=true)",
      r1["cached"] is False and r2["cached"] is True and TTS_CALLS["n"] == 1,
      f"tts_calls={TTS_CALLS['n']} c1={r1['cached']} c2={r2['cached']}")
check("3b) Cache HIT maliyeti 0",
      r2["tts_usd"] == 0.0 and r1["tts_usd"] > 0, f"{r1['tts_usd']} / {r2['tts_usd']}")
check("3c) İki istek AYNI dosyayı gösterir",
      r1["audio_url"] == r2["audio_url"], f"{r1['audio_url']} vs {r2['audio_url']}")
check("3d) Dönen URL mutlak",
      r1["audio_url"].startswith("https://"), r1["audio_url"])

# Farklı ses, aynı metin → ayrı dosya, yeniden üretim
r3 = tts.voice_audio("voice_B", METIN)
check("4) Farklı voice_id → ayrı dosya + yeniden üretim",
      r3["cached"] is False and r3["audio_url"] != r1["audio_url"]
      and TTS_CALLS["n"] == 2, f"tts_calls={TTS_CALLS['n']}")

# Aynı metin + aynı ses her zaman aynı hash (deterministik)
from api.konusma_metni import konusma_metnine_cevir, masal_metni_hazirla  # noqa: E402
_beklenen = hashlib.sha256(
    f"voice_A||{tts.MASAL_PROFILI}||{tts.SES_AYAR_SURUMU}||"
    f"{masal_metni_hazirla(METIN)}".encode("utf-8")).hexdigest()
check("5) Cache anahtarı deterministik (voice_id||profil||ayar_surumu||hazir_metin)",
      _beklenen in r1["audio_url"], r1["audio_url"])

# Profil ve ayar sürümü anahtara GİRMELİ: kalibrasyonla ayar değişince eski
# (hızlı okunmuş) masal sunulmaya devam ederse düzeltme kullanıcıya HİÇ ulaşmaz.
_n0 = TTS_CALLS["n"]
r_sohbet = tts.voice_audio("voice_A", METIN, profil="sohbet")
check("5c) Aynı ses+metin FARKLI profil → ayrı dosya + yeniden üretim",
      r_sohbet["audio_url"] != r1["audio_url"] and TTS_CALLS["n"] == _n0 + 1,
      f"{r_sohbet['audio_url']} vs {r1['audio_url']}")
check("5d) Profil synthesize'a geçiyor",
      TTS_CALLS["son_profil"] == "sohbet", str(TTS_CALLS["son_profil"]))

# Uzun masal metni de tek çağrıda üretilir (flash v2.5 limiti 40.000 karakter)
UZUN = "Bu bir cümledir. " * 400                      # ~6.800 karakter
TTS_CALLS["n"] = 0
r_uzun = tts.voice_audio("voice_A", UZUN)
check("5b) ~6.800 karakterlik masal TEK çağrıda üretildi (bölme gerekmiyor)",
      r_uzun["audio_url"] is not None and TTS_CALLS["n"] == 1,
      f"tts_calls={TTS_CALLS['n']}")

# =============================================================================
# 6-7) /audio route
# =============================================================================
from fastapi.testclient import TestClient        # noqa: E402
from api.db import engine                        # noqa: E402
from api.db.base import Base                     # noqa: E402
from api import models                           # noqa: E402,F401
from api.main import app                         # noqa: E402

Base.metadata.create_all(engine)
c = TestClient(app)

_hash = r1["audio_url"].rsplit("/", 1)[-1]        # <hash>.mp3
r = c.get(f"/audio/{_hash}")                      # AUTH HEADER YOK
check("6) /audio auth'suz erişilebilir (200)",
      r.status_code == 200 and r.headers.get("content-type", "").startswith("audio/mpeg"),
      f"{r.status_code} {r.headers.get('content-type')}")

check("7) /audio path-traversal engeli",
      c.get("/audio/..%2f..%2f.env").status_code in (400, 404), "")
check("7b) /audio hash olmayan ad reddedilir",
      c.get("/audio/kotu-ad.mp3").status_code == 400, "")

# =============================================================================
# 8) Masal kataloğu
# =============================================================================
cat = voice_svc.load_stories()
masallar = cat.get("masallar", [])
ninniler = cat.get("ninniler", [])
check("8) 5 masal + 3 ninni",
      len(masallar) == 5 and len(ninniler) == 3,
      f"masal={len(masallar)} ninni={len(ninniler)}")

_beklenen_basliklar = {
    "Keloğlan ile Sihirli Değnek", "Kırmızı Başlıklı Kız",
    "Üç Küçük Domuzcuk", "Çirkin Ördek Yavrusu", "Ayşecik ile Uyku Perisi",
}
check("8b) Başlıklar sözleşmedeki 5 masal",
      {m["title"] for m in masallar} == _beklenen_basliklar,
      str(sorted(m["title"] for m in masallar)))

check("8c) Tüm masallarda duration_hint '5 dk'",
      all(m.get("duration_hint") == "5 dk" for m in masallar),
      str([m.get("duration_hint") for m in masallar]))

_kisa = [(m["title"], len(m.get("text", "").split()))
         for m in masallar if len(m.get("text", "").split()) < 500]
check("8d) Her masal ≥500 kelime (5 dk TTS)", not _kisa, f"kısa={_kisa}")

# TTS'e düz metin gitmeli: markdown/emoji kalıntısı olmamalı
_kirli = [m["title"] for m in masallar
          if any(t in m.get("text", "") for t in ("**", "##", "- ", "* ", "•"))]
check("8e) Masal metinleri düz (markdown/madde işareti yok)", not _kirli, f"kirli={_kirli}")

check("8f) Ninniler DEĞİŞMEDİ (3 adet, id'ler aynı)",
      {n["id"] for n in ninniler} == {"ninni_dandini", "ninni_uyu_yavrum",
                                      "ninni_ay_isigi"},
      str([n["id"] for n in ninniler]))

# find_story her masalı bulabilmeli
_bulunamayan = [m["id"] for m in masallar if voice_svc.find_story(m["id"]) is None]
check("8g) find_story tüm masalları bulur", not _bulunamayan, f"yok={_bulunamayan}")

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("SES URL + CACHE + MASAL KATALOĞU TESTLERİ (Faz 6.7)")
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
