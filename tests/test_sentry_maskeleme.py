"""
Sentry KVKK maskelemesi — hangi verinin ÜÇÜNCÜ BİR SERVİSE gitmediğini sabitler.

NEDEN VAR: `send_default_pii=False` yeterli sanılıyor ama değil. Sentry
VARSAYILAN OLARAK stack frame'lerdeki YEREL DEĞİŞKENLERİ gönderir; bu uygulamada
o değişkenler `req.message` (annenin gece 3'te yazdığı cümle), `text` (topluluk
gönderisi), `baby.name`, `user.email` tutuyor. Yani tek bir 500 hatası, anne
verisini Sentry'ye taşıyabilirdi.

Üç katman birlikte gerekiyor ve üçü de burada test edilir:
  1. include_local_variables=False → frame değişkenleri hiç toplanmaz
  2. max_request_body_size="never" → istek gövdesi eklenmez
  3. before_send=maskele        → kalan her şey süzülür

Ayrıca: lokalde/testte Sentry AÇILMAMALI (yanlışlıkla geliştirici makinesinden
olay göndermek de bir sızıntıdır).

Çalıştırma: python tests/test_sentry_maskeleme.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"

from api import observability as obs                       # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def govde(x) -> str:
    """Olayın tamamını düz metne çevir — sızıntı aramak için."""
    return repr(x)


# Gerçek sızıntı adayları (bu uygulamadan alınmış tipik değerler).
ANNE_MESAJI = "Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum"
BEBEK_ADI = "Defne"
DOGUM = "2025-09-14"
EPOSTA = "anne@ornek.com"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghijklmnopqrstuvwxyz123456"
GONDERI = "Bu gece hiç uyumadım, topluluk gönderisi metni"


# =============================================================================
# 1) STACK FRAME YEREL DEĞİŞKENLERİ — asıl sızıntı yolu
# =============================================================================
olay = {
    "exception": {"values": [{
        "type": "ValueError",
        "value": "beklenmedik değer",
        "stacktrace": {"frames": [{
            "filename": "api/routers/chat.py",
            "function": "chat",
            "vars": {"req.message": ANNE_MESAJI, "user.email": EPOSTA,
                     "ctx": f"Bebek: {BEBEK_ADI}, doğum {DOGUM}"},
            "pre_context": [f'    message = "{ANNE_MESAJI}"'],
            "context_line": f'    r = chatbot._cevap_uret("{ANNE_MESAJI}")',
            "post_context": ["    return r"],
        }]},
    }]},
}
c = obs.maskele(olay)
_metin = govde(c)
check("1a) Frame yerel değişkenleri (vars) SİLİNDİ",
      "vars" not in _metin, _metin[:200])
check("1b) Anne mesajı olayda YOK", ANNE_MESAJI not in _metin, _metin[:250])
check("1c) Kaynak kodu satırları (context) silindi",
      "context_line" not in _metin and "pre_context" not in _metin, _metin[:200])
check("1d) Bebek adı olayda YOK", BEBEK_ADI not in _metin, "")
check("1e) E-posta olayda YOK", EPOSTA not in _metin, "")
# Ama teşhis için gerekenler KALMALI — maskeleme hatayı işe yaramaz hâle getirmemeli.
check("1f) Hata TİPİ korundu", "ValueError" in _metin, _metin[:200])
check("1g) Dosya adı ve fonksiyon korundu",
      "api/routers/chat.py" in _metin and "chat" in _metin, "")


# =============================================================================
# 2) İSTEK — gövde/çerez/başlık/sorgu
# =============================================================================
olay = {"request": {
    "url": f"https://api/api/v1/chat?token={TOKEN}&email={EPOSTA}",
    "method": "POST",
    "query_string": f"token={TOKEN}",
    "data": {"message": ANNE_MESAJI, "baby_id": "abc"},
    "cookies": {"session": "gizli"},
    "headers": {"Authorization": f"Bearer {TOKEN}", "X-API-Key": "sk_canli",
                "User-Agent": "TavsanApp/1.0", "Content-Type": "application/json"},
    "env": {"REMOTE_ADDR": "1.2.3.4"},
}}
c = obs.maskele(olay)
_metin = govde(c)
check("2a) İstek gövdesi (data) düştü", "data" not in c["request"], _metin[:200])
check("2b) Çerezler düştü", "cookies" not in c["request"], "")
check("2c) env (IP) düştü", "env" not in c["request"], "")
check("2d) Anne mesajı yok", ANNE_MESAJI not in _metin, _metin[:200])
check("2e) Authorization başlığı maskelendi",
      c["request"]["headers"]["Authorization"] == obs.MASKE, _metin[:200])
check("2f) X-API-Key maskelendi",
      c["request"]["headers"]["X-API-Key"] == obs.MASKE, "")
check("2g) Zararsız başlıklar KALDI (teşhis için)",
      c["request"]["headers"]["User-Agent"] == "TavsanApp/1.0", "")
check("2h) query_string maskelendi", c["request"]["query_string"] == obs.MASKE, "")
check("2i) URL'deki sorgu dizesi kırpıldı",
      "?" not in c["request"]["url"] and TOKEN not in _metin, c["request"]["url"])
check("2j) Endpoint yolu KALDI (hangi uçta patladı belli olsun)",
      "/api/v1/chat" in c["request"]["url"], c["request"]["url"])
check("2k) HTTP metodu korundu", c["request"]["method"] == "POST", "")


# =============================================================================
# 3) İSTİSNA MESAJINA GÖMÜLÜ İÇERİK
# =============================================================================
# include_local_variables=False bunu yakalayamaz: içerik metnin İÇİNE gömülü.
olay = {"exception": {"values": [{
    "type": "RuntimeError",
    "value": f"kayıt başarısız: kullanıcı {EPOSTA} token {TOKEN} mesaj: {ANNE_MESAJI}",
}]}}
c = obs.maskele(olay)
_deger = c["exception"]["values"][0]["value"]
check("3a) İstisna mesajındaki e-posta maskelendi", EPOSTA not in _deger, _deger)
check("3b) İstisna mesajındaki token maskelendi", TOKEN not in _deger, _deger)
check("3c) Hata tipi korundu", c["exception"]["values"][0]["type"] == "RuntimeError", "")

_uzun = {"exception": {"values": [{"type": "E", "value": "x" * 5000}]}}
check("3d) Aşırı uzun istisna metni kırpılıyor (içerik taşıyor olabilir)",
      len(obs.maskele(_uzun)["exception"]["values"][0]["value"]) < 300,
      str(len(obs.maskele(_uzun)["exception"]["values"][0]["value"])))


# =============================================================================
# 4) BREADCRUMB / EXTRA / LOG
# =============================================================================
olay = {
    "message": f"chat isteği: {ANNE_MESAJI} ({EPOSTA})",
    "logentry": {"message": "chat: user=%s q=%s", "params": [EPOSTA, ANNE_MESAJI]},
    "breadcrumbs": {"values": [
        {"message": f"SQL INSERT chat_messages content={ANNE_MESAJI}",
         "data": {"content": ANNE_MESAJI, "email": EPOSTA, "durum": 200}},
    ]},
    "extra": {"baby_name": BEBEK_ADI, "dogum_tarihi": DOGUM,
              "story_text": GONDERI, "sure_ms": 120},
    "tags": {"endpoint": "/api/v1/chat", "user_email": EPOSTA},
}
c = obs.maskele(olay)
_metin = govde(c)
# Breadcrumb'lar TAMAMEN düşer: içlerinde SQL sorguları ve log satırları var ve
# oradaki içerik hiçbir desene uymuyor (e-posta değil, token değil, kısa) —
# süzmek yerine kaynağı kapatmak tek güvenli yol.
check("4a) Breadcrumb'lar TAMAMEN düştü", "breadcrumbs" not in c, _metin[:200])
check("4b) Biçimlendirilmiş log mesajı düştü (%s'ler yerine oturmuş hâli)",
      "message" not in c, _metin[:200])
check("4c) Log parametreleri (içerik taşıyan %s'ler) düştü",
      "params" not in c["logentry"], str(c.get("logentry")))
check("4d) logentry biçim dizesi KALDI (kod, veri değil — teşhis için gerekli)",
      c["logentry"]["message"] == "chat: user=%s q=%s", str(c.get("logentry")))
check("4e) extra: bebek adı, doğum tarihi, masal metni maskelendi",
      all(c["extra"][k] == obs.MASKE
          for k in ("baby_name", "dogum_tarihi", "story_text")), str(c["extra"]))
check("4f) extra: zararsız ölçüm korundu", c["extra"]["sure_ms"] == 120, "")
check("4g) tags: user_email maskelendi, endpoint korundu",
      c["tags"]["user_email"] == obs.MASKE
      and c["tags"]["endpoint"] == "/api/v1/chat", str(c["tags"]))
check("4h) Hiçbir yerde anne mesajı/e-posta kalmadı",
      ANNE_MESAJI not in _metin and EPOSTA not in _metin, _metin[:300])


# =============================================================================
# 5) KULLANICI BAĞLAMI — yalnız hash'li id
# =============================================================================
olay = {"user": {"id": "abc123", "email": EPOSTA, "ip_address": "1.2.3.4",
                 "username": "anne"}}
c = obs.maskele(olay)
check("5a) Kullanıcıdan yalnız id kaldı", set(c["user"]) == {"id"}, str(c["user"]))
check("5b) E-posta ve IP düştü", EPOSTA not in govde(c) and "1.2.3.4" not in govde(c), "")

h1 = obs.kullanici_hash("kullanici-1")
h2 = obs.kullanici_hash("kullanici-2")
check("5c) Hash deterministik (aynı kullanıcı = aynı hash)",
      h1 == obs.kullanici_hash("kullanici-1"), h1)
check("5d) Farklı kullanıcı farklı hash", h1 != h2, f"{h1} {h2}")
check("5e) Hash ham id'yi içermiyor (geri çözülemez)",
      "kullanici-1" not in h1 and len(h1) == 16, h1)

_eski_tuz = os.environ["JWT_SECRET"]
os.environ["JWT_SECRET"] = "baska-bir-secret-en-az-otuz-iki-karakter"
check("5f) Hash TUZLU (tuz değişince hash değişir — başka sistemle eşleşmez)",
      obs.kullanici_hash("kullanici-1") != h1, "")
os.environ["JWT_SECRET"] = _eski_tuz


# =============================================================================
# 6) BAŞLATMA KOŞULLARI
# =============================================================================
_dsn_yedek = os.environ.pop("SENTRY_DSN", None)
_ort_yedek = os.environ.get("ENVIRONMENT")

os.environ["ENVIRONMENT"] = "production"
check("6a) DSN yoksa production'da bile AÇILMAZ", obs.sentry_baslat() is False, "")

os.environ["SENTRY_DSN"] = "https://ornek@o0.ingest.sentry.io/0"
os.environ["ENVIRONMENT"] = "development"
check("6b) Lokalde (development) AÇILMAZ — geliştirici makinesinden olay gitmez",
      obs.sentry_baslat() is False, "")

os.environ.pop("SENTRY_DSN", None)
if _dsn_yedek is not None:
    os.environ["SENTRY_DSN"] = _dsn_yedek
if _ort_yedek is not None:
    os.environ["ENVIRONMENT"] = _ort_yedek

check("6c) Test koşusunda Sentry KAPALI", obs.sentry_acik() is False, "")
check("6d) Örnekleme oranı 0.1 (spesifikasyon)", obs.TRACES_ORANI == 0.1,
      str(obs.TRACES_ORANI))

# Ayarların koda gerçekten yazıldığını doğrula — en sessiz hata bunları
# unutmaktır (her şey çalışır, veri sızar).
_kaynak = Path("api/observability.py").read_text(encoding="utf-8")
for ayar in ("send_default_pii=False", "include_local_variables=False",
             'max_request_body_size="never"', "before_send=maskele"):
    check(f"6e) init ayarı yerinde: {ayar}", ayar in _kaynak, "")

check("6f) DSN'in kendisi de maskeleniyor (log/extra'ya düşerse)",
      "o0.ingest.sentry.io" not in obs.metni_temizle(
          "dsn: https://abc@o0.ingest.sentry.io/0"), "")

check("6g) kullaniciyi_isaretle Sentry kapalıyken çökmüyor",
      obs.kullaniciyi_isaretle("abc") is None, "")


# =============================================================================
# 7) DAYANIKLILIK — maskeleme kancası olayı asla patlatmamalı
# =============================================================================
check("7a) Beklenmedik olay şekli çökmüyor",
      obs.maskele({"exception": {"values": None}, "request": "garip"}) is not None, "")
check("7b) Sözlük olmayan girdi olduğu gibi döner",
      obs.maskele("metin degil") == "metin degil", "")
_dongusel = {"extra": {}}
_dongusel["extra"]["kendisi"] = _dongusel["extra"]
check("7c) Kendine referanslı yapı sonsuz döngüye girmiyor",
      obs.maskele(_dongusel) is not None, "")


# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("SENTRY KVKK MASKELEME TEST SONUÇLARI")
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
