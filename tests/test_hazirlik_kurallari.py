"""
İlayda'nın hazırlık kuralları (HAZIRLIK_KURALLARI bayrağı, 2026-10-12).

LLM YOK (mühür, yedek motor), ağ YOK: geçici sqlite + TestClient + dondurulmuş saat.

Kapsam:
  H1  Kod eşlemesi — yeni biçim (w:1/w:0, d:1/d:0) ve eski biçim (a:white_noise,
      w:1, d yok = cevapsız, a:none) birlikte okunuyor
  H2  Kurallar — her destek/konum kodu, emzik 8 ay sınırı, ses, oda (d:0 ve
      cevapsız: aynı kimlik, koşullu metin), peluş/battaniye 12 ay uyarısı;
      beslenme herkese ve ilk; kimlikler sabit ve tekil
  H3  Bayrak KAPALI: plan ön hazırlığı eski motorunki, /plans/today'de
      egitim_oncesi alanı YOK
  H4  Bayrak AÇIK: yeni planın ön hazırlık listesi kurallardan (istem + yedek
      motor metni); çizelge, /plans/today saatleri, timeline ve haftalık özet
      bayrak kapalıyla birebir aynı
  H5  Kart: bekleme bebeğinde başlangıç tarihi + adımlar + uyarılar; eğitimi
      başlamış bebekte ve yenidoğanda null; training_started_at değişmiyor;
      eski ve yeni mobil biçimi aynı kartı (ses/oda farkı dışında) veriyor
  H6  Mevcut plan yeniden üretilmiyor (bayrağı açmak plan satırına dokunmaz)
  H7  Eğitim durumu (2026-10-12): eğitim bugün başlıyorsa istem/yedek motor
      bölümü "Eğitimin ilk günü", geçiş adımları bugünden başlanan metinle;
      eğitim ortasında yeniden üretimde "Eğitim sürerken", geçiş adımları yok;
      bekleme ve bayrak kapalı istem bölümü eskisinin aynısı; çizelge, gün
      sayısı ve training_started_at değişmez
  H8  Okuma anında dönüşüm: eğitim planı döndürülürken metindeki "## Ön
      Hazırlık" bölümü eğitimin o günkü durumuna göre değişir (ilk gün /
      sürerken / tamamlandı → bölüm yok); /plans/today, /plans, /plans/{tarih}
      hepsinde; DB'ye yazılmaz; bayrak kapalıyken metin aynen; "## Eğitim
      Planı", günler, çizelge değişmez; bekleme planı değişmez

Çalıştırma: python tests/test_hazirlik_kurallari.py
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

_DB = Path(tempfile.gettempdir()) / "hazirlik_kurallari_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"              # prod ile aynı: kilit yok
os.environ.pop("HAZIRLIK_KURALLARI", None)

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, SleepPlan                 # noqa: E402
from api.main import app                               # noqa: E402
from api.services import hazirlik                      # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402
from tests.llm_muhuru import canli_cagri_sayisi, muhurle  # noqa: E402

muhurle()
Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))
SIMDI = datetime(2026, 10, 12, 10, 0, tzinfo=TR)
BUGUN = SIMDI.date()


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def bayrak(acik: bool) -> None:
    if acik:
        os.environ["HAZIRLIK_KURALLARI"] = "1"
    else:
        os.environ.pop("HAZIRLIK_KURALLARI", None)


def kimlikler(h: dict, alan: str = "adimlar") -> list[str]:
    return [a["kimlik"] for a in h[alan]]


# =============================================================================
# H1 — kod eşlemesi
# =============================================================================
e = hazirlik.esle("nursing", "loc:parent_bed;a:pacifier;d:0;w:0")
check("H1a) Yeni biçim hayır: ses False, oda karanlık False",
      e["ses"] is False and e["oda_karanlik"] is False and e["konum"] == "parent_bed"
      and e["destek_tr"] == "emzirerek" and e["konum_tr"] == "ortak yatak", e)
e = hazirlik.esle("rocking", "loc:own_bed;a:none;d:1;w:1")
check("H1b) Yeni biçim evet: ses True, oda karanlık True, a:none boş",
      e["ses"] is True and e["oda_karanlik"] is True and e["aksesuarlar"] == [], e)
e = hazirlik.esle("nursing", "loc:crib;a:pacifier,white_noise;w:1")
check("H1c) Eski biçim a:white_noise + w:1 → ses kullanıyor; d yok → cevapsız",
      e["ses"] is True and e["oda_karanlik"] is None, e)
e = hazirlik.esle("holding", "loc:own_bed;a:white_noise")
check("H1d) Eski biçim yalnız a:white_noise → ses kullanıyor", e["ses"] is True, e)
e = hazirlik.esle("holding", "loc:own_bed;a:pacifier;d:1")
check("H1e) Eski biçim w yok, beyaz gürültü yok → ses bilinmiyor (adım yok)",
      e["ses"] is None, e)
e = hazirlik.esle(None, None)
check("H1f) Boş profil çökmez", e["destek"] is None and e["oda_karanlik"] is None, e)

# =============================================================================
# H2 — kurallar
# =============================================================================
h = hazirlik.hazirlik(hazirlik.esle("independent", "loc:own_bed;a:none;d:1;w:0"), 9.0)
check("H2a) En sade profil: yalnız beslenme",
      kimlikler(h) == ["beslenme_1_saat"] and h["uyarilar"] == [], h)
check("H2a) Beslenme metni birebir",
      h["adimlar"][0]["baslik"] == "Beslenme uykudan en az 1 saat önce bitsin"
      and h["adimlar"][0]["aciklama"].startswith("Uykudan önceki son beslenme"), h)
for kod, kimlik in (("nursing", "emzirerek_uyutmayi_birak"), ("rocking", "sallamayi_birak"),
                    ("holding", "kucaktan_yataga"), ("stroller", "pusetten_kucaga_yataga")):
    h = hazirlik.hazirlik(hazirlik.esle(kod, "loc:own_bed;d:1"), 9.0)
    check(f"H2b) {kod} → {kimlik}", kimlikler(h) == ["beslenme_1_saat", kimlik], kimlikler(h))
for loc, kimlik in (("swing", "salincaktan_yataga"), ("car_seat", "salincaktan_yataga"),
                    ("parent_bed", "ortak_yataktan_besige")):
    h = hazirlik.hazirlik(hazirlik.esle("independent", f"loc:{loc};d:1"), 9.0)
    check(f"H2c) loc:{loc} → {kimlik}", kimlikler(h) == ["beslenme_1_saat", kimlik], kimlikler(h))
_k = hazirlik.hazirlik(hazirlik.esle("nursing", "loc:crib;d:1"), 9.0)["adimlar"][1]
check("H2d) Emzirerek metni birebir (3 gün, 1 dk/50 sn/40 sn)",
      _k["baslik"] == "Emzirerek uyutmayı bırak" and "50 sn salla 2 dk bekle" in _k["aciklama"]
      and _k["aciklama"].endswith("beklemeden de başlayabilirsin."), _k)
h7 = hazirlik.hazirlik(hazirlik.esle("independent", "loc:own_bed;a:pacifier;d:1"), 7.9)
h8 = hazirlik.hazirlik(hazirlik.esle("independent", "loc:own_bed;a:pacifier;d:1"), 8.0)
check("H2e) Emzik 8 aydan küçük → emzigi_birak, 8 ay ve üstü → emzik_kullanimi",
      kimlikler(h7)[-1] == "emzigi_birak" and kimlikler(h8)[-1] == "emzik_kullanimi"
      and h8["adimlar"][-1]["baslik"] == "Emzik kullanımı", (kimlikler(h7), kimlikler(h8)))
h = hazirlik.hazirlik(hazirlik.esle("independent", "loc:own_bed;d:1;w:1"), 9.0)
check("H2f) Ses kullanıyor → sesi_kademeli_kapat", "sesi_kademeli_kapat" in kimlikler(h), h)
_d0 = hazirlik.hazirlik(hazirlik.esle("independent", "loc:own_bed;d:0"), 9.0)["adimlar"]
_dy = hazirlik.hazirlik(hazirlik.esle("independent", "loc:own_bed"), 9.0)["adimlar"]
check("H2g) d:0 → odayi_karart (düz metin)",
      _d0[-1]["kimlik"] == "odayi_karart" and _d0[-1]["aciklama"].startswith("Karartma perdesi"),
      _d0[-1])
check("H2g) d yok → aynı kimlik, koşullu metin",
      _dy[-1]["kimlik"] == "odayi_karart" and _dy[-1]["baslik"] == "Odayı karart"
      and _dy[-1]["aciklama"].startswith("Oda karanlık değilse karart:"), _dy[-1])
for aks in ("stuffed_animal", "blanket"):
    _u11 = hazirlik.hazirlik(hazirlik.esle("independent", f"loc:own_bed;a:{aks};d:1"), 11.9)
    _u12 = hazirlik.hazirlik(hazirlik.esle("independent", f"loc:own_bed;a:{aks};d:1"), 12.0)
    check(f"H2h) a:{aks} 12 aydan küçükte uyarı, 12 ayda yok",
          kimlikler(_u11, "uyarilar") == ["yatakta_pelus_battaniye"] and _u12["uyarilar"] == []
          and kimlikler(_u11) == ["beslenme_1_saat"], (_u11, _u12))
_hepsi = hazirlik.hazirlik(hazirlik.esle(
    "nursing", "loc:parent_bed;a:pacifier,white_noise,blanket;d:0;w:1"), 5.0)
_kim = kimlikler(_hepsi)
check("H2i) Hepsi birden: sıra sabit, kimlik tekil",
      _kim == ["beslenme_1_saat", "emzirerek_uyutmayi_birak", "ortak_yataktan_besige",
               "emzigi_birak", "sesi_kademeli_kapat", "odayi_karart"]
      and len(set(_kim)) == len(_kim), _kim)
check("H2j) Yaş bilinmiyorsa yaşa bağlı kurallar uygulanmaz",
      "emzigi_birak" not in kimlikler(hazirlik.hazirlik(
          hazirlik.esle("independent", "loc:own_bed;a:pacifier,blanket;d:1"), None)), "")

# =============================================================================
# Kurulum — bebekler (yaşlar BUGUN'e göre)
# =============================================================================
tok = client.post("/api/v1/auth/register",
                  json={"email": "hazirlik@example.com", "password": "TestPass123!"}
                  ).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


def bebek(ad: str, gun_once: int, method: str, env: str) -> str:
    with saat_sabitle(SIMDI):
        r = client.post("/api/v1/babies", headers=H, json={
            "name": ad, "birth_date": (BUGUN - timedelta(days=gun_once)).isoformat(),
            "feeding_type": "breast", "sleep_method": method, "sleep_environment": env,
            "crying_tolerance": "5", "parent_experience": "none", "night_wakes": 2})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def uret(bid: str) -> dict:
    with saat_sabitle(SIMDI):
        r = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": bid})
    assert r.status_code == 201, r.text[:300]
    return r.json()


def bugun(bid: str) -> dict:
    with saat_sabitle(SIMDI):
        r = client.get(f"/api/v1/plans/today?baby_id={bid}", headers=H)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def get(yol: str, bid: str, **p) -> dict:
    with saat_sabitle(SIMDI):
        r = client.get(yol, headers=H, params={"baby_id": bid, **p})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def baby_alan(bid: str, alan: str):
    db = SessionLocal()
    try:
        return getattr(db.get(Baby, uuid.UUID(bid)), alan)
    finally:
        db.close()


def plan_sil(bid: str) -> None:
    db = SessionLocal()
    try:
        db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(bid)).delete()
        db.commit()
    finally:
        db.close()


def kayit_gonder(bid: str) -> None:
    def an(g, s):
        return (datetime.combine(BUGUN - timedelta(days=g), datetime.min.time(), TR)
                + timedelta(hours=s)).isoformat()
    with saat_sabitle(SIMDI):
        r = client.post("/api/v1/logs/batch", headers=H, json={"logs": [
            {"baby_id": bid, "type": "sleep", "client_id": f"{bid}-g",
             "started_at": an(1, 20), "ended_at": an(0, 6.5)},
            {"baby_id": bid, "type": "nap", "client_id": f"{bid}-n",
             "started_at": an(1, 10), "ended_at": an(1, 11.25)}]})
    assert r.status_code == 200, r.text


B_EGITIM = bebek("Egitim", 280, "nursing", "loc:parent_bed;a:pacifier;d:0;w:1")   # ~9 ay
B_BEKLEME = bebek("Bekleme", 125, "rocking", "loc:crib;a:pacifier,blanket;w:1")   # ~4 ay, eski biçim
B_BEKLEME2 = bebek("Bekleme2", 125, "rocking", "loc:crib;a:pacifier,blanket;d:0;w:1")  # yeni biçim
B_YENI = bebek("Yenidogan", 40, "nursing", "loc:crib;a:none;d:1;w:0")              # ~1 ay
for _b in (B_EGITIM, B_BEKLEME):
    kayit_gonder(_b)

# =============================================================================
# H3 — bayrak kapalı
# =============================================================================
bayrak(False)
from engine import plan_generator                      # noqa: E402
_istem: list = []
_eski_uret = plan_generator.plan_uret


_paramlar: list = []


def _yakala(param, **kw):
    _istem.append([dict(h) for h in param.get("on_hazirlik") or []])
    _paramlar.append(dict(param))
    return _eski_uret(param, **kw)


plan_generator.plan_uret = _yakala
_p_kapali = {b: uret(b) for b in (B_EGITIM, B_BEKLEME)}
_on_kapali = list(_istem)
_today_kapali = {b: bugun(b) for b in (B_EGITIM, B_BEKLEME)}
_tl_kapali = {b: get("/api/v1/logs/timeline", b, days=7) for b in (B_EGITIM, B_BEKLEME)}
_wk_kapali = {b: get("/api/v1/logs/weekly-summary", b) for b in (B_EGITIM, B_BEKLEME)}
check("H3a) Bayrak kapalı: ön hazırlık eski motorunki (kimlik yok)",
      all(not any("kimlik" in h for h in lst) for lst in _on_kapali), _on_kapali)
check("H3b) Bayrak kapalı: /plans/today içeriğinde egitim_oncesi YOK",
      all("egitim_oncesi" not in t["content"] for t in _today_kapali.values()),
      [list(t["content"])[:5] for t in _today_kapali.values()])
_tsa_kapali = {b: baby_alan(b, "training_started_at") for b in (B_EGITIM, B_BEKLEME)}

# =============================================================================
# H6 — bayrağı açmak mevcut planı yeniden üretmez
# =============================================================================
bayrak(True)
_istem.clear()
_today_acik = {b: bugun(b) for b in (B_EGITIM, B_BEKLEME)}
check("H6) Bayrak açıldı, mevcut plan yeniden üretilmedi (istem yok, aynı plan id)",
      _istem == [] and all(_today_acik[b]["id"] == _today_kapali[b]["id"]
                           for b in _today_acik), _istem)

# =============================================================================
# H4 — bayrak açık: yeni plan
# =============================================================================
for b in (B_EGITIM, B_BEKLEME):
    plan_sil(b)
_p_acik = {b: uret(b) for b in (B_EGITIM, B_BEKLEME)}
_on_acik = list(_istem)
_today_acik = {b: bugun(b) for b in (B_EGITIM, B_BEKLEME)}
_on_egitim = [h["kimlik"] for h in _on_acik[0]]
check("H4a) Yeni planın ön hazırlığı kurallardan (9 ay, emzirerek, ortak yatak, emzik, ses, d:0)",
      _on_egitim == ["beslenme_1_saat", "emzirerek_uyutmayi_birak", "ortak_yataktan_besige",
                     "emzik_kullanimi", "sesi_kademeli_kapat", "odayi_karart"], _on_egitim)
_on_bekleme = _on_acik[1]
check("H4b) Uyarı listeye 'Güvenlik uyarısı' olarak giriyor (4 ay, battaniye)",
      _on_bekleme[-1]["kimlik"] == "yatakta_pelus_battaniye"
      and _on_bekleme[-1]["sure"] == "Güvenlik uyarısı", _on_bekleme)
_md = _p_acik[B_EGITIM]["content"]["markdown"]
check("H4c) Yedek motor: eğitim bugün başlıyor → 'Eğitimin ilk günü' bölümü",
      "## Eğitimin ilk günü" in _md and "## Ön Hazırlık" not in _md
      and "**Emzirerek uyutmayı bırak** (Bugünden)" in _md
      and "Bugünden itibaren emzirerek değil sallayarak uyut" in _md
      and "Eğitimden önceki 3 gün" not in _md and "eğitime sonra başla" not in _md
      and "**Beslenme uykudan en az 1 saat önce bitsin**" in _md, _md[:300])
_md_b = _p_acik[B_BEKLEME]["content"]["markdown"]
check("H4c) Yedek motor: bekleme planı bölümü eskisi gibi 'Ön Hazırlık' + 'Öneri'",
      "## Ön Hazırlık" in _md_b and "(Öneri)" in _md_b, _md_b[:200])
for b, ad in ((B_EGITIM, "eğitim"), (B_BEKLEME, "bekleme")):
    check(f"H4d) {ad}: plan şablonu çizelgesi bayrak kapalıyla aynı",
          _p_acik[b]["content"]["schedule"] == _p_kapali[b]["content"]["schedule"], "")
    check(f"H4e) {ad}: /plans/today çizelgesi + saatler aynı",
          _today_acik[b]["content"]["schedule"] == _today_kapali[b]["content"]["schedule"], "")
    def _icerik(c: dict) -> dict:
        # adaptation.hesaplandi_at duvar saati damgasıdır (saat_sabitle onu dondurmaz).
        c = dict(c)
        c["adaptation"] = {k: v for k, v in (c.get("adaptation") or {}).items()
                           if k != "hesaplandi_at"}
        return c
    _ca, _ck = _icerik(_today_acik[b]["content"]), _icerik(_today_kapali[b]["content"])
    _fark = sorted(k for k in set(_ca) | set(_ck) if _ca.get(k) != _ck.get(k))
    # base_plan_id: plan silinip yeniden üretildi (yeni satır), içerik farkı değil.
    check(f"H4f) {ad}: /plans/today farkı yalnız markdown + egitim_oncesi",
          set(_fark) <= {"markdown", "egitim_oncesi", "base_plan_id"}, _fark)
    check(f"H4g) {ad}: timeline ve haftalık özet aynı",
          get("/api/v1/logs/timeline", b, days=7) == _tl_kapali[b]
          and get("/api/v1/logs/weekly-summary", b) == _wk_kapali[b], "")

# =============================================================================
# H5 — "Eğitime başlamadan önce" kartı
# =============================================================================
check("H5a) Eğitimi başlamış bebek: egitim_oncesi null",
      "egitim_oncesi" in _today_acik[B_EGITIM]["content"]
      and _today_acik[B_EGITIM]["content"]["egitim_oncesi"] is None,
      _today_acik[B_EGITIM]["content"].get("egitim_oncesi"))
_kart = _today_acik[B_BEKLEME]["content"]["egitim_oncesi"] or {}
_tahmini = (_today_acik[B_BEKLEME]["content"].get("egitim_baslangic") or {}).get("tahmini_tarih")
check("H5b) Bekleme bebeği: başlangıç tarihi = planın tahmini tarihi",
      _kart.get("egitim_baslangic") == _tahmini and _tahmini is not None, (_kart, _tahmini))
check("H5c) Bekleme bebeği (eski biçim): adımlar + koşullu oda + uyarı",
      [a["kimlik"] for a in _kart.get("adimlar", [])]
      == ["beslenme_1_saat", "sallamayi_birak", "emzigi_birak", "sesi_kademeli_kapat",
          "odayi_karart"]
      and _kart["adimlar"][-1]["aciklama"].startswith("Oda karanlık değilse")
      and [u["kimlik"] for u in _kart.get("uyarilar", [])] == ["yatakta_pelus_battaniye"]
      and set(_kart["adimlar"][0]) == {"kimlik", "baslik", "aciklama"}, _kart)
uret(B_BEKLEME2)
_kart2 = bugun(B_BEKLEME2)["content"]["egitim_oncesi"] or {}
check("H5d) Aynı bebek yeni biçimde (d:0): aynı kimlikler, oda metni düz",
      [a["kimlik"] for a in _kart2.get("adimlar", [])]
      == [a["kimlik"] for a in _kart["adimlar"]]
      and _kart2["adimlar"][-1]["aciklama"].startswith("Karartma perdesi"), _kart2)
uret(B_YENI)
_ty = bugun(B_YENI)
check("H5e) Yenidoğan planı: egitim_oncesi null",
      _ty["content"].get("type") == "yenidogan_ritim" and _ty["content"]["egitim_oncesi"] is None,
      _ty["content"].get("type"))
check("H5f) training_started_at bayrakla değişmedi",
      {b: baby_alan(b, "training_started_at") for b in (B_EGITIM, B_BEKLEME)} == _tsa_kapali,
      _tsa_kapali)
_ileri = BUGUN + timedelta(days=3)
db = SessionLocal()
db.get(Baby, uuid.UUID(B_BEKLEME)).training_started_at = _ileri
db.commit()
db.close()
_k3 = bugun(B_BEKLEME)["content"]["egitim_oncesi"] or {}
check("H5g) İleri tarihli training_started_at: kart o tarihi gösterir",
      _k3.get("egitim_baslangic") == _ileri.isoformat(), _k3.get("egitim_baslangic"))
bayrak(False)
check("H5h) Bayrak tekrar kapalı: alan yok",
      "egitim_oncesi" not in bugun(B_BEKLEME)["content"], "")

# =============================================================================
# H7 — eğitim durumu: istem bölümü ve eğitim ortasında yeniden üretim
# =============================================================================
from api.services import plan_service                   # noqa: E402
_pi = {p_.get("egitim_durumu"): p_ for p_ in _paramlar}
_ist_ilk = plan_generator._build_user_prompt(_pi["ilk_gun"]) if "ilk_gun" in _pi else ""
check("H7a) İstem (ilk gün): bölüm 'Eğitimin ilk günü', erteleme yasağı talimatı",
      "3. ## Eğitimin ilk günü" in _ist_ilk and "EĞİTİMİN İLK GÜNÜ" in _ist_ilk
      and "Eğitim BUGÜN başlıyor" in _ist_ilk and "3. ## Ön Hazırlık" not in _ist_ilk,
      list(_pi))
_ist_bek = plan_generator._build_user_prompt(_pi[None]) if None in _pi else ""
check("H7b) İstem (bekleme, bayrak açık): bölüm satırı ve başlık eskisi gibi",
      "3. ## Ön Hazırlık (varsa gün gün)" in _ist_bek and "\nÖN HAZIRLIK:\n" in _ist_bek
      and "EĞİTİM DURUMU" not in _ist_bek, "")
_ist_kap = plan_generator._build_user_prompt(
    {k: v for k, v in _pi["ilk_gun"].items() if k not in ("egitim_durumu", "egitim_gunu")}
) if "ilk_gun" in _pi else ""
check("H7c) Durum yokken (bayrak kapalı yolu) istem bölümü eskisinin aynısı",
      "3. ## Ön Hazırlık (varsa gün gün)" in _ist_kap and "EĞİTİM DURUMU" not in _ist_kap, "")

bayrak(True)
_ts_eski = BUGUN - timedelta(days=4)
db = SessionLocal()
db.get(Baby, uuid.UUID(B_EGITIM)).training_started_at = _ts_eski
db.commit()
db.close()
_onceki_gun = len(_p_acik[B_EGITIM]["content"].get("days") or [])
plan_sil(B_EGITIM)
_paramlar.clear()
_p5 = uret(B_EGITIM)
_par5 = _paramlar[-1] if _paramlar else {}
_on5 = [h["kimlik"] for h in _par5.get("on_hazirlik") or []]
check("H7d) Eğitimin 5. günü: durum 'suruyor', gün 5",
      _par5.get("egitim_durumu") == "suruyor" and _par5.get("egitim_gunu") == 5,
      (_par5.get("egitim_durumu"), _par5.get("egitim_gunu")))
check("H7e) 5. gün listesinde geçiş adımı YOK (yalnız eğitim boyunca geçerliler)",
      _on5 == ["beslenme_1_saat", "emzik_kullanimi", "sesi_kademeli_kapat", "odayi_karart"]
      and not set(_on5) & set(hazirlik.GECIS_ADIMLARI), _on5)
_md5 = _p5["content"]["markdown"]
_ist5 = plan_generator._build_user_prompt(_par5)
check("H7f) Yedek motor + istem: 'Eğitim sürerken', geçiş başlığı yok",
      "## Eğitim sürerken" in _md5 and "Emzirerek uyutmayı bırak" not in _md5
      and "Önce beşiğe geç" not in _md5 and "3. ## Eğitim sürerken" in _ist5
      and "bugün 5. gün" in _ist5, _md5[:300])
check("H7g) Yeniden üretim: training_started_at ve gün sayısı değişmedi, çizelge aynı",
      baby_alan(B_EGITIM, "training_started_at") == _ts_eski
      and len(_p5["content"].get("days") or []) == _onceki_gun
      and _p5["content"]["schedule"] == _p_kapali[B_EGITIM]["content"]["schedule"],
      (baby_alan(B_EGITIM, "training_started_at"), _onceki_gun))
check("H7h) egitim_durumu: bekleme → None",
      plan_service.egitim_durumu(None, {"uygun_mu": False}, BUGUN) is None, "")
bayrak(False)

# =============================================================================
# H8 — okuma anında "Ön Hazırlık" dönüşümü
# =============================================================================
_ESKI_BOLUM = ("## Ön Hazırlık\n\nEğitime başlamadan önce şunları yapın; 1-3 gün "
               "içinde hazırlanabilirsiniz.\n\n### 1. Destek\nEğitimden önce "
               "sallamayı azaltın.\n\n---\n\n")
_h = hazirlik.hazirlik_bolumunu_degistir
_ornek = ("## Bebek Profili Özeti\nx\n\n" + _ESKI_BOLUM
          + "## Günlük Program\ny\n\n## Eğitim Planı\n\n### Gün 1\nÖn Hazırlık yok\n")
check("H8a) Birim: bölüm değişir, öncesi/sonrası ve Eğitim Planı aynı",
      _h(_ornek, "## Eğitim sürerken\n\n- a\n\n")
      == _ornek.replace(_ESKI_BOLUM, "## Eğitim sürerken\n\n- a\n\n"), "")
check("H8a) Birim: '' → bölüm kaldırılır; bölüm yoksa metin aynı",
      "Ön Hazırlık\n" not in _h(_ornek, "") and _h("## A\nb\n", "x") == "## A\nb\n", "")
_sonra = "## Eğitim Planı\n\n### Gün 1\nz\n\n## Ön Hazırlık\nq\n"
check("H8a) Birim: Eğitim Planı'ndan SONRAKİ hazırlık başlığına dokunulmaz",
      _h(_sonra, "X") == _sonra, "")


def plan_metni(bid: str, md: str | None = None) -> str:
    """Bebeğin bugünkü (son) planının DB'deki markdown'ı; `md` verilirse yazar."""
    db = SessionLocal()
    try:
        p_ = (db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(bid))
              .order_by(SleepPlan.plan_date.desc()).first())
        if md is not None:
            c_ = dict(p_.content)
            c_["markdown"] = md
            p_.content = c_
            db.commit()
        return p_.content["markdown"]
    finally:
        db.close()


def bebek_tarih(bid: str, **alanlar) -> None:
    db = SessionLocal()
    try:
        b_ = db.get(Baby, uuid.UUID(bid))
        for k, v in alanlar.items():
            setattr(b_, k, v)
        db.commit()
    finally:
        db.close()


_md_db = plan_metni(B_EGITIM)
_eski_md = hazirlik.hazirlik_bolumunu_degistir(_md_db, _ESKI_BOLUM)
plan_metni(B_EGITIM, _eski_md)                       # eski (LLM) biçimli metin
bayrak(False)
_t_kapali = bugun(B_EGITIM)
check("H8b) Bayrak kapalı: metin DB'deki gibi aynen",
      _t_kapali["content"]["markdown"] == _eski_md, "")
bayrak(True)
bebek_tarih(B_EGITIM, training_started_at=BUGUN - timedelta(days=4), training_completed_at=None)
_t_sur = bugun(B_EGITIM)
_m_sur = _t_sur["content"]["markdown"]
_ep = lambda m: m[m.find("## Eğitim Planı"):]
_yeni_bolum = hazirlik._BOLUM_RE.search(_m_sur)
_yeni_bolum = _yeni_bolum.group(0) if _yeni_bolum else ""
check("H8c) Eğitim sürüyor: '## Eğitim sürerken', bölümde eski ifade ve geçiş adımı yok",
      _yeni_bolum.startswith("## Eğitim sürerken") and "## Ön Hazırlık" not in _m_sur
      and not any(x in _yeni_bolum for x in ("başlamadan önce", "1-3 gün", "Eğitimden önce",
                                              "Emzirerek uyutmayı bırak", "Önce beşiğe geç"))
      and "**Beslenme uykudan en az 1 saat önce bitsin** (Eğitim boyunca)" in _yeni_bolum,
      _yeni_bolum[:400])
check("H8c) Bölüm DIŞINDAKİ metin birebir aynı (yalnız bölüm değişti)",
      _m_sur.replace(_yeni_bolum, _ESKI_BOLUM, 1) == _eski_md, "")
check("H8d) Eğitim Planı ve sonrası, günler, çizelge aynı; DB'ye yazılmadı",
      _ep(_m_sur) == _ep(_eski_md)
      and _t_sur["content"].get("days") == _t_kapali["content"].get("days")
      and _t_sur["content"]["schedule"] == _t_kapali["content"]["schedule"]
      and plan_metni(B_EGITIM) == _eski_md, "")
bebek_tarih(B_EGITIM, training_started_at=BUGUN)
_m_ilk = bugun(B_EGITIM)["content"]["markdown"]
check("H8e) Eğitim bugün başladı: '## Eğitimin ilk günü', ilk gün metinleri",
      "## Eğitimin ilk günü" in _m_ilk
      and "Bugünden itibaren emzirerek değil sallayarak uyut" in _m_ilk
      and "başlamadan önce" not in _m_ilk, _m_ilk[:400])
bebek_tarih(B_EGITIM, training_started_at=BUGUN - timedelta(days=4))
with saat_sabitle(SIMDI):
    _liste = client.get(f"/api/v1/plans?baby_id={B_EGITIM}", headers=H).json()
    _tarihli = client.get(f"/api/v1/plans/{BUGUN}?baby_id={B_EGITIM}", headers=H).json()
check("H8f) /plans ve /plans/{tarih} de dönüşmüş metni veriyor",
      "## Eğitim sürerken" in _liste[0]["content"]["markdown"]
      and "## Eğitim sürerken" in _tarihli["content"]["markdown"], "")
bebek_tarih(B_EGITIM, training_completed_at=BUGUN - timedelta(days=1))
_m_bit = bugun(B_EGITIM)["content"]["markdown"]
check("H8g) Eğitim tamamlandı: hazırlık bölümü hiç yok, Eğitim Planı aynı",
      "## Ön Hazırlık" not in _m_bit and "## Eğitim sürerken" not in _m_bit
      and "## Eğitimin ilk günü" not in _m_bit and _ep(_m_bit) == _ep(_eski_md), "")
bebek_tarih(B_EGITIM, training_completed_at=None)
_m_bek_db = plan_metni(B_BEKLEME)
check("H8h) Bekleme planı: metin DB'deki gibi aynen",
      bugun(B_BEKLEME)["content"]["markdown"] == _m_bek_db, "")
bayrak(False)
check("H8i) Bayrak tekrar kapalı: eski metin aynen geri",
      bugun(B_EGITIM)["content"]["markdown"] == _eski_md, "")

plan_generator.plan_uret = _eski_uret
check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

# =============================================================================
print("\n" + "=" * 74)
print("HAZIRLIK KURALLARI — kod eşlemesi, adımlar, kart (HAZIRLIK_KURALLARI)")
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
