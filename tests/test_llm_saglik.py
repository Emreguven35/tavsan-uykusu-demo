"""
LLM sağlık izleyicisi testleri (Faz P2) — ağ/LLM çağrısı YOK, deterministik.

NEDEN VAR: 2026-09-14'te Anthropic kredi bakiyesi bitti; tüm sohbet ve plan
üretimi 400 aldı ama /health 200 dönmeye devam etti ve UptimeRobot arızayı
görmedi. Bu dosya o kör noktanın kapalı KALDIĞINI kilitler.

Kapsam:
  1. Hata sınıflandırma (credit/auth/rate_limit/overloaded/error)
  2. Durum makinesi — hata sonrası başarı iyileşme, pencere dışına düşme
  3. /health sözleşmesi — "llm" alanı + status'ün degraded'a düşmesi
  4. Yoklama hız sınırı ve /health'i BLOKLAMAMA garantisi
  5. Gerçek çağrı yolları izleyiciye bağlı mı (chatbot / plan_generator / moderation)

Çalıştırma: python tests/test_llm_saglik.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.setdefault("ENVIRONMENT", "development")

from engine import llm_saglik as ls                    # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


class SahteHata(Exception):
    pass


def _anahtarli():
    """Testler 'anahtar var' varsayar; yoksa durum() 'disabled' döner."""
    os.environ["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY") or "sk-test-yalnizca-test"


_anahtarli()

# =============================================================================
# 1) HATA SINIFLANDIRMA
# =============================================================================
_VAKALAR = [
    ("credit", "Your credit balance is too low to access the Anthropic API."),
    ("credit", "Please go to Plans & Billing to upgrade or purchase credits."),
    ("auth", "invalid x-api-key"),
    ("rate_limit", "Number of request tokens has exceeded your rate limit"),
    ("overloaded", "Overloaded"),
    ("error", "connection reset by peer"),
]
_sinif_hata = [f"{m[:40]!r} → {ls._hata_tipi(SahteHata(m))} (beklenen {bek})"
               for bek, m in _VAKALAR if ls._hata_tipi(SahteHata(m)) != bek]
check("1) Hata sınıflandırma doğru (credit/auth/rate_limit/overloaded/error)",
      not _sinif_hata, str(_sinif_hata))

# Kredi hatası EN KRİTİK olan: insan müdahalesi ister, kendiliğinden geçmez.
check("1b) Gerçek kredi hatası metni 'credit' olarak sınıflanıyor",
      ls._hata_tipi(SahteHata(
          "Error code: 400 - {'type': 'error', 'error': {'type': "
          "'invalid_request_error', 'message': 'Your credit balance is too low "
          "to access the Anthropic API.'}}")) == "credit", "")


# =============================================================================
# 2) DURUM MAKİNESİ
# =============================================================================
ls.sifirla()
check("2a) Hiç olay yokken durum 'unknown' (taze başlatma arıza değildir)",
      ls.durum()[0] == "unknown", ls.durum()[0])

ls.sifirla()
ls.kaydet_basari()
check("2b) Başarıdan sonra 'ok'", ls.durum()[0] == "ok", ls.durum()[0])

ls.kaydet_hata(SahteHata("Your credit balance is too low"))
check("2c) Hatadan sonra 'error'", ls.durum()[0] == "error", ls.durum()[0])

_d, _det = ls.durum(detay=True)
check("2d) Detayda hata tipi ve ardışık sayaç var",
      _det["son_hata_tipi"] == "credit" and _det["ardisik_hata"] == 1, str(_det))

ls.kaydet_hata(SahteHata("Your credit balance is too low"))
check("2e) Ardışık hata sayacı artıyor",
      ls.durum(detay=True)[1]["ardisik_hata"] == 2, "")

ls.kaydet_basari()
check("2f) Hatadan SONRA başarı gelirse 'ok'a döner (arıza kapandı)",
      ls.durum()[0] == "ok", ls.durum()[0])
check("2g) İyileşince ardışık sayaç sıfırlanır",
      ls.durum(detay=True)[1]["ardisik_hata"] == 0, "")

# Pencere dışına düşen ESKİ hata artık arıza sayılmaz.
ls.sifirla()
ls.kaydet_hata(SahteHata("Overloaded"))
ls._durum["son_hata_ts"] = time.time() - (ls.HATA_PENCERESI_SN + 60)
check("2h) Pencere dışındaki eski hata 'error' saymıyor",
      ls.durum()[0] == "ok", ls.durum()[0])

# Anahtar yoksa arıza DEĞİL — yerel/test ortamı.
_yedek = os.environ.pop("ANTHROPIC_API_KEY", None)
check("2i) Anahtar yoksa 'disabled' (arıza değil)",
      ls.durum()[0] == "disabled", ls.durum()[0])
if _yedek:
    os.environ["ANTHROPIC_API_KEY"] = _yedek
_anahtarli()


# =============================================================================
# 3) /health SÖZLEŞMESİ
# =============================================================================
from fastapi.testclient import TestClient                # noqa: E402
from api.main import app                                 # noqa: E402

istemci = TestClient(app)

ls.sifirla()
ls.kaydet_basari()
_r = istemci.get("/health")
_j = _r.json()
check("3a) /health 'llm' alanını döndürüyor",
      "llm" in _j, str(list(_j)))
check("3b) LLM sağlıklıyken llm='ok' ve status bozulmuyor",
      _j["llm"] == "ok" and _j["status"] in ("ok", "degraded"),
      f"llm={_j.get('llm')} status={_j.get('status')}")

ls.kaydet_hata(SahteHata("Your credit balance is too low"))
_j2 = istemci.get("/health").json()
check("3c) KREDİ BİTİNCE llm='error'", _j2["llm"] == "error", str(_j2.get("llm")))
check("3d) KREDİ BİTİNCE status='degraded' (UptimeRobot yakalasın)",
      _j2["status"] == "degraded", str(_j2.get("status")))
check("3e) /health yine 200 dönüyor (healthcheck'i kırma, durumu BİLDİR)",
      _r.status_code == 200, str(_r.status_code))

# Hata mesajı public /health'te SIZMAMALI (altyapı/sağlayıcı detayı).
check("3f) Public /health'te hata mesajı/tipi YOK",
      "llm_saglik" not in _j2 and "credit" not in str(_j2).lower(), str(_j2))

# Detay YALNIZ X-API-Key ile; anahtarsız detay=1 sessizce atlanır.
_j3 = istemci.get("/health?detail=1").json()
check("3g) Anahtarsız detail=1 hata detayını AÇMIYOR",
      "detail" not in _j3 or "llm_saglik" not in (_j3.get("detail") or {}), str(_j3.get("detail")))

ls.kaydet_basari()   # diğer testleri etkilemesin


# =============================================================================
# 4) YOKLAMA — hız sınırı + BLOKLAMAMA
# =============================================================================
ls.sifirla()
ls.kaydet_basari()          # taze gerçek çağrı var
check("4a) Yakın zamanda gerçek çağrı varsa yoklama GEREKMEZ (bedava kalsın)",
      ls._probe_gerekli_mi() is False, "")

ls.sifirla()
check("4b) Hiç olay yoksa yoklama gerekli",
      ls._probe_gerekli_mi() is True, "")
check("4c) Yoklama koşarken ikinci yoklama BAŞLAMAZ (çift çağrı yok)",
      ls._probe_gerekli_mi() is False, "")

# /health yoklamayı beklememeli: tetikleme çağrısı anında dönmeli.
ls.sifirla()
_t0 = time.perf_counter()
ls.yoklamayi_tetikle()
_gecen = time.perf_counter() - _t0
check("4d) yoklamayi_tetikle() BLOKLAMIYOR (<0.5 sn)",
      _gecen < 0.5, f"{_gecen:.3f} sn")

ls.sifirla()
ls.kaydet_basari()


# =============================================================================
# 5) GERÇEK ÇAĞRI YOLLARI İZLEYİCİYE BAĞLI MI?
# =============================================================================
# Kaydetme satırı silinirse /health sessizce kör kalır — kaynak taraması bunu yakalar.
_kaynaklar = {
    "engine/chatbot.py": ("llm_saglik.kaydet_hata", "llm_saglik.kaydet_basari"),
    "engine/plan_generator.py": ("llm_saglik.kaydet_hata", "llm_saglik.kaydet_basari"),
    "api/services/moderation.py": ("llm_saglik.kaydet_hata", "llm_saglik.kaydet_basari"),
}
_eksik = []
for dosya, aranan in _kaynaklar.items():
    metin = (ROOT / dosya).read_text(encoding="utf-8")
    for a in aranan:
        if a not in metin:
            _eksik.append(f"{dosya}: {a}")
check("5) Üç LLM çağrı yolu da izleyiciye bağlı (chatbot/plan/moderasyon)",
      not _eksik, str(_eksik))

# Moderasyon fail-open: hata kullanıcıya yansımaz ama İZLEYİCİYE bildirilmeli,
# yoksa kredi bitse bile o yol sessizce None döner ve arıza görünmez.
_mod = (ROOT / "api/services/moderation.py").read_text(encoding="utf-8")
check("5b) Moderasyonun fail-open dalı hatayı izleyiciye BİLDİRİYOR",
      "llm_saglik.kaydet_hata(e)" in _mod.split("fail-open")[-1]
      or "llm_saglik.kaydet_hata(e)" in _mod, "")


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("LLM SAĞLIK İZLEYİCİSİ TEST SONUÇLARI (Faz P2)")
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
