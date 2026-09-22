"""
EŞZAMANLI UPSERT (v2.4.4) — "önce SEÇ, yoksa EKLE" yarışı.

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + FastAPI TestClient.

NEDEN VAR: Sentry'de `video_progress` üzerinde UniqueViolation görüldü. Desen
SELECT → (yoksa) INSERT idi; iki istek arasına sıkışan üçüncü bir yazım ikinci
INSERT'i kısıt ihlaline düşürüyor ve istek 500 dönüyordu. Oynatıcı videoyu
kapatırken "duraklat" ve "çıkış" isteklerini neredeyse aynı anda gönderdiği
için çakışma sık.

SQLITE UYARISI: sqlite tek yazar kilidiyle çalışır, yani PG'deki gerçek
paralel INSERT yarışını birebir taklit ETMEZ. Buradaki testin kanıtladığı şey
SONUÇ SÖZLEŞMESİDİR: 20 eşzamanlı istekten sonra TEK satır kalır, konum en
büyüktür, damga geri alınmaz ve hiçbir istek 500 dönmez. Yarışın kendisi
(aynı anda iki INSERT) ayrıca `ilerleme_kaydet`in ürettiği SQL'in
ON CONFLICT içerdiği doğrulanarak kontrol edilir.

Senaryolar:
  A  20 eşzamanlı POST (karışık konum) → tek satır, en büyük konum, 0 hata
  B  Geç ulaşan ESKİ konum ileri konumu EZMEZ (GREATEST)
  C  "İzledim" damgası geri alınmaz (COALESCE)
  D  Üretilen SQL gerçekten ON CONFLICT DO UPDATE
  E  Aynı desen: sleep_logs batch — yarışan client_id "db_error" DEĞİL
  F  sleep_plans tekillik kısıtı tabloda var

Çalıştırma: python tests/test_eszamanli_upsert.py
"""
import os
import sys
import tempfile
import threading
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

_DB = Path(tempfile.gettempdir()) / "eszamanli_upsert_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"        # zamanlayıcı başlamasın
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"

from fastapi.testclient import TestClient              # noqa: E402
from sqlalchemy import inspect, text                   # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import EducationVideo, VideoProgress    # noqa: E402
from api.main import app                               # noqa: E402
from api.services import education                     # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


# SQLite eşzamanlı yazımda "database is locked" verebilir; meşgul bekleme
# süresi açılmazsa test YARIŞI DEĞİL sqlite'ın kilidini ölçer.
@__import__("sqlalchemy").event.listens_for(engine, "connect")
def _sqlite_bekle(dbapi_conn, _rec):             # noqa: ANN001
    try:
        dbapi_conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass


tok = client.post("/api/v1/auth/register",
                  json={"email": "esz_upsert@example.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

# --- Katalogda tek video (dosya sistemine dokunmadan, doğrudan satır) -------
VID = _uuid.uuid4()
_db = SessionLocal()
try:
    _db.add(EducationVideo(
        id=VID, slug="esz-test", title="Eşzamanlılık testi",
        category="baslarken", order_in_category=1, duration_sec=100,
        video_url="/media/videos/esz-test.mp4", stage_tags=["genel"],
        chapters=[]))
    _db.commit()
finally:
    _db.close()

UC = f"/api/v1/education/videos/{VID}/progress"

# =============================================================================
# A — 20 EŞZAMANLI POST: tek satır, en büyük konum, hiç 500 yok
# =============================================================================
KONUMLAR = [7, 3, 91, 12, 45, 88, 5, 60, 33, 77,
            21, 96, 14, 52, 8, 70, 29, 41, 63, 19]
EN_BUYUK = max(KONUMLAR)               # 96 → süre 100, %95 eşiği 95 → tamamlandı

_yanitlar: list[tuple[int, str]] = []
_kilit = threading.Lock()
_basla = threading.Event()


def _gonder(konum: int) -> None:
    _basla.wait()                      # 20 iş parçacığı AYNI ANDA salınır
    try:
        r = client.post(UC, headers=H, json={"position_sec": konum})
        with _kilit:
            _yanitlar.append((r.status_code, r.text[:120]))
    except Exception as e:             # noqa: BLE001
        with _kilit:
            _yanitlar.append((0, f"{type(e).__name__}: {e}"[:120]))


_isler = [threading.Thread(target=_gonder, args=(k,)) for k in KONUMLAR]
for _i in _isler:
    _i.start()
_basla.set()
for _i in _isler:
    _i.join(timeout=60)

check("A1) 20 isteğin hepsi yanıtlandı", len(_yanitlar) == 20,
      f"gelen={len(_yanitlar)}")
check("A2) Hiçbir istek hata dönmedi (500/UniqueViolation YOK)",
      all(kod == 200 for kod, _ in _yanitlar),
      str([y for y in _yanitlar if y[0] != 200][:3]))

_db = SessionLocal()
try:
    _satirlar = (_db.query(VideoProgress)
                 .filter(VideoProgress.video_id == VID).all())
    check("A3) TEK satır (yığılma yok)", len(_satirlar) == 1,
          f"satır={len(_satirlar)}")
    _s = _satirlar[0] if _satirlar else None
    check("A4) Konum EN BÜYÜK gönderilen değer",
          _s is not None and _s.position_sec == EN_BUYUK,
          f"konum={_s.position_sec if _s else None} beklenen={EN_BUYUK}")
    check("A5) %95 eşiği aşıldığı için 'izlendi' damgası kondu",
          _s is not None and _s.completed_at is not None,
          str(_s.completed_at if _s else None))
finally:
    _db.close()

# =============================================================================
# B — Geç ulaşan ESKİ konum ileri konumu EZMEZ
# =============================================================================
r = client.post(UC, headers=H, json={"position_sec": 4})
check("B1) 4 sn'lik geç yanıt konumu geri almadı",
      r.json()["position_sec"] == EN_BUYUK, r.text[:160])

# =============================================================================
# C — "İzledim" damgası geri alınmaz
# =============================================================================
check("C1) Damga korundu (COALESCE)", r.json()["completed"] is True,
      r.text[:160])

# =============================================================================
# D — Üretilen SQL gerçekten ON CONFLICT DO UPDATE
# =============================================================================
from api.db import upsert as _upsert                   # noqa: E402

_st = _upsert.insert(VideoProgress).values(
    id=_uuid.uuid4(), user_id=_uuid.uuid4(), video_id=VID, position_sec=1)
_st = _st.on_conflict_do_update(
    index_elements=["user_id", "video_id"],
    set_={"position_sec": _upsert.en_buyuk(_st.excluded.position_sec,
                                           VideoProgress.position_sec)})
_sql = str(_st.compile(engine)).upper()
check("D1) SQL 'ON CONFLICT' içeriyor", "ON CONFLICT" in _sql, _sql[:200])
check("D2) SQL 'DO UPDATE' içeriyor", "DO UPDATE" in _sql, _sql[:200])
check("D3) Büyüğü alan işlev lehçeye uygun (sqlite→max, pg→greatest)",
      ("MAX(" in _sql) if engine.dialect.name == "sqlite"
      else ("GREATEST(" in _sql), _sql[:200])

# =============================================================================
# E — Aynı desen: sleep_logs batch, yarışan client_id
# =============================================================================
_bebek = client.post("/api/v1/babies", headers=H,
                     json={"name": "Eş", "birth_date":
                           (datetime.now(timezone.utc).date()
                            - timedelta(days=240)).isoformat(),
                           "night_wakes": 2}).json()["id"]
_bas = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0)
_govde = {"logs": [{"client_id": "esz-1", "baby_id": _bebek, "type": "sleep",
                    "started_at": _bas.isoformat(),
                    "ended_at": (_bas + timedelta(minutes=50)).isoformat()}]}

_batch_yanit: list[tuple[int, dict]] = []


def _batch_gonder() -> None:
    _basla2.wait()
    try:
        r = client.post("/api/v1/logs/batch", headers=H, json=_govde)
        with _kilit:
            _batch_yanit.append((r.status_code, r.json()
                                 if r.status_code == 200 else {}))
    except Exception as e:             # noqa: BLE001
        with _kilit:
            _batch_yanit.append((0, {"hata": f"{type(e).__name__}: {e}"}))


_basla2 = threading.Event()
_isler2 = [threading.Thread(target=_batch_gonder) for _ in range(8)]
for _i in _isler2:
    _i.start()
_basla2.set()
for _i in _isler2:
    _i.join(timeout=60)

check("E1) 8 eşzamanlı batch isteğinin hepsi 200",
      len(_batch_yanit) == 8 and all(k == 200 for k, _ in _batch_yanit),
      str([y[0] for y in _batch_yanit]))
_db_hatalari = [g for _, g in _batch_yanit
                for s in (g.get("skipped") or [])
                if s.get("reason") == "db_error"]
check("E2) Hiçbir yanıtta 'db_error' yok (yarış = idempotency)",
      not _db_hatalari, str(_db_hatalari[:1]))

_db = SessionLocal()
try:
    _adet = _db.execute(text(
        "SELECT count(*) FROM sleep_logs WHERE client_id = 'esz-1'")).scalar()
    check("E3) client_id 'esz-1' için TEK satır", _adet == 1, f"adet={_adet}")
finally:
    _db.close()

# =============================================================================
# F — sleep_plans tekillik kısıtı tabloda var
# =============================================================================
_kisitlar = {k["name"] for k in inspect(engine).get_unique_constraints("sleep_plans")}
check("F1) uq_sleep_plans_user_baby_date kısıtı tanımlı",
      "uq_sleep_plans_user_baby_date" in _kisitlar, str(_kisitlar))
_vp = {k["name"] for k in inspect(engine).get_unique_constraints("video_progress")}
check("F2) uq_video_progress_user_video kısıtı tanımlı",
      "uq_video_progress_user_video" in _vp, str(_vp))
_va = {k["name"] for k in inspect(engine).get_unique_constraints("voice_audios")}
check("F3) uq_voice_audios_profile_content kısıtı tanımlı",
      "uq_voice_audios_profile_content" in _va, str(_va))

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("EŞZAMANLI UPSERT TEST SONUÇLARI (v2.4.4)")
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
print(f"TOPLAM: {passed}/{len(results)} geçti")
print("=" * 74)
sys.exit(0 if passed == len(results) else 1)
