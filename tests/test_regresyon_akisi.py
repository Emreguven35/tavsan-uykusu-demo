"""
REGRESYON AKIŞI — v1.4 üç kademe (İlayda S9). LLM YOK, ağ YOK, prod DB YOK.

v1.3'te regresyon tespit edilince tek bir bayrak vardı: `restart_program_suggested`
→ mobil "Programı baştan başlatalım mı?" kartını gösteriyordu. İlayda bu yolu
REDDETTİ: "Öncelikle şunu yapıyoruz: kendi uykuya döndü mü çocuk? Bunu soruyoruz
önce. ... 45 gün dolana kadar eğitime yine de devam ediyoruz. 45 günü geride
bıraktıysak pediatri, fizyoterapi ya da ergoterapi kontrolü rica ediyoruz."

Üç kademe:
  1. kendi_donuyor_mu   — anneye sorulur (cevap yok).
  2. devam_45           — "hayır" + 45 gün DOLMADI → eğitime devam.
  3. tibbi_yonlendirme  — "hayır" + 45 gün DOLDU → tıbbi değerlendirme önerisi.
"Evet" cevabı kartı REGRESYON_SESSIZLIK_GUN (7) gün kapatır.

Bu dosya kademeleri, 7 günlük sessizliği, POST /plans/regresyon-cevap ucunu ve
eski bayrağın gerçekten KALKTIĞINI kilitler.

Çalıştırma: python tests/test_regresyon_akisi.py
"""
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

_DB = Path(tempfile.gettempdir()) / "regresyon_akisi_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby                                 # noqa: E402
from api.main import app                                    # noqa: E402
from api.schemas.plan import PlanAdaptResp                  # noqa: E402
from api.services import plan_adapter as pa                 # noqa: E402
from api.services import plan_service                       # noqa: E402

from tests.llm_muhuru import muhur_saglam_mi, muhurle       # noqa: E402
muhurle()                                    # canlı Sonnet YOK

Base.metadata.create_all(bind=engine)
client = TestClient(app)
TODAY = datetime.now(timezone.utc).date()

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def _tok(eposta: str) -> dict:
    r = client.post("/api/v1/auth/register",
                    json={"email": eposta, "password": "TestPass123!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def bebek_kur(H, ay: float = 9.0) -> str:
    dogum = TODAY - timedelta(days=int(ay * 30.44))
    return client.post("/api/v1/babies", headers=H,
                       json={"name": "Reg", "birth_date": dogum.isoformat()}
                       ).json()["id"]


def egitim_basla(H, bid: str, gun_once: int) -> None:
    r = client.patch(f"/api/v1/babies/{bid}", headers=H,
                     json={"training_started_at":
                           (TODAY - timedelta(days=gun_once)).isoformat()})
    assert r.status_code == 200, r.text


def cevapla(H, bid: str, kendi_donuyor: bool):
    return client.post(f"/api/v1/plans/regresyon-cevap?baby_id={bid}",
                       headers=H, json={"kendi_donuyor": kendi_donuyor})


def bebek_satiri(bid: str) -> Baby:
    db = SessionLocal()
    try:
        return db.query(Baby).filter(Baby.id == _uuid.UUID(bid)).one()
    finally:
        db.close()


# =============================================================================
# 1) SAF FONKSİYON — üç kademe
# =============================================================================
print("1) regresyon_karti() kademeleri")

k1 = pa.regresyon_karti(TODAY - timedelta(days=20), None, TODAY)
check("1) Cevap YOK → 1. kademe: kendi_donuyor_mu",
      k1["tip"] == "kendi_donuyor_mu"
      and k1["metin"] == pa.REGRESYON_METINLERI["kendi_donuyor_mu"],
      str(k1))
check("1a) Soru metni '20 dakika' bekleme kuralını içeriyor",
      "20 dakika" in k1["metin"], k1["metin"])

k2 = pa.regresyon_karti(TODAY - timedelta(days=19), False, TODAY)   # 20. gün
check("1b) 'hayır' + 45 gün DOLMADI → devam_45",
      k2["tip"] == "devam_45" and k2["kirkbes_gun_doldu"] is False
      and k2["egitim_gunu"] == 20, str(k2))
check("1c) devam_45 metni 45. günü söylüyor",
      "45. güne kadar" in k2["metin"], k2["metin"])

k3 = pa.regresyon_karti(TODAY - timedelta(days=49), False, TODAY)   # 50. gün
check("1d) 'hayır' + 45 gün DOLDU → tibbi_yonlendirme",
      k3["tip"] == "tibbi_yonlendirme" and k3["kirkbes_gun_doldu"] is True
      and k3["egitim_gunu"] == 50, str(k3))

k4 = pa.regresyon_karti(TODAY - timedelta(days=49), True, TODAY)
check("1e) 'evet' → gösterilecek kart YOK (tip None)",
      k4["tip"] is None and k4["metin"] is None, str(k4))

check("1f) 45. günün TAM kendisi 'doldu' sayılıyor (sınır dahil)",
      pa.regresyon_karti(TODAY - timedelta(days=44), False, TODAY)["tip"]
      == "tibbi_yonlendirme"
      and pa.regresyon_karti(TODAY - timedelta(days=43), False,
                             TODAY)["tip"] == "devam_45",
      f'44 gün önce → {pa.regresyon_karti(TODAY - timedelta(days=44), False, TODAY)["tip"]}')

check("1g) Eğitim başlamamışsa gün None, 45 gün dolmuş SAYILMAZ",
      pa.egitim_gunu(None, TODAY) is None
      and pa.regresyon_karti(None, False, TODAY)["tip"] == "devam_45",
      str(pa.regresyon_karti(None, False, TODAY)))
check("1h) Başlangıç günü 1. gündür (0'dan değil)",
      pa.egitim_gunu(TODAY, TODAY) == 1, str(pa.egitim_gunu(TODAY, TODAY)))

# --- Tıbbi metin TEŞHİS KOYMAZ (danışmanlık sınırı, chatbot.py ile aynı) -----
_tibbi = pa.REGRESYON_METINLERI["tibbi_yonlendirme"]
check("1i) Tıbbi metin yönlendiriyor, teşhis KOYMUYOR",
      "öneriyoruz" in _tibbi
      and not any(k in _tibbi.lower()
                  for k in ["teşhis", "tanı koy", "hastalığı var",
                            "ilaç", "doz", "mg"]),
      _tibbi)
check("1j) Tıbbi metin İlayda'nın saydığı başlıkları içeriyor",
      all(k in _tibbi for k in ["demir", "D vitamini", "magnezyum",
                                "uyku apnesi", "geniz eti"]), _tibbi)
check("1k) Fizyoterapi/ergoterapi seçeneği de öneriliyor",
      "fizyoterapi" in _tibbi and "ergoterapi" in _tibbi, _tibbi)


# =============================================================================
# 2) ESKİ BAYRAK GERÇEKTEN KALKTI
# =============================================================================
print("2) restart_program_suggested kaldırıldı mı")
check("2) PlanAdaptResp'te alan YOK",
      "restart_program_suggested" not in PlanAdaptResp.model_fields,
      str(sorted(PlanAdaptResp.model_fields)))
check("2a) Yeni alanlar şemada VAR",
      all(f in PlanAdaptResp.model_fields
          for f in ["regresyon_karti", "egitim_baslangic_gunu",
                    "kirkbes_gun_doldu"]), "")


# =============================================================================
# 3) 7 GÜNLÜK SESSİZLİK — "evet" cevabı süresizce susturmaz
# =============================================================================
print("3) 'evet' cevabının 7 günlük ömrü")


class _SahteBebek:
    def __init__(self, cevap, gun_once):
        self.regresyon_kendi_donuyor = cevap
        self.regresyon_cevap_at = (
            None if gun_once is None
            else datetime.now(timezone.utc) - timedelta(days=gun_once))


check("3) 'evet' 3 gün önce verildi → hâlâ geçerli (kart kapalı)",
      plan_service.regresyon_cevabi(_SahteBebek(True, 3), TODAY) is True, "")
check("3a) 'evet' 8 gün önce verildi → süresi doldu, YENİDEN sorulur",
      plan_service.regresyon_cevabi(_SahteBebek(True, 8), TODAY) is None, "")
check("3b) 'hayır' cevabının süresi YOK (akış 45 güne göre ilerler)",
      plan_service.regresyon_cevabi(_SahteBebek(False, 30), TODAY) is False, "")
check("3c) Hiç cevap yoksa None",
      plan_service.regresyon_cevabi(_SahteBebek(None, None), TODAY) is None, "")
check("3d) Sessizlik penceresi 7 gün", plan_service.REGRESYON_SESSIZLIK_GUN == 7,
      str(plan_service.REGRESYON_SESSIZLIK_GUN))


# =============================================================================
# 4) CANLI UÇ — POST /plans/regresyon-cevap
# =============================================================================
print("4) POST /plans/regresyon-cevap")
H = _tok("regresyon1@example.com")
BID = bebek_kur(H)
egitim_basla(H, BID, gun_once=9)                      # eğitimin 10. günü

r = cevapla(H, BID, False)
check("4) 'hayır' → 200", r.status_code == 200, r.text)
_g = r.json() if r.status_code == 200 else {}
check("4a) Kart devam_45, eğitim günü 10",
      (_g.get("regresyon_karti") or {}).get("tip") == "devam_45"
      and _g.get("egitim_baslangic_gunu") == 10
      and _g.get("kirkbes_gun_doldu") is False, str(_g))
_b = bebek_satiri(BID)
check("4b) Cevap BEBEĞE yazıldı (kart her açılışta yeniden sorulmasın)",
      _b.regresyon_kendi_donuyor is False and _b.regresyon_cevap_at is not None,
      f"cevap={_b.regresyon_kendi_donuyor} at={_b.regresyon_cevap_at}")

r2 = cevapla(H, BID, True)
check("4c) 'evet' → 200 ve gösterilecek kart YOK",
      r2.status_code == 200 and r2.json()["regresyon_karti"] is None
      and r2.json()["kendi_donuyor"] is True, r2.text)
check("4d) 'evet' cevabı bebeğe yazıldı (kart 7 gün kapalı)",
      bebek_satiri(BID).regresyon_kendi_donuyor is True, "")

egitim_basla(H, BID, gun_once=49)                     # eğitimin 50. günü
r3 = cevapla(H, BID, False)
check("4e) 45 gün dolmuşken 'hayır' → tıbbi yönlendirme",
      r3.status_code == 200
      and r3.json()["regresyon_karti"]["tip"] == "tibbi_yonlendirme"
      and r3.json()["kirkbes_gun_doldu"] is True, r3.text)
check("4f) Tıbbi kart metni yanıtın içinde geliyor",
      "Pediatri kontrolü" in r3.json()["regresyon_karti"]["metin"],
      r3.json()["regresyon_karti"]["metin"])

# --- Sahiplik ve doğrulama ---------------------------------------------------
H2 = _tok("regresyon2@example.com")
check("4g) Başkasının bebeği → 404", cevapla(H2, BID, True).status_code == 404,
      str(cevapla(H2, BID, True).status_code))
check("4h) Gövde eksikse 422",
      client.post(f"/api/v1/plans/regresyon-cevap?baby_id={BID}",
                  headers=H, json={}).status_code == 422, "")
check("4i) Kimliksiz istek 401",
      client.post(f"/api/v1/plans/regresyon-cevap?baby_id={BID}",
                  json={"kendi_donuyor": True}).status_code == 401, "")

# --- Uç PLAN ÜRETMEZ / çizelgeyi değiştirmez ---------------------------------
_plan_once = client.get("/api/v1/plans", headers=H).json()
cevapla(H, BID, False)
check("4j) Uç plan ÜRETMEZ (yalnız cevabı saklar)",
      len(client.get("/api/v1/plans", headers=H).json()) == len(_plan_once),
      f"{len(_plan_once)} → {len(client.get('/api/v1/plans', headers=H).json())}")


# =============================================================================
# 5) ADAPT KATMANI — kart cevaba göre değişiyor
# =============================================================================
print("5) adapt() regresyon kartı")


def _fail_loglar(gece: int) -> list:
    """Son `gece` gecede 'kendine dalamama' sinyali (night_wake)."""
    class L:
        def __init__(s_, bas, bit):
            s_.id = _uuid.uuid4(); s_.type = "night_wake"
            s_.started_at = bas; s_.ended_at = bit
    out = []
    for g in range(gece):
        gun = TODAY - timedelta(days=g)
        bas = datetime(gun.year, gun.month, gun.day, 23, 0, tzinfo=timezone.utc)
        out.append(L(bas, bas + timedelta(minutes=30)))
    return out


# 8 aylık bant (test_plan_adapter ile aynı gerçek KB değerleri) — çizelgeyi
# motorun kendisi kursun, yoksa bant ihlali sanılıp erken dönülür.
_BUCKET = {
    "uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "2.5 - 3.5 Saat"},
    "uyku_sayisi": {"RESMI_DEGER": "2-3"},
    "gunduz_uyku_total": "2.5-3.5 Saat",
    "yatma_vakti": "18:00 - 20:00",
}
_PLAN = {"schedule": pa.build_schedule(_BUCKET, 7 * 60),
         "baseline_night_wakes": 2}
_DONE = TODAY - timedelta(days=13)          # eğitim 13 gün önce bitti
_BASLADI = TODAY - timedelta(days=27)       # 28. gün → 45 dolmadı

_a1 = pa.adapt(_PLAN, _BUCKET, _fail_loglar(3), training_completed_at=_DONE,
               training_started_at=_BASLADI, today=TODAY, now_minute=22 * 60)
check("5) Regresyon + cevap yok → 1. kademe kartı",
      _a1["regression_detected"] is True
      and (_a1["regresyon_karti"] or {}).get("tip") == "kendi_donuyor_mu",
      str(_a1["regresyon_karti"]))
check("5a) Eğitim günü ve 45 gün bayrağı sonuçta VAR",
      _a1["egitim_baslangic_gunu"] == 28 and _a1["kirkbes_gun_doldu"] is False,
      f'gun={_a1["egitim_baslangic_gunu"]} doldu={_a1["kirkbes_gun_doldu"]}')

_a2 = pa.adapt(_PLAN, _BUCKET, _fail_loglar(3), training_completed_at=_DONE,
               training_started_at=_BASLADI, regresyon_kendi_donuyor=True,
               today=TODAY, now_minute=22 * 60)
check("5b) Anne 'evet' dediyse regresyon görülse bile kart YOK",
      _a2["regression_detected"] is True and _a2["regresyon_karti"] is None,
      str(_a2["regresyon_karti"]))

_a3 = pa.adapt(_PLAN, _BUCKET, _fail_loglar(3), training_completed_at=_DONE,
               training_started_at=_BASLADI, regresyon_kendi_donuyor=False,
               today=TODAY, now_minute=22 * 60)
check("5c) 'hayır' → devam_45 kartı",
      (_a3["regresyon_karti"] or {}).get("tip") == "devam_45",
      str(_a3["regresyon_karti"]))

_a4 = pa.adapt(_PLAN, _BUCKET, _fail_loglar(3), training_completed_at=_DONE,
               training_started_at=TODAY - timedelta(days=60),
               regresyon_kendi_donuyor=False, today=TODAY, now_minute=22 * 60)
check("5d) 45 gün dolmuşsa 'hayır' → tıbbi yönlendirme",
      (_a4["regresyon_karti"] or {}).get("tip") == "tibbi_yonlendirme"
      and _a4["kirkbes_gun_doldu"] is True, str(_a4["regresyon_karti"]))

_a5 = pa.adapt(_PLAN, _BUCKET, [], training_completed_at=_DONE,
               training_started_at=_BASLADI, today=TODAY, now_minute=22 * 60)
check("5e) Regresyon YOKSA kart da YOK",
      _a5["regression_detected"] is False and _a5["regresyon_karti"] is None,
      str(_a5["regresyon_karti"]))
check("5f) adapt() sonucunda eski bayrak YOK",
      "restart_program_suggested" not in _a1, str(sorted(_a1.keys())))

# Aşama gün hesabının izine de düşer (mobil tek yerden okuyabilsin).
check("5g) adaptation.regresyon aşamayı taşıyor",
      (_a1["adaptation"]["regresyon"] or {}).get("asama") == "soru"
      and (_a3["adaptation"]["regresyon"] or {}).get("asama") == "devam"
      and (_a4["adaptation"]["regresyon"] or {}).get("asama") == "tibbi",
      f'{_a1["adaptation"]["regresyon"]} | {_a3["adaptation"]["regresyon"]} '
      f'| {_a4["adaptation"]["regresyon"]}')
check("5h) Regresyon yoksa adaptation.regresyon None",
      _a5["adaptation"]["regresyon"] is None
      and _a2["adaptation"]["regresyon"] is None,
      f'{_a5["adaptation"]["regresyon"]} | {_a2["adaptation"]["regresyon"]}')


# =============================================================================
# 6) Bu suite canlı Sonnet ÇAĞIRMADI
# =============================================================================
_ok, _detay = muhur_saglam_mi()
check("6) LLM mührü sağlam (canlı çağrı YOK)", _ok, _detay)


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 78)
print("REGRESYON AKIŞI TEST SONUÇLARI (v1.4 — üç kademe, İlayda S9)")
print("=" * 78)
_gecen = 0
for ad, ok, detay in results:
    if ok:
        _gecen += 1
    else:
        print(f"[FAIL] {ad}\n       {detay}")
print("-" * 78)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
sys.exit(0 if _gecen == len(results) else 1)
