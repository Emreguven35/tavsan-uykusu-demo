"""
Şekerleme uyandırma (KESTIRME_V2 bayrağı, 2026-10-12).

LLM YOK (mühür), ağ YOK (push sahte): geçici sqlite + TestClient + dondurulmuş saat.
Senaryo: 7 aylık bebek (bant 6-8 ay: 3 gündüz uykusu, alt sınır 180 dk), sabah
07:00, gündüz uykuları 30'ar dk → 16:00'dan sonra 18:00-18:30 şekerleme.

Kapsam:
  V0  Bayrak KAPALI: blokta uyandir alanı yok; başlamış şekerleme kaydı eski
      yoldan işlenir (bant tavanı → düşer + "aynı saatlere düşen" uyarısı);
      bildirim turu şekerleme uyandırması göndermez
  V1  Bayrak açık, planlı şekerleme: uyandir=true, uyandir_dk = bitiş
  V2  Geç başlayan şekerleme: açık kayıt şekerleme bloğu olur, uyandir_dk =
      GERÇEK başlangıç + süre; çakışma uyarısı yok; gece yatışı ≥ 60 dk sonra
  V3  Bildirim: uyandır anında BİR kez; sonraki turlarda tekrar yok (tekrar
      gönderim), erken turda yok; kayıt kapanınca yok
  V4  Sessiz saat: uyandır anı 23:00-06:00'a düşerse gönderilmez
  V5  Bildirim tercihi kapalıysa gönderilmez
  V6  Kayıt eksik gün: saati geçmiş planlı uykunun kaydı yoksa şekerleme
      çıkmaz (bayrak kapalıyken çıkıyordu)

Çalıştırma: python tests/test_kestirme_v2.py
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

_DB = Path(tempfile.gettempdir()) / "kestirme_v2_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
os.environ.pop("KESTIRME_V2", None)

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import SleepPlan, User                 # noqa: E402
from api.main import app                               # noqa: E402
from api.services import notifier, plan_adapter        # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402
from tests.llm_muhuru import canli_cagri_sayisi, muhurle  # noqa: E402

muhurle()
Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))
D = datetime(2026, 10, 14, tzinfo=TR)
GIDEN: list[tuple] = []
notifier.push_to_user = lambda db, uid, t, b, data=None: GIDEN.append((uid, t, data)) or 1


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def bayrak(acik: bool) -> None:
    if acik:
        os.environ["KESTIRME_V2"] = "1"
    else:
        os.environ.pop("KESTIRME_V2", None)


def an(gun: int, dk: int) -> datetime:
    return D + timedelta(days=gun, minutes=dk)


class Bebek:
    def __init__(self, ad: str):
        self.email = f"{ad.lower()}@kestirme-ornek.com"
        tok = client.post("/api/v1/auth/register", json={
            "email": self.email, "password": "TestPass123!"}).json()["access_token"]
        self.h = {"Authorization": f"Bearer {tok}"}
        with saat_sabitle(an(-1, 10 * 60)):
            self.bid = client.post("/api/v1/babies", headers=self.h, json={
                "name": ad, "birth_date": (D.date() - timedelta(days=220)).isoformat(),
                "feeding_type": "breast", "sleep_method": "rocking",
                "sleep_environment": "loc:crib;d:1", "crying_tolerance": "5",
                "parent_experience": "none", "night_wakes": 1}).json()["id"]
            r = client.post("/api/v1/plans/generate?sync=true", headers=self.h,
                            json={"baby_id": self.bid})
            assert r.status_code == 201, r.text[:200]
        self.n = 0
        self.log("sleep", an(-1, 19 * 60 + 30), an(0, 7 * 60), saat=an(0, 7 * 60))

    def log(self, tip, bas, bit=None, cid=None, saat=None):
        self.n += 1
        cid = cid or f"{self.bid}-{self.n}"
        with saat_sabitle(saat or bit or bas):
            r = client.post("/api/v1/logs/batch", headers=self.h, json={"logs": [{
                "baby_id": self.bid, "type": tip, "client_id": cid,
                "started_at": bas.astimezone(timezone.utc).isoformat(),
                "ended_at": bit.astimezone(timezone.utc).isoformat() if bit else None}]})
        assert r.status_code == 200, r.text
        return cid

    def gunduz(self, *saatler):
        """Gündüz uykuları (TR saat dakikası), 30'ar dk."""
        for dk in saatler:
            self.log("nap", an(0, dk), an(0, dk + 30))

    def bugun(self, t) -> dict:
        with saat_sabitle(t):
            r = client.get(f"/api/v1/plans/today?baby_id={self.bid}", headers=self.h)
        assert r.status_code == 200, r.text[:200]
        return r.json()["content"]

    def sekerleme(self, t) -> dict | None:
        return next((b for b in self.bugun(t)["schedule"]
                     if b.get("key") == plan_adapter.SEKERLEME_KEY), None)

    def user_id(self):
        s = SessionLocal()
        try:
            return s.query(User).filter(User.email == self.email).one().id
        finally:
            s.close()


def tur(t, uid=None) -> list:
    """Bir bildirim turu; bu turda giden ŞEKERLEME UYANDIRMA bildirimleri
    (`uid` verilirse yalnız o annenin)."""
    once = len(GIDEN)
    s = SessionLocal()
    try:
        with saat_sabitle(t):
            notifier.run_reminder_cycle(s, now=t.astimezone(timezone.utc))
    finally:
        s.close()
    return [g for g in GIDEN[once:]
            if (g[2] or {}).get("kategori") == notifier.KAT_SEKERLEME_UYANDIR
            and (uid is None or g[0] == uid)]


NAPLAR = (9 * 60 + 30, 12 * 60 + 30, 15 * 60 + 30)       # 09:30, 12:30, 15:30

# =============================================================================
# V0 — bayrak kapalı
# =============================================================================
bayrak(False)
b0 = Bebek("Kapali")
b0.gunduz(*NAPLAR)
_s0 = b0.sekerleme(an(0, 16 * 60 + 1))
check("V0a) Kapalı: şekerleme var ama uyandir alanı yok",
      _s0 is not None and "uyandir" not in _s0 and "uyandir_dk" not in _s0, _s0)
b0.log("nap", an(0, 18 * 60 + 10), saat=an(0, 18 * 60 + 10))
_c0 = b0.bugun(an(0, 18 * 60 + 15))
_k0 = [b for b in _c0["schedule"] if b.get("type") == "nap" and b["start_minute"] == 18 * 60 + 10]
check("V0b) Kapalı: başlamış şekerleme kaydı eski yoldan (blok olmaz, bant tavanı)",
      _k0 == [] and plan_adapter.CAKISAN_KAYIT_UYARISI in (_c0.get("uyarilar") or [])
      + ((_c0.get("adaptation") or {}).get("uyarilar") or []),
      [(b.get("key"), b["start_minute"]) for b in _c0["schedule"]])
check("V0c) Kapalı: bildirim turu şekerleme uyandırması göndermez",
      tur(an(0, 18 * 60 + 40)) == [], "")

# =============================================================================
# V1/V2/V3 — bayrak açık, geç başlayan şekerleme
# =============================================================================
bayrak(True)
b1 = Bebek("Acik")
b1.gunduz(*NAPLAR)
_s1 = b1.sekerleme(an(0, 16 * 60 + 1))
check("V1) Planlı şekerleme: uyandir=true, uyandir_dk = bitiş, saat metni",
      _s1 is not None and _s1.get("uyandir") is True
      and _s1.get("uyandir_dk") == _s1["end_minute"]
      and _s1.get("uyandir_saat") == plan_adapter._fmt(_s1["end_minute"]), _s1)
_plan_bas = _s1["start_minute"] if _s1 else 18 * 60
_gec = _plan_bas + 10
_cid = b1.log("nap", an(0, _gec), saat=an(0, _gec))
_c1 = b1.bugun(an(0, _gec + 5))
_s2 = next((b for b in _c1["schedule"] if b.get("key") == plan_adapter.SEKERLEME_KEY), {})
_sure = (_s1 or {}).get("end_minute", 0) - _plan_bas
check("V2a) Geç başlayan: açık kayıt şekerleme bloğu (kaynak kayit, devam)",
      _s2.get("start_minute") == _gec and _s2.get("kaynak") == "kayit"
      and _s2.get("devam") is True, _s2)
check("V2b) uyandir_dk = GERÇEK başlangıç + süre (planlanan bitiş değil)",
      _s2.get("uyandir_dk") == _gec + _sure and _s2.get("uyandir_dk") != (_s1 or {}).get("end_minute"),
      (_s2.get("uyandir_dk"), _gec, _sure))
_uy = _c1.get("uyarilar") or []
check("V2c) Çakışma uyarısı yok, tek şekerleme bloğu",
      plan_adapter.CAKISAN_KAYIT_UYARISI not in _uy
      and sum(1 for b in _c1["schedule"] if b.get("key") == plan_adapter.SEKERLEME_KEY) == 1,
      _uy)
_yat = next(b for b in _c1["schedule"] if b.get("key") == "bedtime")
check("V2d) Gece yatışı şekerleme bitişinden en az 60 dk sonra",
      _yat["start_minute"] >= _s2.get("uyandir_dk", 10 ** 6) + 60,
      (_yat["start_minute"], _s2.get("uyandir_dk")))
_u = _s2.get("uyandir_dk", 0)
_U1 = b1.user_id()
check("V3a) Uyandır anından önceki turda bildirim yok", tur(an(0, _u - 5), _U1) == [], "")
_ilk = tur(an(0, _u), _U1)
check("V3b) Uyandır anında bir bildirim: başlık + saat",
      len(_ilk) == 1 and _ilk[0][1] == notifier.SEKERLEME_UYANDIR_BASLIK
      and (_ilk[0][2] or {}).get("saat") == plan_adapter._fmt(_u), _ilk)
check("V3c) Tekrar gönderim yok (sonraki iki tur)",
      tur(an(0, _u + 5), _U1) == [] and tur(an(0, _u + 9), _U1) == [], "")
b1.log("nap", an(0, _gec), an(0, _u + 3), cid=_cid)
_c3 = b1.bugun(an(0, _u + 4))
_s3 = next((b for b in _c3["schedule"] if b.get("key") == plan_adapter.SEKERLEME_KEY), {})
check("V3d) Kayıt kapanınca blok kayıttan, devam yok; bildirim yok",
      _s3.get("end_minute") == _u + 3 and not _s3.get("devam")
      and tur(an(0, _u + 6), _U1) == [], _s3)

# =============================================================================
# V4 — sessiz saat (birim: uyandır anı 23:10)
# =============================================================================
_gonderilen: list = []
_eski_gonder = notifier._gonder
notifier._gonder = lambda *a, **k: _gonderilen.append(a[4]) or True
_blok = {"key": plan_adapter.SEKERLEME_KEY, "type": "nap", "start_minute": 22 * 60 + 40,
         "end_minute": 23 * 60 + 10, "uyandir": True, "uyandir_dk": 23 * 60 + 10,
         "uyandir_saat": "23:10", "devam": True}
_sahte = type("B", (), {"id": uuid.uuid4(), "name": "Ada"})()
notifier._sekerleme_uyandir(None, None, _sahte, None, {"schedule": [_blok]},
                            23 * 60 + 10, D.date(), {})
check("V4a) Uyandır anı sessiz saatte (23:10) → gönderilmez", _gonderilen == [], _gonderilen)
notifier._sekerleme_uyandir(None, None, _sahte, None,
                            {"schedule": [dict(_blok, uyandir_dk=18 * 60 + 30)]},
                            18 * 60 + 30, D.date(), {})
check("V4b) Aynı blok gündüz saatinde → gönderilir (birim kontrolü)",
      len(_gonderilen) == 1 and ":uyandir:" in _gonderilen[0], _gonderilen)
notifier._sekerleme_uyandir(None, None, _sahte, None,
                            {"schedule": [dict(_blok, uyandir_dk=18 * 60 + 30, devam=False)]},
                            18 * 60 + 30, D.date(), {})
check("V4c) Kayıt kapalıysa (bebek uyanmış) → gönderilmez", len(_gonderilen) == 1, _gonderilen)
notifier._gonder = _eski_gonder

# =============================================================================
# V5 — bildirim tercihi kapalı
# =============================================================================
b5 = Bebek("Tercih")
b5.gunduz(*NAPLAR)
_s5 = b5.sekerleme(an(0, 16 * 60 + 1)) or {}
b5.log("nap", an(0, _s5.get("start_minute", 18 * 60)), saat=an(0, _s5.get("start_minute", 18 * 60)))
_db = SessionLocal()
_u5 = _db.get(User, b5.user_id())
_u5.notification_prefs = {**(_u5.notification_prefs or {}), notifier.TERCIH_UYKU: False}
_db.commit()
_db.close()
_u5dk = (b5.sekerleme(an(0, _s5.get("start_minute", 18 * 60) + 2)) or {}).get("uyandir_dk", 0)
_giden5 = tur(an(0, _u5dk), b5.user_id())
check("V5) Uyku bildirimi tercihi kapalı → şekerleme uyandırması gitmez", _giden5 == [], _giden5)

# =============================================================================
# V6 — kayıt eksik gün
# =============================================================================
b6 = Bebek("Eksik")
b6.gunduz(NAPLAR[1], NAPLAR[2])                # 1. uykunun kaydı YOK
bayrak(False)
_k6 = b6.sekerleme(an(0, 16 * 60 + 1))
bayrak(True)
_a6 = b6.sekerleme(an(0, 16 * 60 + 1))
check("V6a) Kapalıyken kayıt eksik günde de şekerleme çıkıyordu (fark kanıtı)",
      _k6 is not None, _k6)
check("V6b) Açıkken kayıt eksik günde şekerleme YOK", _a6 is None, _a6)
b6.gunduz(NAPLAR[0])                           # eksik kayıt girildi
check("V6c) Eksik kayıt girilince şekerleme geri gelir",
      b6.sekerleme(an(0, 16 * 60 + 1)) is not None, "")

# =============================================================================
# V7–V9 — kayıt tamlığı ve eksik kayıt bildirimi (İlayda 12 Ekim, 8. soru)
# =============================================================================
def tur_eksik(t, uid) -> list:
    once = len(GIDEN)
    s = SessionLocal()
    try:
        with saat_sabitle(t):
            notifier.run_reminder_cycle(s, now=t.astimezone(timezone.utc))
    finally:
        s.close()
    return [g for g in GIDEN[once:]
            if (g[2] or {}).get("kategori") == notifier.KAT_EKSIK_KAYIT and g[0] == uid]


b7 = Bebek("Yarim")
for _dk in NAPLAR[:2]:                         # 10'ar dk; 3. uyku (15:30) henüz gelmedi
    b7.log("nap", an(0, _dk), an(0, _dk + 10))
bayrak(False)
_k7 = b7.sekerleme(an(0, 13 * 60 + 30))
bayrak(True)
_a7 = b7.sekerleme(an(0, 13 * 60 + 30))
check("V7a) Kapalıyken uykular bitmeden (tahmini bloklarla) şekerleme çiziliyordu (fark kanıtı)",
      _k7 is not None, _k7)
check("V7b) Açıkken yaşın beklediği sayıda uyku kayıtlı değilse şekerleme önerilmez", _a7 is None, _a7)
b7.gunduz(NAPLAR[2])
check("V7c) 3. uyku kaydedilip açık sürünce şekerleme gelir",
      b7.sekerleme(an(0, 16 * 60 + 1)) is not None, "")
_ad7 = b7.bugun(an(0, 16 * 60 + 1))["adaptation"]
check("V7d) Uykular tam: kestirme_kayit_eksik false", _ad7.get("kestirme_kayit_eksik") is False, _ad7)

b8 = Bebek("Bildirim")
b8.gunduz(NAPLAR[0])                           # 12:30 ve 15:30 kaydı yok
_ad8 = b8.bugun(an(0, 16 * 60 + 30))["adaptation"]
check("V8a) Saati geçmiş planlı uykunun kaydı yok → kestirme_kayit_eksik true, şekerleme yok",
      _ad8.get("kestirme_kayit_eksik") is True and b8.sekerleme(an(0, 16 * 60 + 30)) is None, _ad8)
_U8 = b8.user_id()
_e1 = tur_eksik(an(0, 16 * 60 + 30), _U8)
check("V8b) Eksik kayıt bildirimi şekerleme penceresinden (17:00) önce bir kez gider",
      len(_e1) == 1 and _e1[0][1] == notifier.EKSIK_KAYIT_BASLIK, _e1)
check("V8c) Aynı gün ikinci kez gitmez", tur_eksik(an(0, 16 * 60 + 45), _U8) == [], "")
from engine import yontem as _yontem
check("V8d) Bildirim metni yöntem tablosuyla aynı ('Bugünkü uykuları eksik mi kaydettiniz?')",
      notifier.EKSIK_KAYIT_GOVDE == _yontem.deger("kestirme.eksik_kayit_bildirimi")["metin"]
      and _yontem.deger("kestirme.eksik_kayit_bildirimi")["gunde_en_fazla"] == 1)

b9 = Bebek("Gec")
b9.gunduz(NAPLAR[0])
check("V9a) Şekerleme penceresinden sonra (17:10) eksik kayıt bildirimi gitmez",
      tur_eksik(an(0, 17 * 60 + 10), b9.user_id()) == [], "")
bayrak(False)
check("V9b) Bayrak kapalı: adaptation'da kestirme_kayit_eksik alanı YOK, bildirim yok",
      "kestirme_kayit_eksik" not in b8.bugun(an(0, 16 * 60 + 30))["adaptation"]
      and tur_eksik(an(1, 16 * 60 + 30), _U8) == [], "")

bayrak(False)
check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

# =============================================================================
print("\n" + "=" * 74)
print("ŞEKERLEME UYANDIRMA — KESTIRME_V2")
print("=" * 74)
_gecen = 0
for ad, ok, detay in results:
    print(f"  {'[PASS]' if ok else '[FAIL]'} {ad}")
    if not ok:
        print(f"         → {detay}")
    _gecen += int(ok)
print("-" * 74)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
print("=" * 74)
sys.exit(0 if _gecen == len(results) else 1)
