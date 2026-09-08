"""
Plan gün bölümleri (content["days"]) — ayrıştırma, doğrulama ve geri doldurma.

BUG: gün başlıklarını LLM yazıyordu, biçim sabit değildi ve istemci markdown'ı
regex'liyordu; aynı backend sürümünden 5 ayrı kalıp ölçüldü, eğitim ekranı iki kez
sessizce boş kaldı. Artık ayrıştırma sunucuda yapılıyor, sonuç yapısal alanda
saklanıyor, ayrıştırılamayan plan reddedilip yeniden ürettiriliyor.

Bu dosya ÜÇ şeyi sabitler:
  1. Ölçülen beş kalıbın (ve tek gün biçimlerinin) tamamı ayrışıyor.
  2. Eksik/çakışan/boşluklu çıktı SESSİZCE GEÇMİYOR — DayParseError yükseliyor.
  3. Eski planlar okuma yolunda days ile zenginleşiyor, markdown aynen duruyor.

Çalıştırma: python tests/test_plan_gunleri.py
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

from engine import plan_generator, plan_gunleri as pg          # noqa: E402
from engine.parameter_engine import parametre_uret             # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


TIP = "13_gun_dirençli"
GUNLER = 13
ASAMALAR = [(1, 3), (4, 6), (7, 9), (10, 12), (13, 13)]


def plan_metni(basliklar: list[str], bolum_basligi: str = "## Eğitim Planı") -> str:
    """Verilen gün başlıklarından gerçekçi bir plan markdown'ı kur."""
    p = ["# Ada İçin Uyku Eğitimi Planı", "",
         "## Günlük Program (Saat Saat)", "",
         "| Saat | Etkinlik |", "| 07:00 | Uyanış |", "",
         "### Gün içi not", "Bu bir gün başlığı değildir.", "",
         bolum_basligi, "", "13 günlük kademeli plan.", ""]
    for i, b in enumerate(basliklar):
        p += [b, "", f"Bu aşamanın açıklaması {i}.", "",
              "#### Bu gün kısa gündüz uykusu olursa", "Dışarıda 1 dakika bekleyin.", "",
              "#### Bu gün yoğun direnç olursa (B Planı)", "45 dakika kuralı.", ""]
    p += ["## Gece Uyanmaları Protokolü", "", "5 dakika bekleyin.", "",
          "## Başarı Kriterleri", "", "- 1. gün: yatağında uykuya geçti", ""]
    return "\n".join(p)


# =============================================================================
# 1) ÖLÇÜLEN BEŞ KALIP — hepsi ayrışmalı
# =============================================================================
OLCULEN = {
    "emoji + en-dash + iki nokta": [
        "### 📍 Gün 1–3: Beşik Yanı", "### 📍 Gün 4–6: Oda Ortası",
        "### 📍 Gün 7–9: Kapı", "### 📍 Gün 10–12: Kapı Eşiği",
        "### 📍 Gün 13: Yatır-çık"],
    "'Günler' + kısa tire": [
        "### 🗓️ Günler 1-3: Beşik Yanı", "### 🗓️ Günler 4-6: Oda Ortası",
        "### 🗓️ Günler 7-9: Kapı", "### 🗓️ Günler 10-12: Kapı Eşiği",
        "### 🗓️ Günler 13: Yatır-çık"],
    "boru ayracı": [
        "### Gün 1–3 | Beşik Yanı", "### Gün 4–6 | Oda Ortası",
        "### Gün 7–9 | Kapı", "### Gün 10–12 | Kapı Eşiği",
        "### Gün 13 | Yatır-çık"],
    "sayı önce ('1. – 3. Gün')": [
        "### 🔵 1. – 3. Gün: Beşik Yanı", "### 🔵 4. – 6. Gün: Oda Ortası",
        "### 🔵 7. – 9. Gün: Kapı", "### 🔵 10. – 12. Gün: Kapı Eşiği",
        "### 🔵 13. Gün: Yatır-çık"],
    "'1–3. Günler' + eğik çizgili etiket": [
        "### 📅 1–3. Günler: Beşik / Yatak Yanı", "### 📅 4–6. Günler: Oda Ortası",
        "### 📅 7–9. Günler: Kapı", "### 📅 10–12. Günler: Kapı Eşiği",
        "### 📅 13. Günler: Yatır-çık"],
}
for ad, basliklar in OLCULEN.items():
    try:
        days = pg.build_days(plan_metni(basliklar), TIP, GUNLER)
        aralik = [(d["start"], d["end"]) for d in days]
        check(f"1) {ad} → 5 aşama", aralik == ASAMALAR, str(aralik))
        check(f"1) {ad} → etiketler dolu", all(d["label"] for d in days),
              str([d["label"] for d in days]))
        check(f"1) {ad} → her blokta o günün metni",
              all(f"açıklaması {i}." in days[i]["markdown"] for i in range(5)), "")
    except pg.DayParseError as e:
        check(f"1) {ad} → ayrışmalı", False, f"DayParseError: {e}")

# Karışık kalıplar aynı planda (LLM tutarsızlığı)
_karisik = ["### 📍 Gün 1–3: Beşik Yanı", "### Günler 4-6 | Oda Ortası",
            "### 7. – 9. Gün: Kapı", "### 10–12. Günler: Kapı Eşiği",
            "**Gün 13: Yatır-çık**"]
try:
    _d = pg.build_days(plan_metni(_karisik[:4] + ["### Gün 13: Yatır-çık"]), TIP, GUNLER)
    check("1z) Aynı planda karışık kalıplar", [(x["start"], x["end"]) for x in _d] == ASAMALAR, "")
except pg.DayParseError as e:
    check("1z) Aynı planda karışık kalıplar", False, str(e))

# =============================================================================
# 2) BOZUK ÇIKTI SESSİZCE GEÇMEMELİ
# =============================================================================
def reddedilmeli(ad, markdown, gunler=GUNLER):
    try:
        days = pg.build_days(markdown, TIP, gunler)
        check(f"2) {ad} → reddedilmeli", False,
              f"kabul edildi: {[(d['start'], d['end']) for d in days]}")
    except pg.DayParseError:
        check(f"2) {ad} → reddedilmeli", True, "")


reddedilmeli("eksik aşama (4 başlık)", plan_metni([
    "### Gün 1-3: Beşik", "### Gün 4-6: Oda", "### Gün 7-9: Kapı",
    "### Gün 10-13: Eşik"]))
reddedilmeli("gün boşluğu (7,8,9 yok)", plan_metni([
    "### Gün 1-3: Beşik", "### Gün 4-6: Oda", "### Gün 10-10: Kapı",
    "### Gün 11-12: Eşik", "### Gün 13: Yatır-çık"]))
reddedilmeli("çakışan aralık", plan_metni([
    "### Gün 1-3: Beşik", "### Gün 3-6: Oda", "### Gün 7-9: Kapı",
    "### Gün 10-12: Eşik", "### Gün 13: Yatır-çık"]))
reddedilmeli("13 tek gün başlığı (aşama değil)", plan_metni(
    [f"### Gün {g}: Aşama" for g in range(1, 14)]))
reddedilmeli("hiç gün başlığı yok", plan_metni([]))
reddedilmeli("Eğitim Planı bölümü yok",
             "# Plan\n\n## Günlük Program\n\n### Gün 1-3: Beşik\n\nmetin\n")
reddedilmeli("gün sayısı taşkın (14. gün)", plan_metni([
    "### Gün 1-3: Beşik", "### Gün 4-6: Oda", "### Gün 7-9: Kapı",
    "### Gün 10-12: Eşik", "### Gün 13-14: Yatır-çık"]))

# =============================================================================
# 3) YANLIŞ POZİTİF YOK — gün başlığı olmayanlar başlık sayılmamalı
# =============================================================================
for baslik in ["Günlük Program (Saat Saat)", "Gündüz uykusu protokolü",
               "Gece Uyanmaları Protokolü", "Başarı Kriterleri",
               "Bu gün kısa gündüz uykusu olursa", "Eğitim Planı — 13 Günlük",
               "Ön Hazırlık", "Dikkat Edilmesi Gerekenler"]:
    check(f"3) gün başlığı DEĞİL: {baslik!r}",
          pg.parse_gun_basligi(baslik) is None, str(pg.parse_gun_basligi(baslik)))

# "Ön Hazırlık"taki gün başlıkları Eğitim Planı'na karışmamalı
_onhaz = ("# Plan\n\n## Ön Hazırlık\n\n### Gün 1: Karartma perdesi al\n\nmetin\n\n"
          "### Gün 2: Paravan kur\n\nmetin\n\n"
          + plan_metni(["### Gün 1-3: Beşik", "### Gün 4-6: Oda", "### Gün 7-9: Kapı",
                        "### Gün 10-12: Eşik", "### Gün 13: Yatır-çık"]))
try:
    _d = pg.build_days(_onhaz, TIP, GUNLER)
    check("3z) Ön Hazırlık gün başlıkları sayılmıyor",
          [(x["start"], x["end"]) for x in _d] == ASAMALAR,
          str([(x["start"], x["end"]) for x in _d]))
except pg.DayParseError as e:
    check("3z) Ön Hazırlık gün başlıkları sayılmıyor", False, str(e))

# =============================================================================
# 4) MERDİVEN KB'DEN GELİYOR (kod içinde kopya yok)
# =============================================================================
_a13 = pg.asamalar("13_gun_dirençli")
check("4a) 13 günlük merdiven 5 aşama", len(_a13) == 5, str(len(_a13)))
check("4b) 13 günlük sınırlar", [(a["start"], a["end"]) for a in _a13] == ASAMALAR, "")
check("4c) Pozisyon metinleri KB'den dolu", all(a["position"] for a in _a13), "")
_a6 = pg.asamalar("6_gun_buyuk_cocuk")          # 24+ ay istisnası
check("4d) 6 günlük plan tipi de çözülüyor",
      [(a["start"], a["end"]) for a in _a6] == [(1, 2), (3, 3), (4, 4), (5, 5), (6, 6)],
      str([(a["start"], a["end"]) for a in _a6]))
try:
    _d6 = pg.build_days(plan_metni([pg.baslik_satiri(a) for a in _a6]),
                        "6_gun_buyuk_cocuk", 6)
    check("4e) 6 günlük plan uçtan uca ayrışıyor", len(_d6) == 5, str(len(_d6)))
except pg.DayParseError as e:
    check("4e) 6 günlük plan uçtan uca ayrışıyor", False, str(e))
check("4f) Kısa etiket parantezi atıyor",
      pg.kisa_etiket("Beşik yanı (sandalye veya ayakta)") == "Beşik yanı",
      pg.kisa_etiket("Beşik yanı (sandalye veya ayakta)"))
check("4g) Kanonik başlık biçimi", pg.baslik_satiri(_a13[0]) == "### Gün 1-3: Beşik yanı",
      pg.baslik_satiri(_a13[0]))
check("4h) Tek günlük aşama başlığı", pg.baslik_satiri(_a13[4]) == "### Gün 13: Yatır-çık",
      pg.baslik_satiri(_a13[4]))

# =============================================================================
# 5) YEDEK MOTOR (API anahtarsız) da yapısal gün üretmeli
# =============================================================================
_param = parametre_uret({"bebek_ad": "Ada", "dogum_tarihi": "2025-11-08",
                         "dogum_haftasi": 40, "beslenme": "anne sütü",
                         "destek": "kucakta", "oda": "ayrı oda",
                         "dayanma_siniri": "orta", "deneyim": "ilk",
                         "gece_uyanma": "3"})
_md = plan_generator._fallback_plan(_param)
try:
    _fd = pg.build_days(_md, _param["plan_secimi"]["tip"], _param["plan_secimi"]["gunler"])
    check("5a) Yedek motor planı ayrışıyor",
          [(d["start"], d["end"]) for d in _fd] == ASAMALAR, "")
    check("5b) Yedek planda her blok kendi metnini taşıyor",
          all(30 < len(d["markdown"]) < 900 for d in _fd),
          str([len(d["markdown"]) for d in _fd]))
    check("5c) Son blok gece protokolünü yutmamış",
          "Gece Uyanmaları Protokolü" not in _fd[-1]["markdown"], "")
except pg.DayParseError as e:
    check("5a) Yedek motor planı ayrışıyor", False, str(e))

# Prompt'a dayatılan başlık bloğu gerçekten kanonik satırları içermeli
_blok = plan_generator._gun_basliklari_blok(_param)
check("5d) Prompt gün başlıklarını dayatıyor",
      all(pg.baslik_satiri(a) in _blok for a in _a13), _blok)
_prompt = plan_generator._build_user_prompt(_param)
check("5e) Kural prompt'a girmiş", "GÜN BAŞLIKLARI — BİÇİM SABİTTİR" in _prompt, "")
check("5f) Kanonik satırlar prompt'ta", "### Gün 10-12: Kapı eşiği" in _prompt, "")
# Prompt cache ayracı bozulmamalı (sabit ön-ek + değişken kısım)
check("5g) Prompt cache ayracı duruyor",
      plan_generator._CACHE_SPLIT_MARKER in _prompt, "")
_bloklar = plan_generator._build_cached_content(_param)
check("5h) İki bloğun birleşimi prompt ile aynı",
      _bloklar[0]["text"] + _bloklar[1]["text"] == _prompt, "")
check("5i) Gün başlıkları DEĞİŞKEN blokta (cache bozulmasın)",
      "### Gün 1-3:" in _bloklar[1]["text"] and "### Gün 1-3:" not in _bloklar[0]["text"], "")

# =============================================================================
# 6) OKUMA YOLU — eski plandan türetme (days_from_content)
# =============================================================================
_eski = {"markdown": plan_metni(["### 📍 Gün 1–3: Beşik Yanı", "### 📍 Gün 4–6: Oda Ortası",
                                 "### 📍 Gün 7–9: Kapı", "### 📍 Gün 10–12: Kapı Eşiği",
                                 "### 📍 Gün 13: Yatır-çık"]),
         "plan_secimi": {"tip": TIP, "gunler": GUNLER}, "bucket": "8_ay"}
_turetilen = pg.days_from_content(_eski)
check("6a) Eski plandan days türetiliyor",
      [(d["start"], d["end"]) for d in _turetilen] == ASAMALAR, str(_turetilen[:1]))
check("6b) Ayrıştırılamayan eski plan HATA YÜKSELTMEZ",
      pg.days_from_content({"markdown": "# Plan\n\nHiç bölüm yok.",
                            "plan_secimi": {"tip": TIP, "gunler": GUNLER}}) == [], "")
check("6c) Eksik alanlar güvenli", pg.days_from_content({}) == []
      and pg.days_from_content(None) == [], "")
check("6d) markdown DEĞİŞMİYOR (geriye uyum)",
      _eski["markdown"] == plan_metni(["### 📍 Gün 1–3: Beşik Yanı",
                                       "### 📍 Gün 4–6: Oda Ortası",
                                       "### 📍 Gün 7–9: Kapı",
                                       "### 📍 Gün 10–12: Kapı Eşiği",
                                       "### 📍 Gün 13: Yatır-çık"]), "")

# Sözleşme alanları
_d0 = _turetilen[0]
check("6e) Sözleşme alanları tam",
      set(_d0) == {"start", "end", "label", "position", "markdown"}, str(sorted(_d0)))
check("6f) start/end tamsayı", isinstance(_d0["start"], int) and isinstance(_d0["end"], int), "")
check("6g) Etiket boşsa KB pozisyonundan doluyor",
      (pg.build_days(plan_metni(["### Gün 1-3", "### Gün 4-6", "### Gün 7-9",
                                 "### Gün 10-12", "### Gün 13"]), TIP, GUNLER)[0]["label"]
       == "Beşik yanı"), "")


# =============================================================================
# 7) SERVİS BAĞLANTISI — üretim days üretir, okuma yolu eskiyi doldurur
# =============================================================================
import tempfile                                              # noqa: E402
import uuid as _uuid                                         # noqa: E402
from datetime import date as _date, timedelta as _timedelta  # noqa: E402

_DB = Path(tempfile.gettempdir()) / "plan_gunleri_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ.pop("ANTHROPIC_API_KEY", None)      # yedek motor yolu (ağ YOK, ücretsiz)

from api.db import Base, SessionLocal, engine as _engine     # noqa: E402
import api.models                                            # noqa: E402,F401
from api.models import Baby, SleepPlan, User                  # noqa: E402
from api.services import plan_service                         # noqa: E402

Base.metadata.create_all(bind=_engine)
_db = SessionLocal()
_user = User(email="plan_gunleri@example.com", password_hash="x")
_db.add(_user); _db.flush()
_baby = Baby(user_id=_user.id, name="Ada", birth_date=_date(2025, 11, 8))
_db.add(_baby); _db.commit(); _db.refresh(_baby); _db.refresh(_user)

# 7a) Üretim: content["days"] dolu gelmeli (yedek motorla)
_content = plan_service.generate_content(_baby, None, 40)
check("7a) generate_content days üretiyor",
      [(d["start"], d["end"]) for d in _content.get("days") or []] == ASAMALAR,
      str(_content.get("days") and len(_content["days"])))
check("7b) markdown aynen duruyor (geriye uyum)",
      isinstance(_content.get("markdown"), str) and "## Eğitim Planı" in _content["markdown"], "")
check("7c) days blokları markdown'ın içinden geliyor",
      all(d["markdown"] in _content["markdown"] for d in _content["days"]), "")

# 7d) Ayrıştırma başarısızsa plan REDDEDİLİR (sessizce boş days DÖNMEZ)
_gercek_uret = plan_generator.plan_uret
_BOZUK_MD = "# Plan\n\n## Eğitim Planı\n\nGün başlığı hiç yok.\n"
plan_generator.plan_uret = lambda param, usage_sink=None: _BOZUK_MD
try:
    plan_service.generate_content(_baby, None, 40)
    check("7d) Bozuk çıktı → PlanError", False, "plan kabul edildi")
except plan_service.PlanError as e:
    check("7d) Bozuk çıktı → PlanError", "gün bölümleri" in str(e).lower(), str(e)[:90])
finally:
    plan_generator.plan_uret = _gercek_uret

# 7e) Okuma yolu: days'siz ESKİ plan GET'te doldurulur ve DB'ye yazılır
_eski_content = dict(_content)
_eski_markdown = _eski_content["markdown"]
_eski_content.pop("days")
_plan = SleepPlan(user_id=_user.id, baby_id=_baby.id,
                  plan_date=_date.today() - _timedelta(days=5), content=_eski_content)
_db.add(_plan); _db.commit(); _db.refresh(_plan)
check("7e) Hazırlık: eski planda days yok", not (_plan.content or {}).get("days"), "")

_yukseltilmis = plan_service.ensure_current_schema(_db, _plan)
check("7f) ensure_current_schema days dolduruyor",
      [(d["start"], d["end"]) for d in (_yukseltilmis.content or {}).get("days") or []]
      == ASAMALAR, str((_yukseltilmis.content or {}).get("days")))
check("7g) markdown okuma yolunda DEĞİŞMEDİ",
      _yukseltilmis.content["markdown"] == _eski_markdown, "")

_db.expire_all()                                   # DB'ye gerçekten yazıldı mı
_taze = _db.get(SleepPlan, _plan.id)
check("7h) days DB'ye kalıcı yazıldı",
      len((_taze.content or {}).get("days") or []) == 5, "")

# 7i) İkinci okuma yeniden yazmamalı (idempotent)
_once = _taze.content["days"]
plan_service.ensure_current_schema(_db, _taze)
check("7i) İkinci okumada days aynı", _db.get(SleepPlan, _plan.id).content["days"] == _once, "")

# 7j) "Bugüne taşıma" dalı da days taşımalı (GET /plans/today, kayıt yokken)
_bugun = plan_service.ensure_today_plan(_db, _user, _baby)
check("7j) ensure_today_plan days taşıyor",
      len((_bugun.content or {}).get("days") or []) == 5,
      str(len((_bugun.content or {}).get("days") or [])))
# 7k/7l) Claude yolunda YENİDEN ÜRETİM: ilk çıktı bozuksa plan reddedilip
# tekrar ürettirilir. Yedek motorda tekrar YOK (deterministik, aynı metni verir).
_gecerli_md = plan_generator._fallback_plan(_param)
os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
_eski_has = plan_generator.HAS_ANTHROPIC
plan_generator.HAS_ANTHROPIC = True
_cagri = {"n": 0}


def _sahte_uret(cikti_sirasi):
    def _f(param, usage_sink=None):
        _cagri["n"] += 1
        return cikti_sirasi[min(_cagri["n"] - 1, len(cikti_sirasi) - 1)]
    return _f


try:
    _cagri["n"] = 0
    plan_generator.plan_uret = _sahte_uret([_BOZUK_MD, _gecerli_md])
    _c = plan_service.generate_content(_baby, None, 40)
    check("7k) İlk çıktı bozuk → yeniden üretilip kurtarılıyor",
          _cagri["n"] == 2 and len(_c.get("days") or []) == 5,
          f"cagri={_cagri['n']} days={len(_c.get('days') or [])}")

    _cagri["n"] = 0
    plan_generator.plan_uret = _sahte_uret([_BOZUK_MD])
    try:
        plan_service.generate_content(_baby, None, 40)
        check("7l) İki deneme de bozuk → PlanError", False, "plan kabul edildi")
    except plan_service.PlanError:
        check("7l) İki deneme de bozuk → PlanError",
              _cagri["n"] == plan_service.PLAN_DAYS_MAX_DENEME, f"cagri={_cagri['n']}")
finally:
    plan_generator.plan_uret = _gercek_uret
    plan_generator.HAS_ANTHROPIC = _eski_has
    os.environ.pop("ANTHROPIC_API_KEY", None)
    _db.close()


# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("PLAN GÜN BÖLÜMLERİ (content.days) TEST SONUÇLARI")
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
