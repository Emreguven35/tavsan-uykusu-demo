"""
Plan seçimi — mizaç × dayanma × yaş matrisi.

NEDEN VAR: keşif raporunda çıktı ki plan seçimini doğrulayan HİÇBİR test yoktu
(mevcut testler ya dallanıyor ya kaydediyordu, assert etmiyordu). Bu yüzden
seçim mantığı sessizce değişebilirdi.

Bu dosya İKİ AŞAMALI yazıldı:
  1. Değişiklikten ÖNCE mevcut davranış (mizaç/dayanma/tercihe göre 5 vs 13)
     assert edildi ve YEŞİL geçtiği görüldü.
  2. Sonra karar sabitlendi (herkes 13 gün) ve beklentiler güncellendi.
Böylece git diff'te "ne değişti" tek bakışta görünür.

Çalıştırma: python tests/test_plan_secimi.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.parameter_engine import egitim_plani_secimi  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def yas(ay: float) -> dict:
    return {"duzeltilmis_ay": ay, "gercek_ay": ay,
            "prematüre_mi": False, "dogum_haftasi": 40}


def sec(ay: float, mizac: str = "", dayanma: str = "", tercih: str = "") -> str:
    return egitim_plani_secimi(
        {"mizac": mizac, "dayanma_siniri": dayanma, "yaklasim_tercihi": tercih},
        yas(ay))["tip"]


# =============================================================================
# 1) MİZAÇ × DAYANMA MATRİSİ  (<24 ay)
# =============================================================================
# DEĞİŞİKLİK ÖNCESİ beklenti (referans için burada duruyor):
#   sakin/boş        → 5_gun_standart  (dayanma ne olursa olsun)
#   hassas/duyarlı   → 13_gun_dirençli (dayanma ne olursa olsun)
#   inatçı/huysuz    → dayanma düşükse 13, değilse 5
# DEĞİŞİKLİK SONRASI: mizaç ve dayanma plan süresini ETKİLEMEZ → hepsi 13.
MIZACLAR = ["sakin", "hassas", "duyarlı", "inatçı", "huysuz", "zor", "kararlı", ""]
DAYANMALAR = ["30-45 dakika", "10-20 dakika", "hiç dayanamıyorum",
              "45-60 dakika", "az", ""]

_matris_hata = []
for _m in MIZACLAR:
    for _d in DAYANMALAR:
        t = sec(8, _m, _d)
        if t != "13_gun_dirençli":
            _matris_hata.append(f"mizaç={_m!r} dayanma={_d!r} → {t}")
check(f"1) Mizaç × dayanma matrisi ({len(MIZACLAR)}×{len(DAYANMALAR)}="
      f"{len(MIZACLAR) * len(DAYANMALAR)} kombinasyon) HEPSİ 13 günlük",
      not _matris_hata, "\n       ".join(_matris_hata[:8]))

check("1b) Mizaç artık plan süresini ETKİLEMİYOR",
      len({sec(8, m, "30-45 dakika") for m in MIZACLAR}) == 1,
      str({m: sec(8, m, "30-45 dakika") for m in MIZACLAR}))

check("1c) Dayanma sınırı artık plan süresini ETKİLEMİYOR",
      len({sec(8, "inatçı", d) for d in DAYANMALAR}) == 1,
      str({d: sec(8, "inatçı", d) for d in DAYANMALAR}))


# =============================================================================
# 2) YAKLAŞIM TERCİHİ ARTIK YOK SAYILIYOR
# =============================================================================
# Alan SİLİNMEDİ (geriye uyumluluk: eski profiller/DB kayıtları taşıyor olabilir),
# ama motor plan süresini buradan BELİRLEMİYOR.
TERCIHLER = [
    "5 günlük standart plan (daha hızlı, daha çok ağlama olabilir)",
    "13 günlük kademeli plan (daha yumuşak, daha uzun süreç)",
    "hızlı olsun", "standart", "yumuşak", "kademeli", "uzun",
    "1 aylık program (ilk 2 hafta destekle uyku)",
    "İlayda Hanım'ın önereceği plan (bebeğe göre)",
    "",
]
_tercih_hata = [f"{t!r} → {sec(8, 'sakin', '30-45 dakika', t)}"
                for t in TERCIHLER
                if sec(8, "sakin", "30-45 dakika", t) != "13_gun_dirençli"]
check("2) Yaklaşım tercihi plan süresini ETKİLEMİYOR (10 farklı ifade)",
      not _tercih_hata, "\n       ".join(_tercih_hata))

check("2b) '5 günlük standart' açıkça seçilse bile 13 günlük veriliyor",
      sec(8, "sakin", "45 dakika",
          "5 günlük standart plan (daha hızlı)") == "13_gun_dirençli", "")

check("2c) Profilde yaklasim_tercihi HİÇ OLMASA da çalışıyor "
      "(alan opsiyonel, KeyError yok)",
      egitim_plani_secimi({"mizac": "sakin"}, yas(8))["tip"] == "13_gun_dirençli",
      "")


# =============================================================================
# 3) YAŞ SINIRLARI
# =============================================================================
_yas_hata = []
for _ay in [5, 5.1, 6, 8, 11.9, 12, 15, 18, 23.9]:
    t = sec(_ay, "sakin", "45 dakika")
    if t != "13_gun_dirençli":
        _yas_hata.append(f"{_ay} ay → {t}")
check("3) 24 ay ALTINDAKİ her yaş 13 günlük plan alıyor",
      not _yas_hata, str(_yas_hata))

# 24+ ay istisnası KORUNUYOR (İlayda onayı bekleniyor — koda TODO düşüldü).
_buyuk_hata = [f"{ay} ay → {sec(ay, 'sakin', '45 dakika')}"
               for ay in [24, 24.1, 30, 36, 48]
               if sec(ay, "sakin", "45 dakika") != "6_gun_buyuk_cocuk"]
check("3b) 24+ ay istisnası korunuyor → 6_gun_buyuk_cocuk",
      not _buyuk_hata, str(_buyuk_hata))

check("3c) 24 ay sınırı KESİN (23.9 → 13 gün, 24.0 → büyük çocuk)",
      sec(23.9) == "13_gun_dirençli" and sec(24.0) == "6_gun_buyuk_cocuk",
      f"23.9={sec(23.9)} 24.0={sec(24.0)}")

check("3d) 24+ ayda mizaç/dayanma/tercih de ETKİSİZ",
      len({sec(30, m, d, t) for m in ("hassas", "sakin")
           for d in ("hiç", "45 dakika") for t in ("5 günlük", "")}) == 1, "")


# =============================================================================
# 4) 1 AYLIK PROGRAM — bayrakla devre dışı
# =============================================================================
from engine import parameter_engine as pe                        # noqa: E402

check("4) 1_ay_program bayrağı var ve KAPALI",
      hasattr(pe, "BIR_AY_PROGRAM_AKTIF") and pe.BIR_AY_PROGRAM_AKTIF is False,
      str(getattr(pe, "BIR_AY_PROGRAM_AKTIF", "bayrak yok")))

check("4b) Bayrak kapalıyken '1 aylık program' tercihi 13 günlük veriyor",
      sec(8, "sakin", "45 dakika",
          "1 aylık program (ilk 2 hafta destekle uyku)") == "13_gun_dirençli", "")

# Kod SİLİNMEDİ: bayrak açılırsa 1 aylık program yine üretilebilmeli.
pe.BIR_AY_PROGRAM_AKTIF = True
try:
    _bir_ay = egitim_plani_secimi(
        {"mizac": "sakin", "dayanma_siniri": "45 dakika",
         "yaklasim_tercihi": "1 aylık program"}, yas(8))
    check("4c) Bayrak AÇILIRSA 1_ay_program yeniden üretilebiliyor (kod duruyor)",
          _bir_ay["tip"] == "1_ay_program" and _bir_ay["gunler"] == 28,
          str(_bir_ay.get("tip")))
    check("4d) 1 aylık programın 3-4. hafta alt yöntemi 13 güne SABİT",
          _bir_ay.get("alt_yontem_tip") == "13_gun_dirençli"
          and _bir_ay.get("alt_yontem_gunler") == 13,
          f"{_bir_ay.get('alt_yontem_tip')} / {_bir_ay.get('alt_yontem_gunler')}")
finally:
    pe.BIR_AY_PROGRAM_AKTIF = False          # bayrağı geri kapat


# =============================================================================
# 5) PLAN İÇERİĞİ — merdiven değişmedi
# =============================================================================
_p = egitim_plani_secimi({"mizac": "sakin"}, yas(8))
check("5) 13 günlük planın gün sayısı 13",
      _p["gunler"] == 13, str(_p["gunler"]))

from engine.parameter_engine import bekleme_sureleri_planla       # noqa: E402

_b = bekleme_sureleri_planla("13_gun_dirençli")["kademeli_uzaklasma"]
_BEKLENEN_MERDIVEN = {
    "gun_1_3": "Beşik yanı", "gun_4_6": "Oda ortası", "gun_7_9": "Kapı",
    "gun_10_12": "Kapı eşiği", "gun_13": "Yatır-çık",
}
_merdiven_hata = [k for k, bas in _BEKLENEN_MERDIVEN.items()
                  if k not in _b or not _b[k].startswith(bas)]
check("5b) Merdiven kayıttaki eşlemeyle aynı "
      "(1-3 beşik yanı, 4-6 oda ortası, 7-9 kapı, 10-12 eşik, 13 yatır-çık)",
      not _merdiven_hata, f"sorunlu={_merdiven_hata} gercek={_b}")


# =============================================================================
# 6) KURAL KB'DEN OKUNUYOR
# =============================================================================
from engine.parameter_engine import load_kb                       # noqa: E402

_kb = load_kb()
_kural = _kb.get("global_rules", {}).get("egitim_plani_secimi")
check("6) KB'de global_rules.egitim_plani_secimi kaydı var",
      isinstance(_kural, dict), str(type(_kural)))

check("6b) KB varsayılan planı 13 günlük",
      (_kural or {}).get("varsayilan_plan", {}).get("tip") == "13_gun_dirençli"
      and (_kural or {}).get("varsayilan_plan", {}).get("gunler") == 13,
      str((_kural or {}).get("varsayilan_plan")))

check("6c) KB 24+ ay istisnasını kaydediyor",
      any(i.get("plan", {}).get("tip") == "6_gun_buyuk_cocuk"
          for i in (_kural or {}).get("istisnalar", [])),
      str((_kural or {}).get("istisnalar")))

# Motor GERÇEKTEN KB'den okuyor mu? KB'yi geçici değiştirip çıktının değiştiğini
# gör — sabit kodlanmış olsaydı bu test kaldırdı.
_orijinal = _kural["varsayilan_plan"]["gunler"]
try:
    import engine.parameter_engine as _pe
    _sahte = {k: v for k, v in _kb.items()}
    _sahte["global_rules"] = dict(_kb["global_rules"])
    _sahte["global_rules"]["egitim_plani_secimi"] = {
        **_kural,
        "varsayilan_plan": {**_kural["varsayilan_plan"], "gunler": 99},
    }
    _gercek_load = _pe.load_kb
    _pe.load_kb = lambda: _sahte
    _degisti = egitim_plani_secimi({"mizac": "sakin"}, yas(8))["gunler"]
finally:
    _pe.load_kb = _gercek_load
check("6d) Motor kuralı GERÇEKTEN KB'den okuyor (sabit kodlanmamış)",
      _degisti == 99, f"KB'de 99 yazıldı, motor {_degisti} döndü")


# =============================================================================
# 7) GÜN EŞLEMESİ TUTARLILIĞI — plan ile sohbet çelişmemeli
# =============================================================================
# Herkes 13 günlük programa geçince, sohbetin 5 günlük gün numaralandırmasını
# öğretmesi doğrudan çelişki üretiyordu ("3. gün oda ortası" derken planda
# 3. gün beşik yanı). Korpustan arşiv kaydı çıkarıldı + prompt'a açık gün
# listesi konuldu. Buradaki kontroller DETERMİNİSTİK (LLM çağrısı yok).
from engine import chatbot as _cb                                 # noqa: E402

check("7) Arşiv kayıtları korpusa GİRMİYOR (uygulanmayan kural aranamaz)",
      not [u for u in _cb.build_corpus() if "ARSIV" in u["chunk_id"].upper()], "")

_kb_gr = load_kb()["global_rules"]
check("7b) 5 günlük merdiven KB'de ARŞİV olarak işaretli (silinmedi)",
      any("ARSIV" in k.upper() and "5_gun" in k for k in _kb_gr),
      str([k for k in _kb_gr if "kademeli" in k]))

check("7c) Geçerli merdiven 13 günlük ve KB'de işaretli",
      "_durum" in _kb_gr.get("kademeli_uzaklasma_13_gun_dirençli", {}), "")

# Prompt gün gün açık liste taşımalı: aralık ("1-3. gün") sınır ucunda
# yanlış yorumlanıyordu, tek tek yazılınca düzeldi.
_sp = _cb.SYSTEM_PROMPT
_eksik_gun = [g for g in range(1, 14) if f"{g}. gün" not in _sp]
check("7d) Sistem promptu 13 günün TAMAMINI tek tek listeliyor",
      not _eksik_gun, f"eksik günler={_eksik_gun}")

check("7e) Prompt eski 5 günlük numaralandırmayı taşımamayı söylüyor",
      "ESKİ programa aittir" in _sp and "TAŞIMA" in _sp, "")


# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("PLAN SEÇİMİ TEST SONUÇLARI")
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
