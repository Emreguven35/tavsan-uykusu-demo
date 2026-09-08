"""
K0 normalizasyon regresyon testi — Türkçe ı/i çakışması.

BUG: normalizasyon "ı" harfini "i"ye katlıyordu. Böylece EN YAYGIN anne cümlesi
("bebeğim gece sık sık uyanıyor") küfür sözlüğündeki "sik" köküne çarpıp K0'da
bloklanıyordu. Aynı hata "sıkıntı", "sıkışık", "sıkıcı", "sıkıldım" gibi bebek
uykusu/beslenme sözlüğünün göbeğindeki kelimeleri de vuruyordu.

ÇÖZÜM (üç katman):
  1. "ı" korunur — ı ve i Türkçe'de AYRI harflerdir; küçültme de Türkçe kurallı
     yapılır (I→ı, İ→i), yoksa "SIK SIK" yine "sik"e düşerdi.
  2. Sözlük eşleşmesinde ı→i katlanmış biçime de bakılır (sözlük ascii'dir:
     "gerizekali"), ama katlamanın tek tehlikeli çakışması olan "sik-" ailesi
     BLOKLANMAZ — belirsiz sayılır.
  3. Noktasız yazan anne ("sik sik uyaniyor") harf harf küfürle aynıdır; bu
     biçim K0'ı geçer, K1'de "belirsiz_hakaret" ile flagged olur, kararı K2'deki
     Haiku bağlama bakarak verir. Fail-open: masum anne susturulmaz.

Çalıştırma: python tests/test_moderasyon_normalizasyon.py
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

from api.services import moderation as m          # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def temiz(etiket, metin):
    """K0'dan geçmeli (engel YOK)."""
    r = m.check_content(metin)
    check(f"{etiket}: {metin!r} → geçer", r is None, f"engellendi: {r}")


def engelli(etiket, metin, sebep="hakaret"):
    r = m.check_content(metin)
    check(f"{etiket}: {metin!r} → {sebep}", r == sebep, f"dönen: {r}")


# =============================================================================
# 1) ASIL BUG — "sık" ailesi asla hakaret sayılmaz
# =============================================================================
for cumle in [
    "bebeğim gece sık sık uyanıyor",              # bug raporundaki cümle
    "bebeğim sık uyanıyor ne yapmalıyım",
    "gece çok sık emmek istiyor",
    "sıkıntı yaşıyorum uyku düzeninde",
    "bu sıkça oluyor endişelendim",
    "bezi sıkışık geliyor bacaklarını sıkıyor",
    "kundağı sıkı sarmak lazım mı",
    "akşamları çok sıkılıyor ve ağlıyor",
    "ben de çok sıkıldım açıkçası",
    "mama sıkışması olabilir mi",
    "sıkıcı bir rutin oldu",
    "arabada sıkıştırma yapıyor",
]:
    temiz("1) sık ailesi", cumle)

# Büyük harf tuzağı: Python .lower() "I"yı "i" yapar → Türkçe kurallı olmalı.
# (Baştan sona büyük harf ayrı bir kural olan caps-lock spam'ine takılır; burada
# ölçülen o değil, ı/i katlaması.)
for cumle in ["Bebeğim SIK uyanıyor", "SIKINTI yaşıyorum uyku düzeninde",
              "Sıkışık geliyor", "SIK SIK uyanıyor diye yazmıştım"]:
    temiz("1b) büyük harf", cumle)

# =============================================================================
# 2) Noktasız klavye — bloklanmaz ama K2 incelemesine gider
# =============================================================================
for cumle in ["bebegim gece sik sik uyaniyor", "cok sik uyaniyor", "sikisik geliyor",
              "sikinti yasiyorum", "bebegim cok sikildi"]:
    temiz("2) noktasız sık", cumle)

flagged, sebepler = m.risk_flags("bebegim gece sik sik uyaniyor", 10)
check("2b) noktasız 'sik' → K1 flagged (Haiku'ya devir)",
      flagged and "belirsiz_hakaret" in sebepler, str(sebepler))
flagged2, sebepler2 = m.risk_flags("bebeğim gece sık sık uyanıyor", 10)
check("2c) doğru yazılmış 'sık' → flag bile YOK",
      not flagged2 and not sebepler2, str(sebepler2))

hard, belirsiz = m.scan_profanity("sik sik uyaniyor")
check("2d) scan_profanity: net değil, belirsiz", (not hard) and belirsiz, f"{hard},{belirsiz}")

# =============================================================================
# 3) GERİLEME YOK — gerçek hakaret hâlâ bloklanıyor
# =============================================================================
for kufur in [
    "siktir git",           # net çekim: "sık-" ailesinde karşılığı yok
    "siktir lan",
    "sikeyim seni",
    "sikerim böyle işi",
    "sikimde değil",
    "sikik herif",
    "orospu herif",
    "orospu çocuğu",
    "kahpe",
    "yavşak",
    "piç kurusu",
    "pezevenk",
]:
    engelli("3) net hakaret", kufur)

# ı içeren hakaretler: sözlük ascii, kullanıcı Türkçe yazar → katlanmış biçim şart
for kufur in ["gerizekalı", "gerizekali", "amına koyayım", "kancık", "sürtük",
              "şerefsiz", "haysiyetsiz", "namussuz"]:
    engelli("3b) ı içeren hakaret", kufur)

# Kaçırma teknikleri
engelli("3c) leetspeak", "s1kt1r git")
engelli("3d) ayraç kaçışı", "s.i.k.t.i.r")
engelli("3e) ayraç kaçışı (boşluk)", "a m i n a k o y a y i m")
engelli("3f) harf tekrarı", "siktiiiir")

# =============================================================================
# 4) Eski false-positive koruması bozulmadı (ebeveyn/beslenme bağlamı)
# =============================================================================
for cumle in ["bebeğim memeyi bırakmıyor", "hıyar turşusu tarifi", "sikke koleksiyonu",
              "aşı sonrası ateşi çıktı", "salatalık rendeledim", "çocuğu parka götürdüm"]:
    temiz("4) masum bağlam", cumle)

# =============================================================================
# 5) Diğer K0 kapıları etkilenmedi
# =============================================================================
engelli("5a) telefon", "ara beni 05321234567", "iletisim_bilgisi")
engelli("5b) url", "detaylar sitemde www.ornek.com", "iletisim_bilgisi")
engelli("5c) boş", "   ", "bos")

# K1 risk sözlükleri ı katlaması olmadan da çalışmalı
f_tib, s_tib = m.risk_flags("bebeğe aşı yaptırdım ateşi çıktı", 10)
check("5d) K1 tıbbi ('aşı' → 'ası' olsa bile yakalanır)",
      f_tib and "tibbi_risk" in s_tib, str(s_tib))
f_tic, s_tic = m.risk_flags("uygun fiyata satılık kıyafet tıkla", 10)
check("5e) K1 ticari ('satılık'/'tıkla' yakalanır)",
      f_tic and "ticari" in s_tic, str(s_tic))

# =============================================================================
# 6) Normalizasyon birimi
# =============================================================================
check("6a) _basic 'ı'yı korur", m._basic("sık") == "sık", m._basic("sık"))
check("6b) _basic 'sik'e dokunmaz", m._basic("sik") == "sik", m._basic("sik"))
check("6c) 'sık' ile 'sik' AYRI", m._basic("sık") != m._basic("sik"), "")
check("6d) Türkçe küçültme: I→ı", m._basic("SIK") == "sık", m._basic("SIK"))
check("6e) Türkçe küçültme: İ→i", m._basic("İYİ") == "iyi", m._basic("İYİ"))
check("6f) diğer diyakritikler hâlâ katlanır", m._basic("ŞEKER ÇÖREĞİ") == "seker coregi",
      m._basic("ŞEKER ÇÖREĞİ"))
check("6g) belirsiz: 'sik'", m._is_ambiguous_sik("sik"), "")
check("6h) belirsiz DEĞİL: 'siktir'", not m._is_ambiguous_sik("siktir"), "")
check("6i) belirsiz DEĞİL: 'sikeyim'", not m._is_ambiguous_sik("sikeyim"), "")
check("6j) belirsiz kökler güçlü listede değil",
      not any(m._is_ambiguous_sik(w) for w in m._STRONG), "")


# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("K0 NORMALİZASYON (ı/i ÇAKIŞMASI) TEST SONUÇLARI")
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
