"""
3-5 ay bandı (egitim_bekleme) testleri — LLM/DB/ağ YOK, deterministik.

Bu bant iki sınır arasında kalıyor: yenidoğan rehberi 3 ayda bitiyor, eğitim
alt sınırı 5. ay. Dört düzeltme burada kilitleniyor:

  1. egitim_baslangic MOTORDAN gelir — LLM tarih hesaplamaz.
     Ölçülen hata: 34 haftalık prematürede model "doğum + 5 ay" yapıyordu ve
     düzeltilmiş yaşa göre doğru tarihten 46 GÜN ERKEN sonuç veriyordu.
  2. night_wake_protocol EKLENMEZ — eğitim protokolü, eğitime uygun olmayan
     bebekte yeri yok (yenidoğan rehberindeki gerekçenin aynısı).
  3. plan_secimi `days` ile TUTARLI — önizleme işaretli, mobil yanlış rozet basmasın.
  4. Merdiven ÖNİZLEME olarak döner: days dolu + her kayıtta preview=true +
     content.egitim_onizleme=true + markdown'da güçlü uyarı.

Ayrıca: bu yolda ASLA PlanError yükselmemeli (502 regresyonu geri gelmesin).

Çalıştırma: python tests/test_egitim_bekleme.py
"""
import datetime as dtm
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.pop("ANTHROPIC_API_KEY", None)      # yedek motor: LLM çağrısı YOK

from api.services import plan_service                       # noqa: E402
from engine import plan_generator, yenidogan                # noqa: E402
from engine.parameter_engine import parametre_uret          # noqa: E402

# LLM MÜHRÜ (Faz 4) — import'lardan SONRA: load_dotenv anahtarı geri
# yükleyebiliyor, dosya başındaki os.environ.pop tek başına yetmiyor.
from tests.llm_muhuru import muhurle, muhur_saglam_mi   # noqa: E402
muhurle()

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


class SahteBebek:
    feeding_type = "anne sütü"
    sleep_method = "emzirerek"
    sleep_environment = "kendi odası"
    crying_tolerance = "orta"
    parent_experience = "ilk bebeğim"
    night_wakes = 3
    user_id = "test-user"
    id = "test-baby"

    def __init__(self, gun_once: int, ad: str = "Mira"):
        self.name = ad
        self.birth_date = dtm.date.today() - dtm.timedelta(days=gun_once)


def _icerik(gun_once: int, dogum_haftasi: int = 40) -> dict:
    return plan_service.generate_content(SahteBebek(gun_once), None, dogum_haftasi)


# Profiller: (etiket, gün_önce, doğum_haftası, beklenen_tip)
ZAMANINDA_4AY = ("zamanında 4.0 ay", 122, 40, plan_service.TYPE_BEKLEME)
PREMATURE_37 = ("34 hf prematüre, takvim 5.2 / düzeltilmiş 3.7 ay", 158, 34,
                plan_service.TYPE_BEKLEME)
ZAMANINDA_52 = ("zamanında 5.2 ay", 158, 40, plan_service.TYPE_EGITIM)

_bekleme = _icerik(ZAMANINDA_4AY[1], ZAMANINDA_4AY[2])
_prem = _icerik(PREMATURE_37[1], PREMATURE_37[2])
_egitim = _icerik(ZAMANINDA_52[1], ZAMANINDA_52[2])


# =============================================================================
# 0) BANT SINIRLARI — 3-5 ay gerçekten bu tipe düşüyor mu?
# =============================================================================
_sinir = {2.9: plan_service.TYPE_YENIDOGAN, 3.1: plan_service.TYPE_BEKLEME,
          4.0: plan_service.TYPE_BEKLEME, 4.9: plan_service.TYPE_BEKLEME,
          5.1: plan_service.TYPE_EGITIM}
_sinir_hata = []
for ay, bek in _sinir.items():
    t = _icerik(int(ay * 30.44))["type"]
    if t != bek:
        _sinir_hata.append(f"{ay} ay → {t} (beklenen {bek})")
check("0) Bant sınırları: <3 yenidoğan, 3-5 bekleme, 5+ eğitim",
      not _sinir_hata, str(_sinir_hata))


# =============================================================================
# 1) egitim_baslangic — TARİH MOTORDAN, PREMATÜREDE DOĞRU
# =============================================================================
check("1a) egitim_bekleme'de egitim_baslangic VAR",
      isinstance(_bekleme.get("egitim_baslangic"), dict),
      str(_bekleme.get("egitim_baslangic")))
check("1b) egitim_plani'nda egitim_baslangic YOK (eğitim zaten başlıyor)",
      "egitim_baslangic" not in _egitim, "")

_eb = _bekleme["egitim_baslangic"]
check("1c) Alan sözleşmesi tam (alt_sinir_ay / tahmini_tarih / kalan_gun / aciklama)",
      set(_eb) >= {"alt_sinir_ay", "tahmini_tarih", "kalan_gun", "aciklama"}
      and _eb["alt_sinir_ay"] == 5 and _eb["kalan_gun"] > 0, str(_eb))

# PREMATÜRE — ölçülen 46 günlük hata burada kilitleniyor.
_p_eb = _prem["egitim_baslangic"]
_dogum = SahteBebek(PREMATURE_37[1]).birth_date
_naif = (_dogum + dtm.timedelta(days=int(5 * 30.44))).isoformat()
_dogru = yenidogan.egitim_uygunluk_tarihi(
    _dogum.isoformat(),
    parametre_uret({"bebek_ad": "P", "dogum_tarihi": _dogum.isoformat(),
                    "dogum_haftasi": 34})["yas"]["duzeltilmis_ay"])["tahmini_tarih"]
check("1d) PREMATÜRE: tarih DÜZELTİLMİŞ yaşa göre (naif doğum+5ay DEĞİL)",
      _p_eb["tahmini_tarih"] == _dogru and _p_eb["tahmini_tarih"] != _naif,
      f"motor={_p_eb['tahmini_tarih']} naif={_naif}")

_fark = (dtm.date.fromisoformat(_p_eb["tahmini_tarih"])
         - dtm.date.fromisoformat(_naif)).days
check("1e) Prematürede naif hesap belirgin biçimde ERKEN kalıyor (>30 gün)",
      _fark > 30, f"fark={_fark} gün")

# Zamanında doğanda düzeltilmiş = takvim → iki hesap yakınsar (sağlamlık kontrolü).
_z_dogum = SahteBebek(ZAMANINDA_4AY[1]).birth_date
_z_naif = (_z_dogum + dtm.timedelta(days=int(5 * 30.44))).isoformat()
check("1f) Zamanında doğanda motor ve naif hesap ±3 gün içinde",
      abs((dtm.date.fromisoformat(_eb["tahmini_tarih"])
           - dtm.date.fromisoformat(_z_naif)).days) <= 3,
      f"motor={_eb['tahmini_tarih']} naif={_z_naif}")

# PROMPT: tarih verilmeli ve modele hesap yasaklanmalı.
_param = parametre_uret({"bebek_ad": "Mira",
                         "dogum_tarihi": _z_dogum.isoformat(), "dogum_haftasi": 40,
                         "beslenme": "anne sütü", "destek": "emzirerek",
                         "oda": "kendi odası", "gece_uyanma": "3"})
_param["egitim_baslangic"] = _eb
_prompt = plan_generator._build_user_prompt(_param)
check("2a) Prompt'ta motorun tarihi AYNEN geçiyor",
      _eb["tahmini_tarih"] in _prompt, "")
check("2b) Prompt'ta kalan gün sayısı geçiyor",
      str(_eb["kalan_gun"]) in _prompt, "")
check("2c) Prompt modele TARİH HESAPLAMAYI açıkça yasaklıyor",
      "TARİH KURALI" in _prompt and "Kendin TARİH HESAPLAMA" in _prompt, "")
check("2d) Prompt prematüre uyarısını taşıyor (takvim yaşından hesap YANLIŞ)",
      "düzeltilmiş yaşa göre hesaplanmıştır" in _prompt, "")

# Eğitim planında bu blok HİÇ olmamalı (gereksiz bağlam + yanlış kip).
_e_param = parametre_uret({"bebek_ad": "E",
                           "dogum_tarihi": SahteBebek(ZAMANINDA_52[1]).birth_date.isoformat(),
                           "dogum_haftasi": 40})
# NOT: "EĞİTİM HENÜZ UYGUN DEĞİL" ifadesi 6. KURAL'da da geçiyor (her planda,
# koşullu anlatım). Bloğun kendisini ayırt eden işaret tarih başlığıdır.
_e_prompt = plan_generator._build_user_prompt(_e_param)
check("2e) Eğitim planı prompt'unda bekleme BLOĞU yok (tarih verilmiyor)",
      "EĞİTİMİN AÇILACAĞI TARİH" not in _e_prompt
      and "TARİH KURALI" not in _e_prompt, "")


# =============================================================================
# 3) night_wake_protocol EKLENMİYOR
# =============================================================================
check("3a) egitim_bekleme'de night_wake_protocol YOK (eğitim protokolü)",
      "night_wake_protocol" not in _bekleme, str(list(_bekleme)))
check("3b) Prematüre bekleme içeriğinde de YOK",
      "night_wake_protocol" not in _prem, "")
check("3c) egitim_plani'nda VAR (gerileme yok)",
      "night_wake_protocol" in _egitim, "")


# =============================================================================
# 4) plan_secimi TUTARLILIĞI
# =============================================================================
_ps = _bekleme["plan_secimi"]
check("4a) plan_secimi ÖNİZLEME işaretli",
      _ps.get("onizleme") is True, str(_ps))
check("4b) Açıklama 'bugün uygulanmaz'ı açıkça söylüyor",
      "ÖNİZLEME" in _ps.get("aciklama", "") and "bugün uygulanmaz" in _ps.get("aciklama", ""),
      _ps.get("aciklama", "")[:120])
check("4c) tip/gunler KORUNUYOR (5. ayda hangi program başlayacak görünsün)",
      _ps.get("tip") == "13_gun_dirençli" and _ps.get("gunler") == 13, str(_ps))
check("4d) egitim_plani'nda onizleme bayrağı YOK",
      "onizleme" not in _egitim["plan_secimi"], str(_egitim["plan_secimi"]))
# Motorun kendi param'ı kirlenmemeli (kopya üzerinde çalışılıyor mu?).
check("4e) parametre_uret çıktısı kirletilmiyor (kopya alınıyor)",
      "onizleme" not in (parametre_uret({
          "bebek_ad": "x", "dogum_tarihi": _z_dogum.isoformat(),
          "dogum_haftasi": 40})["plan_secimi"]), "")


# =============================================================================
# 5) MERDİVEN ÖNİZLEMESİ
# =============================================================================
check("5a) days DOLU (önizleme ayrıştırıldı)",
      len(_bekleme["days"]) == 5,
      f"{len(_bekleme['days'])} aşama")
check("5b) HER gün kaydında preview=True",
      all(d.get("preview") is True for d in _bekleme["days"]),
      str([d.get("preview") for d in _bekleme["days"]]))
check("5c) content.egitim_onizleme = True",
      _bekleme.get("egitim_onizleme") is True, str(_bekleme.get("egitim_onizleme")))
check("5d) Merdiven sınırları doğru (1-3 / 4-6 / 7-9 / 10-12 / 13)",
      [(d["start"], d["end"]) for d in _bekleme["days"]]
      == [(1, 3), (4, 6), (7, 9), (10, 12), (13, 13)],
      str([(d["start"], d["end"]) for d in _bekleme["days"]]))
check("5e) egitim_plani'nda preview bayrağı YOK (gerçek plan)",
      all("preview" not in d for d in _egitim["days"]), "")
check("5f) egitim_plani'nda egitim_onizleme YOK",
      "egitim_onizleme" not in _egitim, "")

_md = _bekleme["markdown"]
check("5g) Markdown'da GÜÇLÜ önizleme uyarısı var",
      "ÖNİZLEME" in _md and "bugün uygulanmaz" in _md.lower(),
      [s for s in _md.splitlines() if "ÖNİZLEME" in s][:2])
check("5h) Uyarı eğitimin BAŞLAYACAĞI TARİHİ söylüyor",
      _eb["tahmini_tarih"] in _md, "")
check("5i) Günlük program bölümü de VAR (asıl içerik)",
      "## Günlük Program" in _md,
      [l for l in _md.splitlines() if l.startswith("## ")])
check("5j) Çizelge dolu (saat planlaması yapılabiliyor)",
      len(_bekleme["schedule"]) >= 4, str(len(_bekleme["schedule"])))


# =============================================================================
# 6) 502 REGRESYONU GERİ GELMESİN
# =============================================================================
# Bu yolda ayrıştırma başarısız olsa BİLE PlanError YÜKSELMEMELİ: bugün
# uygulanacak bir merdiven yok, önizleme eksik kalır ama plan verilir.
_orijinal = plan_generator.plan_uret
try:
    plan_generator.plan_uret = lambda param, usage_sink=None: (
        "# Plan\n\n## Eğitim Uygunluğu\n\nUygun değil.\n\n## Eğitim Planı\n\n"
        "Burada hiç gün başlığı yok.\n")
    _bozuk = _icerik(122)
    check("6a) Önizleme ayrıştırılamasa bile PlanError YOK",
          _bozuk["type"] == plan_service.TYPE_BEKLEME, "")
    check("6b) Ayrıştırılamayan önizlemede days boş kalır (plan yine verilir)",
          _bozuk["days"] == [], str(_bozuk["days"]))
    check("6c) Bu durumda bile egitim_baslangic ve onizleme bayrağı var",
          _bozuk.get("egitim_onizleme") is True
          and isinstance(_bozuk.get("egitim_baslangic"), dict), "")
except plan_service.PlanError as e:
    check("6a) Önizleme ayrıştırılamasa bile PlanError YOK", False, f"PlanError: {e}")
finally:
    plan_generator.plan_uret = _orijinal

# Eğitim planında ise ayrıştırma başarısızlığı HÂLÂ reddedilmeli (gerileme yok).
try:
    plan_generator.plan_uret = lambda param, usage_sink=None: "# Plan\n\nGün başlığı yok.\n"
    _icerik(ZAMANINDA_52[1])
    check("6d) Eğitim planında ayrıştırma hatası HÂLÂ PlanError veriyor", False,
          "PlanError beklenirken içerik döndü")
except plan_service.PlanError:
    check("6d) Eğitim planında ayrıştırma hatası HÂLÂ PlanError veriyor", True, "")
finally:
    plan_generator.plan_uret = _orijinal


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("3-5 AY BANDI (egitim_bekleme) TEST SONUÇLARI")
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
