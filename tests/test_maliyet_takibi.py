"""
Maliyet takibi — api_usage kaydı, fiyatlandırma ve /admin/usage raporu.

NEDEN VAR: /ask yanıtındaki `maliyet.llm_usd` karakter sayısından TAHMİN
ediyordu (4 karakter ≈ 1 token) ve prompt caching'i hiç görmüyordu. Cache'ten
okunan token normal fiyatın %10'u, cache'e yazılan %125'i; bu iki çarpanı
görmeyen bir tahminle "cache bize ne kazandırdı" sorusu cevaplanamaz.

Bu dosyanın sabitlediği tuzaklar:
  1. Bilinmeyen model SESSİZCE 0 dolar yazmamalı — maliyet tablosunu "her şey
     bedava" gösteren en tehlikeli hata bu olurdu. Üst sınırdan hesaplanır.
  2. Kayıt ana isteği YAVAŞLATMAMALI ve hata SESSİZ YUTULMALI — DB düşerse anne
     cevabını yine de alır.
  3. Cache HIT'inde api_usage'a satır AÇILMAMALI (dış servis çağrısı yok);
     yoksa hem maliyet hem "çağrı sayısı" şişer.
  4. Tabloda İÇERİK olmamalı (KVKK) — yalnız sayaçlar.

Çalıştırma: python tests/test_maliyet_takibi.py
"""
import logging
import os
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

_DB = Path(tempfile.gettempdir()) / "maliyet_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"

from fastapi.testclient import TestClient                  # noqa: E402

from api import tts                                        # noqa: E402
from api.config import (                                   # noqa: E402
    ANTHROPIC_FIYATLARI, CACHE_OKUMA_CARPANI, CACHE_YAZMA_CARPANI,
    GUNLUK_MALIYET_ESIGI_USD,
)
from api.db import Base, SessionLocal, engine              # noqa: E402
import api.models                                          # noqa: E402,F401
from api.models import ApiUsage, ChatMessage, CommunityProfile  # noqa: E402
from api.main import app                                   # noqa: E402
from api.services import usage                             # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []

# Testlerde iş parçacığı beklemek yerine SENKRON yaz — determinizm için.
usage.SENKRON_MOD = True


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def yakin(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def kayit_sil() -> None:
    db = SessionLocal()
    try:
        db.query(ApiUsage).delete()
        db.commit()
    finally:
        db.close()
    usage.gun_durumu_sifirla()


def kayitlar() -> list[ApiUsage]:
    db = SessionLocal()
    try:
        return db.query(ApiUsage).order_by(ApiUsage.created_at).all()
    finally:
        db.close()


def kayit_ol(email: str) -> tuple[str, str]:
    """Kayıt ol, (access_token, user_id) döndür. /auth/me endpoint'i yok;
    user_id DB'den okunur."""
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "parola12345"})
    if r.status_code == 409:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": "parola12345"})
    tok = r.json()["access_token"]
    from api.models import User
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.email == email).one().id
    finally:
        db.close()
    return tok, str(uid)


# =============================================================================
# 1) FİYATLANDIRMA — cache indirimi/zammı DOĞRU uygulanıyor mu?
# =============================================================================
HAIKU = "claude-haiku-4-5"
_h = ANTHROPIC_FIYATLARI[HAIKU]

check("1a) Girdi+çıktı fiyatı 1M token üzerinden",
      yakin(usage.anthropic_maliyet(HAIKU, 1_000_000, 0), _h["in"])
      and yakin(usage.anthropic_maliyet(HAIKU, 0, 1_000_000), _h["out"]),
      str(usage.anthropic_maliyet(HAIKU, 1_000_000, 0)))

check("1b) Cache OKUMA indirimli (%10) fiyatlanıyor",
      yakin(usage.anthropic_maliyet(HAIKU, 0, 0, cached_tokens=1_000_000),
            _h["in"] * CACHE_OKUMA_CARPANI),
      str(usage.anthropic_maliyet(HAIKU, 0, 0, cached_tokens=1_000_000)))

check("1c) Cache YAZMA zamlı (%125) fiyatlanıyor",
      yakin(usage.anthropic_maliyet(HAIKU, 0, 0, cache_write_tokens=1_000_000),
            _h["in"] * CACHE_YAZMA_CARPANI),
      str(usage.anthropic_maliyet(HAIKU, 0, 0, cache_write_tokens=1_000_000)))

check("1d) Cache okuma, tam fiyattan UCUZ (indirim gerçekten uygulanıyor)",
      usage.anthropic_maliyet(HAIKU, 0, 0, cached_tokens=1000)
      < usage.anthropic_maliyet(HAIKU, 1000, 0), "")

# Sonnet (plan üretimi) Haiku'dan pahalı olmalı — tablo karışmış olmasın.
check("1e) Sonnet, Haiku'dan pahalı (fiyat tablosu karışmamış)",
      usage.anthropic_maliyet("claude-sonnet-4-6", 1_000_000, 0)
      > usage.anthropic_maliyet(HAIKU, 1_000_000, 0), "")

# EN ÖNEMLİ KONTROL: bilinmeyen model 0 dolar YAZMAMALI.
_bilinmeyen = usage.anthropic_maliyet("claude-yeni-model-9", 1_000_000, 1_000_000)
check("1f) Bilinmeyen model SIFIR değil, üst sınırdan fiyatlanıyor",
      _bilinmeyen > 0 and _bilinmeyen >= usage.anthropic_maliyet(HAIKU, 1_000_000, 1_000_000),
      f"${_bilinmeyen:.4f}")

check("1g) ElevenLabs karakter başına fiyatlanıyor",
      yakin(usage.elevenlabs_maliyet("eleven_flash_v2_5", 10_000),
            10_000 * 0.00011), "")
check("1h) Bilinmeyen TTS modeli de sıfır değil",
      usage.elevenlabs_maliyet("yeni-tts", 1000) > 0, "")
check("1i) Ses klonlama karakterle DEĞİL işlem başına fiyatlanıyor",
      usage.maliyet_hesapla(usage.SERVIS_ELEVENLABS, "voice-clone", characters=99999,
                            operation=usage.OP_VOICE_CLONE) == 0.0, "")

check("1j) cache_kazanci = tam fiyat ile indirimli fiyat farkı",
      yakin(usage.cache_kazanci(HAIKU, 1_000_000),
            _h["in"] * (1 - CACHE_OKUMA_CARPANI)), "")


# =============================================================================
# 2) USAGE BLOĞU OKUMA — SDK yanıtı None-güvenli çözülüyor mu?
# =============================================================================
class _SahteUsage:
    input_tokens = 120
    output_tokens = 45
    cache_read_input_tokens = 900
    cache_creation_input_tokens = 30


class _SahteYanit:
    usage = _SahteUsage()


_u = usage.anthropic_usage(_SahteYanit())
check("2a) usage bloğu dört sayacı da taşıyor",
      _u == {"input_tokens": 120, "output_tokens": 45,
             "cached_tokens": 900, "cache_write_tokens": 30}, str(_u))


class _UsageYok:
    usage = None


check("2b) usage yoksa sıfırlarla döner (çökmez)",
      usage.anthropic_usage(_UsageYok()) == {
          "input_tokens": 0, "output_tokens": 0,
          "cached_tokens": 0, "cache_write_tokens": 0}, "")


class _KismiUsage:
    class usage:                     # noqa: N801 — sahte SDK nesnesi
        input_tokens = 10
        output_tokens = None         # SDK bazı alanları None döndürebiliyor


check("2c) None alanlar 0 sayılır (TypeError yok)",
      usage.anthropic_usage(_KismiUsage())["output_tokens"] == 0, "")

# Motor tarafındaki kopya (engine/chatbot._usage_ozet) ile aynı sonucu vermeli —
# ayrışırsa maliyet sessizce yanlış hesaplanır.
from engine import chatbot as _cb                              # noqa: E402
check("2d) Motor içindeki kopya aynı sonucu üretiyor (ayrışma yok)",
      _cb._usage_ozet(_SahteYanit()) == _u, str(_cb._usage_ozet(_SahteYanit())))


# =============================================================================
# 3) KAYIT — asenkron, hataya dayanıklı, KVKK
# =============================================================================
kayit_sil()
usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_CHAT, model=HAIKU,
             usage=_u, user_id=None, duration_ms=123)
k = kayitlar()
check("3a) Kayıt yazıldı", len(k) == 1, str(len(k)))
if k:
    r = k[0]
    check("3b) Sayaçlar olduğu gibi saklandı",
          (r.input_tokens, r.output_tokens, r.cached_tokens, r.cache_write_tokens)
          == (120, 45, 900, 30), str(r.input_tokens))
    check("3c) Maliyet fiyat tablosundan hesaplandı",
          yakin(r.estimated_cost_usd,
                round(usage.anthropic_maliyet(HAIKU, 120, 45, 900, 30), 6), 1e-6),
          str(r.estimated_cost_usd))
    check("3d) duration_ms saklandı", r.duration_ms == 123, str(r.duration_ms))

# KVKK: modelde içerik kolonu OLMAMALI.
_kolonlar = {c.name for c in ApiUsage.__table__.columns}
_yasak = {"content", "text", "message", "prompt", "answer", "cevap", "soru", "email"}
check("3e) KVKK: tabloda içerik kolonu YOK",
      not (_kolonlar & _yasak), str(sorted(_kolonlar & _yasak)))
check("3f) Beklenen kolonlar mevcut",
      {"service", "operation", "model", "input_tokens", "output_tokens",
       "cached_tokens", "characters", "estimated_cost_usd", "user_id",
       "duration_ms", "created_at"} <= _kolonlar, str(sorted(_kolonlar)))

# Hata SESSİZ yutulmalı: yazma patlasa bile çağıran etkilenmez.
_gercek_yaz = usage._yaz


def _patlayan_yaz(kayit):
    raise RuntimeError("DB düştü")


usage._yaz = _patlayan_yaz
_patladi = False
try:
    usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_CHAT, model=HAIKU, usage=_u)
except Exception:
    _patladi = True
usage._yaz = _gercek_yaz
check("3g) Yazma hatası SESSİZ yutuluyor (ana istek etkilenmez)", not _patladi, "")

# Bilinmeyen operation da kaydedilmeli (veri kaybetme), yalnız uyarı loglanmalı.
kayit_sil()
usage.kaydet(usage.SERVIS_ANTHROPIC, "yeni_operasyon", model=HAIKU, usage=_u)
check("3h) Bilinmeyen operation kaydı DÜŞÜRÜLMÜYOR", len(kayitlar()) == 1, "")

# Asenkron mod: kuyruk üzerinden yazılıyor mu?
usage.SENKRON_MOD = False
kayit_sil()
usage.kaydet(usage.SERVIS_ELEVENLABS, usage.OP_TTS,
             model="eleven_flash_v2_5", characters=500)
usage.bekle()
check("3i) Asenkron kuyruk kaydı gerçekten yazıyor", len(kayitlar()) == 1,
      str(len(kayitlar())))
usage.SENKRON_MOD = True


# =============================================================================
# 4) GÜNLÜK EŞİK ALARMI
# =============================================================================
kayit_sil()


class _LogYakala(logging.Handler):
    def __init__(self):
        super().__init__()
        self.kritikler = []

    def emit(self, record):
        if record.levelno >= logging.CRITICAL:
            self.kritikler.append(record.getMessage())


_yakala = _LogYakala()
logging.getLogger("tavsan.usage").addHandler(_yakala)

# Eşiğin ALTINDA kalan bir çağrı uyarı üretmemeli.
usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_CHAT, model=HAIKU,
             usage={"input_tokens": 1000, "output_tokens": 100})
check("4a) Eşik altında kritik uyarı YOK", not _yakala.kritikler,
      str(_yakala.kritikler))

# Eşiği tek başına aşan bir çağrı → CRITICAL.
_buyuk = int(GUNLUK_MALIYET_ESIGI_USD / (_h["out"] / 1_000_000)) + 1000
usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_PLAN_GENERATE, model=HAIKU,
             usage={"input_tokens": 0, "output_tokens": _buyuk})
check("4b) Eşik aşılınca CRITICAL log düşüyor", len(_yakala.kritikler) == 1,
      str(_yakala.kritikler))
check("4c) Uyarı metni eşiği ve tutarı söylüyor",
      bool(_yakala.kritikler) and "EŞİĞİ AŞILDI" in _yakala.kritikler[0]
      and "$" in _yakala.kritikler[0], str(_yakala.kritikler[:1]))

# Aynı gün ikinci kez uyarı YAĞMURU olmamalı.
usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_CHAT, model=HAIKU,
             usage={"input_tokens": 0, "output_tokens": _buyuk})
check("4d) Aynı gün tekrar tekrar uyarı basılmıyor", len(_yakala.kritikler) == 1,
      str(len(_yakala.kritikler)))
logging.getLogger("tavsan.usage").removeHandler(_yakala)


# =============================================================================
# 5) TTS — metnin KENDİSİ değil UZUNLUĞU kaydediliyor
# =============================================================================
kayit_sil()
_ORIJINAL_POST = tts.requests.post


class _TTSYanit:
    status_code = 200
    content = b"ID3FAKE"

    def raise_for_status(self):
        return None


tts.requests.post = lambda *a, **k: _TTSYanit()
GIZLI = "Bir varmış bir yokmuş, çok gizli bir masal metni."
tts.synthesize(GIZLI, voice_id="v1", profil="masal",
               usage_op=usage.OP_TTS, user_id=None)
tts.requests.post = _ORIJINAL_POST

k = kayitlar()
check("5a) TTS çağrısı kaydedildi", len(k) == 1, str(len(k)))
if k:
    check("5b) characters = metnin UZUNLUĞU", k[0].characters == len(GIZLI),
          f"{k[0].characters} vs {len(GIZLI)}")
    check("5c) Metnin kendisi hiçbir kolonda YOK (KVKK)",
          all(GIZLI not in str(getattr(k[0], c) or "") for c in _kolonlar), "")
    check("5d) Servis/operasyon doğru",
          k[0].service == usage.SERVIS_ELEVENLABS and k[0].operation == usage.OP_TTS,
          f"{k[0].service}/{k[0].operation}")

# TTS hatasında kayıt AÇILMAMALI (ücretlendirilmeyen çağrı deftere girmesin).
kayit_sil()
tts.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kota"))
tts.synthesize("deneme", voice_id="v1", usage_op=usage.OP_TTS)
tts.requests.post = _ORIJINAL_POST
check("5e) Başarısız TTS çağrısı kaydedilmiyor", len(kayitlar()) == 0,
      str(len(kayitlar())))


# =============================================================================
# 6) /chat — LLM'e gidildiğinde kayıt açılır, CACHE HIT'te AÇILMAZ
# =============================================================================
kayit_sil()
tok, uid = kayit_ol("maliyet@ornek.com")
BASLIK = {"Authorization": f"Bearer {tok}"}

_gercek_cevap = _cb._cevap_uret


def _sahte_llm(message, yas=None, baby_context=None):
    return {"cevap": "Deneme cevabı.", "cache_hit": False, "kaynaklar": [],
            "anahtar": "x" * 16, "llm": True, "in_chars": 10, "out_chars": 10,
            "retrieval_layer": "k1", "top_score": 0.9,
            "model": HAIKU, "usage": {"input_tokens": 500, "output_tokens": 80,
                                      "cached_tokens": 200, "cache_write_tokens": 0}}


def _sahte_cache(message, yas=None, baby_context=None):
    return {"cevap": "Cache'ten cevap.", "cache_hit": True, "kaynaklar": [],
            "anahtar": "y" * 16, "llm": False, "in_chars": 0, "out_chars": 0,
            "retrieval_layer": None, "top_score": None}


_cb._cevap_uret = _sahte_llm
r = client.post("/api/v1/chat", headers=BASLIK, json={"message": "merhaba", "history": []})
check("6a) /chat 200 döndü", r.status_code == 200, str(r.status_code))
k = kayitlar()
check("6b) LLM çağrısı için kayıt açıldı", len(k) == 1, str(len(k)))
if k:
    check("6c) Gerçek usage sayaçları kaydedildi",
          (k[0].input_tokens, k[0].output_tokens, k[0].cached_tokens) == (500, 80, 200),
          str((k[0].input_tokens, k[0].output_tokens, k[0].cached_tokens)))
    check("6d) user_id kaydedildi", str(k[0].user_id) == str(uid),
          f"{k[0].user_id} vs {uid}")
    check("6e) duration_ms ölçüldü", k[0].duration_ms is not None, "")

_cb._cevap_uret = _sahte_cache
client.post("/api/v1/chat", headers=BASLIK, json={"message": "merhaba", "history": []})
check("6f) CACHE HIT'te YENİ kayıt açılmıyor (çağrı yok, maliyet yok)",
      len(kayitlar()) == 1, str(len(kayitlar())))
_cb._cevap_uret = _gercek_cevap


# =============================================================================
# 7) /admin/usage — yetki + rapor
# =============================================================================
r = client.get("/api/v1/admin/usage", headers=BASLIK)
check("7a) Moderatör olmayan 403 alıyor", r.status_code == 403, str(r.status_code))
check("7b) Auth'suz erişim 401",
      client.get("/api/v1/admin/usage").status_code == 401, "")

# Kullanıcıyı moderatör yap.
db = SessionLocal()
try:
    db.add(CommunityProfile(user_id=_uuid.UUID(str(uid)), nickname="mod_test",
                            is_moderator=True))
    db.commit()
finally:
    db.close()

# Bilinen veri kur: 2 anthropic + 1 elevenlabs.
kayit_sil()
usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_CHAT, model=HAIKU,
             usage={"input_tokens": 1_000_000, "output_tokens": 0,
                    "cached_tokens": 1_000_000, "cache_write_tokens": 0},
             user_id=uid)
usage.kaydet(usage.SERVIS_ANTHROPIC, usage.OP_PLAN_GENERATE, model="claude-sonnet-4-6",
             usage={"input_tokens": 1_000_000, "output_tokens": 0})
usage.kaydet(usage.SERVIS_ELEVENLABS, usage.OP_TTS, model="eleven_flash_v2_5",
             characters=10_000)

r = client.get("/api/v1/admin/usage", headers=BASLIK)
check("7c) Moderatör 200 alıyor", r.status_code == 200, r.text[:200])
d = r.json() if r.status_code == 200 else {}

_beklenen = (usage.anthropic_maliyet(HAIKU, 1_000_000, 0, 1_000_000, 0)
             + usage.anthropic_maliyet("claude-sonnet-4-6", 1_000_000, 0)
             + usage.elevenlabs_maliyet("eleven_flash_v2_5", 10_000))
check("7d) Toplam maliyet doğru", yakin(d.get("toplam_usd", -1), round(_beklenen, 6), 1e-5),
      f"{d.get('toplam_usd')} vs {_beklenen}")
check("7e) Çağrı sayısı doğru", d.get("cagri_sayisi") == 3, str(d.get("cagri_sayisi")))

_servis = {x["ad"]: x for x in d.get("servis", [])}
check("7f) Servis kırılımı iki servisi de içeriyor",
      set(_servis) == {"anthropic", "elevenlabs"}, str(sorted(_servis)))
check("7g) Servis kırılımı maliyete göre azalan sıralı",
      [x["usd"] for x in d.get("servis", [])]
      == sorted([x["usd"] for x in d.get("servis", [])], reverse=True), "")

_op = {x["ad"]: x for x in d.get("operasyon", [])}
check("7h) Operasyon kırılımı chat/plan_generate/tts içeriyor",
      set(_op) == {"chat", "plan_generate", "tts"}, str(sorted(_op)))

check("7i) Gün serisi bugünü içeriyor", len(d.get("gunluk", [])) == 1,
      str(d.get("gunluk")))

# Prompt cache özeti: 1M okundu, 2M tam fiyatlı → oran 1/3.
_pc = d.get("cache", {}).get("prompt_cache", {})
check("7j) Prompt cache oranı okunan/(okunan+tam) formülüyle",
      yakin(_pc.get("oran", -1), round(1_000_000 / 3_000_000, 4), 1e-4), str(_pc))
check("7k) Cache kazancı pozitif ve tam fiyatın %90'ı kadar",
      yakin(_pc.get("kazanc_usd", -1),
            round(usage.cache_kazanci(HAIKU, 1_000_000), 6), 1e-6), str(_pc))

check("7l) group_by=service seçilen kırılımı döndürüyor",
      client.get("/api/v1/admin/usage?group_by=service", headers=BASLIK)
      .json()["gruplar"][0]["ad"] in ("anthropic", "elevenlabs"), "")
check("7m) group_by=operation seçilen kırılımı döndürüyor",
      "ad" in client.get("/api/v1/admin/usage?group_by=operation",
                         headers=BASLIK).json()["gruplar"][0], "")
check("7n) Geçersiz group_by reddediliyor (422)",
      client.get("/api/v1/admin/usage?group_by=hafta", headers=BASLIK)
      .status_code == 422, "")

# Tarih aralığı gerçekten süzüyor mu?
_dun = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
r_dun = client.get(f"/api/v1/admin/usage?from={_dun}&to={_dun}", headers=BASLIK)
check("7o) Geçmiş tarih aralığında kayıt yok (filtre çalışıyor)",
      r_dun.status_code == 200 and r_dun.json()["cagri_sayisi"] == 0,
      r_dun.text[:150])
check("7p) from > to reddediliyor (400)",
      client.get("/api/v1/admin/usage?from=2026-08-25&to=2026-08-01",
                 headers=BASLIK).status_code == 400, "")

check("7q) Eşik değeri raporda görünüyor",
      d.get("gunluk_esik_usd") == GUNLUK_MALIYET_ESIGI_USD, str(d.get("gunluk_esik_usd")))
check("7r) Eşiği aşan gün listesi var (bugün aşılmadı)",
      d.get("esigi_asan_gunler") == [], str(d.get("esigi_asan_gunler")))

# Cevap cache oranı chat_messages'tan okunuyor mu?
db = SessionLocal()
try:
    for cached in (True, True, False, False):
        db.add(ChatMessage(user_id=_uuid.UUID(str(uid)), role="assistant",
                           content="x", cached=cached))
    db.commit()
finally:
    db.close()
_cc = client.get("/api/v1/admin/usage", headers=BASLIK).json()["cache"]["cevap_cache"]
check("7s) Cevap cache isabet oranı chat_messages'tan hesaplanıyor",
      _cc["toplam"] >= 4 and _cc["hit"] >= 2 and 0 < _cc["oran"] <= 1, str(_cc))
check("7t) Cevap cache kazancı 'tahmini' olarak raporlanıyor",
      "tahmini_kazanc_usd" in _cc and _cc["tahmini_kazanc_usd"] >= 0, str(_cc))


# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("MALİYET TAKİBİ TEST SONUÇLARI")
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
