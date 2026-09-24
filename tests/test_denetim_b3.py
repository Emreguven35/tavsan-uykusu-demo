"""
DENETİM B3 — rapor bulgularının düzeltmeleri. LLM YOK (mühürlü), ağ YOK.

  1  5 ay altı / bekleme planı: eğitim günü ve aşama YOK; training_started_at
     yazılmaz; eski veride bile egitim_baslangic_gunu None, aşama eğitim öncesi
  2  Gündüz çakışması: herhangi bir örtüşme → birleşim; bant tavanını aşan
     kayıt blok olmaz + uyarı (Bebek 16, 09-23 gerçek verisi)
  3  Açık / K17 tahmini uyku: şekerleme yok, uyarı, siradaki_blok tahmini
  4  Doğum tarihi: gelecek / 36 aydan eski → Türkçe 422; eski kayda plan uyarısı
  5  X-App-Version: kullanıcıya yazılır, biçimsiz değer yazılmaz
  6  Denetim raporu: özet, "Kayıt girilmemiş" satırı, sürüm, e-posta

Çalıştırma: python tests/test_denetim_b3.py
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

_DB = Path(tempfile.gettempdir()) / "denetim_b3_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "console"
os.environ["DENETIM_ROOT"] = str(Path(tempfile.gettempdir()) / "denetim_b3_cikti")
os.environ["DENETIM_ALICILARI"] = "ilayda@ornek.test, emre@ornek.test,bozuk"
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "denetim_b3_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, SleepPlan, User                # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import denetim, plan_adapter as pa, plan_service  # noqa: E402
from engine import yas_bantlari                             # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


BUGUN = datetime.now(timezone.utc).date()
TZ = pa.TZ_OFFSET_MIN


def hesap(email: str, gun_yas: int, **ek):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}
                      ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/v1/babies", headers=h, json={
        "name": "Bebek", "night_wakes": 2,
        "birth_date": (BUGUN - timedelta(days=gun_yas)).isoformat(),
        **TAM_PROFIL, **ek})
    return h, r.json().get("id"), r


def utc(gun: date, dk: int) -> datetime:
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - TZ))


class L:
    def __init__(self, tip, bas, bit=None, gun=None):
        gun = gun or BUGUN
        self.id = uuid.uuid4()
        self.type = tip
        self.started_at = utc(gun, bas)
        self.ended_at = None if bit is None else utc(gun, bit)


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# =============================================================================
# 1) 5 ay altı / bekleme planı
# =============================================================================
H1, B1, _ = hesap("uc-aylik@gercek.com", 95)                 # ~3.1 ay → bekleme
g = client.post("/api/v1/plans/generate?sync=true", headers=H1, json={"baby_id": B1})
check("1a) 3 aylık bebeğe bekleme planı", g.status_code == 201
      and g.json()["content"]["type"] == "egitim_bekleme",
      f'{g.status_code} {g.json().get("content", {}).get("type") if g.status_code == 201 else g.text[:100]}')
r = client.patch(f"/api/v1/babies/{B1}", headers=H1,
                 json={"training_started_at": (BUGUN - timedelta(days=6)).isoformat()})
check("1b) Bekleme planında training_started_at YAZILMAZ (istek yine 200)",
      r.status_code == 200 and r.json()["training_started_at"] is None,
      f'{r.status_code} {r.json().get("training_started_at")}')
r = client.patch(f"/api/v1/babies/{B1}", headers=H1, json={"mevcut_asama": "kapi"})
v = client.get("/api/v1/education/videos", headers=H1).json()
check("1c) /education/videos aşama eğitim öncesi (anne beyanı 'kapi' olsa da)",
      v["asama"]["kod"] == "egitim_oncesi", str(v["asama"]))

# Eski bozuk veri: alan DB'de dolu kalmış (temizlik öncesi prod durumu).
_db = SessionLocal()
try:
    _db.get(Baby, uuid.UUID(B1)).training_started_at = BUGUN - timedelta(days=6)
    _db.commit()
finally:
    _db.close()
p = client.get(f"/api/v1/plans/today?baby_id={B1}", headers=H1).json()["content"]
check("1d) Eski veride bile /plans/today egitim_baslangic_gunu None",
      (p.get("adaptation") or {}).get("egitim_baslangic_gunu") is None,
      str((p.get("adaptation") or {}).get("egitim_baslangic_gunu")))
check("1e) Eski veride bile aşama eğitim öncesi",
      client.get("/api/v1/education/videos", headers=H1).json()["asama"]["kod"]
      == "egitim_oncesi", "")

_Hn, _Bn, _rn = hesap("yenidogan@gercek.com", 30,
                      training_started_at=BUGUN.isoformat())
check("1f) Yenidoğan oluşturulurken gelen training_started_at yazılmaz",
      _rn.status_code == 201 and _rn.json()["training_started_at"] is None,
      str(_rn.json().get("training_started_at")))

H8, B8, _ = hesap("sekiz-aylik@gercek.com", 245)
client.post("/api/v1/plans/generate?sync=true", headers=H8, json={"baby_id": B8})
r = client.patch(f"/api/v1/babies/{B8}", headers=H8,
                 json={"training_started_at": (BUGUN - timedelta(days=6)).isoformat()})
check("1g) Eğitim planındaki bebekte training_started_at yazılır",
      r.json()["training_started_at"] == (BUGUN - timedelta(days=6)).isoformat(), "")
p8 = client.get(f"/api/v1/plans/today?baby_id={B8}", headers=H8).json()["content"]
check("1h) Eğitim planında egitim_baslangic_gunu = 7",
      (p8.get("adaptation") or {}).get("egitim_baslangic_gunu") == 7,
      str((p8.get("adaptation") or {}).get("egitim_baslangic_gunu")))
check("1i) Eğitim planında aşama Kapı (7. gün)",
      client.get("/api/v1/education/videos", headers=H8).json()["asama"]["kod"] == "kapi", "")

# =============================================================================
# 2) Gündüz çakışması — Bebek 16, 09-23 (gerçek kayıtlar, 14 aylık, 2 uykulu)
# =============================================================================
tpl14 = pa.build_schedule({}, 7 * 60, yas_ay=14, tek_uyku=False)
bant14 = yas_bantlari.yas_bandi_getir(14.0, tek_uyku=False)
w14 = pa.sabit_wake_minute(tpl14)
bebek16 = [L("sleep", hm("07:30"), hm("07:30")), L("nap", hm("10:30"), hm("11:10")),
           L("sleep", hm("10:34"), hm("13:40")), L("sleep", hm("10:34"), hm("13:40")),
           L("sleep", hm("11:10"), hm("11:10")), L("sleep", hm("13:42"), hm("13:42")),
           L("sleep", hm("14:44"), hm("16:04")), L("sleep", hm("14:45"), hm("16:04")),
           L("sleep", hm("15:18"), hm("16:05")), L("nap", hm("15:45"), hm("16:45")),
           L("nap", hm("18:15"))]
r16 = pa.recompute_day(tpl14, bant14, w14, bebek16, now_minute=21 * 60,
                       gun=BUGUN, tz_offset_min=TZ)
naplar = [b for b in r16["schedule"] if b.get("type") == "nap"]
check("2a) Bebek 16: tam 2 gündüz uykusu (eskiden 4)", len(naplar) == 2,
      str([(b["key"], b["time"], b.get("end")) for b in naplar]))
check("2b) 1. uyku tek birleşik blok 10:30–13:40",
      naplar and (naplar[0]["time"], naplar[0].get("end")) == ("10:30", "13:40"),
      str(naplar[:1]))
check("2c) 2. uyku tek birleşik blok 14:44–16:45 (%32 örtüşme de birleşir)",
      len(naplar) > 1 and (naplar[1]["time"], naplar[1].get("end")) == ("14:44", "16:45"),
      str(naplar[1:2]))
_uy = r16["adaptation"]["uyarilar"]
check("2d) Bant tavanı aşıldı → 'aynı saatlere düşen' uyarısı",
      pa.CAKISAN_KAYIT_UYARISI in _uy, str(_uy))
_ys = r16["adaptation"]["yok_sayilan_kayitlar"]
check("2e) Birleşen kayıtlar yok sayılanlarda 'cakisma' koduyla görünüyor",
      sum(1 for y in _ys if y["kod"] == "cakisma") >= 4, str([y["kod"] for y in _ys]))
r2 = pa.recompute_day(tpl14, bant14, w14,
                      [L("nap", hm("10:00"), hm("11:30")),
                       L("nap", hm("11:20"), hm("12:40"))],
                      now_minute=21 * 60, gun=BUGUN, tz_offset_min=TZ)
_n2 = [b for b in r2["schedule"] if b.get("type") == "nap" and b.get("kaynak") == "kayit"]
check("2f) 10 dk'lık örtüşme bile tek uyku (10:00–12:40)",
      len(_n2) == 1 and (_n2[0]["time"], _n2[0]["end"]) == ("10:00", "12:40"),
      str([(b["time"], b.get("end")) for b in _n2]))
check("2g) Tavan aşılmadıysa çakışma uyarısı YOK",
      pa.CAKISAN_KAYIT_UYARISI not in r2["adaptation"]["uyarilar"], "")

# =============================================================================
# 3) Açık / tahmini uyku → şekerleme yok
# =============================================================================
tpl9 = pa.build_schedule({}, 7 * 60, yas_ay=9)
bant9 = yas_bantlari.yas_bandi_getir(9.0)
w9 = pa.sabit_wake_minute(tpl9)
gece = L("sleep", hm("20:00"), None, BUGUN - timedelta(days=1))
gece.ended_at = utc(BUGUN, w9)
r3 = pa.recompute_day(tpl9, bant9, w9,
                      [gece, L("nap", hm("09:30"), hm("09:55")), L("nap", hm("13:00"))],
                      now_minute=hm("13:40"), gun=BUGUN, tz_offset_min=TZ)
check("3a) Açık uyku varken şekerleme eklenmez",
      pa.SEKERLEME_KEY not in {b["key"] for b in r3["schedule"]}
      and r3["adaptation"]["sekerleme"] is None, str(r3["adaptation"]["sekerleme"]))
check("3b) Açık uyku uyarısı", pa.ACIK_UYKU_UYARISI in r3["adaptation"]["uyarilar"],
      str(r3["adaptation"]["uyarilar"]))
_sb = r3["adaptation"]["siradaki_blok"] or {}
check("3c) siradaki_blok tahmini + eksik_kayit=son_uyku_bitisi",
      _sb.get("guven") == "tahmini" and _sb.get("eksik_kayit") == "son_uyku_bitisi", str(_sb))
r3b = pa.recompute_day(tpl9, bant9, w9,
                       [gece, L("nap", hm("09:30"), hm("09:55")), L("nap", hm("10:30"))],
                       now_minute=hm("17:30"), gun=BUGUN, tz_offset_min=TZ)
check("3d) K17 ile tahminen kapatılan uyku da şekerlemeyi engeller",
      r3b["adaptation"]["sekerleme"] is None
      and pa.ACIK_UYKU_UYARISI in r3b["adaptation"]["uyarilar"],
      f'{r3b["adaptation"]["sekerleme"]} {r3b["adaptation"]["uyarilar"]}')
r3c = pa.recompute_day(tpl9, bant9, w9,
                       [gece, L("nap", hm("09:30"), hm("09:55")),
                        L("nap", hm("12:30"), hm("12:55"))],
                       now_minute=hm("17:30"), gun=BUGUN, tz_offset_min=TZ)
check("3e) (kontrol) kayıtlar kapalıyken kısa gün şekerleme alabiliyor, uyarı yok",
      pa.ACIK_UYKU_UYARISI not in r3c["adaptation"]["uyarilar"]
      and r3c["adaptation"]["sekerleme"] is not None, str(r3c["adaptation"]["sekerleme"]))

# =============================================================================
# 4) Doğum tarihi doğrulama
# =============================================================================
_, _, rg = hesap("gelecek@gercek.com", -10)
check("4a) Gelecek doğum tarihi → 422 Türkçe", rg.status_code == 422
      and "ileri bir tarih" in rg.json().get("detail", ""), rg.text[:150])
_, _, ry = hesap("yasli@gercek.com", 37 * 31)
check("4b) 36 aydan eski → 422 Türkçe", ry.status_code == 422
      and "36 aydan eski" in ry.json().get("detail", ""), ry.text[:150])
r = client.patch(f"/api/v1/babies/{B8}", headers=H8,
                 json={"birth_date": (BUGUN + timedelta(days=3)).isoformat()})
check("4c) PATCH ile gelecek tarih → 422", r.status_code == 422, str(r.status_code))
_, _, ro = hesap("sinir@gercek.com", 35 * 30)
check("4d) 35 aylık kabul", ro.status_code == 201, str(ro.status_code))
H77, B77, _ = hesap("yetmisyedi@gercek.com", 200)
_db = SessionLocal()
try:
    _db.get(Baby, uuid.UUID(B77)).birth_date = BUGUN - timedelta(days=int(77 * 30.44))
    _db.commit()
finally:
    _db.close()
g77 = client.post("/api/v1/plans/generate?sync=true", headers=H77, json={"baby_id": B77})
p77 = client.get(f"/api/v1/plans/today?baby_id={B77}", headers=H77).json()
check("4e) 77 aylık eski kayıtta plan uyarısı 'doğum tarihini kontrol'",
      plan_service.DOGUM_TARIHI_KONTROL_UYARISI in (p77.get("content", {}).get("uyarilar") or []),
      str(p77.get("content", {}).get("uyarilar"))[:200])

# =============================================================================
# 5) X-App-Version
# =============================================================================
client.get("/api/v1/babies", headers={**H8, "X-App-Version": "1.0.0+21"})
client.get("/api/v1/babies", headers={**H1, "X-App-Version": "1.0.0+18"})
client.get("/api/v1/babies", headers={**H77, "X-App-Version": "<script>"})
_db = SessionLocal()
try:
    _u8 = _db.query(User).filter(User.email == "sekiz-aylik@gercek.com").one()
    _u77 = _db.query(User).filter(User.email == "yetmisyedi@gercek.com").one()
    check("5a) Sürüm + görülme zamanı yazıldı",
          _u8.app_version == "1.0.0+21" and _u8.app_version_seen_at is not None,
          f"{_u8.app_version} {_u8.app_version_seen_at}")
    check("5b) Biçimsiz sürüm yazılmadı", _u77.app_version is None, str(_u77.app_version))
finally:
    _db.close()
check("5c) build ayrıştırma", denetim.surum_build("1.0.0+21") == 21
      and denetim.surum_build("1.0.0") is None, "")

# =============================================================================
# 6) Denetim raporu + e-posta
# =============================================================================
DUN = denetim.dun()
for h, b, cid in ((H8, B8, "r8"), (H1, B1, "r1")):
    client.post("/api/v1/logs/batch", headers=h, json={"logs": [
        {"baby_id": b, "type": "wake", "client_id": cid + "w", "started_at": utc(DUN, 7 * 60).isoformat()},
        {"baby_id": b, "type": "nap", "client_id": cid, "started_at": utc(DUN, 10 * 60).isoformat(),
         "ended_at": utc(DUN, 11 * 60).isoformat()}]})
client.post("/api/v1/logs/batch", headers=H77, json={"logs": [
    {"baby_id": B77, "type": "feed", "client_id": "f77", "started_at": utc(DUN, 9 * 60).isoformat()}]})
_db = SessionLocal()
try:
    veri = denetim.rapor(_db, DUN)
    yol, link, n = denetim.rapor_yaz(_db, DUN)
finally:
    _db.close()
html = yol.read_text("utf-8")
_no = {b["no"]: b for b in veri["bolumler"]}
check("6a) Yalnız UYKU kaydı olanlar ayrıntılı (beslenme tek başına değil)", n == 2, n)
check("6b) Diğerleri 'Kayıt girilmemiş' satırında",
      "Kayıt girilmemiş:" in html and len(veri["kayitsiz"]) >= 3, str(veri["kayitsiz"]))
check("6c) Özet kutuları", all(k in html for k in (
    "kayıtlı bebek", "sabah uyanışı girilmiş", "çakışan / sıfır süreli kayıt",
    "anne geri bildirimi", "eski sürümde anne")), "")
check("6d) Eski sürüm sayısı başta (1.0.0+18 → 1)", veri["ozet"]["eski_surum"] == 1,
      str(veri["ozet"]))
check("6e) Başlıkta anne sürümü", "uygulama 1.0.0+21" in html
      and 'class="surum eski">uygulama 1.0.0+18' in html, "")
check("6f) 3 aylık bebekte eğitim günü/aşama yok",
      any("eğitim başlamamış" in x and "Eğitim öncesi" in x
          for x in html.split("<section>")[1:] if "uygulama 1.0.0+18" in x), "")
sonuc = denetim.eposta_gonder(DUN, link, n)
check("6g) E-posta her geçerli alıcıya (bozuk adres atlandı)",
      [r["alici"] for r in sonuc] == ["ilayda@ornek.test", "emre@ornek.test"]
      and all(r.get("ok") for r in sonuc), str(sonuc))
check("6h) Konu biçimi", denetim.eposta_konusu(date(2026, 9, 23))
      == "Tavşan Uykusu — günlük denetim 23.09.2026", denetim.eposta_konusu(date(2026, 9, 23)))

# =============================================================================
# 7) Topluluk: tohum içeriği → resmi hesap, uzman rozeti yalnız gerçek uzmanda
# =============================================================================
from api.models import CommunityProfile, Like, Reply, Thread  # noqa: E402
from api.schemas.community import CATEGORIES                   # noqa: E402
from scripts import topluluk_resmi_hesap as trh                # noqa: E402

_db = SessionLocal()
try:
    def _kullanici(eposta, takma, uzman=False, mod=False):
        u = User(email=eposta, password_hash="x")
        _db.add(u); _db.flush()
        _db.add(CommunityProfile(user_id=u.id, nickname=takma, is_expert=uzman,
                                 is_moderator=mod, status="active", post_count=0))
        _db.flush()
        return u
    _t1 = _kullanici("tavsan-seed-ayse-1@example.com", "Ayşe Anne")
    _t2 = _kullanici("tavsan-seed-uzmanzeynep-2@example.com", "Uzman Zeynep", uzman=True)
    _il = _kullanici("ilayda@gercek.com", "ilaydakani", uzman=True, mod=True)
    _em = _kullanici("emre@gercek.com", "Emre Güven", uzman=True, mod=True)
    _an = _kullanici("anne@gercek2.com", "Gerçek Anne")
    _simdi = datetime.now(timezone.utc)
    _k = Thread(user_id=_t1.id, category=CATEGORIES[0], title="Gece uyanmaları",
                body="Bebeğim gece çok uyanıyor", status="published",
                last_activity_at=_simdi, like_count=5, expert_replied=True)
    _db.add(_k); _db.flush()
    _db.add_all([Reply(thread_id=_k.id, user_id=_t2.id, body="Uzman cevabı", status="published"),
                 Reply(thread_id=_k.id, user_id=_an.id, body="Bizde de oldu", status="published"),
                 Like(user_id=_t2.id, target_type="thread", target_id=_k.id),
                 Like(user_id=_an.id, target_type="thread", target_id=_k.id)])
    _db.commit()
    _kid = _k.id
    _kuru = trh.tasi(_db, uygula=False, uzman_kalsin={"ilaydakani"})
    check("7a) Kuru koşu hiçbir şey değiştirmez",
          _db.query(User).filter(User.email.like(trh.TOHUM_DESENI)).count() == 2
          and _db.get(Thread, _kid).user_id == _t1.id, str(_kuru))
    _rp = trh.tasi(_db, uygula=True, uzman_kalsin={"ilaydakani"})
finally:
    _db.close()
check("7b) 1 konu + 1 yanıt taşındı, 2 tohum hesabı silindi",
      _rp["tasinan_konu"] == 1 and _rp["tasinan_yanit"] == 1
      and _rp["silinen_tohum_hesap"] == 2, str(_rp))
check("7c) Uzman rozeti yalnız İlayda'da", _rp["uzman_rozeti_kalan"] == ["ilaydakani"]
      and set(_rp["uzman_rozeti_kaldirilan"]) == {"Uzman Zeynep", "Emre Güven"}, str(_rp))
_hk = {"Authorization": "Bearer " + client.post("/api/v1/auth/register", json={
    "email": "okur@gercek.com", "password": "TestPass123!"}).json()["access_token"]}
_dt = client.get(f"/api/v1/community/threads/{_kid}", headers=_hk).json()
check("7d) Konu yazarı 'Tavşan Uykusu Ekibi' + rozet resmi (uzman değil)",
      _dt.get("nickname") == trh.RESMI_TAKMA_AD and _dt.get("badge") == "resmi"
      and _dt.get("is_official") is True and _dt.get("is_expert") is False, str(_dt)[:250])
_yr = {r["body"]: r for r in _dt.get("replies", [])}
check("7e) Tohum uzmanın cevabı artık resmi hesapta; gerçek annenin cevabı yerinde",
      _yr.get("Uzman cevabı", {}).get("badge") == "resmi"
      and _yr.get("Bizde de oldu", {}).get("nickname") == "Gerçek Anne", str(_yr)[:250])
check("7f) Sayaçlar gerçek veriden: beğeni 1 (tohumunki silindi), uzman cevapladı False",
      _dt.get("like_count") == 1 and _dt.get("expert_replied") is False,
      f'{_dt.get("like_count")} {_dt.get("expert_replied")}')

# =============================================================================
print("=" * 78)
print("DENETİM B3 TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
