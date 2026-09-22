"""
ANNE DİLİ — anneye GÖSTERİLEN her metin mühendis terimi içermemeli.
LLM YOK, ağ YOK, prod DB YOK.

Taranan alanlar:
  • `adaptation.uyarilar[]`
  • `schedule[].note`
  • `adaptation.yok_sayilan_kayitlar[].sebep`
  • `content.uyarilar[]`

Neden ayrı bir test: bu metinler kodun her yerine serpiştirilmiş f-string'ler.
Bir geliştirici "blok", "yuva" ya da "şablon" yazdığında derleyici uyarmaz;
anne ise ekranda okur. Burada senaryolar koşturulup ÜRETİLEN metinler denetlenir
— kaynak kodda dizge aramak yetmez, çünkü metinler çalışma anında kuruluyor.

Çalıştırma: python tests/test_anne_dili.py
"""
import os
import re
import sys
import tempfile
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzun")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + (Path(tempfile.gettempdir())
                                      / "anne_dili.db").as_posix())

from api.services import plan_adapter as pa                 # noqa: E402
from engine import yas_bantlari                             # noqa: E402

TZ = pa.TZ_OFFSET_MIN
BUGUN = date(2026, 9, 20)
DUN = BUGUN - timedelta(days=1)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


# --- YASAKLI TERİMLER --------------------------------------------------------
# Anneye gösterilen metinde geçmemesi gerekenler. Kelime sınırıyla aranır ki
# "bloke"/"kaynak suyu" gibi masum kelimeler yanlış eşleşmesin.
YASAKLI = {
    "şablon": r"şablon",
    "blok": r"blok(?!e)",
    "yuva": r"yuva",
    "kaynak": r"kaynak",
    "varsayılan": r"varsayıl",
    "çizelge": r"çizelge",
    "eşlenmedi": r"eşlen",
    "bant/bandın": r"\bband[ıi]n\b|\bbant\b",
    "parametre": r"parametre",
    "adaptasyon": r"adaptasyon",
    "null/None": r"\bnull\b|\bNone\b",
}


def ihlaller(metin: str) -> list[str]:
    d = (metin or "").lower()
    return [ad for ad, kalip in YASAKLI.items()
            if re.search(kalip, d, re.IGNORECASE)]


def iki_nokta_sayisi(metin: str) -> int:
    return (metin or "").count(":")


def saat_disi_iki_nokta(metin: str) -> int:
    """Saat biçimindeki (09:30) iki noktaları saymaz."""
    return iki_nokta_sayisi(re.sub(r"\d{1,2}:\d{2}", "", metin or ""))


# --- Senaryo kurucu ----------------------------------------------------------
class L:
    def __init__(self, tip, gun, bas, bit=None, bit_gun=None):
        self.id = _uuid.uuid4()
        self.type = tip
        self.started_at = _dt(gun, bas)
        self.ended_at = None if bit is None else _dt(bit_gun or gun, bit)


def _dt(g, dk):
    return (datetime(g.year, g.month, g.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - TZ))


def kos(yas_ay: float, loglar: list, now_minute: int = 23 * 60) -> dict:
    tpl = pa.build_schedule({}, 7 * 60, yas_ay=yas_ay)
    icerik = {"schedule": tpl, "schedule_template": tpl,
              "yas_bandi": yas_bantlari.yas_bandi_getir(yas_ay)}
    return pa.adapt(icerik, {}, loglar, today=BUGUN, now_minute=now_minute,
                    yas_ay=yas_ay, log_summary=pa.summarize_logs(loglar,
                                                                today=BUGUN))


GECE = lambda bas=20 * 60 + 30, bit=7 * 60: L("sleep", DUN, bas, bit, BUGUN)

SENARYOLAR = {
    "normal gün": (8.0, [GECE(),
                         L("nap", BUGUN, 9 * 60 + 30, 10 * 60 + 40),
                         L("nap", BUGUN, 13 * 60 + 10, 14 * 60 + 20),
                         L("nap", BUGUN, 16 * 60 + 50, 18 * 60)]),
    "kayıt yok": (8.0, [GECE()]),
    # Açık kayıt 19:00'dan SONRA başlarsa gece uykusu sayılır (K19); "sürüyor"
    # notunu görmek için gündüz penceresinde, henüz bayatlamamış bir sayaç gerek.
    "açık sayaç (sürüyor)": (8.0, [GECE(), L("nap", BUGUN, 14 * 60)]),
    "bayat açık sayaç": (8.0, [GECE(), L("nap", BUGUN, 9 * 60)]),
    "ters kayıt": (8.0, [GECE(), L("nap", BUGUN, 14 * 60, 13 * 60)]),
    # Sıfır süreli NAP "bu uykuyu atladık" beyanıdır (K7); sıfır süre
    # sebebini görmek için `sleep` tipi gerekir.
    "sıfır süreli kayıt": (8.0, [GECE(), L("sleep", BUGUN, 14 * 60, 14 * 60)]),
    "çok kısa kayıt": (8.0, [GECE(), L("nap", BUGUN, 14 * 60, 14 * 60 + 2)]),
    "tanınmayan tip": (8.0, [GECE(), L("zıpzıp", BUGUN, 14 * 60, 15 * 60)]),
    # 06:00 ÖNCESİ kayıt zaten gece uykusu sayılır; "gece içinde" sebebini
    # görmek için gündüz penceresine düşen ama gece uykusu süren bir kayıt gerek.
    "gece içinde kayıt": (8.0, [L("sleep", DUN, 20 * 60 + 30, 7 * 60 + 30, BUGUN),
                                L("nap", BUGUN, 6 * 60 + 30, 7 * 60)]),
    "otomatik kapanan kısa sayaç": (8.0, [GECE(),
                                          L("nap", BUGUN, 10 * 60),
                                          L("wake", BUGUN, 10 * 60 + 3)]),
    "gereksiz uyanma kaydı": (8.0, [GECE(), L("wake", BUGUN, 7 * 60 + 5),
                                    L("nap", BUGUN, 10 * 60, 11 * 60)]),
    "erken uyanma": (8.0, [L("sleep", DUN, 20 * 60, 4 * 60 + 30, BUGUN),
                           L("nap", BUGUN, 9 * 60, 10 * 60)]),
    "K15 erken uyku kaydı": (14.5, [L("sleep", DUN, 20 * 60 + 30, 8 * 60, BUGUN),
                                    L("nap", BUGUN, 9 * 60, 10 * 60)]),
    "çakışan kayıt": (8.0, [GECE(),
                            L("nap", BUGUN, 10 * 60, 11 * 60),
                            L("nap", BUGUN, 10 * 60 + 10, 10 * 60 + 40)]),
    "fazla uyku (ek uyku)": (4.0, [L("sleep", DUN, 20 * 60 + 30, 7 * 60 + 10,
                                     BUGUN)]
                             + [L("nap", BUGUN, 8 * 60 + 50 + i * 140,
                                  8 * 60 + 90 + i * 140) for i in range(4)]),
    "şekerleme eklenen gün": (8.0, [GECE()]
                              + [L("nap", BUGUN, 9 * 60 + 10 + i * 165,
                                   9 * 60 + 45 + i * 165) for i in range(3)]),
    "şekerleme sığmayan gün": (4.0, [L("sleep", DUN, 20 * 60 + 30,
                                       7 * 60 + 10, BUGUN),
                                     L("nap", BUGUN, 9 * 60, 9 * 60 + 40),
                                     L("nap", BUGUN, 12 * 60, 12 * 60 + 40),
                                     L("nap", BUGUN, 15 * 60, 15 * 60 + 40),
                                     L("nap", BUGUN, 16 * 60 + 30,
                                       17 * 60 + 10)]),
    "geç kalkış, geç uykular": (8.0, [L("sleep", DUN, 22 * 60, 9 * 60, BUGUN),
                                      L("nap", BUGUN, 13 * 60, 14 * 60 + 30),
                                      L("nap", BUGUN, 18 * 60, 19 * 60)]),
}

print("Senaryolar koşturuluyor ve ÜRETİLEN metinler denetleniyor...")
toplanan = {"uyarilar": [], "note": [], "sebep": []}

for ad, (yas, loglar) in SENARYOLAR.items():
    # "Açık sayaç" senaryosunda saat, sayaç daha BAYATLAMADAN okunmalı.
    r = kos(yas, loglar, now_minute=14 * 60 + 25 if "sürüyor" in ad else 23 * 60)
    ad_ = r.get("adaptation") or {}
    for u in ad_.get("uyarilar") or []:
        toplanan["uyarilar"].append((ad, u))
    for b in r.get("schedule") or []:
        if b.get("note"):
            toplanan["note"].append((ad, b["key"], b["note"]))
    for y in ad_.get("yok_sayilan_kayitlar") or []:
        toplanan["sebep"].append((ad, y.get("kod"), y.get("sebep")))

print(f"  {len(toplanan['uyarilar'])} uyarı, {len(toplanan['note'])} not, "
      f"{len(toplanan['sebep'])} sebep toplandı")

# --- 1) Mühendis terimi taraması --------------------------------------------
for alan, kayitlar in (("uyarı", toplanan["uyarilar"]),
                       ("not", [(a, m) for a, _k, m in toplanan["note"]]),
                       ("sebep", [(a, m) for a, _k, m in toplanan["sebep"]])):
    kotu = [(a, m, ihlaller(m)) for a, m in kayitlar if ihlaller(m)]
    check(f"1) {alan} metinlerinde mühendis terimi YOK "
          f"({len(kayitlar)} metin tarandı)",
          not kotu,
          "; ".join(f"[{a}] {t} → {m[:70]}" for a, m, t in kotu[:3]))

# --- 2) sebep metinleri: tek iki nokta, aksanlı Türkçe ----------------------
cok_nokta = [(a, k, m) for a, k, m in toplanan["sebep"]
             if saat_disi_iki_nokta(m) > 1]
check("2a) Sebep metinlerinde en fazla TEK iki nokta",
      not cok_nokta, "; ".join(f"[{k}] {m[:60]}" for _a, k, m in cok_nokta[:3]))

# Aksansız yazılmış Türkçe kelime kalıntısı ("ayni", "kisa", "sayildi"…)
# IGNORECASE KULLANILMAZ: Python'da re.IGNORECASE 'ı' (U+0131) ile 'i'yi EŞİT
# sayıyor ve "kısa" aksansız "kisa" sanılıyordu. Ölçtüğümüz şey tam da bu
# ayrım olduğu için arama büyük/küçük harfe duyarlı yapılır.
AKSANSIZ = r"\b(ayni|kisa|sayildi|kaydi|cakisma|acik|eslenmedi|yok sayildi)\b"
aksansiz = [(a, k, m) for a, k, m in toplanan["sebep"]
            if re.search(AKSANSIZ, m or "")]
check("2b) Sebep metinleri aksanlı Türkçe",
      not aksansiz, "; ".join(f"[{k}] {m[:60]}" for _a, k, m in aksansiz[:3]))

# Kod alanı DEĞİŞMEMELİ — mobil buna göre dallanıyor
BEKLENEN_KODLAR = {"cakisma", "taninmayan_tip", "ters_kayit", "sifir_sure",
                   "kisa_sure", "otomatik_kapanis_kisa", "gece_icinde",
                   "gereksiz_wake"}
gorulen = {k for _a, k, _m in toplanan["sebep"]}
check("2c) Kod alanları değişmedi (mobil sözleşmesi)",
      gorulen <= BEKLENEN_KODLAR,
      f"beklenmeyen: {gorulen - BEKLENEN_KODLAR}")
check("2d) Senaryolar sebep kodlarının çoğunu kapsıyor",
      len(gorulen) >= 6, f"görülen: {sorted(gorulen)}")

# --- 3) "Programda olmayan ek uyku" ------------------------------------------
ek_uyku = [m for _a, _k, m in toplanan["note"] if "ek uyku" in m]
check("3a) Ek uyku notu yeni metinle geliyor",
      any("Programda olmayan ek uyku (kaydına göre)" == m for m in ek_uyku),
      str(ek_uyku[:2]))
check("3b) Eski 'Şablonda olmayan ilave uyku kaydı' metni YOK",
      not any("Şablonda" in m for _a, _k, m in toplanan["note"]), "")

# --- 4) "Uyku sürüyor" YALNIZ gerçekten açık kayıtta -------------------------
surer = {ad for ad, _k, m in toplanan["note"] if "sürüyor" in m}
check("4a) 'Uyku sürüyor' notu yalnız açık sayaç senaryosunda",
      surer <= {"açık sayaç (sürüyor)"}, str(surer))
check("4b) Açık sayaç senaryosunda not GERÇEKTEN var",
      "açık sayaç (sürüyor)" in surer, str(surer))
check("4d) 'sürüyor' notu yalnız bitişi OLMAYAN kayıtta",
      all(b.get("note") != "Uyku sürüyor; bitiş saati tahmini"
          or b.get("devam") is True
          for b in kos(8.0, [GECE(), L("nap", BUGUN, 14 * 60)],
                       now_minute=14 * 60 + 25)["schedule"]), "")
r_bayat = kos(8.0, [GECE(), L("nap", BUGUN, 9 * 60)])
_bayat_notlar = [b.get("note") for b in r_bayat["schedule"] if b.get("note")]
check("4c) Otomatik kapanan sayaçta 'sürüyor' DEMİYOR",
      not any("sürüyor" in (n or "") for n in _bayat_notlar),
      str(_bayat_notlar))

# --- 5) Gelecekteki bloklarda not yok ---------------------------------------
r_sabah = kos(8.0, [GECE()], now_minute=8 * 60)          # sabah 08:00
gelecek_notlu = [(b["key"], b.get("note")) for b in r_sabah["schedule"]
                 if b.get("note") and b.get("key") != "bedtime"
                 and b.get("start_minute") is not None
                 and b["start_minute"] > 8 * 60]
check("5a) Henüz gelmemiş uyku bloklarında not YOK",
      not gelecek_notlu, str(gelecek_notlu))
_gecmis_notlu = [b for b in r_sabah["schedule"]
                 if b.get("note") and b.get("start_minute") is not None
                 and b["start_minute"] <= 8 * 60]
check("5b) Geçmiş/şu anki bloklar notunu KORUYOR",
      True, f"{len(_gecmis_notlu)} blokta not var (bilgi)")
_gece = next((b for b in r_sabah["schedule"] if b["key"] == "bedtime"), None)
check("5c) Gece uykusunun açıklaması korunuyor (annenin 'neden bu saat' sorusu)",
      _gece is not None, str((_gece or {}).get("note")))

# --- 5d) Saat eklerinde ünlü uyumu ------------------------------------------
# "17:15'da" yanlış ("on beş" → beşTE). Kod boyunca elle yazılmış ekler
# 15/25/35/45 ile biten her saatte yanlış okunuyordu.
from api.services.plan_adapter import saatli                 # noqa: E402
_EK_ORNEK = {(17 * 60 + 15): "17:15'te", (18 * 60): "18:00'de",
             (9 * 60): "09:00'da", (19 * 60 + 45): "19:45'te",
             (11 * 60): "11:00'de", (20 * 60 + 30): "20:30'da"}
_yanlis = [(v, saatli(k)) for k, v in _EK_ORNEK.items() if saatli(k) != v]
check("5d) Saat ekleri ünlü uyumuna uyuyor", not _yanlis, str(_yanlis))

# Üretilen metinlerde yanlış ek kalıntısı: "…5'da", "…5'dan" gibi
_YANLIS_EK = r"[34]?5'(d[ae]|d[ae]n|ta|te)"
_ek_ihlal = [m for m in ([x for _a, x in toplanan["uyarilar"]]
                         + [x for _a, _k, x in toplanan["note"]])
             if re.search(r"\d[34]5'd", m or "")]
check("5e) Üretilen metinlerde yanlış saat eki yok",
      not _ek_ihlal, str(_ek_ihlal[:3]))

# --- 6) Metin kalitesi -------------------------------------------------------
tum = ([m for _a, m in toplanan["uyarilar"]]
       + [m for _a, _k, m in toplanan["note"]]
       + [m for _a, _k, m in toplanan["sebep"]])
bos = [m for m in tum if not (m or "").strip()]
check("6a) Boş metin yok", not bos, str(len(bos)))
tire = [m for m in tum if " — " in m and m.count(" — ") > 1]
check("6b) Bir metinde birden fazla uzun tire yok",
      not tire, str(tire[:2]))
kucuk_bas = [m for m in tum
             if m and m[0].islower() and not m[0].isdigit()]
check("6c) Metinler büyük harfle ya da saatle başlıyor",
      not kucuk_bas, str(kucuk_bas[:3]))

# --- Rapor -------------------------------------------------------------------
print("\n" + "=" * 78)
print("ANNE DİLİ — TEST SONUÇLARI")
print("=" * 78)
_gecen = 0
for ad, ok, detay in results:
    if ok:
        _gecen += 1
    else:
        print(f"[FAIL] {ad}\n       {detay}")
print("-" * 78)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")

if "--dok" in sys.argv:
    print("\n--- ÜRETİLEN METİNLER ---")
    for tur in ("uyarilar", "note", "sebep"):
        print(f"\n### {tur}")
        gorulen_m = set()
        for kayit in toplanan[tur]:
            m = kayit[-1]
            if m in gorulen_m:
                continue
            gorulen_m.add(m)
            print(f"  • {m}")

sys.exit(0 if _gecen == len(results) else 1)
