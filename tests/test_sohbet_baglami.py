"""
Sor (POST /chat) — bebek bağlamı hiçbir veri biçiminde cevabı engellemesin.

2026-09-25 OLAYI: 3 aylık (bekleme planı) bebeğin annesi akşam her soruda
"şu an yanıt veremedi" aldı. Sebep yaş/plan tipi DEĞİL: aynı dakikada başlayan
biri AÇIK biri KAPALI iki `nap` kaydı (sayaç + elle giriş) baby_context'te
`sorted([(900, 900), (900, None)])` → TypeError → 500. Kayıt 3 günlük bağlam
penceresinde kaldıkça HER soru düşüyordu.

  M  Plan tipi (eğitim 11 ay / bekleme 3 ay / yenidoğan 2 hafta / eksik profil /
     doğum tarihsiz) × kayıt biçimi (yok / normal / açık+kapalı aynı dakika nap /
     süresiz+süreli aynı dakika gece uyanması) → hepsi 200, bağlam kurulur
  G  Bağlam kurucusu PATLASA bile soru bağlamsız cevaplanır (200) ve loglanır
  K  Premium/kota reddi 403 + premium_required + Türkçe detail (5xx değil)
  A  Başkasının bebeği yine 404 (koruma gevşemedi)

Çalıştırma: python tests/test_sohbet_baglami.py
"""
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "sohbet_baglami_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["BETA_MODE"] = "true"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.services import baby_context                       # noqa: E402
from api.zaman import TR, bugun_tr                          # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


_GELEN_CTX: list = []


def _sahte_cevap(soru, yas_bandi=None, baby_context=None, **k):
    _GELEN_CTX.append(baby_context)
    return {"cevap": "Cevap", "cache_hit": False, "kaynaklar": [], "llm": False,
            "retrieval_layer": "k1", "top_score": 0.9}


chatbot._cevap_uret = _sahte_cevap
BUGUN = bugun_tr()


def tr(gun_farki, s, dk=0):
    g = BUGUN + timedelta(days=gun_farki)
    return datetime(g.year, g.month, g.day, s, dk, tzinfo=TR).isoformat()


BEBEKLER = {
    "egitim_11ay": {"birth_date": (BUGUN - timedelta(days=335)).isoformat(), **TAM_PROFIL},
    "bekleme_3ay": {"birth_date": (BUGUN - timedelta(days=97)).isoformat(), **TAM_PROFIL},
    "yenidogan_2hafta": {"birth_date": (BUGUN - timedelta(days=14)).isoformat()},
    "eksik_profil": {"birth_date": (BUGUN - timedelta(days=200)).isoformat()},
    "dogum_tarihsiz": {},
}
KAYITLAR = {
    "kayitsiz": [],
    "normal": [("nap", tr(0, 9, 15), tr(0, 10, 45)), ("night_wake", tr(0, 2, 0), tr(0, 2, 20))],
    # Olaydaki çift: 15:00 başlayan 0 dk kapalı + aynı dakika açık
    "acik_kapali_nap": [("nap", tr(0, 15, 0), tr(0, 15, 0)), ("nap", tr(0, 15, 0), None)],
    "suresiz_sureli_uyanma": [("night_wake", tr(0, 3, 0), None),
                              ("night_wake", tr(0, 3, 0), tr(0, 3, 15))],
}

sayac = 0
for tip, govde in BEBEKLER.items():
    for kayit_ad, kayitlar in KAYITLAR.items():
        sayac += 1
        tok = client.post("/api/v1/auth/register", json={
            "email": f"sor{sayac}@gercek.com", "password": "TestPass123!"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        bid = client.post("/api/v1/babies", headers=h,
                          json={"name": "Musa", "night_wakes": 1, **govde}).json()["id"]
        if tip != "dogum_tarihsiz" and tip != "eksik_profil":
            client.post("/api/v1/plans/generate?sync=true", headers=h, json={"baby_id": bid})
        if kayitlar:
            client.post("/api/v1/logs/batch", headers=h, json={"logs": [
                {"baby_id": bid, "type": t, "started_at": b, "ended_at": e,
                 "client_id": f"c{sayac}-{i}"} for i, (t, b, e) in enumerate(kayitlar)]})
        _GELEN_CTX.clear()
        r = client.post("/api/v1/chat", headers=h,
                        json={"message": "Bebeğim neden uyumuyor?", "baby_id": bid})
        check(f"M) {tip} × {kayit_ad} → 200 + bağlam kuruldu",
              r.status_code == 200 and _GELEN_CTX and _GELEN_CTX[0]
              and ("Musa" in _GELEN_CTX[0]),
              f"{r.status_code} {r.text[:150]} ctx={str(_GELEN_CTX[:1])[:80]}")

# Olaydaki çiftin bağlamda nasıl özetlendiği (açık kayıt bitişsiz yazılır)
_ctx_olay = next((c for c in _GELEN_CTX if c), "")

# =============================================================================
# G — bağlam kurucusu patlasa bile cevap
# =============================================================================
_gercek = baby_context.build_baby_context


def _patla(*a, **k):
    raise TypeError("'<' not supported between instances of 'int' and 'NoneType'")


baby_context.build_baby_context = _patla
tok = client.post("/api/v1/auth/register", json={
    "email": "patlayan@gercek.com", "password": "TestPass123!"}).json()["access_token"]
hp = {"Authorization": f"Bearer {tok}"}
bp = client.post("/api/v1/babies", headers=hp, json={"name": "B", **BEBEKLER["bekleme_3ay"]}).json()["id"]
_GELEN_CTX.clear()
r = client.post("/api/v1/chat", headers=hp, json={"message": "soru", "baby_id": bp})
check("G1) Bağlam hatası cevabı engellemiyor: 200, bağlamsız",
      r.status_code == 200 and _GELEN_CTX == [None], f"{r.status_code} {_GELEN_CTX}")
baby_context.build_baby_context = _gercek

# =============================================================================
# A — başkasının bebeği
# =============================================================================
r = client.post("/api/v1/chat", headers=hp, json={"message": "soru", "baby_id": str(uuid.uuid4())})
check("A1) Başkasının / olmayan bebek → 404 (yutulmadı)", r.status_code == 404, str(r.status_code))

# =============================================================================
# K — kota reddi (BETA kapalı, alt süreç yerine ayar önbelleği temizlenerek)
# =============================================================================
from api.config import get_settings                         # noqa: E402
os.environ.pop("BETA_MODE", None)
get_settings.cache_clear()
tok = client.post("/api/v1/auth/register", json={
    "email": "kota@gercek.com", "password": "TestPass123!"}).json()["access_token"]
hk = {"Authorization": f"Bearer {tok}"}
yanitlar = [client.post("/api/v1/chat", headers=hk, json={"message": f"s{i}"}) for i in range(4)]
son = yanitlar[-1]
check("K1) 4. soru 403 + premium_required + Türkçe detail (5xx değil)",
      son.status_code == 403 and son.json().get("premium_required") is True
      and "ücretsiz sorunuzu" in son.json().get("detail", ""), son.text[:200])
os.environ["BETA_MODE"] = "true"
get_settings.cache_clear()

# =============================================================================
print("=" * 78)
print("SOR — BEBEK BAĞLAMI TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
