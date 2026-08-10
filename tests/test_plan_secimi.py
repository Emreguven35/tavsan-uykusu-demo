"""
Plan seçimi — mizaç × dayanma × yaş matrisi.

NEDEN VAR: keşif raporunda çıktı ki plan seçimini doğrulayan HİÇBİR test yoktu
(mevcut testler ya `tip`'e göre dallanıyor ya sonucu JSON'a kaydediyordu, assert
etmiyordu). Seçim mantığı sessizce değişebilirdi.

Bu dosya MEVCUT DAVRANIŞI sabitler. Karar değiştiğinde bu beklentiler
güncellenecek — böylece git diff'te tam olarak neyin değiştiği görünür.

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


D13 = "13_gun_dirençli"
D5 = "5_gun_standart"

# =============================================================================
# 1) MİZAÇ × DAYANMA MATRİSİ  (<24 ay, tercih belirtilmemiş)
# =============================================================================
#   sakin/boş      → 5   (dayanma ne olursa olsun)
#   hassas/duyarlı → 13  (dayanma ne olursa olsun)
#   inatçı ailesi  → dayanma DÜŞÜKSE 13, değilse 5
INATCI = ("inatçı", "huysuz", "zor", "kararlı")
HASSAS = ("hassas", "duyarlı")
NOTR = ("sakin", "")
DUSUK = ("10-20 dakika", "hiç dayanamıyorum", "15 dakika", "20 dakika")
YUKSEK = ("30-45 dakika", "45-60 dakika", "")


def beklenen(mizac: str, dayanma: str) -> str:
    if mizac in HASSAS:
        return D13
    if mizac in INATCI and dayanma in DUSUK:
        return D13
    return D5


_hata = []
for _m in NOTR + HASSAS + INATCI:
    for _d in DUSUK + YUKSEK:
        _bek, _ger = beklenen(_m, _d), sec(8, _m, _d)
        if _bek != _ger:
            _hata.append(f"mizaç={_m!r} dayanma={_d!r}: beklenen {_bek}, gelen {_ger}")
_toplam = len(NOTR + HASSAS + INATCI) * len(DUSUK + YUKSEK)
check(f"1) Mizaç × dayanma matrisi ({_toplam} kombinasyon)",
      not _hata, "\n       ".join(_hata[:8]))

check("1b) Hassas mizaç dayanmaya BAKMADAN 13 günlük",
      all(sec(8, m, d) == D13 for m in HASSAS for d in DUSUK + YUKSEK), "")
check("1c) Sakin mizaç dayanmaya BAKMADAN 5 günlük",
      all(sec(8, m, d) == D5 for m in NOTR for d in DUSUK + YUKSEK), "")
check("1d) İnatçı mizaç: düşük dayanma → 13, yüksek dayanma → 5",
      all(sec(8, m, d) == D13 for m in INATCI for d in DUSUK)
      and all(sec(8, m, d) == D5 for m in INATCI for d in YUKSEK), "")


# =============================================================================
# 2) YAKLAŞIM TERCİHİ — mizaç/dayanmayı EZİYOR
# =============================================================================
check("2) Tercih '13 günlük kademeli' → sakin mizaçta bile 13",
      sec(8, "sakin", "45 dakika", "13 günlük kademeli plan") == D13, "")
check("2b) Tercih '5 günlük standart' → hassas mizaçta bile 5",
      sec(8, "hassas", "10 dakika", "5 günlük standart plan") == D5, "")
check("2c) Tercih 'yumuşak'/'kademeli'/'uzun' → 13",
      all(sec(8, "sakin", "45 dakika", t) == D13
          for t in ("yumuşak", "kademeli", "uzun")), "")
check("2d) Tercih 'hızlı'/'standart' → 5",
      all(sec(8, "hassas", "10 dakika", t) == D5
          for t in ("hızlı", "standart")), "")
check("2e) Tercih '1 aylık program' → 1_ay_program (28 gün)",
      sec(8, "sakin", "45 dakika", "1 aylık program") == "1_ay_program", "")


# =============================================================================
# 3) YAŞ SINIRLARI
# =============================================================================
check("3) 24+ ay → 6_gun_buyuk_cocuk (mizaç/dayanma/tercih ETKİSİZ)",
      all(sec(ay, m, d, t) == "6_gun_buyuk_cocuk"
          for ay in (24, 30, 36) for m in ("hassas", "sakin")
          for d in ("hiç", "45 dakika") for t in ("5 günlük", "")), "")
check("3b) 24 ay sınırı kesin (23.9 → 24 ay altı davranışı)",
      sec(23.9, "sakin", "45 dakika") == D5
      and sec(24.0, "sakin", "45 dakika") == "6_gun_buyuk_cocuk", "")


# =============================================================================
# 4) ALT-DİZE EŞLEŞME HATASI (keşifte bulundu — mevcut davranış)
# =============================================================================
# "biraz" içinde "az", "60-100 dakika" içinde "10" geçtiği için yüksek toleranslı
# anne DÜŞÜK tolerans sayılıyor. Bu testler HATAYI BELGELER; düzeltilince
# beklentiler tersine çevrilecek.
check("4) HATA: 'biraz dayanabiliyorum' düşük tolerans sayılıyor",
      sec(8, "inatçı", "biraz dayanabiliyorum") == D13,
      "hata düzeltilirse burası 5 olmalı")
check("4b) HATA: '60-100 dakika' düşük tolerans sayılıyor",
      sec(8, "inatçı", "60-100 dakika") == D13,
      "hata düzeltilirse burası 5 olmalı")
check("4c) Kontrol: 'uzun süre dayanırım' doğru şekilde YÜKSEK sayılıyor",
      sec(8, "inatçı", "uzun süre dayanırım") == D5, "")


# =============================================================================
# 5) PLAN İÇERİĞİ — merdiven
# =============================================================================
from engine.parameter_engine import bekleme_sureleri_planla       # noqa: E402

_b = bekleme_sureleri_planla(D13)["kademeli_uzaklasma"]
_BEKLENEN_MERDIVEN = {
    "gun_1_3": "Beşik yanı", "gun_4_6": "Oda ortası", "gun_7_9": "Kapı",
    "gun_10_12": "Kapı eşiği", "gun_13": "Yatır-çık",
}
_merdiven_hata = [k for k, bas in _BEKLENEN_MERDIVEN.items()
                  if k not in _b or not _b[k].startswith(bas)]
check("5) 13 günlük merdiven: 1-3 beşik yanı, 4-6 oda ortası, 7-9 kapı, "
      "10-12 eşik, 13 yatır-çık",
      not _merdiven_hata, f"sorunlu={_merdiven_hata}")
check("5b) Gün sayısı 13",
      egitim_plani_secimi({"mizac": "hassas"}, yas(8))["gunler"] == 13, "")


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
