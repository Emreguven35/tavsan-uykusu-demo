"""
BETA DENETİMİ — plan geri bildirimi, D-3, iş durumu paylaşımı, tipsiz plan,
İlayda günlük denetim sayfası. LLM YOK (mühürlü), ağ YOK.

Kapsam:
  F   POST /feedback/plan: 201 + anlık görüntü; aynı client_id → 200, kopya
      yok; created_at istemcinin zamanı; başkasının bebeği 404; bozuk gövde 422
  D   D-3: eksik profil → Türkçe 422 + eksik_alanlar; overrides tamamlar;
      0-3 ay muaf; tam profil → üretim
  J   Plan işi başka worker'dan (bellekte yok) DB'den okunur; sahiplik; bayat iş
  T   Tipsiz plan: tip_turet + ensure_current_schema kalıcı yazar; adaptasyon
      kopyası da tipli
  R   Denetim: yalnız gerçek bebek, kişisel veri yok, geri bildirim görünür,
      imzalı bağlantı 200 / bozuk imza 403 / süresi dolmuş 403

Çalıştırma: python tests/test_beta_denetim.py
"""
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "beta_denetim_test.db"
if _DB.exists():
    _DB.unlink()
_DENETIM = Path(tempfile.gettempdir()) / "beta_denetim_cikti"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["DENETIM_ROOT"] = str(_DENETIM)
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "beta_denetim_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import (Baby, PlanFeedback, PlanUretimIsi,  # noqa: E402
                        SleepPlan, User)
from api.main import app                                    # noqa: E402
from api.services import denetim, plan_jobs, plan_service   # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


SIMDI = datetime.now(timezone.utc).replace(microsecond=0)
BUGUN = SIMDI.date()


def hesap(email: str, **bebek):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}
                      ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    govde = {"name": "Zeynep", "night_wakes": 2,
             "birth_date": (BUGUN - timedelta(days=270)).isoformat(),
             **TAM_PROFIL, **bebek}
    bid = client.post("/api/v1/babies", headers=h, json=govde).json()["id"]
    return h, bid


def yerel(gun: date, dk: int) -> str:
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - 180)).isoformat()


# =============================================================================
# D — D-3: eksik profil
# =============================================================================
tok = client.post("/api/v1/auth/register",
                  json={"email": "d3@gercek.com", "password": "TestPass123!"}
                  ).json()["access_token"]
HD = {"Authorization": f"Bearer {tok}"}
_bos = client.post("/api/v1/babies", headers=HD, json={
    "name": "Boş", "birth_date": (BUGUN - timedelta(days=250)).isoformat()}).json()["id"]
r = client.post("/api/v1/plans/generate", headers=HD, json={"baby_id": _bos})
j = r.json()
check("D1) Boş profil → 422 (202 DEĞİL)", r.status_code == 422,
      f"{r.status_code} {r.text[:150]}")
check("D2) eksik_alanlar beş zorunlu alanı listeliyor",
      set(j.get("eksik_alanlar", [])) == {a for a, _k, _e in plan_service.ZORUNLU_PROFIL},
      str(j.get("eksik_alanlar")))
check("D3) detail Türkçe ve alan adlarını söylüyor",
      "profilde eksik" in j.get("detail", "") and "beslenme şekli" in j.get("detail", ""),
      j.get("detail"))
check("D4) Etiketler de ayrı listede", "uyku ortamı" in j.get("eksik_alan_etiketleri", []),
      str(j.get("eksik_alan_etiketleri")))
r = client.post("/api/v1/plans/generate?sync=true", headers=HD, json={"baby_id": _bos})
check("D5) Senkron yol da 422", r.status_code == 422, str(r.status_code))
r = client.post("/api/v1/plans/generate?sync=true", headers=HD, json={
    "baby_id": _bos, "profile_overrides": {
        "beslenme": "anne sütü", "destek": "kucak", "oda": "kendi odası",
        "dayanma_siniri": "10 dk", "deneyim": "ilk"}})
check("D6) profile_overrides eksikleri tamamlıyor → 201", r.status_code == 201,
      f"{r.status_code} {r.text[:120]}")
_yeni = client.post("/api/v1/babies", headers=HD, json={
    "name": "Minik", "birth_date": (BUGUN - timedelta(days=30)).isoformat()}).json()["id"]
r = client.post("/api/v1/plans/generate?sync=true", headers=HD, json={"baby_id": _yeni})
check("D7) 0-3 ay muaf (rehber bu alanları kullanmıyor) → 201",
      r.status_code == 201, f"{r.status_code} {r.text[:120]}")
_kismi = client.post("/api/v1/babies", headers=HD, json={
    "name": "Yarım", "birth_date": (BUGUN - timedelta(days=250)).isoformat(),
    **{k: v for k, v in TAM_PROFIL.items() if k != "crying_tolerance"}}).json()["id"]
r = client.post("/api/v1/plans/generate", headers=HD, json={"baby_id": _kismi})
check("D8) Tek eksik alan → yalnız o listelenir",
      r.status_code == 422 and r.json().get("eksik_alanlar") == ["crying_tolerance"],
      r.text[:150])

# =============================================================================
# F — Plan geri bildirimi
# =============================================================================
H, BID = hesap("anne@gercek.com")
client.patch(f"/api/v1/babies/{BID}", headers=H,
             json={"training_started_at": (BUGUN - timedelta(days=4)).isoformat()})
_gen = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": BID})
check("F0) Tam profille plan üretildi", _gen.status_code == 201, _gen.text[:120])
client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": BID, "type": "sleep", "client_id": "gece-f",
    "started_at": yerel(BUGUN - timedelta(days=1), 20 * 60),
    "ended_at": yerel(BUGUN, 7 * 60 + 10)}]})

_an = SIMDI - timedelta(hours=2)
govde = {"baby_id": BID, "block_key": "nap_1", "block_time": "10:30",
         "secenek": "cok_erken", "metin": "Zeynep bu saatte hiç uyumuyor",
         "client_id": "fb-1", "created_at": _an.isoformat()}
r = client.post("/api/v1/feedback/plan", headers=H, json=govde)
check("F1) İlk geri bildirim 201", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
_fid = r.json().get("id")
r2 = client.post("/api/v1/feedback/plan", headers=H, json=govde)
check("F2) Aynı client_id → 200 + aynı id + duplicate", r2.status_code == 200
      and r2.json().get("id") == _fid and r2.json().get("duplicate") is True,
      f"{r2.status_code} {r2.text[:150]}")
_db = SessionLocal()
try:
    _satirlar = _db.query(PlanFeedback).all()
    check("F3) DB'de tek satır (kopya yazılmadı)", len(_satirlar) == 1, len(_satirlar))
    _s = _satirlar[0]
    _ca = _s.created_at if _s.created_at.tzinfo else _s.created_at.replace(tzinfo=timezone.utc)
    check("F4) created_at istemcinin zamanı", _ca == _an, f"{_ca} vs {_an}")
    _a = _s.anlik or {}
    check("F5) Görüntü: schedule + adaptation + plan_id",
          bool(_a.get("schedule")) and "adaptation" in _a and _a.get("plan_id"),
          str(list(_a)))
    check("F6) Görüntü: günün kayıtları", any(k["type"] == "sleep" for k in _a.get("kayitlar", [])),
          str(_a.get("kayitlar"))[:150])
    check("F7) Görüntü: yaş + bant + eğitim günü + aşama",
          (_a.get("yas") or {}).get("duzeltilmis_ay") and (_a.get("bant") or {}).get("id")
          and _a.get("egitim_gunu") == 5 and _a.get("asama") == "oda_ortasi",
          f'{_a.get("yas")} {_a.get("bant")} {_a.get("egitim_gunu")} {_a.get("asama")}')
finally:
    _db.close()

H2, BID2 = hesap("yabanci@gercek.com")
r = client.post("/api/v1/feedback/plan", headers=H2, json={**govde, "client_id": "fb-x"})
check("F8) Başkasının bebeği → 404", r.status_code == 404, str(r.status_code))
r = client.post("/api/v1/feedback/plan", headers=H, json={**govde, "client_id": ""})
check("F9) Boş client_id → 422", r.status_code == 422, str(r.status_code))
r = client.post("/api/v1/feedback/plan", headers=H2,
                json={**govde, "baby_id": BID2, "client_id": "fb-1"})
check("F10) client_id kullanıcı bazlı: başka annenin aynı client_id'si yeni kayıt",
      r.status_code == 201, str(r.status_code))

# =============================================================================
# J — plan işi başka worker'dan görünür
# =============================================================================
_u = "11111111-1111-1111-1111-111111111111"
_j = plan_jobs.create_job(_u, BID)
plan_jobs._set(_j, started=True)
with plan_jobs._LOCK:
    plan_jobs._JOBS.pop(_j)                           # "başka worker": bellekte yok
_g = plan_jobs.get_job(_j, _u)
check("J1) Bellekte olmayan iş DB'den okunuyor (404 değil)",
      _g is not None and _g["status"] == plan_jobs.STATUS_PROCESSING
      and _g["queue_position"] == 0, str(_g))
check("J2) Başka kullanıcı yine göremez", plan_jobs.get_job(_j, "baskasi") is None, "")
plan_jobs._set(_j, status=plan_jobs.STATUS_DONE, plan_id="p-1")
check("J3) Durum değişikliği DB'ye yazılıyor",
      (plan_jobs.get_job(_j, _u) or {}).get("status") == plan_jobs.STATUS_DONE, "")
_eski = plan_jobs.create_job(_u, BID)
with plan_jobs._LOCK:
    plan_jobs._JOBS.pop(_eski)
_db = SessionLocal()
try:
    _db.get(PlanUretimIsi, _eski).created_at = SIMDI - timedelta(hours=1)
    _db.commit()
finally:
    _db.close()
_g = plan_jobs.get_job(_eski, _u)
check("J4) Süreç ölmüş bayat iş failed + Türkçe mesaj",
      _g and _g["status"] == plan_jobs.STATUS_FAILED and "yeniden" in (_g["error"] or ""),
      str(_g))

# =============================================================================
# T — tipsiz eski plan
# =============================================================================
check("T1) tip_turet: uygun → egitim_plani, uygun_mu=False → bekleme",
      plan_service.tip_turet({"uygun_mu": True}) == "egitim_plani"
      and plan_service.tip_turet({"uygun_mu": False}) == "egitim_bekleme"
      and plan_service.tip_turet({"type": "yenidogan_ritim"}) == "yenidogan_ritim", "")
_db = SessionLocal()
try:
    _p = _db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BID)).first()
    _c = dict(_p.content)
    _c.pop("type", None)
    _p.content = _c
    _db.commit()
    _pid = _p.id
finally:
    _db.close()
r = client.get(f"/api/v1/plans/today?baby_id={BID}", headers=H)
check("T2) Tipsiz plan okunurken type döner", r.status_code == 200
      and r.json()["content"].get("type") == "egitim_plani",
      f'{r.status_code} {r.json().get("content", {}).get("type") if r.status_code == 200 else r.text[:100]}')
_db = SessionLocal()
try:
    _hepsi = _db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BID)).all()
    check("T3) DB'de de tipsiz plan kalmadı",
          all((p.content or {}).get("type") for p in _hepsi),
          [(p.plan_date, (p.content or {}).get("type")) for p in _hepsi])
finally:
    _db.close()

# =============================================================================
# R — Günlük denetim
# =============================================================================
DUN = denetim.dun()
_HT, _BT = hesap("test-1@example.com", name="TestBebek")
for h, b, cid in ((H, BID, "dun-1"), (_HT, _BT, "dun-t")):
    client.post("/api/v1/logs/batch", headers=h, json={"logs": [
        {"baby_id": b, "type": "nap", "client_id": cid,
         "started_at": yerel(DUN, 10 * 60), "ended_at": yerel(DUN, 11 * 60)},
        {"baby_id": b, "type": "night_wake", "client_id": cid + "w",
         "started_at": yerel(DUN, 2 * 60)}]})
client.post("/api/v1/feedback/plan", headers=H, json={
    **govde, "client_id": "fb-dun", "metin": "zeynep erken uyandı, anne@gercek.com",
    "created_at": yerel(DUN, 15 * 60)})

# Rapor DÜNÜN planını okur; bu suitede plan bugün üretildi → düne taşı.
_db = SessionLocal()
try:
    for _p in _db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BID)).all():
        _p.plan_date = DUN
    _db.commit()
finally:
    _db.close()
_db = SessionLocal()
try:
    _yol, _link, _n = denetim.rapor_yaz(_db, DUN)
finally:
    _db.close()
_html = _yol.read_text("utf-8")
check("R1) Dosya DENETIM_ROOT/{tarih}.html", _yol == _DENETIM / f"{DUN.isoformat()}.html",
      str(_yol))
check("R2) Yalnız gerçek bebek (test hesabı dahil değil)", _n == 1, _n)
import re as _re                                          # noqa: E402
check("R3) 'Bebek N, X aylık' başlığı (ad yok, numara)",
      bool(_re.search(r"<h2>Bebek \d+, \d+ aylık</h2>", _html)),
      _html[_html.find("<h2>"):_html.find("<h2>") + 60])
check("R4) Kişisel veri yok: bebek adı / e-posta / test bebeği geçmiyor",
      "zeynep" not in _html.lower() and "@gercek.com" not in _html
      and "TestBebek" not in _html and "example.com" not in _html, "")
check("R5) Geri bildirim temizlenmiş metinle görünüyor",
      "[bebek] erken uyandı" in _html and "[e-posta]" in _html, "")
check("R6) Plan + gerçek şeritleri ve karşılaştırma tablosu var",
      "Sabah planı" in _html and "Gerçek" in _html and "<table>" in _html, "")
check("R7) Bağlantı 7 gün geçerli imza taşıyor",
      "exp=" in _link and "sig=" in _link
      and 6.9 * 86400 < int(_link.split("exp=")[1].split("&")[0]) - SIMDI.timestamp() <= 7 * 86400 + 60,
      _link)
_yol_q = _link[_link.index("/denetim/"):]
r = client.get(_yol_q)
check("R8) İmzalı bağlantı 200 + text/html + noindex", r.status_code == 200
      and r.headers["content-type"].startswith("text/html")
      and "noindex" in r.headers.get("x-robots-tag", ""), f"{r.status_code}")
r = client.get(_yol_q.replace("sig=", "sig=0"))
check("R9) Bozuk imza → 403", r.status_code == 403, str(r.status_code))
_exp = int(_yol_q.split("exp=")[1].split("&")[0])
r = client.get(_yol_q.replace(f"exp={_exp}", f"exp={_exp + 1}"))
check("R10) Süre değiştirilmiş bağlantı → 403", r.status_code == 403, str(r.status_code))
r = client.get("/denetim/../etc.html?exp=1&sig=x")
check("R11) Geçersiz tarih yolu reddediliyor", r.status_code in (400, 404), str(r.status_code))

# =============================================================================
print("=" * 78)
print("BETA DENETİMİ TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
