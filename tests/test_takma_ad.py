"""
Takma ad — bebeğin gerçek adı ve doğum tarihi yapay zekâ sağlayıcısına gitmez.

KARAR (2026-10-07): istemde gerçek ad yerine aynı ses uyumuna sahip takma ad
gider (son ünlü grubu a/ı · e/i · o/u · ö/ü ve ad sonu ünlü/yumuşak/sert aynı),
cevapta gerçek ad geri konur; modelin yazdığı ekler gerçek ad için de doğru kalır.

Kapsam:
  T1  Ses uyumu: her gruptan adlarda takma adın grubu ve son türü aynı
  T2  Ekler: takma adla yazılmış her hâl eki, geri çevrilince gerçek adın
      DOĞRU ekiyle aynı (bağımsız ek üreticisiyle karşılaştırılır)
  T3  Sorudaki ad: kesmeli/kesmesiz/BÜYÜK harf yazımlar değişir; "canım",
      "ada" (kara parçası), "Adalet", "umut", "Umutsuz", "Durum" DEĞİŞMEZ
  T4  Sohbet ucu: istemde (soru + bağlam) gerçek ad yok, takma ad var; yanıtta
      gerçek ad doğru ekle; DB'de annenin özgün sorusu duruyor
  T5  Plan üretimi: istemde gerçek ad, doğum tarihi (ISO ve GG.AA.YYYY) yok; yaş
      (ay + gün) ve prematürede düzeltilmiş yaş var; sağlık notu TAM BİR KEZ;
      üretilen metinde gerçek ad
  T6  profile_overrides izin listesi: kimlik alanları, kilo_durumu, yanlış tip
      atılır; metin 200 / sağlık 500 karakterle kırpılır

Çalıştırma: python tests/test_takma_ad.py
"""
import os
import re
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "takma_ad_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"            # Sor premium kapısı (beta: herkes premium)
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "takma_ad_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import ChatMessage                          # noqa: E402
from api.main import app                                    # noqa: E402
from api.schemas.plan import override_suz                   # noqa: E402
from api.services import takma_ad                           # noqa: E402
from api.zaman import bugun_tr                              # noqa: E402
from engine import chatbot, plan_generator                  # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle    # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


# --- Bağımsız Türkçe ek üreticisi (modülün iç fonksiyonlarını KULLANMAZ) ----
GRUP = {"a": "a", "ı": "a", "e": "e", "i": "e", "o": "o", "u": "o", "ö": "ö", "ü": "ö"}
IKI = {"a": "a", "e": "e", "o": "a", "ö": "e"}
DORT = {"a": "ı", "e": "i", "o": "u", "ö": "ü"}


def ses(ad):
    k = ad.replace("I", "ı").replace("İ", "i").lower().split()[-1]
    grup = next(GRUP[h] for h in reversed(k) if h in GRUP)
    tur = "unlu" if k[-1] in GRUP else ("sert" if k[-1] in "fstkçşhp" else "yumusak")
    return grup, tur


def ek(ad, hal):
    g, tur = ses(ad)
    u = tur == "unlu"
    d = "t" if tur == "sert" else "d"
    return {
        "tamlayan": ("n" if u else "") + DORT[g] + "n",
        "belirtme": ("y" if u else "") + DORT[g],
        "yonelme": ("y" if u else "") + IKI[g],
        "bulunma": d + IKI[g],
        "ayrilma": d + IKI[g] + "n",
        "vasita": ("y" if u else "") + "l" + IKI[g],
    }[hal]


ADLAR = ["Ada", "Elif", "Can", "Umut", "Duru", "Öykü", "Gönül", "Melis",
         "Aslı", "Burak", "İpek", "Ayşe Nur", "Nehir", "Toprak", "Ömer"]
HALLER = ["tamlayan", "belirtme", "yonelme", "bulunma", "ayrilma", "vasita"]

# =============================================================================
# T1 — ses uyumu
# =============================================================================
for ad in ADLAR:
    t = takma_ad.takma_ad_sec(ad)
    check(f"T1) {ad} → {t}: aynı ünlü grubu + aynı son tür, gerçek addan farklı",
          t and ses(t) == ses(ad) and t.lower() != ad.lower(), f"{ses(ad)} vs {ses(t)}")
check("T1) Takma ad gerçek adla çakışırsa yedek seçilir (Zalva → Zarva)",
      takma_ad.takma_ad_sec("Zalva") == "Zarva", takma_ad.takma_ad_sec("Zalva"))
check("T1) Boş ad → takma ad yok", takma_ad.takma_ad_sec("") is None
      and takma_ad.takma_ad_sec(None) is None, "")

# =============================================================================
# T2 — ekler geri çevrilince gerçek ad için doğru
# =============================================================================
_hatali = []
for ad in ADLAR:
    t = takma_ad.takma_ad_sec(ad)
    for hal in HALLER:
        model_yazdi = f"Bugün {t}'{ek(t, hal)} uykusu."
        geri = takma_ad.adi_geri_koy(model_yazdi, t, ad)
        beklenen = f"Bugün {ad}'{ek(ad, hal)} uykusu."
        if geri != beklenen:
            _hatali.append((ad, hal, geri, beklenen))
check("T2) Tüm adlar × 6 hâl eki: geri çevrilen ek gerçek ad için doğru",
      not _hatali, _hatali[:4])
check("T2) BÜYÜK harfli takma ad da çevrilir",
      takma_ad.adi_geri_koy("ZALVA uyudu", "Zalva", "Ada") == "ADA uyudu", "")

# =============================================================================
# T3 — sorudaki ad
# =============================================================================
VAKALAR = [
    ("Can", "Can'ın uykusu kötü, canım çok yoruldum. CAN dün ağladı; Canla oyun oynadık.",
     "Zalvan'ın uykusu kötü, canım çok yoruldum. Zalvan dün ağladı; Zalvanla oyun oynadık."),
    ("Ada", "Adanın sabahı erken. ada'ya gittik; ada güzeldi, Adalet önemli. Ada uyudu.",
     "Zalvanın sabahı erken. Zalva'ya gittik; ada güzeldi, Adalet önemli. Zalva uyudu."),
    ("Umut", "Umut'un gecesi kötü, umut ediyorum. Umutsuz hissediyorum.",
     "Zolvut'un gecesi kötü, umut ediyorum. Umutsuz hissediyorum."),
    ("Duru", "Durum kötü. Duru'yu uyutamıyorum, Durudan önce.",
     "Durum kötü. Zolvu'yu uyutamıyorum, Zolvudan önce."),
    ("Elif", "Elif'e ne yapayım? elif harfi değil.", "Zelvit'e ne yapayım? elif harfi değil."),
    ("Gönül", "Gönül'ün uykusu, gönülden istiyorum.", "Zölvün'ün uykusu, gönülden istiyorum."),
    ("İpek", "İpek'in yastığı ipek.", "Zelvit'in yastığı ipek."),
]
for ad, soru, beklenen in VAKALAR:
    sonuc = takma_ad.adi_gizle(soru, ad, takma_ad.takma_ad_sec(ad))
    check(f"T3) {ad}: doğru yerler değişti, kelimeler korundu", sonuc == beklenen,
          f"\n      çıktı   : {sonuc}\n      beklenen: {beklenen}")

# =============================================================================
# T4 — sohbet ucu
# =============================================================================
tok = client.post("/api/v1/auth/register",
                  json={"email": "takma@test.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
DOGUM = bugun_tr() - timedelta(days=223)
PROFIL = {"birth_date": DOGUM.isoformat(), "feeding_type": "breast", "sleep_method": "rocking",
          "sleep_environment": "loc:own_bed;d:1", "crying_tolerance": "5",
          "parent_experience": "none", "night_wakes": 2}
_YAKALA: dict = {}
_asil_cevap = chatbot._cevap_uret


def _sahte_cevap(soru, yas_bandi=None, baby_context=None):
    _YAKALA.update(soru=soru, ctx=baby_context or "")
    t = re.search(r"(Z[aeoö]l?r?v\w*)", baby_context or "")
    t = t.group(1).split(",")[0] if t else "?"
    return {"cevap": f"{t}'{ek(t, 'tamlayan')} uykusu için {t}'{ek(t, 'yonelme')} rutin.",
            "cache_hit": False, "kaynaklar": [], "llm": False, "retrieval_layer": "k1",
            "top_score": None}


chatbot._cevap_uret = _sahte_cevap
for ad in ["Ada", "Can", "Umut", "Öykü", "Melis"]:
    bid = client.post("/api/v1/babies", headers=H, json={"name": ad, **PROFIL}).json()["id"]
    soru = f"{ad}'{ek(ad, 'tamlayan')} gece uykusu bozuldu, {ad} ne zaman yatmalı?"
    r = client.post("/api/v1/chat", headers=H, json={"message": soru, "baby_id": bid})
    t = takma_ad.takma_ad_sec(ad)
    istem = _YAKALA.get("soru", "") + "\n" + _YAKALA.get("ctx", "")
    check(f"T4) {ad}: istemde (soru+bağlam) gerçek ad YOK, takma ad var",
          r.status_code == 200 and ad not in istem and t in istem, istem[:160])
    beklenen = f"{ad}'{ek(ad, 'tamlayan')} uykusu için {ad}'{ek(ad, 'yonelme')} rutin."
    check(f"T4) {ad}: yanıtta gerçek ad doğru ekle", r.json().get("answer") == beklenen,
          f"{r.json().get('answer')} | beklenen {beklenen}")
    check(f"T4) {ad}: istemde doğum tarihi yok", DOGUM.isoformat() not in istem
          and DOGUM.strftime("%d.%m.%Y") not in istem, "")
db = SessionLocal()
_son = db.query(ChatMessage).filter(ChatMessage.role == "user").order_by(
    ChatMessage.created_at.desc()).first()
db.close()
check("T4) DB'de annenin ÖZGÜN sorusu duruyor (gerçek adla)",
      _son is not None and "Melis" in _son.content, _son.content if _son else None)
chatbot._cevap_uret = _asil_cevap

# =============================================================================
# T5 — plan üretimi istemi
# =============================================================================
_PARAM: dict = {}
_asil_uret = plan_generator.plan_uret


def _sahte_uret(param, usage_sink=None):
    _PARAM["istem"] = plan_generator._build_user_prompt(param)
    _PARAM["profil"] = dict(param["profile_summary"])
    return plan_generator._fallback_plan(param)


plan_generator.plan_uret = _sahte_uret
PREMATUR = dict(PROFIL, birth_date=(bugun_tr() - timedelta(days=260)).isoformat(),
                dogum_haftasi=34)
for ad, govde, saglik in (("Gönül", PROFIL, "hafif egzama ve reflü"),
                          ("Umut", PREMATUR, "kalp ameliyatı geçirdi")):
    bid = client.post("/api/v1/babies", headers=H,
                      json={"name": ad, **govde, "saglik_problemi": saglik}).json()["id"]
    r = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": bid})
    istem = _PARAM.get("istem", "")
    dogum = govde["birth_date"]
    gg = "{2}.{1}.{0}".format(*dogum.split("-"))
    t = takma_ad.takma_ad_sec(ad)
    check(f"T5) {ad}: plan üretildi", r.status_code == 201, r.text[:160])
    check(f"T5) {ad}: istemde gerçek ad YOK, takma ad var", ad not in istem and t in istem,
          [s for s in istem.splitlines() if ad in s][:3])
    check(f"T5) {ad}: istemde doğum tarihi YOK (ISO ve GG.AA.YYYY)",
          dogum not in istem and gg not in istem, "")
    check(f"T5) {ad}: istemde yaş (ay + gün) var",
          re.search(r"\d+ ay \d+ gün", _PARAM["profil"].get("yas", "")) is not None,
          _PARAM["profil"].get("yas"))
    check(f"T5) {ad}: sağlık notu istemde TAM BİR KEZ", istem.count(saglik) == 1
          if "ameliyat" not in saglik else istem.count(saglik) == 1, istem.count(saglik))
    md = (r.json().get("content") or {}).get("markdown", "")
    check(f"T5) {ad}: üretilen metinde gerçek ad var, takma ad yok",
          ad in md and t not in md, md[:120])
check("T5) Prematürede düzeltilmiş yaş da gidiyor",
      re.search(r"\d+ ay \d+ gün", _PARAM["profil"].get("duzeltilmis_yas", "")) is not None,
      _PARAM["profil"])

# =============================================================================
# T6 — profile_overrides izin listesi
# =============================================================================
_s = override_suz({"bebek_ad": "Gerçek", "dogum_tarihi": "2026-01-01", "kilo_durumu": "iyi",
                   "beslenme": "x" * 500, "saglik_problemi": "y" * 900, "tek_uyku": "evet",
                   "oda": {"a": 1}, "ogle_yatis_dk": 90, "uyaniklik_penceresi_dk": True,
                   "emzik": "evet", "bilinmeyen": "z"})
check("T6a) Kimlik alanları, kilo_durumu, bilinmeyen anahtar atıldı",
      not ({"bebek_ad", "dogum_tarihi", "kilo_durumu", "bilinmeyen"} & set(_s)), _s)
check("T6b) Metin 200, sağlık 500 karakterle kırpıldı",
      len(_s["beslenme"]) == 200 and len(_s["saglik_problemi"]) == 500, "")
check("T6c) Yanlış tip atıldı (tek_uyku metin, oda sözlük, sayıda bool)",
      "tek_uyku" not in _s and "oda" not in _s and "uyaniklik_penceresi_dk" not in _s
      and _s.get("ogle_yatis_dk") == 90 and _s.get("emzik") == "evet", _s)
bid = client.post("/api/v1/babies", headers=H, json={"name": "Ada", **PROFIL}).json()["id"]
r = client.post("/api/v1/plans/generate?sync=true", headers=H, json={
    "baby_id": bid, "profile_overrides": {"bebek_ad": "GercekAd", "dogum_tarihi": "2020-02-02",
                                          "deneyim": "ilk"}})
check("T6d) Uçta: override ile gönderilen kimlik istemde yok",
      r.status_code == 201 and "GercekAd" not in _PARAM["istem"]
      and "2020-02-02" not in _PARAM["istem"] and _PARAM["profil"].get("deneyim") == "ilk",
      r.text[:120])
plan_generator.plan_uret = _asil_uret

check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

# =============================================================================
print("\n" + "=" * 74)
print("TAKMA AD — gerçek ad ve doğum tarihi yapay zekâya gitmez")
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
