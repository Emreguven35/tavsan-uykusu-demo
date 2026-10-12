"""
Yöntem tablosu (data/yontem_tablosu.json) + okuyucu (engine/yontem.py) + salt
okur denetim betiği (scripts/yontem_denetim.py).

2026-10-12: 13 günlük programın sayıları üç ayrı düzyazıda duruyordu (KB,
parameter_engine, plan_adapter) ve kısa gündüzde birbirini tutmuyordu. Tablo
tutarlı değerleri doldurur, çelişenleri "ilayda_bekliyor" diye işaretler.
Bu sürümde HİÇBİR uç ya da iş tabloyu okumuyor.

  S  Şema: gerçek tablo geçerli; kaynaksız dolu alan, değerli bekleyen alan,
     sorusuz bekleyen, adaysız+notsuz bekleyen, bilinmeyen durum, bilinmeyen
     bölüm, eksik bölüm, kırık atıf, notsuz sürüm → hata
  B  Bekleyen alan: listede tam 5 alan; okunursa BekleyenAlanHatasi (varsayılan
     YOK); adaylar kaynaklı; dolu alan ve atıf okunur; bant başına atıf okunmaz
  K  Kaynakla tutarlılık: tablodaki dolu değerler kodun bugünkü sabitleriyle aynı
  D  Denetim çıkarımı: raporlardaki gerçek cümlelerden sayı + rol + durum;
     yedek motor planında uyumsuz satır yok; rapor plan metni içermiyor
  G  Hiçbir uç/iş tabloyu okumuyor (api/, engine/ içinde yontem importu yok)

Çalıştırma: python tests/test_yontem.py
"""
import copy
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import tempfile                                              # noqa: E402
_DB = Path(tempfile.gettempdir()) / "yontem_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"   # notifier sabitleri için import
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from engine import yontem                                    # noqa: E402
import yontem_denetim as den                                 # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


HAM = json.loads(yontem.TABLO_PATH.read_text(encoding="utf-8"))

# =============================================================================
# S — şema
# =============================================================================
check("S) Gerçek tablo geçerli", yontem.dogrula(HAM) == [], yontem.dogrula(HAM))
check("S) Sürüm okunuyor", yontem.surum() == HAM["surum"], yontem.surum())


def bozuk(degistir) -> list[str]:
    t = copy.deepcopy(HAM)
    degistir(t)
    return yontem.dogrula(t)


def _hata_var(hatalar, parca):
    return any(parca in h for h in hatalar)


check("S) Kaynaksız dolu alan → hata",
      _hata_var(bozuk(lambda t: t["seans"]["mola_dk"].pop("kaynak")), "seans.mola_dk: dolu alanda kaynak yok"))
check("S) Onay alanı olmayan dolu alan → hata",
      _hata_var(bozuk(lambda t: t["seans"]["mola_dk"].pop("onay")), "seans.mola_dk: onay alanı yok"))
check("S) Bekleyen alanda deger → hata",
      _hata_var(bozuk(lambda t: t["kucak_araligi_sn"]["dizi"].update(deger=25)),
                "bekleyen alanda deger olamaz"))
check("S) Sorusuz bekleyen alan → hata",
      _hata_var(bozuk(lambda t: t["kucak_araligi_sn"]["dizi"].pop("soru")), "soru yok"))
check("S) Adaysız ve kaynak notsuz bekleyen alan → hata",
      _hata_var(bozuk(lambda t: t["kucak_araligi_sn"]["dizi"].update(adaylar=[])),
                "aday yoksa kaynak_notu zorunlu"))
check("S) Kaynaksız aday → hata",
      _hata_var(bozuk(lambda t: t["kucak_araligi_sn"]["dizi"]["adaylar"][0].pop("kaynak")),
                "adaylar[0]: kaynak yok"))
check("S) Bilinmeyen durum ('onaylandi') → hata",
      _hata_var(bozuk(lambda t: t["kucak_araligi_sn"]["dizi"].update(durum="onaylandi")),
                "bilinmeyen durum"))
check("S) Bilinmeyen bölüm → hata",
      _hata_var(bozuk(lambda t: t.update(uydurma={"x": {"deger": 1}})), "tanınmayan bölüm: uydurma"))
check("S) Eksik bölüm → hata",
      _hata_var(bozuk(lambda t: t.pop("seans")), "eksik ya da boş bölüm: seans"))
check("S) Kırık atıf → hata",
      _hata_var(bozuk(lambda t: t["asama_gecisi"]["kural"].update(
          atif="yas_bantlari.json#evrensel_kurallar.yok_boyle")), "bulunamadı"))
check("S) yas_bantlari dışına atıf → hata",
      _hata_var(bozuk(lambda t: t["asama_gecisi"]["kural"].update(atif="kb.json#x")),
                "atıf yalnız"))
check("S) Notu olmayan sürüm → hata",
      _hata_var(bozuk(lambda t: t.update(surum="9.9")), "9.9"))

# =============================================================================
# B — bekleyen alanlar
# =============================================================================
# v0.4 (İlayda 12 Ekim): 5 bekleyen alan kapandı; kucak aralık dizisi bağlamı belirsiz → bekleyen
BEKLEYEN = {"kucak_araligi_sn.dizi"}
check("B) bekleyen_alanlar() yalnız kucak dizisi (v0.4)", set(yontem.bekleyen_alanlar()) == BEKLEYEN,
      yontem.bekleyen_alanlar())
for yol in sorted(BEKLEYEN):
    try:
        v = yontem.deger(yol)
        check(f"B) {yol} okunursa hata (varsayılan dönmez)", False, f"döndü: {v!r}")
    except yontem.BekleyenAlanHatasi as e:
        check(f"B) {yol} okunursa BekleyenAlanHatasi", e.yol == yol and yol in str(e)
              and isinstance(e, yontem.YontemTablosuHatasi), str(e))
check("B) Kucak dizisi bekleyen; eski değer kaynaklı aday",
      yontem.adaylar("kucak_araligi_sn.dizi")[0]["deger"] == [30, 60, 90, 120]
      and all(a.get("kaynak") for a in yontem.adaylar("kucak_araligi_sn.dizi")))


def _kayit4(yol):
    return [k.get("dosya") for k in yontem.kaynaklar(yol) if str(k.get("dosya", "")).startswith("ilayda-cevaplar-4/")]


check("V4) Gece ilk bekleme 5 (1. gece), gece içi +1; kaynak ilayda-cevaplar-4 kayıtları",
      yontem.deger("gece_uyanma.ilk_gece_ilk_bekleme_dk") == 5 and yontem.deger("gece_uyanma.gece_ici_artis_dk") == 1
      and any("09.01.45" in d for d in _kayit4("gece_uyanma.ilk_gece_ilk_bekleme_dk"))
      and any("09.03.55" in d for d in _kayit4("gece_uyanma.gece_ici_artis_dk")))
check("V4) Kapı/eşik temas: histerikse temas, değilse sesle sakinleştirip çık (09.05.19)",
      "histerik" in yontem.deger("gece_uyanma.icerde_temas_kapi")
      and "sesle sakinleştirip çık" in yontem.deger("gece_uyanma.icerde_temas_kapi")
      and any("09.05.19" in d for d in _kayit4("gece_uyanma.icerde_temas_kapi")))
check("V4) Kısa gündüz 4-12: dış her gün +5, toplam 45, üst sınır 45 (08.59.23)",
      yontem.deger("kisa_gunduz.gun_4_12_dis_ic_dk") == {"gunluk_dis_artis_dk": 5, "toplam_dk": 45,
                                                         "dis_ust_sinir_dk": 45}
      and any("08.59.23" in d for d in _kayit4("kisa_gunduz.gun_4_12_dis_ic_dk")))
check("V4) Protokol kartı onaylı; son adım destek gecesi / diğer geceler ayrı (09.05.50)",
      set(yontem.deger("gece_uyanma.protokol_karti")) >= {"son_destek_gecesi", "son_diger_geceler"}
      and any("09.05.50" in d for d in _kayit4("gece_uyanma.protokol_karti")))
check("V4) Destek gecesi tek yerde: seans.destek_gecesi_egitim_gunleri == [1]; diğer geceler uyuyana kadar",
      yontem.deger("seans.destek_gecesi_egitim_gunleri") == [1]
      and "uyuyana kadar" in yontem.deger("seans.uc_tekrar_sonrasi")["gece_diger"])
check("V4) Kucak kuralı, beyaz gürültü 50-60, oda bölme (poşetle değil), kestirme kayıt tamlığı",
      (yontem.deger("kucak_araligi_sn.kural")["histerik_degilken_kucak_son_gun"] == 7
       and yontem.deger("hazirlik.beyaz_gurultu_db") == {"en_az": 50, "en_fazla": 60}
       and yontem.deger("hazirlik.oda_bolme")["poset_ile_bolme"] is False
       and yontem.deger("hazirlik.oda_bolme")["cam_karartma_poseti"] is True
       and yontem.deger("kestirme.eksik_kayit_bildirimi")["gunde_en_fazla"] == 1))
check("V4) Sürüm 0.4 ve notu var", yontem.surum() == "0.4" and "0.4" in yontem.tablo()["surum_notlari"])

# İlayda kaynaklı dolu alanlar (v0.2)
def _ilayda_kaynakli(yol):
    return any(str(k.get("tur", "")).startswith("ilayda") for k in yontem.kaynaklar(yol))
check("B) Kısa gündüz gün 1-3 İlayda'dan: 5+40 / 10+35 / 15+30",
      yontem.deger("kisa_gunduz.gun_1_3_dis_ic_dk")["gun_2"] == {"dis": 10, "ic": 35}
      and _ilayda_kaynakli("kisa_gunduz.gun_1_3_dis_ic_dk"))
check("B) 3 tekrar sonrası gece/gündüz ayrı (İlayda kayıt37)",
      "destek verip uyutulur" in yontem.deger("seans.uc_tekrar_sonrasi")["gece"]
      and "uyanık tutulur" in yontem.deger("seans.uc_tekrar_sonrasi")["gunduz"]
      and _ilayda_kaynakli("seans.uc_tekrar_sonrasi"))
check("B) Yatır-çık en fazla 20-25 (İlayda kayıt37)",
      yontem.deger("yatir_cik.en_fazla_dk") == [20, 25] and _ilayda_kaynakli("yatir_cik.en_fazla_dk"))
check("B) Gece akışı katmanlı: dış bekleme → 45 dk uğraş → 15 dk rutin ×3",
      yontem.deger("gece_uyanma.akis")["3_deneme_dk"] == 45 and yontem.deger("gece_uyanma.akis")["4_rutin_dk"] == 15
      and yontem.deger("gece_uyanma.akis")["6_tekrar_en_fazla"] == 3)
check("B) Gün sınırı 06:00, gece yarısı sonrası önceki günün gecesi",
      yontem.deger("gece_uyanma.gun_siniri") == {"gun_en_erken_baslangic": "06:00",
                                                 "gece_yarisi_sonrasi": "onceki_gunun_gecesi"})
check("B) Hazırlık (12 Ekim): beslenme 60 dk, sallama 60/60 → 50/120 → 40/180",
      yontem.deger("hazirlik.beslenme_bitis_once_dk") == 60
      and [(a["salla"], a["bekle"]) for a in yontem.deger("hazirlik.sallama_gecisi")["adimlar_sn"]]
      == [(60, 60), (50, 120), (40, 180)])
check("B) Her dolu alanın kaynağı türlü (tur alanı)",
      all(k.get("tur") for b in yontem.BOLUMLER for a in (yontem.tablo()[b] or {}).values()
          if isinstance(a, dict) and "deger" in a for k in a["kaynak"]))
check("B) Dolu alan okunur", yontem.deger("seans.mola_dk") == 15
      and yontem.deger("gece_uyanma.taban_dk") == 5)
check("B) Atıf yas_bantlari'ndan çözülür (kopya yok)",
      yontem.deger("asama_gecisi.kural")["asamada_en_fazla_gun"] == 7
      and yontem.deger("bildirim.kestirme_sonrasi_gece_gecis") == 60)
try:
    yontem.deger("kisa_gunduz.kisa_uyku_tanimi")
    check("B) Bant başına atıf tek değere indirgenmez", False)
except yontem.BekleyenAlanHatasi:
    check("B) Bant başına atıf tek değere indirgenmez", False, "BekleyenAlanHatasi olmamalı")
except yontem.YontemTablosuHatasi as e:
    check("B) Bant başına atıf tek değere indirgenmez", "yas_bantlari" in str(e), str(e))
try:
    yontem.deger("seans.yok_boyle")
    check("B) Olmayan alan → hata", False)
except yontem.YontemTablosuHatasi:
    check("B) Olmayan alan → hata", True)
check("B) Tabloda kısa uyku eşiği / uyanıklık sayısı KOPYALANMAMIŞ",
      "kisa_uyku_esigi_dk\": 4" not in yontem.TABLO_PATH.read_text(encoding="utf-8")
      and "uyaniklik_penceresi_dk\": [" not in yontem.TABLO_PATH.read_text(encoding="utf-8"))

# =============================================================================
# K — kaynakla tutarlılık (tablo bugünkü koddan sapmasın)
# =============================================================================
from engine.parameter_engine import bekleme_sureleri_planla  # noqa: E402
from api.services import plan_adapter                         # noqa: E402

_b13 = bekleme_sureleri_planla("13_gun_dirençli")
check("K) Merdiven pozisyonları parameter_engine ile aynı",
      [a["pozisyon"] for a in yontem.deger("merdiven.asamalar")]
      == list(_b13["kademeli_uzaklasma"].values()))
check("K) Merdiven gün aralıkları 1-3/4-6/7-9/10-12/13",
      [tuple(a["gun"]) for a in yontem.deger("merdiven.asamalar")]
      == [(1, 3), (4, 6), (7, 9), (10, 12), (13, 13)])
check("K) Regresyon eşiği ve günü plan_adapter sabitleriyle aynı",
      yontem.deger("regresyon.kendi_donme_esik_dk") == plan_adapter.SELF_SOOTHE_FAIL_MIN
      and yontem.deger("regresyon.devam_gun") == plan_adapter.REGRESYON_45_GUN)
check("K) Saklanan kart metni (plan_adapter.NIGHT_WAKE_PROTOCOL) kodda aynen; onaylı kart tabloda (okuma anında)",
      "45 dk bekleyip" in plan_adapter.NIGHT_WAKE_PROTOCOL["aciklama"]
      and "45 dakika" in yontem.deger("gece_uyanma.protokol_karti")["2"])
_sab = yontem.deger("bildirim.metin_sablonlari")
from api.services import notifier                             # noqa: E402
check("K) once_gunduz şablonu + uyku_once_dk = bugünkü notifier metni",
      _sab["once_gunduz"].format(uyku_once_dk=yontem.deger("bildirim.uyku_once_dk"))
      == notifier.DIZI_METIN[("once", "gunduz")][1]
      and yontem.deger("bildirim.uyku_once_dk") == notifier.UYKU_ONCE_DK)
check("K) once_gece: şablon kodun 30'uyla bugünkü metni verir; tablo İlayda'nın 15'i (fark belgeli)",
      _sab["once_gece"].format(gece_rutin_once_dk=notifier.UYKU_ONCE_DK)
      == notifier.DIZI_METIN[("once", "gece")][1]
      and yontem.deger("bildirim.gece_rutin_once_dk") == 15 != notifier.UYKU_ONCE_DK)
check("K) sekerleme şablonu + kestirme atfı = notifier metni",
      _sab["sekerleme_uyandir"].format(
          ad="{ad}", gece_gecis_saat=yontem.deger("bildirim.kestirme_sonrasi_gece_gecis") // 60)
      == notifier.SEKERLEME_UYANDIR_GOVDE)

# =============================================================================
# D — denetim çıkarımı (raporlardaki gerçek cümleler)
# =============================================================================
K = den.kurallar()


def coz(cumle):
    return [(r, round(d, 3), den.sayi_durumu(r, d, K)) for r, d in den.satir_sayilari(cumle)]


ORNEKLER = [
    # KB global_rules.bekleme_sureleri.egitim_kisa_gunduz_uykusu (rapor madde 2)
    ("Gün 1: dış 5dk + iç 40dk. Gün 2: 10+35. Gün 3: 15+30.",
     [("dis", 5, "uyumlu"), ("ic", 40, "uyumlu"), ("dis", 10, "uyumlu"),
      ("ic", 35, "uyumlu"), ("dis", 15, "uyumlu"), ("ic", 30, "uyumlu")]),
    # parameter_engine 13 gün kısa gündüz (yedek motor plan satırı)
    ("**Bu gün kısa gündüz uykusu olursa:** Dış bekleme kademesi: 1 dakika → 1,5 dakika → 2 dakika "
     "(içeri girince o günün pozisyonundan beklemeye devam, toplam ~45 dk hedefi).",
     # v0.4: gün 4-12 dolu → kod dizisi tabloyla çelişir
     [("dis", 1, "uyumsuz"), ("dis", 1.5, "uyumsuz"), ("dis", 2, "uyumsuz"), ("seans", 45, "uyumlu")]),
    # Kucağa alma satırı (21/21 planda aynı)
    ("**Kucağa alma bekleme süreleri:** 30 saniye → 1 dakika → 1.5 dakika → 2 dakika diye artış",
     # v0.4: kucak aralık dizisinin bağlamı belirsiz (İlayda 12 Ekim, 9. soru) → bekliyor
     [("kucak", 0.5, "bekliyor"), ("kucak", 1, "bekliyor"), ("kucak", 1.5, "bekliyor"), ("kucak", 2, "bekliyor")]),
    # 45 dakika kuralı satırı
    ("**45 dakika kuralı:** 45 dakika denenme + 15-30 dakika rutin molası + 45 dakika daha (uykuya kadar)",
     [("mola", 15, "uyumlu"), ("mola", 30, "uyumlu"), ("seans", 45, "uyumlu"),
      ("seans", 45, "uyumlu"), ("seans", 45, "uyumlu")]),
    # B planı
    ("45 dakika direnç olursa → 15 dakika rutin molası → 45 dakika yeniden deneme. Çok dirençli "
     "bebeklerde rutin molası 30 dakikaya çıkarılabilir. Maksimum 3 tekrar.",
     [("seans", 45, "uyumlu"), ("mola", 15, "uyumlu"), ("seans", 45, "uyumlu"),
      ("mola", 30, "uyumlu"), ("tekrar", 3, "uyumlu")]),
    # Gece uyanma protokolü
    ("1. uyanma 5 dk, sonra her uyanmada artırarak devam. Ertesi gün bir önceki günün başlangıcının "
     "ÜSTÜNDE başlanır. Asla 5 dk altına inme.",
     # v0.4: ilk bekleme ve gece içi artış dolu → taban üstü uyumlu
     [("gece_uyanma", 5, "uyumlu"), ("gece_uyanma", 5, "uyumlu")]),
    # Yatır-çık (max 20-25 bekliyor)
    ("**Yatır-çık sonrası:** STANDART ilerleme 5 → 10 → 15 → 20 dk. Max 20-25 dk. 21 gün hiç dalmazsa "
     "max 45 dk veya tıbbi yönlendirme.",
     [("yatir_cik", 5, "uyumlu"), ("yatir_cik", 10, "uyumlu"), ("yatir_cik", 15, "uyumlu"),
      ("yatir_cik", 20, "uyumlu"), ("yatir_cik", 20, "uyumlu"), ("yatir_cik", 25, "uyumlu"),
      ("yatir_cik", 45, "uyumlu")]),
    # Rapor madde 4b: Haiku cevapları (sentetik)
    ("**Sonraki uyanmalarda:** Her uyanmada bekleme süresi artar. İkinci uyanmada 10 dakika, "
     "üçüncü uyanmada 15 dakika bekleyin.",
     [("gece_uyanma", 10, "uyumlu"), ("gece_uyanma", 15, "uyumlu")]),
    ("Gecenin bundan sonraki uyanmalarında ise bekleme süresini artırmaya devam edin — ikinci "
     "uyanmada 6-7 dakika, üçüncüde 8-9 dakika gibi.",
     [("gece_uyanma", 6, "uyumlu"), ("gece_uyanma", 7, "uyumlu"),
      ("gece_uyanma", 8, "uyumlu"), ("gece_uyanma", 9, "uyumlu")]),
    ("- **1. gündüz uykusu (09:30):** Dışarıdan 10 dakika bekleyin, sonra içeri girin ve beşiğin "
     "yanında oturup toplam 35 dakika daha bekleyin (dış+iç = 45 dakika hedef)",
     [("dis", 10, "uyumlu"), ("ic", 35, "uyumlu"), ("seans", 45, "uyumlu")]),
    ("- **Eğer şiddetli direnç varsa:** 45 dakika dirençten sonra 15-30 dakikalık rutin molası verin "
     "(şarkı söyleyin, hafifçe sıvazlayın), sonra yeniden yatırın — maksimum 3 tekrar.",
     [("seans", 45, "uyumlu"), ("mola", 15, "uyumlu"), ("mola", 30, "uyumlu"), ("tekrar", 3, "uyumlu")]),
    # Rutin süresi (mola değil) karşılaştırılmaz
    ("Yatış öncesi son 15-20 dakikada sakin bir rutin yapın (oku, sessiz oyun, kucakta).",
     [("rutin", 15, "uyumlu"), ("rutin", 20, "uyumlu")]),
    # Yedek motor plan satırları: artış miktarı ve başarı ölçütü
    ("**Bekleme süresi artışı:** Bekleme süresi artışı KATI DEĞİLDİR: standart 5'er dakikadır, ancak "
     "çok dirençli çocukta 1 dakika, hatta 30 saniye aralıklarla artırılabilir (5 → 6 → 7 → 8 gibi).",
     [("artis", 1, "uyumlu"), ("artis", 0.5, "uyumlu")]),
    ("- 2. gün: direnç süresi 10 dakikaya düşmüş olmalı", [("basari", 10, "kapsam_disi")]),
    # Sentetik (prod plan kalıbı, metin kopyalanmadı): kısa gündüzde "uyanırsa" gece değildir
    ("Kısa gündüz uykusundan uyanırsa dışarıda 1 dakika → 1,5 dakika → 2 dakika bekleyin.",
     [("dis", 1, "uyumsuz"), ("dis", 1.5, "uyumsuz"), ("dis", 2, "uyumsuz")]),
    ("Gece uyanırsa 2 dakika bekleyip içeri girin.", [("gece_uyanma", 2, "uyumsuz")]),
    # Saat ve gün numarası süre sayılmaz
    ("Bugün 09:30–10:30 arasında ilk gündüz uykunuzu hedefliyorsunuz; Gün 1-3 beşik yanı.", []),
]
for cumle, beklenen in ORNEKLER:
    gelen = coz(cumle)
    check(f"D) {cumle[:58]}…", sorted((r, float(d), s) for r, d, s in gelen)
          == sorted((r, float(d), s) for r, d, s in beklenen), gelen)

# Uyumsuz: tablonun dolu alanıyla çelişen sayı (taban altı gece, standart dışı yatır-çık, mola)
check("D) Taban altı gece bekleme (2 dk) → uyumsuz",
      den.sayi_durumu("gece_uyanma", 2, K) == "uyumsuz")
check("D) Yatır-çık dizisinde 8 dk → uyumsuz", den.sayi_durumu("yatir_cik", 8, K) == "uyumsuz")
check("D) Mola 20 dk → uyumsuz", den.sayi_durumu("mola", 20, K) == "uyumsuz")
check("D) Rutin 10 dk (en az 15) → uyumsuz", den.sayi_durumu("rutin", 10, K) == "uyumsuz")
check("D) Gün 1-3 bölümünde dış 1 dk → uyumsuz (İlayda: 5/10/15)",
      den.sayi_durumu("dis", 1, K, (1, 3)) == "uyumsuz" and den.sayi_durumu("dis", 5, K, (1, 3)) == "uyumlu"
      and den.sayi_durumu("ic", 35, K, (2, 2)) == "uyumlu" and den.sayi_durumu("ic", 35, K, (1, 1)) == "uyumsuz")
check("D) Yatır-çık bölümünde dış 20 / iç 25 uyumlu, dış 5 uyumsuz",
      den.sayi_durumu("dis", 20, K, (13, 13)) == "uyumlu" and den.sayi_durumu("ic", 25, K, (13, 13)) == "uyumlu"
      and den.sayi_durumu("dis", 5, K, (13, 13)) == "uyumsuz")
check("D) Gün 4-6 bölümünde dış 1,5 dk uyumsuz; 20/25/30 uyumlu (v0.4: dış her gün +5)",
      den.sayi_durumu("dis", 1.5, K, (4, 6)) == "uyumsuz"
      and all(den.sayi_durumu("dis", v, K, (4, 6)) == "uyumlu" for v in (20, 25, 30))
      and den.sayi_durumu("ic", 25, K, (4, 6)) == "uyumlu")
check("D) Satır durumu: bir uyumsuz sayı satırı uyumsuz yapar",
      den.satir_durumu(["uyumlu", "bekliyor", "uyumsuz"]) == "uyumsuz"
      and den.satir_durumu(["uyumlu", "bekliyor"]) == "bekliyor"
      and den.satir_durumu(["belirsiz"]) == "belirsiz")

# Yedek motor planı (kodun kendi metni) tabloyla çelişmemeli
from engine import plan_gunleri, plan_generator                # noqa: E402
from engine.parameter_engine import parametre_uret            # noqa: E402

_param = parametre_uret({"bebek_ad": "Ada", "dogum_tarihi": "2025-07-01", "dogum_haftasi": 40,
                         "beslenme": "anne sütü", "destek": "kucakta", "oda": "ayrı oda",
                         "dayanma_siniri": "orta", "deneyim": "ilk", "gece_uyanma": "3"})
_md = plan_generator._fallback_plan(_param)
_icerik = {"type": "egitim_plani", "markdown": _md, "plan_secimi": _param["plan_secimi"],
           "days": plan_gunleri.build_days(_md, _param["plan_secimi"]["tip"],
                                           _param["plan_secimi"]["gunler"])}
_s = den.plan_denetle(_icerik, K)
# Kodun kendi plan metni gün 1-3'te '1 → 1,5 → 2' yazıyor; İlayda 5/10/15 diyor →
# BEKLENEN uyumsuzluk yalnız bu (başka hiçbir rol/sayı uyumsuz olmamalı).
check("D) Yedek motor planında uyumsuzluk YALNIZ gün 1-3 kısa gündüz kademesi (kod ≠ İlayda)",
      _s["satir"]["uyumsuz"] > 0 and set(_s["uyumsuz"]) <= {"dis:1", "dis:1.5", "dis:2"},
      (dict(_s["satir"]), dict(_s["uyumsuz"])))
check("D) Yedek motor planında bekleyen satırlar var (gün 4-12 kısa gündüz, gece)",
      _s["satir"]["bekliyor"] > 0, dict(_s["bekliyor"]))
_rapor = den.rapor([{"content": _icerik}])
check("D) Rapor plan metni içermiyor (yalnız sayı ve rol)",
      "Kucağa alma" not in _rapor and "Beşik yanı" not in _rapor and "Ada" not in _rapor
      and "13_gun_dirençli" in _rapor, _rapor[:300])
_rapor_eski = den.rapor([{"content": dict(_icerik, plan_secimi={"tip": "6_gun_buyuk_cocuk", "gunler": 6})}])
check("D) Tablo dışı plan tipi karşılaştırılmaz", "tablo dışı plan tipi" in _rapor_eski)

# =============================================================================
# G — tabloyu yalnız bayraklı iki modül okur; çağrıları bayrak kapısının ardında
# =============================================================================
_okuyan = []
for kok in ("api", "engine", "app.py", "pages"):
    yol = ROOT / kok
    dosyalar = [yol] if yol.is_file() else list(yol.rglob("*.py")) if yol.exists() else []
    for f in dosyalar:
        if f.name == "yontem.py" and f.parent.name == "engine":
            continue
        metin = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bimport\s+yontem\b|from\s+engine\s+import\s+[^\n]*\byontem\b|engine\.yontem\b|yontem_tablosu",
                     metin):
            _okuyan.append(f.relative_to(ROOT).as_posix())
check("G) Tabloyu yalnız yontem_kart (YONTEM_TABLOSU) ve sor_kural (SOR_KURAL_BLOGU/"
      "SOR_SAYI_DOGRULAMA) okur",
      sorted(_okuyan) == ["api/services/sor_kural.py", "api/services/yontem_kart.py",
                          "engine/parameter_engine.py"], _okuyan)
_pe = (ROOT / "engine/parameter_engine.py").read_text(encoding="utf-8")
check("G) parameter_engine tabloyu yalnız YONTEM_TABLOSU açıkken okur (13 günlük kısa gündüz metni)",
      re.search(r'if \(os\.getenv\("YONTEM_TABLOSU"\) or ""\)\.strip\(\)\.lower\(\) not in '
                r'\("1", "true", "yes", "on"\):\s*\n\s*return None\s*\n\s*from engine import yontem', _pe)
      is not None)
_plans = (ROOT / "api/routers/plans.py").read_text(encoding="utf-8")
_notif = (ROOT / "api/services/notifier.py").read_text(encoding="utf-8")
_chat = (ROOT / "api/routers/chat.py").read_text(encoding="utf-8")
check("G) Çağrılar bayrak kapısının ardında (plans, notifier, chat)",
      re.search(r"if yontem_kart\.acik_mi\(\):\s*\n\s*try:\s*\n\s*resp\.content = yontem_kart\.plan_icerigi_isle", _plans)
      and "yontemli = yontem_kart.acik_mi()" in _notif
      and "if yontemli else UYKU_ONCE_DK" in _notif and "if yontemli and adim == \"once\"" in _notif
      and "sor_kural.blok_acik_mi() or sor_kural.dogrulama_acik_mi()" in _chat
      and "mod = sor_kural.dogrulama_modu() if kural else \"kapali\"" in _chat
      and "if mod != \"kapali\" and r.get(\"llm\")" in _chat)

# =============================================================================
print("\n" + "=" * 70)
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(f"[{'OK ' if ok else 'XX '}] {ad}" + ("" if ok else f"\n        → {detay[:400]}"))
print("=" * 70)
print(f"SONUÇ: {gecen}/{len(results)}")
sys.exit(0 if gecen == len(results) else 1)
