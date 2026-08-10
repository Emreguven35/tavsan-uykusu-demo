"""
Korpus filtreleri — gün↔aşama temizliği ve danışmanlık lojistiği arşivi (Faz O3).

NEDEN VAR: iki sessiz sızıntı ölçümle yakalandı.
  1. GÜN NUMARALANDIRMASI — ham transkriptler ESKİ 5 günlük programı anlatıyor
     ("üçüncü gün oda ortası", "beşinci gün yatır-çık"). 18 ayrı kayıtta geçiyordu.
     Uygulanan program 13 GÜNLÜK. Model "3. gündeyim" sorusuna "oda ortası",
     "6. gün" sorusuna "yatır-çık" diyordu (ölçüm 11/13 → filtre + gün gün liste
     birimi sonrası 13/13).
  2. DANIŞMANLIK LOJİSTİĞİ — rapor/video/tablo gönderme, iletişim saatleri,
     paket, ücret iadesi metodolojiyle aynı chunk'ta. "Ben beceremiyorum" gibi
     sorular "danışmanınıza yazın" cevabına kayıyordu. Uygulama danışman değil,
     ÜRÜN (ölçüm 1/12 → 0/12).

Bu dosya iki filtrenin de yerinde kalmasını sabitler. Kaynak dosya chunks.json
DEĞİŞTİRİLMEZ — filtreler okuma anında uygulanır (marka temizliğiyle aynı desen);
bu da ayrıca test edilir, yoksa bir sonraki dokunuşta transkript arşivi bozulur.

Çalıştırma: python tests/test_korpus_filtreleri.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from engine import chatbot as cb  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


UNITS = cb.build_corpus()
METINLER = {u["chunk_id"]: u["text"] for u in UNITS}
HAM = {x["chunk_id"]: x["text"] for x in cb.load_chunks()}
ARSIV = cb._arsiv_chunk_idleri()


def cumleler(t: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", t)


def gun_asama_cumlesi(c: str) -> bool:
    return bool((cb._GUN_DESENI.search(c) and cb._ASAMA_DESENI.search(c))
                or cb._KAC_GUNDE_DESENI.search(c))


# --- 1) gun_asama_temizle birim davranışı --------------------------------------
check("1a) Gün↔aşama cümlesi düşer",
      cb.gun_asama_temizle("Birinci gün, ikinci gün beşik yanı, üçüncü gün oda ortasındaydık.") == "",
      cb.gun_asama_temizle("Birinci gün, ikinci gün beşik yanı, üçüncü gün oda ortasındaydık."))

_teknik = "Otuz saniye bekledik. Sonra kucağa aldık ve yatırdık."
check("1b) Teknik anlatım KORUNUR (gün numarası yoksa dokunulmaz)",
      cb.gun_asama_temizle(_teknik) == _teknik, cb.gun_asama_temizle(_teknik))

_karma = "Otuz saniye bekleyin. Beşinci gün sadece yatırıp çık yaparız. Sonra sandalyeye dönün."
_ciktı = cb.gun_asama_temizle(_karma)
check("1c) Karma metinde SADECE eşleme cümlesi düşer",
      "Otuz saniye" in _ciktı and "sandalyeye dönün" in _ciktı and "Beşinci gün" not in _ciktı,
      _ciktı)

check("1d) Gün numarası TEK BAŞINA cümleyi düşürmez",
      cb.gun_asama_temizle("Dördüncü günde her şey aynıdır.") != "", "")

check("1e) 'odasının/odamızın ortası' varyantı da yakalanır",
      cb.gun_asama_temizle("Üçüncü gün odasının ortasındayız.") == "", "")

check("1f) 'beş günde yatır çık' süre iddiası düşer",
      cb.gun_asama_temizle("Genelde yüzde doksan bebekte beş günde yatırcık yaparız.") == "", "")

check("1g) 'üçüncü gün uzaklaşmayacağız' da bir eşleme iddiasıdır",
      cb.gun_asama_temizle("O zaman üçüncü gün uzaklaşmayacağız, dördüncü gün uzaklaşacağız.") == "",
      "")

check("1h) Boş/None girdi çökmez",
      cb.gun_asama_temizle("") == "" and cb.gun_asama_temizle(None) is None, "")


# --- 2) Korpusta eski numaralandırma kalmadı -----------------------------------
_kalan = [(cid, c.strip()) for cid, t in METINLER.items()
          for c in cumleler(t) if gun_asama_cumlesi(c)]
# Curated 'kural_' birimleri filtreden MUAF (gözden geçirilmiş içerik; cümle
# düşürmek anlamlarını bozuyor) — onlar bu sayımın dışında.
_kalan_transkript = [x for x in _kalan if not x[0].startswith("kural_")]
check("2a) Transkript birimlerinde gün↔aşama eşlemesi KALMADI",
      not _kalan_transkript, f"{len(_kalan_transkript)} cümle: {_kalan_transkript[:3]}")

check("2b) Curated 'kural_' birimleri filtreden muaf (metinleri bozulmadı)",
      all(METINLER.get(cid) == HAM.get(cid) or cb.marka_temizle(HAM[cid]) == METINLER[cid]
          for cid in METINLER if cid.startswith("kural_")), "")

check("2c) Eski 5 günlük eşlemenin bilinen taşıyıcıları temizlendi",
      all(not gun_asama_cumlesi(c)
          for cid in ("kayıt36_chunk_044", "kayıt37_chunk_036", "kayıt38_chunk_017",
                      "kayıt6_chunk_001", "kayıt26_chunk_001")
          if cid in METINLER
          for c in cumleler(METINLER[cid])), "")


# --- 3) Lojistik arşivi ---------------------------------------------------------
check("3a) Arşiv listesi dolu ve dosyadan okunuyor", len(ARSIV) >= 25, len(ARSIV))

check("3b) Arşivdeki hiçbir chunk korpusa girmedi",
      not (ARSIV & set(METINLER)), sorted(ARSIV & set(METINLER))[:5])

check("3c) Ücret iadesi kaydının TAMAMI arşivde (kayıt28)",
      all(f"kayıt28_chunk_{i:03d}" in ARSIV for i in range(1, 9)), "")

check("3d) Paket/iletişim kuralları kaydı arşivde (kayıt19)",
      all(f"kayıt19_chunk_{i:03d}" in ARSIV for i in range(1, 5)), "")

check("3e) Metodoloji chunk'ları arşivlenMEDİ (aşırı temizlik yok)",
      all(cid in METINLER for cid in
          ("kayıt36_chunk_005", "kayıt36_chunk_016", "kayıt36_chunk_085",
           "kayıt37_chunk_030", "kayıt21_chunk_017")), "")

_arsiv_orani = len(ARSIV) / max(len(HAM), 1)
check("3f) Arşiv korpusun küçük bir bölümü (<%10)", _arsiv_orani < 0.10,
      f"%{100*_arsiv_orani:.1f}")

check("3g) Her arşiv kaydının GEREKÇESİ yazılı",
      all(x.get("gerekce", "").strip()
          for x in json.loads((cb.DATA_DIR / "chunk_konulari.json")
                              .read_text(encoding="utf-8"))["arsiv"]), "")


# --- 4) Kaynak dosya bozulmadı --------------------------------------------------
# Filtreler OKUMA anında uygulanır; transkript arşivi olduğu gibi durmalı.
check("4a) chunks.json'daki chunk sayısı değişmedi (506)", len(HAM) == 506, len(HAM))
check("4b) Arşivlenen chunk'lar chunks.json'da HÂLÂ duruyor",
      all(cid in HAM for cid in ARSIV),
      sorted(cid for cid in ARSIV if cid not in HAM)[:5])
check("4c) Filtrelenen cümleler kaynak dosyada duruyor",
      "beşinci gün" in HAM.get("kayıt36_chunk_042", "").lower(), "")


# --- 5) 13 günlük merdivenin gün gün aranabilir birimi ---------------------------
_ID = "global_rule:kademeli_uzaklasma_13_gun_dirençli.gun_gun_liste"
check("5a) Gün gün merdiven birimi korpusta var", _ID in METINLER, "")
_m = METINLER.get(_ID, "")
_eksik = [g for g in range(1, 14) if f"{g}. gün" not in _m]
check("5b) 13 günün TAMAMI tek tek yazılı (aralık gösterimi sınırda yanlış okunuyordu)",
      not _eksik, f"eksik={_eksik}")
check("5c) Eşleme doğru: 3. gün beşik yanı, 6. gün oda ortası, 13. gün yatır-çık",
      re.search(r"3\. gün: Beşik yanı", _m) and re.search(r"6\. gün: Oda ortası", _m)
      and re.search(r"13\. gün: Yatır-çık", _m), _m[:200])
check("5d) Metin KB'den türetiliyor (elle yazılmadı — merdiven değişirse birim de değişir)",
      "gun_gun_liste" not in json.loads(
          (cb.DATA_DIR / "master_knowledge_base.json").read_text(encoding="utf-8")
      )["global_rules"]["kademeli_uzaklasma_13_gun_dirençli"], "")
check("5e) Birim eski numaralandırmanın geçersiz olduğunu söylüyor",
      "5 günlük" in _m and "uygulanmıyor" in _m, "")


# --- 6) Korpus hacmi makul kaldı ------------------------------------------------
_chunk_sayisi = sum(1 for u in UNITS if u["source"] == "chunk")
check("6a) Chunk birimi sayısı = 506 - arşiv", _chunk_sayisi == 506 - len(ARSIV),
      f"{_chunk_sayisi} (beklenen {506-len(ARSIV)})")
check("6b) Toplam korpus 550'nin üzerinde (aşırı budama yok)", len(UNITS) > 550, len(UNITS))
_bos = [u["chunk_id"] for u in UNITS if not u["text"].strip()]
check("6c) Filtre sonrası boş birim yok", not _bos, _bos[:5])


# --- Özet ----------------------------------------------------------------------
print("=" * 74)
print("KORPUS FİLTRELERİ TEST SONUÇLARI")
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
