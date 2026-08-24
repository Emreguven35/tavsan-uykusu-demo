"""
Bekleme süresi artışının esnekliği — İlayda düzeltmesi (2026-08-25).

NEDEN VAR: cevaplar "5, 10, 15, 20 dakika" ilerlemesini KATI bir kural gibi
sunuyordu. Doğrusu üç parçalıdır:
  1) standart ilerleme 5'er dakikalık artıştır,
  2) çocuk çok dirençliyse artış 1 dakikaya, hatta 30 saniyeye indirilebilir
     (5 → 6 → 7 → 8 gibi),
  3) DEĞİŞMEZ KURAL: bekleme süresi her gün MUTLAKA artar — bir önceki günden
     düşük de olamaz, bir önceki günle AYNI da olamaz (ikisi de alışkanlığa
     dönüşür). Esneklik artışın MİKTARINDADIR, artışın kendisinde değil.
  4) Bedeli: artış ne kadar küçükse öğrenme süreci o kadar uzar. Esneklik
     verilirken bu bedel de söylenmelidir.

Bu dosya iki yönü birden sabitler: dayatma gevşedi AMA değişmez kural durdu.
Yalnız "esneyebilir" demek yetmez — "aynı kalabilir" cevabı da bir regresyondur.

Çalıştırma: python tests/test_bekleme_esnekligi.py
"""
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()
load_dotenv(ROOT.parent / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from engine import chatbot as cb                                   # noqa: E402
from engine.parameter_engine import bekleme_sureleri_planla        # noqa: E402

HAS_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


KB = json.loads((cb.DATA_DIR / "master_knowledge_base.json").read_text(encoding="utf-8"))
GR = KB["global_rules"]
ANAHTAR = "bekleme_suresi_artis_esnekligi (ek 2026-08-25)"


# --- 1) KB kaydı ----------------------------------------------------------------
check("1a) Kural global_rules altında", ANAHTAR in GR, sorted(GR)[:3])

KURAL = GR.get(ANAHTAR, {})
# NOT: metin HAM karşılaştırılır, lower()'lanmaz. Türkçe'de "İ".lower() birleşik
# noktalı 'i̇' üretir ve "DEĞİLDİR" araması sessizce kaçar; "I".lower() ise 'ı'
# değil 'i' verir. Vurgu için büyük harfle yazılmış kuralları küçülterek aramak
# bu yüzden güvenilir değil.
_metin = " ".join(str(v) for v in KURAL.values())

check("1b) Standart ilerleme 5'er dakika olarak yazılı",
      "5'er dakika" in _metin and "5 → 10 → 15 → 20" in _metin, _metin[:160])

check("1c) Katı olmadığı ve daha küçük artışın (1 dk / 30 sn) mümkün olduğu yazılı",
      "KATI bir kural DEĞİLDİR" in _metin
      and "1'er dakika" in _metin and "30'ar saniye" in _metin, "")

check("1d) DEĞİŞMEZ KURAL yazılı: her gün artar, düşük DE aynı DA olamaz",
      "her gün MUTLAKA artar" in _metin
      and "DÜŞÜK olamaz" in _metin and "AYNI da olamaz" in _metin, "")

check("1e) Küçük artışın bedeli (süreç uzar) yazılı",
      "uzar" in _metin, "")

check("1f) Esnekliğin artışın MİKTARINDA olduğu, artışın kendisinde olmadığı ayrımı var",
      "MİKTARINDADIR" in _metin, "")

check("1g) Kural, bekleme süreleriyle ilgili mevcut kayıtla İLİŞKİLENDİRİLDİ",
      "bekleme_sureleri" in _metin, "")

# İlişkilendirmenin diğer yönü: eski kayıt da yeni kurala işaret etmeli, yoksa
# retrieval eski kaydı tek başına getirdiğinde yine katı okunur.
_bs = " ".join(str(v) for v in GR["bekleme_sureleri"].values())
check("1h) bekleme_sureleri kaydı artık 5-10-15-20'yi MUTLAK sunmuyor (standart + geri referans)",
      "STANDART" in _bs and "bekleme_suresi_artis_esnekligi" in _bs, _bs[:200])

check("1i) Kayıt arşiv değil (korpusa girer — 'ARSIV' geçmiyor)",
      "ARSIV" not in ANAHTAR.upper(), ANAHTAR)


# --- 2) Korpus birimleri ---------------------------------------------------------
UNITS = cb.build_corpus()
METINLER = {u["chunk_id"]: u["text"] for u in UNITS}
_ESNEKLIK_BIRIMLERI = [cid for cid in METINLER if cid.startswith(f"global_rule:{ANAHTAR}")]

check("2a) Kuralın alt maddeleri aranabilir birim oldu",
      len(_ESNEKLIK_BIRIMLERI) >= 5, f"{len(_ESNEKLIK_BIRIMLERI)} birim")

_degismez = [c for c in _ESNEKLIK_BIRIMLERI if "MUTLAKA artar" in METINLER[c]]
check("2b) DEĞİŞMEZ KURAL birimi korpusta", bool(_degismez), str(_ESNEKLIK_BIRIMLERI))

_bedel = [c for c in _ESNEKLIK_BIRIMLERI if "uzar" in METINLER[c]]
check("2c) Bedel (süreç uzar) birimi korpusta", bool(_bedel), "")


# --- 3) Retrieval ------------------------------------------------------------------
cb.init_index()

SORU = "birinci gece 5 dakika bekledim, ikinci gece 6 dakika bekleyebilir miyim"
_sonuc = cb.retrieve(SORU, top_k=6, min_score=0.0)
_idler = [s["chunk_id"] for s in _sonuc]
check("3a) Esneklik kuralı sorunun ilk 6 sonucunda",
      any(cid.startswith(f"global_rule:{ANAHTAR}") for cid in _idler),
      str(_idler))

_soru2 = "bekleme süresini her gün ne kadar artırmalıyım"
_idler2 = [s["chunk_id"] for s in cb.retrieve(_soru2, top_k=6, min_score=0.0)]
check("3b) 'Ne kadar artırmalıyım' sorusu da kurala ulaşıyor",
      any(cid.startswith(f"global_rule:{ANAHTAR}") for cid in _idler2),
      str(_idler2))

check("3c) Soru cevaplanabilir katmanda (K4 değil)",
      cb._katman_belirle(
          float(_sonuc[0].get("_score", 0.0)) if _sonuc else 0.0,
          cb._alan_sinyali(SORU, None), False,
          ebeveynlik=cb._ebeveynlik_sinyali(SORU),
          kapsam_disi=cb._kapsam_disi_sinyali(SORU)) != "k4", str(_idler[:2]))


# --- 4) SYSTEM_PROMPT ---------------------------------------------------------------
SP = cb.SYSTEM_PROMPT
check("4a) SYSTEM_PROMPT katı dayatmayı açıkça reddediyor",
      "KATI DAYATMA YOK" in SP or "KATI bir kural DEĞİLDİR" in SP, "")
check("4b) SYSTEM_PROMPT değişmez kuralı taşıyor (her gün artar)",
      "her gün MUTLAKA artar" in SP, "")
check("4c) SYSTEM_PROMPT bedeli söylemeyi zorunlu kılıyor",
      "öğrenme süreci o kadar uzar" in SP, "")
check("4d) SYSTEM_PROMPT küçük artış örneğini veriyor (30 saniye / 1 dakika)",
      "30 saniye" in SP and "1 dakika" in SP, "")


# --- 5) Plan motoru ------------------------------------------------------------------
for _tip in ("13_gun_dirençli", "6_gun_buyuk_cocuk", "5_gun_standart"):
    _b = bekleme_sureleri_planla(_tip)
    check(f"5) {_tip}: plan parametrelerinde artis_esnekligi var",
          "artis_esnekligi" in _b, str(sorted(_b)))
    check(f"5) {_tip}: yatır-çık kademesi STANDART olarak sunuluyor",
          "STANDART" in _b["yatir_cik_sonrasi"], _b["yatir_cik_sonrasi"][:80])
    check(f"5) {_tip}: gece uyanma kademesi ertesi günün ÜSTÜNDE başlamayı söylüyor",
          "ÜSTÜNDE" in _b["gece_uyanma_dis_bekleme"], _b["gece_uyanma_dis_bekleme"])


# --- 6) CANLI — İlayda'nın istediği cevap gerçekten geliyor mu? -------------------
# EVET denmeli, AMA sürecin uzayacağı da söylenmeli. Ayrıca "aynı kalabilir"
# cevabı REGRESYONDUR (değişmez kural).
_OLUMLU = re.compile(
    r"\bevet\b|bekleyebilirsiniz|artırabilirsiniz|çıkabilirsiniz|mümkün|olabilir|"
    r"uygundur|sorun (?:yok|olmaz)|yapabilirsiniz", re.IGNORECASE)
_UZAR = re.compile(r"uza(?:r|yabilir|yacak|tır|tabilir)|daha uzun|zaman al", re.IGNORECASE)
# Model aynı şeyi çok farklı diziyor: "artış mutlaka olmalı", "her gün artmalı",
# "aynı süre bekleme", "daha az veya aynı". Kalıp bunların hepsini tutmalı —
# aksi halde DAVRANIŞ doğruyken test kalır (ölçümde bir kez böyle oldu).
_ARTIS_ZORUNLU = re.compile(
    r"her (?:gün|gece)|mutlaka art|artmalı|artması gerek|artış (?:mutlaka|şart)|"
    r"mutlaka olmalı|aynı kal|aynı süre|düşük ol|daha az", re.IGNORECASE)
_AYNI_KALABILIR = re.compile(
    r"aynı (?:süre|dakika)(?:yi|yı)? (?:tekrar )?(?:bekleyebilir|koruyabilir|"
    r"sürdürebilir)|aynı kalmasında (?:bir )?(?:sakınca|sorun) yok", re.IGNORECASE)


def _sor(soru: str) -> tuple[str, str]:
    cb._cache_state["entries"] = []
    cb._rebuild_emb_matrix()
    r = cb._cevap_uret(soru)
    return r["cevap"], r["retrieval_layer"]


if not HAS_KEY:
    print("[ATLA ] Canlı bölüm — ANTHROPIC_API_KEY yok")
else:
    _cevap, _katman = _sor("birinci gece 5 dakika bekledim, "
                           "ikinci gece 6 dakika bekleyebilir miyim?")
    print(f"\n--- [{_katman}] 5 dk → 6 dk sorusu")
    print("    " + _cevap[:600].replace("\n", " "))

    check("6a) Cevap üretildi (K4 değil)", _katman != "k4", _katman)
    check("6b) EVET diyor — 1 dakikalık artışı kabul ediyor",
          bool(_OLUMLU.search(_cevap)), _cevap[:250])
    check("6c) Küçük artışın bedelini söylüyor (süreç uzar)",
          bool(_UZAR.search(_cevap)), _cevap[:400])
    check("6d) Artışın zorunlu olduğunu da hatırlatıyor",
          bool(_ARTIS_ZORUNLU.search(_cevap)), _cevap[:400])
    check("6e) Katı '10 dakika olmalı' dayatması yok",
          not re.search(r"(?:mutlaka|kesinlikle|şart(?:tır)?)\s*(?:10|on)\s*dakika",
                        _cevap, re.IGNORECASE), _cevap[:300])

    # DEĞİŞMEZ KURAL regresyonu: esneklik açıldı diye "aynı kalabilir" DENMEMELİ.
    _cevap2, _katman2 = _sor("birinci gece 5 dakika bekledim, "
                             "ikinci gece de 5 dakika bekleyebilir miyim?")
    print(f"\n--- [{_katman2}] 5 dk → 5 dk sorusu (değişmez kural)")
    print("    " + _cevap2[:600].replace("\n", " "))

    check("6f) Aynı süreyi tekrarlamaya ONAY VERMİYOR",
          not _AYNI_KALABILIR.search(_cevap2), _cevap2[:300])
    # Gövdede düzeltmek YETMEZ: anne ilk cümleyi okuyup uygular. Ölçümde cevap
    # "Evet, ikinci gecede de 5 dakika ile başlayabilirsiniz — ama..." diye
    # açılıyordu; açılış cümlesi tek başına değişmez kuralı bozuyor.
    check("6f2) Cevabın AÇILIŞI onaylayıcı değil (ilk cümle 'Evet' demiyor)",
          not re.match(r"\s*(?:\*\*)?evet\b", _cevap2, re.IGNORECASE),
          _cevap2[:120])
    check("6g) Her gün artması gerektiğini söylüyor",
          bool(_ARTIS_ZORUNLU.search(_cevap2)), _cevap2[:400])


# --- Özet ------------------------------------------------------------------------
print("\n" + "=" * 74)
print("BEKLEME SÜRESİ ESNEKLİĞİ TEST SONUÇLARI (İlayda düzeltmesi 2026-08-25)")
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
