"""
Yaş bandı tablosu testleri (Faz Y) — LLM/DB/ağ YOK, tamamen deterministik.

Kapsam:
  1. ARA YAŞ KALMASIN — 0-36 ay arasındaki HER ay bir banda düşer, tüm sayısal
     alanları doludur ve çizelge üretilebilir.
  2. İlayda tablosunun değerleri BİREBİR — her bant tek tek doğrulanır.
  3. Çizelge kurucu — çizelge tablodan kurulur, gece yatışı gece uykusu
     aralığına oturur, uyanıklık penceresi bant aralığındadır.
  4. 12-18 ay tek/çift uyku ayrımı + ÜÇ geçiş şartı (şüphede 2 uyku).
  5. Evrensel kestirme kuralı — tetiklenme senaryoları.
  6. 24-36 ay öğlen uykusu reddi protokolü.
  7. plan_adapter entegrasyonu — build_schedule/adapt tablodan beslenir,
     KB yolu geriye uyumlu kalır.
  8. Chat retrieval köprüsü — "9 aylık" sorusu boşluğa düşmez.

Çalıştırma: python tests/test_yas_bantlari.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.services import plan_adapter as pa      # noqa: E402
from engine import yas_bantlari as yb            # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


S = 60          # 1 saat = 60 dk (okunurluk için)


# =============================================================================
# 1) ARA YAŞ KALMASIN — 0-36 ay tam kapsam
# =============================================================================
_ZORUNLU = ("uyaniklik_penceresi_dk", "gunduz_uyku_sayisi",
            "gunduz_uyku_toplam_dk", "gece_uykusu_dk")

_eksik: list[str] = []
_cizelgesiz: list[str] = []
_bulunan_bantlar: set[str] = set()
for _ay in range(0, 37):
    b = yb.yas_bandi_getir(_ay)
    _bulunan_bantlar.add(b["id"])
    for alan in _ZORUNLU:
        v = b.get(alan)
        if not v or v[0] is None:
            _eksik.append(f"{_ay} ay/{alan}")
    sch = pa.build_schedule({}, 7 * S, yas_ay=_ay)
    if not sch or not any(x["type"] == "nap" for x in sch):
        _cizelgesiz.append(f"{_ay} ay")

check("1) 0-36 ay: her ay bir banda düşer, sayısal alanları dolu",
      not _eksik, f"eksik={_eksik}")
check("1b) 0-36 ay: her ay için çizelge üretilebilir",
      not _cizelgesiz, f"çizelgesiz={_cizelgesiz}")
check("1c) Tablonun 7 bandının hepsi 0-36 aralığında kullanılıyor",
      _bulunan_bantlar == set(yb.bant_idleri()),
      f"bulunan={sorted(_bulunan_bantlar)} tablo={sorted(yb.bant_idleri())}")

# Yarım aylar ve negatif/aşırı değerler de banda düşmeli (çökmemeli).
_ara = [yb.yas_bandi_getir(a)["id"] for a in (0.0, 2.9, 5.5, 8.99, 11.5, 17.9,
                                              23.5, 35.9, 36.0, 48.0, -1.0)]
check("1d) Ara/uç yaşlar (0.0, 2.9, 8.99, 36, 48, -1) banda düşer",
      all(x in yb.bant_idleri() for x in _ara), str(_ara))

# Bantlar ÇAKIŞMAZ ve boşluk bırakmaz.
_siniri_kontrol = []
for b in yb.tablo()["bantlar"]:
    alt = yb.yas_bandi_getir(b["ay_min"])["id"]
    if alt != b["id"]:
        _siniri_kontrol.append(f"{b['id']} alt sınırı {alt}'e düştü")
check("1e) Bant alt sınırları kendi bandına düşer (çakışma yok)",
      not _siniri_kontrol, str(_siniri_kontrol))


# =============================================================================
# 2) İLAYDA TABLOSU — bant bant birebir doğrulama
# =============================================================================
# İlayda'nın RESMİ tablosu (v1.1). (ay, ww, uyku_sayisi, gunduz_toplam, gece, toplam)
BEKLENEN = [
    (1,  [40, 80],    [4, 5], [300, 420],  [480, 600], [900, 1080]),  # 0-2 ay  15-18s
    (4,  [90, 135],   [3, 4], [240, 300],  [600, 660], [840, 960]),   # 3-5 ay  14-16s
    (7,  [120, 180],  [3, 3], [180, 240],  [600, 660], [840, 840]),   # 6-8 ay  14s
    (10, [180, 240],  [2, 2], [120, 180],  [600, 720], [840, 840]),   # 9-12 ay 14s
    (14, [180, 240],  [2, 2], [120, None], [660, 720], [780, 840]),   # 12-18   13-14s
    (20, [300, 360],  [1, 1], [120, None], [600, 660], [720, 780]),   # 18-24   12-13s
    (30, [330, 420],  [1, 1], [60, None],  [600, 660], [660, 750]),   # 24-36   11-12,5s
]
_hatalar = []
for ay, ww, n, gunduz, gece, toplam in BEKLENEN:
    b = yb.yas_bandi_getir(ay)
    got = (b["uyaniklik_penceresi_dk"], b["gunduz_uyku_sayisi"],
           b["gunduz_uyku_toplam_dk"], b["gece_uykusu_dk"],
           b["toplam_gunluk_uyku_dk"])
    if got != (ww, n, gunduz, gece, toplam):
        _hatalar.append(f"{ay} ay ({b['id']}): {got} != {(ww, n, gunduz, gece, toplam)}")
check("2) Tüm bantların değerleri İlayda RESMİ tablosuyla birebir aynı",
      not _hatalar, str(_hatalar))

# v1.1 düzeltmeleri tek tek (regresyon olursa hangisi bozuldu görülsün).
check("2a1) 3-5 ay gündüz uykusu ARALIK: 4-5 saat (eskiden 'min 4 saat')",
      yb.yas_bandi_getir(4)["gunduz_uyku_toplam_dk"] == [240, 300],
      str(yb.yas_bandi_getir(4)["gunduz_uyku_toplam_dk"]))
check("2a2) 9-12 ay gündüz uykusu ARALIK: 2-3 saat (eskiden 'min 2 saat')",
      yb.yas_bandi_getir(10)["gunduz_uyku_toplam_dk"] == [120, 180],
      str(yb.yas_bandi_getir(10)["gunduz_uyku_toplam_dk"]))
check("2a3) 18-24 ay penceresi TABLODA verildi (5-6 saat), artık devralınmıyor",
      yb.yas_bandi_getir(20)["uyaniklik_penceresi_dk"] == [300, 360]
      and yb.yas_bandi_getir(20)["uyaniklik_penceresi_kaynak"] is None,
      f"ww={yb.yas_bandi_getir(20)['uyaniklik_penceresi_dk']} "
      f"kaynak={yb.yas_bandi_getir(20)['uyaniklik_penceresi_kaynak']}")

# Tablo iç tutarlılığı: gündüz + gece aralıkları, bildirilen toplamla ÖRTÜŞMELİ.
# İleride tabloya yazım hatası girerse bu kontrol yakalar.
_tutarsiz = []
for ay, *_ in BEKLENEN:
    b = yb.yas_bandi_getir(ay)
    g_lo, g_hi = b["gunduz_uyku_toplam_dk"]
    n_lo, n_hi = b["gece_uykusu_dk"]
    t_lo, t_hi = b["toplam_gunluk_uyku_dk"]
    # gündüz üst sınırı yoksa toplamdan türet (üstten sınırsız sayılır)
    kaba_lo, kaba_hi = g_lo + n_lo, (g_hi + n_hi) if g_hi else t_hi
    if kaba_hi < t_lo or kaba_lo > t_hi:
        _tutarsiz.append(f"{b['id']}: gündüz+gece {kaba_lo}-{kaba_hi} ∩ "
                         f"toplam {t_lo}-{t_hi} = BOŞ")
check("2a4) Tablo iç tutarlı: gündüz+gece aralığı bildirilen toplamla örtüşüyor",
      not _tutarsiz, str(_tutarsiz))

# 8. ayda uyku sayısı 2'ye DÜŞÜRÜLMEZ — 6-8 ay bandında 3 SABİT.
_b8 = yb.yas_bandi_getir(8)
check("2b) 8. ay: gündüz uyku sayısı SABİT 3 (2'ye düşürülmez)",
      _b8["gunduz_uyku_sayisi"] == [3, 3] and _b8["gunduz_uyku_sayisi_sabit"]
      and yb.cizelge_parametreleri(_b8)["uyku_sayisi"] == 3,
      f"sayi={_b8['gunduz_uyku_sayisi']} cizelge="
      f"{yb.cizelge_parametreleri(_b8)['uyku_sayisi']}")

# Devralma MEKANİZMASI hâlâ çalışıyor mu? (v1.1'de 18-24 ay tabloya girdi, ama
# gelecekte bir bandın alanı eksik kalırsa sessiz varsayılana düşülmemeli.)
_sahte = {"id": "test", "ad": "test", "ay_min": 0, "ay_max": 1,
          "uyaniklik_penceresi_devir": "12-18_ay.tek_uyku"}
_devir, _kaynak = yb._pencere_devral(_sahte)
check("2c) Devralma mekanizması korunuyor (eksik pencere → komşu bant + kaynak)",
      _devir == [240, 360] and _kaynak == "12-18_ay.tek_uyku",
      f"devir={_devir} kaynak={_kaynak}")


# =============================================================================
# 3) ÇİZELGE KURUCU — tablo tek kaynak
# =============================================================================
_sorunlar = []
for ay in range(0, 37):
    b = yb.yas_bandi_getir(ay)
    cp = yb.cizelge_parametreleri(b)
    sch = pa.build_schedule({}, 7 * S, yas_ay=ay)
    naps = [x for x in sch if x["type"] == "nap"]
    bed = next(x for x in sch if x["key"] == "bedtime")
    wake = next(x for x in sch if x["key"] == "wake")

    # a) uyku sayısı bandın aralığında
    n_lo, n_hi = b["gunduz_uyku_sayisi"]
    if not (n_lo <= len(naps) <= n_hi):
        _sorunlar.append(f"{ay} ay: {len(naps)} uyku, bant {n_lo}-{n_hi}")
    # b) uyanıklık penceresi bandın aralığında
    ww_lo, ww_hi = b["uyaniklik_penceresi_dk"]
    if not (ww_lo <= cp["uyaniklik_penceresi_dk"] <= ww_hi):
        _sorunlar.append(f"{ay} ay: pencere {cp['uyaniklik_penceresi_dk']} ∉ [{ww_lo},{ww_hi}]")
    # c) gündüz toplam uyku minimumu tutuyor
    if cp["gunduz_toplam_dk"] < b["gunduz_uyku_toplam_dk"][0]:
        _sorunlar.append(f"{ay} ay: gündüz {cp['gunduz_toplam_dk']} < "
                         f"min {b['gunduz_uyku_toplam_dk'][0]}")
    # d) gece yatışı, gece uykusu süresinden türeyen aralıkta
    g_lo, g_hi = b["gece_uykusu_dk"]
    gece = (wake["start_minute"] + 1440) - bed["start_minute"]
    if not (g_lo <= gece <= g_hi):
        _sorunlar.append(f"{ay} ay: gece uykusu {gece} ∉ [{g_lo},{g_hi}]")
    # e) son uyku ile yatış arası pencere içinde
    if naps:
        gap = bed["start_minute"] - naps[-1]["end_minute"]
        if not (ww_lo <= gap <= ww_hi):
            _sorunlar.append(f"{ay} ay: son uyku-yatış {gap} ∉ [{ww_lo},{ww_hi}]")
    # f) tablo aralıkları 24 saatlik döngüyü kapatabildi mi
    if not cp["cozuldu"]:
        _sorunlar.append(f"{ay} ay: {cp['not']}")
    # g) 24 saatlik TOPLAM uyku bandın ihtiyacına oturuyor mu (v1.1)
    t_lo, t_hi = b["toplam_gunluk_uyku_dk"]
    if t_lo and not (t_lo <= cp["toplam_uyku_dk"] <= (t_hi or t_lo)):
        _sorunlar.append(f"{ay} ay: toplam {cp['toplam_uyku_dk']} ∉ [{t_lo},{t_hi}]")

check("3) Her yaş için çizelge bant aralıklarını sağlıyor (uyku sayısı, "
      "pencere, gündüz min, gece süresi)", not _sorunlar, str(_sorunlar[:6]))

# 9 aylık somut çizelge. v1.1'de pencere 210 → 200 dk'ya indi: 9-12 ay toplam
# uyku ihtiyacı 14 saat ve toplam = 1440 - (n+1)×pencere kimliği gereği 210 dk
# yalnız 13,5 saat veriyordu. İlk uyku 07:00 + 3 saat 20 dk = 10:20.
_s9 = pa.build_schedule({}, 7 * S, yas_ay=9)
check("3b) 9 aylık: 2 gündüz uykusu, ilk uyku 10:20 (toplam 14 saate oturur)",
      len([x for x in _s9 if x["type"] == "nap"]) == 2
      and _s9[1]["time"] == "10:20"
      and yb.cizelge_parametreleri(yb.yas_bandi_getir(9))["toplam_uyku_dk"] == 840,
      str([(x["key"], x["time"], x["end"]) for x in _s9]))

# 15 aylık: KB'de uyaniklik_penceresi HİÇ YOKTU → eskiden varsayılan (2-3 saat)
# kullanılıyordu. Artık tablodan 3-4 saat gelir.
_s15 = pa.build_schedule({}, 7 * S, yas_ay=15)
_ilk15 = next(x for x in _s15 if x["type"] == "nap")
check("3c) 15 aylık: pencere varsayılana DÜŞMÜYOR (ilk uyku 10:30, 3.5 saat)",
      _ilk15["time"] == "10:30" and len([x for x in _s15 if x["type"] == "nap"]) == 2,
      f"ilk_uyku={_ilk15['time']} uyku_sayisi="
      f"{len([x for x in _s15 if x['type'] == 'nap'])}")


# =============================================================================
# 4) 12-18 AY — TEK / ÇİFT UYKU AYRIMI
# =============================================================================
_iki = yb.yas_bandi_getir(15, tek_uyku=False)
_tek = yb.yas_bandi_getir(15, tek_uyku=True)
check("4) 12-18 ay iki uyku: 3-4 saat pencere, 2 uyku, min 2 saat",
      _iki["uyaniklik_penceresi_dk"] == [180, 240]
      and _iki["gunduz_uyku_sayisi"] == [2, 2]
      and _iki["gunduz_uyku_toplam_dk"] == [120, None],
      f"{_iki['uyaniklik_penceresi_dk']} {_iki['gunduz_uyku_sayisi']}")
check("4b) 12-18 ay tek uyku: 4-6 saat pencere, 1 uyku, min 2 saat",
      _tek["uyaniklik_penceresi_dk"] == [240, 360]
      and _tek["gunduz_uyku_sayisi"] == [1, 1]
      and _tek["gunduz_uyku_toplam_dk"] == [120, None],
      f"{_tek['uyaniklik_penceresi_dk']} {_tek['gunduz_uyku_sayisi']}")

check("4c) Varsayılan varyant = 2 uyku (şüphede çocuk 2 uyku bandındadır)",
      yb.yas_bandi_getir(15)["varyant"] == "iki_uyku",
      str(yb.yas_bandi_getir(15)["varyant"]))

# Çizelge de ayrışıyor mu?
_sch_iki = pa.build_schedule({}, 7 * S, yas_ay=15, tek_uyku=False)
_sch_tek = pa.build_schedule({}, 7 * S, yas_ay=15, tek_uyku=True)
check("4d) Çizelge ayrışır: 2 uyku vs 1 uyku",
      len([x for x in _sch_iki if x["type"] == "nap"]) == 2
      and len([x for x in _sch_tek if x["type"] == "nap"]) == 1,
      f"iki={[x['time'] for x in _sch_iki]} tek={[x['time'] for x in _sch_tek]}")

# --- Üç geçiş şartı: ÜÇÜ BİRDEN --------------------------------------------
_hepsi = yb.tek_uykuya_gecis_degerlendir(ogle_yatis_dk=12 * S + 30,
                                         tek_ogun_uyku_dk=130,
                                         uyaniklik_penceresi_dk=300)
check("4e) Üç şart sağlanıyor → tek uyku",
      _hepsi["tek_uyku"] is True, _hepsi["gerekce"])

_erken = yb.tek_uykuya_gecis_degerlendir(ogle_yatis_dk=11 * S + 30,   # 11:30 → İHLAL
                                         tek_ogun_uyku_dk=130,
                                         uyaniklik_penceresi_dk=300)
check("4f) Öğlen uykusuna 12:00'den ÖNCE yatıyor → hâlâ 2 uyku",
      _erken["tek_uyku"] is False
      and not _erken["sartlar"][0]["saglandi"],
      f"tek_uyku={_erken['tek_uyku']} sart1={_erken['sartlar'][0]['saglandi']}")

_kisa = yb.tek_uykuya_gecis_degerlendir(ogle_yatis_dk=12 * S + 30,
                                        tek_ogun_uyku_dk=90,          # <2 saat
                                        uyaniklik_penceresi_dk=300)
check("4g) Tek öğünde 2 saatten az uyku → hâlâ 2 uyku",
      _kisa["tek_uyku"] is False and not _kisa["sartlar"][1]["saglandi"],
      f"tek_uyku={_kisa['tek_uyku']}")

_dar = yb.tek_uykuya_gecis_degerlendir(ogle_yatis_dk=12 * S + 30,
                                       tek_ogun_uyku_dk=130,
                                       uyaniklik_penceresi_dk=210)    # 3.5 saat
check("4h) Pencere 4-6 saat DIŞINDA → hâlâ 2 uyku",
      _dar["tek_uyku"] is False and not _dar["sartlar"][2]["saglandi"],
      f"tek_uyku={_dar['tek_uyku']}")

_gis = yb.tek_uykuya_gecis_degerlendir(ogle_yatis_dk=13 * S,
                                       tek_ogun_uyku_dk=130,
                                       uyaniklik_penceresi_dk=400)    # >6 saat
check("4i) Pencere 6 saatten UZUN → şart sağlanmaz",
      _gis["tek_uyku"] is False, f"tek_uyku={_gis['tek_uyku']}")

_olculmemis = yb.tek_uykuya_gecis_degerlendir()
check("4j) Hiçbir şart ölçülmemiş → tek uykuya GEÇİLMEZ (şüphede 2 uyku)",
      _olculmemis["tek_uyku"] is False, _olculmemis["gerekce"])


# =============================================================================
# 5) EVRENSEL KESTİRME KURALI — 30 dk + 1 saat sonra gece uykusu
# =============================================================================
_proto = yb.kestirme_protokolu()
check("5) Kestirme protokolü sözleşmesi (tetik/sure_dk/gece_uykusuna_gecis_dk)",
      _proto["tetik"] == "gündüz min süre tamamlanmadı"
      and _proto["sure_dk"] == 30 and _proto["gece_uykusuna_gecis_dk"] == 60,
      str({k: _proto[k] for k in ("tetik", "sure_dk", "gece_uykusuna_gecis_dk")}))

# 6-8 ay: gündüz minimum 3 saat (180 dk)
_b7 = yb.yas_bandi_getir(7)
_k_eksik = yb.kestirme_degerlendir(_b7, 150)        # 30 dk eksik → TETİKLENİR
_k_tam = yb.kestirme_degerlendir(_b7, 180)          # tam minimum → tetiklenmez
_k_fazla = yb.kestirme_degerlendir(_b7, 220)        # fazla → tetiklenmez
_k_yok = yb.kestirme_degerlendir(_b7, None)         # kayıt yok → tetiklenmez

check("5b) Gündüz minimum tutmadı (150 < 180) → kestirme GEREKLİ, 30 dk eksik",
      _k_eksik["gerekli"] is True and _k_eksik["eksik_dk"] == 30
      and _k_eksik["sure_dk"] == 30 and _k_eksik["gece_uykusuna_gecis_dk"] == 60,
      str(_k_eksik))
check("5c) Gündüz minimum TAM tutuldu (180) → kestirme gerekmez",
      _k_tam["gerekli"] is False and _k_tam["eksik_dk"] == 0, str(_k_tam))
check("5d) Minimumun üstünde (220) → kestirme gerekmez",
      _k_fazla["gerekli"] is False, str(_k_fazla))
check("5e) Gündüz uyku kaydı YOK → kestirme tetiklenmez (uydurma yok)",
      _k_yok["gerekli"] is False and _k_yok["gerceklesen_dk"] is None, str(_k_yok))

# Tüm bantlarda geçerli mi?
_bantsiz = [yb.yas_bandi_getir(a)["id"] for a in (1, 4, 7, 10, 14, 20, 30)
            if not yb.kestirme_degerlendir(yb.yas_bandi_getir(a), 0)["gerekli"]]
check("5f) Kural TÜM bantlarda geçerli (0 dk gündüz uykusu → hepsinde tetiklenir)",
      not _bantsiz, f"tetiklenmeyen={_bantsiz}")

# 24-36 ay minimum 1 saat — 45 dk uyursa tetiklenir, 70 dk uyursa tetiklenmez.
_b30 = yb.yas_bandi_getir(30)
check("5g) 24-36 ay: 45 dk → tetiklenir, 70 dk → tetiklenmez (min 1 saat)",
      yb.kestirme_degerlendir(_b30, 45)["gerekli"] is True
      and yb.kestirme_degerlendir(_b30, 70)["gerekli"] is False,
      f"45dk={yb.kestirme_degerlendir(_b30, 45)['eksik_dk']} eksik")

# Bant çıktısında da bulunmalı (plan içeriğine buradan taşınır).
check("5h) Her çözülmüş bant kestirme protokolünü taşır",
      all("kestirme_protokolu" in yb.yas_bandi_getir(a) for a in range(0, 37)), "")


# --- Kestirme tetikleyicisi ALT SINIRDIR (İlayda teyidi) --------------------
# v1.1'de gündüz aralıklarına ÜST sınır eklendi; tetikleyici DEĞİŞMEMELİ.
_b4 = yb.yas_bandi_getir(4)            # 3-5 ay: gündüz 4-5 saat (240-300)
check("5i) 3-5 ay: tetikleyici ALT sınır (240). 239 dk → tetiklenir",
      yb.kestirme_degerlendir(_b4, 239)["gerekli"] is True
      and yb.kestirme_degerlendir(_b4, 239)["eksik_dk"] == 1
      and yb.kestirme_degerlendir(_b4, 240)["gerekli"] is False,
      f"min={_b4['gunduz_uyku_toplam_dk']}")
check("5j) ÜST sınır tetikleyici DEĞİL: 3-5 ay 300+ dk uyku kestirme üretmez",
      yb.kestirme_degerlendir(_b4, 320)["gerekli"] is False,
      str(yb.kestirme_degerlendir(_b4, 320)))
_b10 = yb.yas_bandi_getir(10)          # 9-12 ay: gündüz 2-3 saat (120-180)
check("5k) 9-12 ay: 119 dk → tetiklenir, 120 ve 200 dk → tetiklenmez",
      yb.kestirme_degerlendir(_b10, 119)["gerekli"] is True
      and yb.kestirme_degerlendir(_b10, 120)["gerekli"] is False
      and yb.kestirme_degerlendir(_b10, 200)["gerekli"] is False,
      f"min={_b10['gunduz_uyku_toplam_dk']}")


# =============================================================================
# 5-B) 24 SAATLİK TOPLAM UYKU — "bebeğim yeterince uyuyor mu?" (v1.1)
# =============================================================================
_b7t = yb.yas_bandi_getir(7)                       # 6-8 ay: toplam 14 saat (840)
_az = yb.toplam_uyku_degerlendir(_b7t, 180, 600)   # 780 → 60 dk eksik
_tam = yb.toplam_uyku_degerlendir(_b7t, 210, 630)  # 840 → tam
_cok = yb.toplam_uyku_degerlendir(_b7t, 240, 660)  # 900 → fazla
check("5B) Toplam 780 dk (<840) → yeterli DEĞİL, 60 dk eksik",
      _az["yeterli"] is False and _az["eksik_dk"] == 60 and _az["durum"] == "az",
      str(_az))
check("5B-b) Toplam 840 dk → yeterli",
      _tam["yeterli"] is True and _tam["durum"] == "yeterli", str(_tam))
check("5B-c) Toplam 900 dk → yeterli (fazla olarak işaretlenir)",
      _cok["yeterli"] is True and _cok["durum"] == "fazla"
      and _cok["fazla_dk"] == 60, str(_cok))

# Yarım veriden "yetersiz uyuyor" sonucu ÇIKARILMAZ (yanlış alarm olmasın).
check("5B-d) Gündüz VEYA gece verisi eksikse değerlendirme yapılmaz",
      yb.toplam_uyku_degerlendir(_b7t, 180, None)["durum"] == "veri_yok"
      and yb.toplam_uyku_degerlendir(_b7t, None, 600)["durum"] == "veri_yok"
      and yb.toplam_uyku_degerlendir(_b7t, None, None)["yeterli"] is None, "")

# Aralıklı hedefte (24-36 ay: 11-12,5 saat) alt/üst sınırlar
_b30t = yb.yas_bandi_getir(30)
check("5B-e) 24-36 ay hedefi aralık (660-750): 650→az, 700→yeterli, 800→fazla",
      yb.toplam_uyku_degerlendir(_b30t, 60, 590)["durum"] == "az"
      and yb.toplam_uyku_degerlendir(_b30t, 60, 640)["durum"] == "yeterli"
      and yb.toplam_uyku_degerlendir(_b30t, 90, 710)["durum"] == "fazla",
      f"hedef={_b30t['toplam_gunluk_uyku_dk']}")


# =============================================================================
# 6) 24-36 AY — ÖĞLEN UYKUSU REDDİ PROTOKOLÜ
# =============================================================================
_a1 = yb.ogle_uykusu_reddi_adimi(1)
_a2 = yb.ogle_uykusu_reddi_adimi(2)
_a3 = yb.ogle_uykusu_reddi_adimi(3)
_a9 = yb.ogle_uykusu_reddi_adimi(9)           # 3'ten sonrası son adımda kalır
check("6) Reddi protokolü: 1→07:00, 2→06:00, 3→öğlen uykusu kademeli kaldırılır",
      _a1["saat"] == "07:00" and _a2["saat"] == "06:00"
      and _a3["aksiyon"] == "ogle_uykusu_kademeli_kaldirma",
      f"{_a1['saat']} / {_a2['saat']} / {_a3['aksiyon']}")
check("6b) 3'ten fazla red → son adımda kalır, taşma yok",
      _a9["aksiyon"] == "ogle_uykusu_kademeli_kaldirma"
      and yb.ogle_uykusu_reddi_adimi(0) is None, str(_a9["aksiyon"]))
check("6c) Protokol 24-36 ay bandına bağlı",
      "ogle_uykusu_reddi_protokolu" in yb.yas_bandi_getir(30)
      and "ogle_uykusu_reddi_protokolu" not in yb.yas_bandi_getir(10), "")


# =============================================================================
# 7) plan_adapter ENTEGRASYONU
# =============================================================================
TODAY = datetime(2026, 8, 3, tzinfo=timezone.utc).date()
TZ = pa.TZ_OFFSET_MIN


class FakeLog:
    def __init__(self, type_, started_at, ended_at=None):
        self.type, self.started_at, self.ended_at = type_, started_at, ended_at


def _utc(day_offset: int, local_h: int, local_m: int = 0):
    base = datetime(2026, 8, 3, tzinfo=timezone.utc) - timedelta(days=day_offset)
    return base + timedelta(hours=local_h, minutes=local_m) - timedelta(minutes=TZ)


def wake_logs(h: int, m: int = 0, days: int = 3) -> list[FakeLog]:
    return [FakeLog("wake", _utc(d, h, m)) for d in range(days)]


# 7a) KB parametreleri BOŞ olsa bile yaş verildiğinde çizelge doğru kurulur —
#     tablo gerçekten tek kaynak (KB'ye düşmüyor).
_bos_kb = pa.build_schedule({}, 7 * S, yas_ay=7)
_kb_dolu = pa.build_schedule(
    {"uyaniklik_penceresi": "9 Saat", "uyku_sayisi": "9",
     "gunduz_uyku_total": "9 Saat", "yatma_vakti": "01:00 - 02:00"},
    7 * S, yas_ay=7)
check("7) yas_ay verildiğinde KB metinleri YOK SAYILIR (tablo tek kaynak)",
      _bos_kb == _kb_dolu and len([x for x in _bos_kb if x["type"] == "nap"]) == 3,
      f"esit={_bos_kb == _kb_dolu} uyku={len([x for x in _bos_kb if x['type']=='nap'])}")

# 7b) yas_ay YOKSA eski KB yolu aynen çalışır (geriye uyumluluk).
BUCKET_8AY = {
    "uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "2.5 - 3.5 Saat"},
    "uyku_sayisi": {"RESMI_DEGER": "2-3"},
    "gunduz_uyku_total": "2.5-3.5 Saat",
    "yatma_vakti": "18:00 - 20:00",
}
_legacy = pa.build_schedule(BUCKET_8AY, 7 * S)
check("7b) yas_ay yoksa KB yolu korunur (geriye uyumluluk)",
      _legacy[1]["time"] == "10:00" and len([x for x in _legacy if x["type"] == "nap"]) == 2,
      str([(x["key"], x["time"]) for x in _legacy]))

# 7c) Günlük ±45 dk kaydırma tek başına yeniden üretim TETİKLEMEZ (çizelgenin
#     tamamı kaydığında bant ölçütleri değişmez).
_plan9 = {"schedule": pa.build_schedule({}, 7 * S, yas_ay=9)}
_r = pa.adapt(_plan9, {}, pa.summarize_logs(wake_logs(7, 45), today=TODAY),
              today=TODAY, yas_ay=9)
check("7c) +45 dk kaydırma → kaydırılır, yeniden üretim gerekmez",
      _r["adjusted"] is True and _r["shift_minutes"] == 45
      and _r["regenerate_required"] is False,
      f"shift={_r['shift_minutes']} required={_r['regenerate_required']} "
      f"reasons={_r['reasons']}")

# 7d) BANT DEĞİŞİMİ → yeniden üretim. 8 aylık çizelge (3 uyku) 10 aylık bantla
#     (2 uyku) değerlendirilirse çizelge artık geçersizdir.
_plan8 = {"schedule": pa.build_schedule({}, 7 * S, yas_ay=8)}
_r_bant = pa.adapt(_plan8, {}, pa.summarize_logs(wake_logs(7, 45), today=TODAY),
                   today=TODAY, yas_ay=10)
check("7d) Bebek bant atladı (3 uyku çizelgesi, 2 uyku bandı) → regenerate_required",
      _r_bant["regenerate_required"] is True and _r_bant["adjusted"] is False,
      f"required={_r_bant['regenerate_required']} reasons={_r_bant['reasons']}")

# 7d2) KAYMA KENDİLİĞİNDEN SINIRLIDIR: bant ölçütleri eşit kaydırmaya duyarsız
#      olduğu için mutlak duvar saati sınırı uydurulmadı. Buna gerek de yok —
#      summarize_logs sabah uyanışını yalnız MORNING_WINDOW (04:00-11:00) içinde
#      arar, dolayısıyla çizelge 11:00'i geçecek şekilde kaydırılamaz.
_gec_plan = {"schedule": pa.build_schedule({}, 10 * S + 30, yas_ay=9)}   # uyanış 10:30
_r_gec = pa.adapt(_gec_plan, {},
                  pa.summarize_logs([FakeLog("wake", _utc(d, 11, 30))    # 11:30 → pencere DIŞI
                                     for d in range(3)], today=TODAY),
                  today=TODAY, yas_ay=9)
check("7d2) Sabah penceresi dışındaki (11:30) uyanış kaydı kaydırma üretmez",
      _r_gec["adjusted"] is False and _r_gec["shift_minutes"] == 0,
      f"shift={_r_gec['shift_minutes']} reasons={_r_gec['reasons']}")

# Sınır içinde kalan kaydırma normal işler (kural fazla hassas değil).
_normal = {"schedule": pa.build_schedule({}, 7 * S, yas_ay=9)}
_r_normal = pa.adapt(_normal, {}, pa.summarize_logs(wake_logs(7, 40), today=TODAY),
                     today=TODAY, yas_ay=9)
check("7d3) Sabah aralığı içindeki kaydırma yeniden üretim TETİKLEMEZ",
      _r_normal["adjusted"] is True and _r_normal["regenerate_required"] is False,
      f"shift={_r_normal['shift_minutes']} required={_r_normal['regenerate_required']}")

# 7e) Kestirme kuralı adapt() çıktısına yansır.
_naps_az = [FakeLog("nap", _utc(1, 10), _utc(1, 11)),        # 60 dk
            FakeLog("nap", _utc(1, 14), _utc(1, 15))]        # 60 dk → toplam 120
_sum_az = pa.summarize_logs(wake_logs(7) + _naps_az, today=TODAY)
_r_kest = pa.adapt(_plan8, {}, _sum_az, today=TODAY, yas_ay=7)
check("7e) Gündüz toplam 120 dk (<180) → adapt kestirme gerekli der",
      _sum_az["avg_day_sleep_minutes"] == 120.0
      and _r_kest["kestirme"]["gerekli"] is True
      and _r_kest["kestirme"]["eksik_dk"] == 60
      and any("kestirme" in s for s in _r_kest["reasons"]),
      f"gunduz={_sum_az['avg_day_sleep_minutes']} kestirme={_r_kest['kestirme']}")

_naps_yeterli = [FakeLog("nap", _utc(1, 9), _utc(1, 10, 20)),    # 80
                 FakeLog("nap", _utc(1, 13), _utc(1, 14, 20)),   # 80
                 FakeLog("nap", _utc(1, 16), _utc(1, 16, 40))]   # 40 → 200
_sum_yeterli = pa.summarize_logs(wake_logs(7) + _naps_yeterli, today=TODAY)
_r_yeterli = pa.adapt(_plan8, {}, _sum_yeterli, today=TODAY, yas_ay=7)
check("7f) Gündüz toplam 200 dk (>180) → kestirme gerekmez",
      _sum_yeterli["avg_day_sleep_minutes"] == 200.0
      and _r_yeterli["kestirme"]["gerekli"] is False,
      f"gunduz={_sum_yeterli['avg_day_sleep_minutes']} "
      f"kestirme={_r_yeterli['kestirme']['gerekli']}")

# 7e2) 24 saatlik toplam uyku değerlendirmesi adapt() çıktısına yansır (v1.1).
#      Gece 20:00 yatış → 07:00 uyanış = 660 dk; gündüz 120 dk → toplam 780.
#      6-8 ay ihtiyacı 840 dk → 60 dk eksik.
_gece_loglar = [FakeLog("sleep", _utc(d + 1, 20), _utc(d, 7)) for d in range(3)]
_sum_toplam = pa.summarize_logs(_naps_az + _gece_loglar, today=TODAY)
_r_toplam = pa.adapt(_plan8, {}, _sum_toplam, today=TODAY, yas_ay=7)
check("7e2) adapt: 24 saatlik toplam uyku eksikse raporlanır",
      _sum_toplam["avg_night_sleep_minutes"] == 660.0
      and _r_toplam["toplam_uyku"]["gerceklesen_dk"] == 780
      and _r_toplam["toplam_uyku"]["eksik_dk"] == 60
      and _r_toplam["toplam_uyku"]["durum"] == "az"
      and any("24 saatlik toplam" in s for s in _r_toplam["reasons"]),
      f"gece={_sum_toplam['avg_night_sleep_minutes']} "
      f"toplam={_r_toplam['toplam_uyku']}")

# 7g) avg_day_sleep_minutes (gün başına toplam) ile avg_nap_minutes (uyku başına)
#     karıştırılmamalı.
check("7g) avg_day_sleep_minutes gün TOPLAMI, avg_nap_minutes uyku ORTALAMASI",
      _sum_az["avg_day_sleep_minutes"] == 120.0
      and _sum_az["avg_nap_minutes"] == 60.0,
      f"gun={_sum_az['avg_day_sleep_minutes']} uyku={_sum_az['avg_nap_minutes']}")

# 7h) yas_ay yoksa kestirme değerlendirmesi YAPILMAZ (sessiz varsayım yok).
_r_bantsiz = pa.adapt(_plan8, BUCKET_8AY, _sum_az, today=TODAY)
check("7h) Bant çözülemezse kestirme None (uydurma değerlendirme yok)",
      _r_bantsiz["kestirme"] is None, str(_r_bantsiz["kestirme"]))


# =============================================================================
# 8) parametre_uret + chat retrieval köprüsü
# =============================================================================
from engine.parameter_engine import parametre_uret          # noqa: E402
from engine import chatbot                                  # noqa: E402

_dogum = (datetime.now(timezone.utc).date() - timedelta(days=int(9 * 30.44)))
_param = parametre_uret({"bebek_ad": "Test", "dogum_tarihi": _dogum.isoformat(),
                         "dogum_haftasi": 40})
check("8) parametre_uret yapılandırılmış bandı ve kestirme kuralını taşır",
      _param["yas_bandi"]["id"] == "9-12_ay"
      and _param["kestirme_protokolu"]["sure_dk"] == 30,
      f"bant={_param['yas_bandi']['id']}")
check("8b) Plan parametrelerindeki sayılar tablodan geliyor (9-12 ay)",
      _param["parametreler"]["uyaniklik_penceresi"] == "3 saat - 4 saat"
      and _param["parametreler"]["uyku_sayisi"] == "2 uyku (SABİT)"
      and _param["parametreler"]["gunduz_uyku_total"] == "2 saat - 3 saat",
      f"{_param['parametreler']['uyaniklik_penceresi']} / "
      f"{_param['parametreler']['uyku_sayisi']} / "
      f"{_param['parametreler']['gunduz_uyku_total']}")
# KB'nin toplam_uyku_24h değeri ("12-15 Saat") tabloyla çelişiyor → tablo kazanır.
check("8b2) toplam_uyku_24h KB'den DEĞİL tablodan (9-12 ay = 14 saat)",
      _param["parametreler"]["toplam_uyku_24h"] == "14 saat",
      str(_param["parametreler"]["toplam_uyku_24h"]))

# "9 aylık" sorusu artık boşluğa düşmez.
_bantlar, _ay = chatbot.bant_coz("9 aylık bebeğim gece çok uyanıyor")
_blok = chatbot.yas_bandi_blok(_bantlar, _ay)
check("8c) '9 aylık' sorusu → yaş bandı bloğu üretilir (boşluk yok)",
      bool(_blok) and "3 saat - 4 saat" in _blok and "9-12 ay" in _blok,
      _blok[:160].replace("\n", " | "))

# KB'de parametresi HİÇ olmayan aralık (15 ay) da artık dolu geliyor.
_b15, _a15 = chatbot.bant_coz("15 aylık çocuğum öğlen uykusunu reddediyor")
_blok15 = chatbot.yas_bandi_blok(_b15, _a15)
check("8d) '15 aylık' sorusu → tablodan tam parametre (eskiden KB'de yoktu)",
      "12-18 ay" in _blok15 and "3 saat - 4 saat" in _blok15
      and "Tek uykuya geçiş şartları" in _blok15,
      _blok15[:200].replace("\n", " | "))

# Evrensel kestirme kuralı her bant bloğunda taşınır.
check("8e) Kestirme kuralı her yaş bandı bloğuna eklenir",
      "kestirme" in _blok.lower() and "kestirme" in _blok15.lower(), "")

# 30 aylık → öğlen uykusu reddi protokolü bağlama girer.
_b30c, _a30 = chatbot.bant_coz("30 aylık çocuk öğlen uykusuna yatmıyor")
_blok30 = chatbot.yas_bandi_blok(_b30c, _a30)
check("8f) '30 aylık' → öğlen uykusu reddi protokolü bağlama girer",
      "reddi protokolü" in _blok30 and "06:00" in _blok30,
      _blok30[-200:].replace("\n", " | "))

# Korpusta yaş bandı metin birimleri var mı?
_units = chatbot.build_corpus()
_yb_units = [u for u in _units if u["source"] == "yas_bandi"]
check("8g) Korpusta yaş bandı metin birimleri var (her bant + evrensel kurallar)",
      len(_yb_units) >= 11
      and any("kestirme" in u["chunk_id"] for u in _yb_units)
      and any("tek_uykuya_gecis" in u["chunk_id"] for u in _yb_units)
      and any("ogle_uykusu_reddi" in u["chunk_id"] for u in _yb_units),
      f"birim={len(_yb_units)}")

# Metin formu ile motor sayıları AYNI kaynaktan mı? (ayrışma testi)
_metin_9 = next(u for u in _yb_units if u["chunk_id"] == "yas_bandi:9-12_ay")
check("8h) Metin formu motorla ayrışmıyor (aynı sayılar)",
      "3 saat - 4 saat" in _metin_9["text"] and "2 saat - 3 saat" in _metin_9["text"],
      _metin_9["text"][:120].replace("\n", " | "))

# 24 saatlik toplam hem korpus metnine hem chat bağlamına girmeli (v1.1).
check("8i) 24 saatlik toplam korpus metninde var (9-12 ay = 14 saat)",
      "TOPLAM uyku ihtiyacı" in _metin_9["text"] and "14 saat" in _metin_9["text"],
      _metin_9["text"].replace("\n", " | "))
check("8j) 24 saatlik toplam chat bağlamına giriyor",
      "TOPLAM uyku ihtiyacı" in _blok and "14 saat" in _blok,
      [s for s in _blok.split("\n") if "TOPLAM" in s])
# KB'nin çelişen "12-15 Saat" değeri bağlama SIZMAMALI.
check("8k) KB'nin çelişen toplam değeri ('12-15 Saat') bağlama sızmıyor",
      "12-15 Saat" not in _blok, [s for s in _blok.split("\n") if "12-15" in s])


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("YAŞ BANDI TABLOSU TEST SONUÇLARI (Faz Y)")
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
