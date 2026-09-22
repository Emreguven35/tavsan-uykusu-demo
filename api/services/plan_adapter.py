"""
Adaptif plan motoru — KURAL TABANLI, LLM KULLANMAZ (Faz 6.1).

Amaç: uygulamadaki uyudu/uyandı kayıtları (sleep_logs) plana geri beslensin.
Tüm mantık deterministiktir: aynı girdi → aynı çıktı. Bu yüzden birim testle
tam kapsanabilir ve maliyet üretmez (Claude çağrısı YOK).

SÜRÜM 2 — GÜN İÇİ KAYMA MOTORU (K1-K9, metodoloji sahibinin kuralları).
v1'de çizelge son 3 günün ORTALAMA sabah uyanışına göre ±45 dk kaydırılıyor ve
kaydırılmış çizelge ertesi günün TABANI oluyordu. Bunun üç sonucu vardı: gündüz
uykularının kayması plana hiç yansımıyordu, kayma günlerce birikiyordu ve gün
içinde bir kez hesaplanıp kilitleniyordu. Hepsi kaldırıldı.

Akış:
    1. build_schedule()  — YAŞ BANDI TABLOSUNDAN (data/yas_bantlari.json, Faz Y)
       ÜRETİM ANINDA bir kez çizelge kur. Bu çizelge content.schedule_template
       olarak saklanır ve EĞİTİM BOYUNCA DEĞİŞMEZ (K1).
    2. recompute_day()   — BUGÜNÜN çizelgesini şablon + BUGÜNÜN kayıtlarından
       zincirleme kur: gerçek uyanış/uyku bitişi + uyanıklık penceresi (K2/K3).
       Kayıt yoksa blok "planlandığı gibi oldu" sayılır ve işaretlenir (K6);
       sonradan kayıt gelirse varsayım bozulur ve zincir yeniden akar (K7).
    3. summarize_logs()  — YALNIZ İSTATİSTİK (regresyon sayımı, chat bağlamı).
       Çizelgeyi artık BELİRLEMEZ; sabah uyanışının ortalaması alınmaz (K5).
    4. detect_regression() — İlayda protokolü: eğitim bittikten ≥13 gün sonra
       "kendine dalamama" sinyali görülürse REGRESYON bayrağı kaldır (Faz 6.1R).

İKİ AYRI KATMAN (karıştırılmamalı):
  • Gün içi yeniden hesaplama → o günün ritmi; yarına TAŞINMAZ (K1/K2).
  • Regresyon tespiti → eğitim TAMAMLANDIKTAN sonraki geri gidişin yakalanması.
Regresyon hiçbir şeyi otomatik üretmez. v1.4'te (İlayda S9) "Programı baştan
başlatalım mı?" kartı KALDIRILDI; yerine üç kademeli akış geldi: önce anneye
"kendi uykuya dönüyor mu?" sorulur (POST /plans/regresyon-cevap), dönmüyorsa
45 gün dolana kadar EĞİTİME DEVAM edilir, 45 gün dolduysa tıbbi değerlendirme
önerilir. Bkz. `regresyon_karti()`.

ZAMAN DİLİMİ: sleep_logs UTC saklanır, plan çizelgesi ise yerel duvar saatidir.
Kullanıcı bazlı timezone alanı henüz YOK; TZ_OFFSET_MIN varsayılanı Türkiye
(UTC+3, DST yok). Çok ülkeli sürümde users tablosuna timezone eklenmelidir.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from engine import yas_bantlari

logger = logging.getLogger("tavsan.plan_adapter")

# --- Sabitler (spec) ---------------------------------------------------------
# LOOKBACK_DAYS artık YALNIZ İSTATİSTİK penceresidir (regresyon sayımı, chat
# bağlamı, "yeterince uyuyor mu"). Gün planı buna BAKMAZ — v2'de plan yalnız
# BUGÜNÜN kayıtlarından hesaplanır (K2).
LOOKBACK_DAYS = 3
TZ_OFFSET_MIN = 180             # UTC+3 (Europe/Istanbul)

# K5 + K10 — Sabah uyanışı tespiti.
# Sabah uyanışı = günün SON uyanışıdır (ilk gündüz uykusundan önceki). Saat
# sınırı YOKTUR: 09:00'da uyanan bebeğin günü 09:00'dan kurulur (K10.2).
# v2.1'de kaldırılanlar: 04:00 sabit MORNING_WINDOW ve hedefe göreli
# "±90 dk tolerans" (SABAH_TOLERANS_DK). Yerine tek bir mutlak sınır geldi:
#
# K10.1 — GÜNÜN BAŞLANGICI EN ERKEN 06:00'DIR.
# Bebek 06:00'dan önce uyanıp TEKRAR UYUMADIYSA gerçek saat ne olursa olsun gün
# 06:00'dan başlatılır. 06:00'dan önce uyanıp tekrar uyuduysa o uyanış bir gece
# bölünmesidir ve sabah uyanışı SON uyanıştır.
GUN_BASLANGICI_EN_ERKEN = 6 * 60             # 06:00

# K10.3 — Erken uyanmada güne eklenen ek şekerleme bloğu.
# Gündüz uyku SAYISINDAN biri değildir (bant n=3 derken bu sayılmaz); şablona da
# girmez, yalnız o günün çizelgesinde görünür. K4 gün taşırsa bu blok ASLA
# kaldırılmaz — önce normal gündüz uykuları kısaltılır (K10.4).
SEKERLEME_KEY = "sekerleme"
# Süre ve pencere artık TABLODAN gelir (yas_bantlari v1.4
# evrensel_kurallar.kestirme_protokolu). Buradaki değerler yalnız tablo
# okunamazsa kullanılan emniyet payıdır.
SEKERLEME_DK = 30                       # geriye uyum: taban süre
SEKERLEME_VARSAYILAN_PENCERE = (17 * 60, 19 * 60)
# 12-18 ay TEK UYKU varyantında pencere daralır: İlayda "ortalama 18:00 gibi
# sabit bir saatte yaptırıyoruz şekerlemeyi, ya da 19:00 gibi" dedi.
SEKERLEME_TEK_UYKU_PENCERE = (18 * 60, 19 * 60)


def sekerleme_basligi(sure_dk: int) -> str:
    """Başlık süreye göre — sabit "Şekerleme (30 dk)" artık yanlış olabilir."""
    return f"Şekerleme ({int(sure_dk)} dk)"

# Bir `sleep` kaydının "gece uykusu" sayılması için ölçüt (K5/K-senaryosu):
# ya bandın gece uykusu ALT SINIRININ bu oranı kadar sürmüş olmalı, ya da
# bir önceki yerel günde başlamış olmalı (gece uykusu gece yarısını aşar).
# 70 dakikalık bir `sleep` kaydı gece uykusu OLAMAZ; gündüz uykusu işlenir.
GECE_UYKUSU_MIN_ORAN = 0.5

# --- Regresyon protokolü (İlayda, Faz 6.1R) ---------------------------------
# Eğitim bitiminden bu kadar gün SONRA regresyon aranmaya başlanır. 14 günlük
# modülün son gününden itibaren ≥13 gün → "eğitim oturmuş olmalıydı" eşiği.
REGRESSION_MIN_DAYS_AFTER_TRAINING = 13
# Bir gece uyanmasının "kendine dalamama" sayılması için minimum süre.
SELF_SOOTHE_FAIL_MIN = 20
# Bu sinyalin kaç AYRI gecede görülmesi gerektiği (son 3 gece içinde).
REGRESSION_MIN_NIGHTS = 2
REGRESSION_LOOKBACK_NIGHTS = 3

# 45-15-45 gece direnme protokolü — HESAPLAMA DEĞİL, plan İÇERİĞİ kuralıdır.
# Plan üretiminde content.night_wake_protocol olarak saklanır (mobil gösterir).
NIGHT_WAKE_PROTOCOL = {
    "resist_minutes": 45,
    "routine_minutes": 15,
    "repeat": True,
    "aciklama": ("45 dk bekleyip kendi kendine dalmasına izin verin; dalamazsa "
                 "15 dk kısa rutin yapın, sonra tekrar 45 dk deneyin — uykuya "
                 "geçene kadar."),
}

DEFAULT_WAKE_MIN = 7 * 60       # plan/kayıt yoksa varsayılan sabah uyanışı 07:00
DEFAULT_BEDTIME_RANGE = (19 * 60, 21 * 60)   # yatma_vakti tanımsız bandlar için
DEFAULT_WAKE_WINDOW = (120, 180)             # uyanıklık penceresi tanımsızsa
DEFAULT_DAY_SLEEP = (2 * 60, 3 * 60)         # gündüz uyku toplamı tanımsızsa

# İstatistik (kaydırma DEĞİL): gece yatışı bu pencerede aranır.
BEDTIME_WINDOW = (16 * 60, 24 * 60 + 2 * 60)  # 16:00–02:00 (ertesi güne taşabilir)

# "Atlandı" kaydı: bebek o uykuyu HİÇ yapmadı (K7). İki biçim de kabul edilir —
# açık tip, ya da süresi sıfır olan bir `nap` kaydı (eski istemciler için).
ATLANDI_TIPI = "nap_skipped"

# --- K12/K13 — kayıt semantiği (v2.2) ----------------------------------------
# Beta annelerinin gerçek verisinde ölçülen dört hata bu sabitlerin etrafında
# toplanıyor. Hepsi TEK yerde tanımlı: eşik iki yerde ayrı yazıldığında biri
# güncellenip diğeri unutuluyordu (K11 ile regresyon protokolünde yaşandı).

# K12.1 — Bir uyku kaydının YUVAYA EŞLENEBİLMESİ için gereken en kısa süre.
# Altındaki kayıtlar gerçek bir uyku değil, dokunmatik kazası ya da anlık
# "kaydet/durdur"dur; yuvayı tüketip zinciri oradan başlatıyorlardı.
UYKU_MIN_SURE_DK = 5

# K12.2 — Bitişi girilmemiş gece uyanması için SAYIM amaçlı varsayılan süre.
# Yalnız "ne kadar sürdü" sorusuna yaklaşık cevap üretir; kaydın kendisi
# DEĞİŞTİRİLMEZ ve bu değer hiçbir zaman DB'ye yazılmaz.
GECE_UYANMA_VARSAYILAN_DK = 10

# K12.2 — "Uzun uyanma" (kendine dalamama) alt metriğinin eşiği. ANA gece
# uyanma sayımı bu eşiğe BAKMAZ; eskiden bakıyordu ve 3 uyanmalı bir gece
# "0 uyanma" olarak raporlanıyordu (beta verisinde ölçüldü).
UZUN_UYANMA_MIN_DK = SELF_SOOTHE_FAIL_MIN

# --- K19 — UYKU TİPİ SAATE GÖRE BELİRLENİR (v2.2.2) -------------------------
# ÜRÜN KARARI: mobil artık anneye uyku tipi sordurmuyor. Anne yalnız "Uyudu" /
# "Uyandı" diyor; gündüz/gece ayrımını BACKEND yapıyor. İstemcinin gönderdiği
# type (sleep/nap/sekerleme) artık yalnız "bu bir uyku" bilgisidir; SINIFI
# aşağıdaki fonksiyon verir. DB'deki type'a DOKUNULMAZ — eski istemciler
# (build 16/18) farklı type göndermeye devam edebilir ve hepsi desteklenir.
#
# Eski kural başlangıç saatini SABAH HEDEFİYLE karşılaştırıyordu; hedef
# kullanıcıdan kullanıcıya değiştiği için aynı saatteki kayıt bir bebekte gece,
# diğerinde gündüz sayılabiliyordu. Yeni kural mutlak duvar saatidir.
GUNDUZ_UYKUSU = "gunduz_uykusu"
GECE_UYKUSU = "gece_uykusu"
UYKU_ETIKETLERI = {GUNDUZ_UYKUSU: "Gündüz uykusu", GECE_UYKUSU: "Gece uykusu"}

GUNDUZ_PENCERE_BAS = 6 * 60      # 06:00 — bundan önce başlayan uyku gecedir
# v1.4 (İlayda, 2026-09-21): "17 çok erken gece uykusu için. Gece uykusu için
# 19:00 ve sonrasını baz almamız gerekiyor. 17:00 bizim için kısa gündüz
# uykusu, şekerleme ya da ilave uyku saatleri olur."
GUNDUZ_PENCERE_BIT = 19 * 60     # 19:00 — bundan sonra başlayan uyku gecedir

# İSTİSNA: 19:00'dan sonra başlayan KISA ve KAPALI kayıt, gece yatışı değil
# başarısız bir yatış denemesi / geç şekerlemedir. Açık kayıtta istisna
# UYGULANMAZ: süresi bilinmeyen bir 20:00 kaydını "kısa" varsaymak gerçek gece
# uykusunu gündüz uykusuna çevirirdi.
#
# ÜST SINIR 24:00 — spec "19:00'dan sonra" diyor ama sınırsız bırakılırsa
# 02:00'de 30 dk uyuyup uyanan bebeğin o parçası "gündüz kısa uykusu" olurdu.
# Gece yarısından sonrası her koşulda gecedir.
AKSAM_ISTISNA_BIT = 24 * 60      # 00:00 (ertesi gün)
# Eşik artık SABİT DEĞİL, banda bağlı (kisa_uyku_esigi_dk): 6 ay altı 45 dk,
# 6 ay ve üstü 60 dk. Bant yoksa 6 ay+ değerine düşülür — uygulamanın kullanıcı
# kitlesinin ezici çoğunluğu orada.
VARSAYILAN_KISA_UYKU_ESIGI_DK = 60

# --- K20 — parça kayıt birleştirme ------------------------------------------
# Anne uzun bir uykuyu birden çok kayda bölüyor (14:00-14:36 + 14:36-15:46) ya
# da kısa bir uyanıklıktan sonra yeniden uyutuyor (09:36 bitiş, 09:50 başlangıç).
# Bunlar ne kopya ne çakışma; K13/K18 hiçbiri yakalamıyordu ve gün 3 yerine 5
# gündüz uykusu görünüyordu (gerçek vakada ölçüldü).
#
# EŞİK v1.4'te İKİYE AYRILDI (İlayda, S4): "bir uykunun bir saat sürmesi
# gerekiyor. Ama çocuk 15 dakika uyudu. 45 dakika boyunca çocuğu uykuya geri
# döndürmeye çalıştık ve çocuk geri uyudu... bizim için bu tek uyku."
# Yani ilk parça KISA kaldıysa (bandın kisa_uyku_esigi_dk'sının altında) anne
# hedef süreyi tutturmak için uzun süre uğraşır — boşluk 45 dk'ya kadar aynı
# uykudur. İlk parça zaten TAM bir uykuysa 15 dk'lık kısa bir uyanıklık aynı
# uykunun devamı sayılır, daha fazlası ayrı uykudur.
PARCA_BIRLESTIRME_DK = int(os.getenv("PARCA_BIRLESTIRME_DK") or 15)
PARCA_BIRLESTIRME_KISA_DK = int(os.getenv("PARCA_BIRLESTIRME_KISA_DK") or 45)

# --- K16/K17 — açık (ended_at null) kayıt kuralları (v2.2.1) ----------------
# Gerçek vaka: aynı bebekte 06:30, 08:24 ve 08:26 başlangıçlı ÜÇ açık uyku
# kaydı vardı. Üçü birden "sürüyor" sayılıyor, üçü de ayrı yuva tüketiyor ve
# gün 5 gündüz uykusuyla + "şablonda olmayan ilave uyku" notlarıyla çıkıyordu.

# K17.1 — Açık bir `nap` kaydının "artık sürmüyor" sayılma eşiği:
# bandın uyku süresinin 2 katı, ama en az bu kadar. Bebek 3 saattir aralıksız
# kestirmiyor; kaydı kapatmak unutulmuştur.
K17_NAP_ESIK_MIN_DK = 3 * 60
# Açık GECE uykusu için aynı eşik. Gece uykusu saatler sürer, bu yüzden çok
# daha geniş: 14 saati aşmışsa artık uyku değil, unutulmuş sayaçtır.
K17_GECE_ESIK_DK = 14 * 60

# K13.1 — İki uyku kaydının "aynı uyku" sayılması için gereken örtüşme oranı.
# Payda KISA olan kaydın süresidir: 10 dakikalık bir kayıt 2 saatlik bir kaydın
# içine düşüyorsa örtüşme %100'dür, %8 değil.
CAKISMA_ESIGI = 0.5


# =============================================================================
# Yaş bandı çözümü — TEK KAYNAK: data/yas_bantlari.json (Faz Y)
# =============================================================================
# Çizelge ve uyanıklık penceresi kontrolü artık İlayda'nın yapılandırılmış yaş
# bandı tablosundan beslenir. Aşağıdaki KB metin ayrıştırıcıları YALNIZCA geriye
# uyumluluk içindir: yaş bilgisi taşımayan eski çağrılar ve Faz Y öncesi
# saklanmış planlar için. Yeni kod yolları daima `yas_ay` geçirir.


def bant_coz(bucket_params: Any, yas_ay: float | None,
             tek_uyku: bool | None = None) -> dict | None:
    """Etkin yaş bandını çöz. Çözülemezse None (çağıran KB yoluna düşer).

    Öncelik: açık `yas_ay` > bucket_params'ın kendisi zaten çözülmüş bantsa o."""
    if yas_ay is not None:
        return yas_bantlari.yas_bandi_getir(yas_ay, tek_uyku=tek_uyku)
    if yas_bantlari.bant_mi(bucket_params):
        return bucket_params
    return None


# =============================================================================
# Yaş bandı parametre ayrıştırıcıları (GERİYE UYUMLULUK — KB serbest metinleri)
# =============================================================================
# KB metinleri elle yazılmıştır ve biçimleri tutarsızdır:
#   "40-60 Dakika" | "90 Dakika" | "1.5 - 2.5 Saat" | "5 Saat 30 Dakika - 7 Saat"
#   "2.5 - 3.5 Saat (görsel) / 3-4 Saat (resmi)" | "tüm gün (...)" | None
# Bu yüzden ayrıştırıcılar savunmacıdır ve çözemezse None döner (çağıran
# varsayılana düşer) — asla exception fırlatmaz.

_NUM = r"\d+(?:[.,]\d+)?"


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def _parse_duration_single(text: str, default_unit: str | None = None) -> int | None:
    """'2 Saat 30 Dakika' / '90 Dakika' / '1.5' → dakika. Çözemezse None.

    default_unit: birimsiz sayı için ('saat'|'dakika') — '40-60 Dakika' ifadesinde
    sol taraf birimsizdir, sağdan miras alır."""
    text = text.strip().lower()
    if not text:
        return None
    total = 0.0
    found = False
    # Önce 'X saat' ve 'Y dakika' parçalarını topla (ikisi bir arada olabilir).
    for m in re.finditer(rf"({_NUM})\s*(saat|dakika|dk)", text):
        val, unit = _to_float(m.group(1)), m.group(2)
        total += val * 60 if unit == "saat" else val
        found = True
    if found:
        return int(round(total))
    # Birimsiz tek sayı → default_unit'e göre yorumla.
    m = re.search(rf"({_NUM})", text)
    if m and default_unit:
        val = _to_float(m.group(1))
        return int(round(val * 60 if default_unit == "saat" else val))
    return None


def _detect_unit(text: str) -> str | None:
    t = text.lower()
    if "saat" in t:
        return "saat"
    if "dakika" in t or "dk" in t:
        return "dakika"
    return None


def parse_duration_range(raw: Any) -> tuple[int, int] | None:
    """'1.5 - 2.5 Saat' → (90, 150). Aralık değilse (90,90). Çözemezse None."""
    if isinstance(raw, dict):                      # {'RESMI_DEGER_...': '...'}
        raw = _pick_official(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.split("/")[0]                       # '... (görsel) / ...' → ilk alternatif
    text = re.sub(r"\([^)]*\)", " ", text)         # parantez içi açıklamayı at
    if not re.search(_NUM, text):                  # 'tüm gün' gibi sayısız ifade
        return None
    unit = _detect_unit(text)
    parts = re.split(r"\s*[-–—]\s*", text.strip(), maxsplit=1)
    lo = _parse_duration_single(parts[0], unit)
    hi = _parse_duration_single(parts[1], unit) if len(parts) > 1 else lo
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return (min(lo, hi), max(lo, hi))


def parse_time_range(raw: Any) -> tuple[int, int] | None:
    """'18:00 - 20:00' → (1080, 1200) (gece yarısından itibaren dakika)."""
    if not isinstance(raw, str):
        return None
    times = re.findall(r"(\d{1,2}):(\d{2})", raw)
    if not times:
        return None
    mins = [int(h) * 60 + int(m) for h, m in times]
    if len(mins) == 1:
        return (mins[0], mins[0])
    return (min(mins[:2]), max(mins[:2]))


def parse_count(raw: Any) -> int | None:
    """'3-4' → 3, '4+' → 4, '2' → 2. Aralıkta ALT sınır kullanılır (deterministik)."""
    if isinstance(raw, dict):
        raw = _pick_official(raw)
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    nums = re.findall(r"\d+", raw)
    return int(nums[0]) if nums else None


def _pick_official(d: dict) -> Any:
    """Sözlükten 'resmi' değeri seç — KB'de birden çok kaynak (görsel/ses) olabilir."""
    for k in d:
        if "RESMI" in k.upper():
            return d[k]
    return next(iter(d.values()), None)


def _mid(rng: tuple[int, int]) -> int:
    return (rng[0] + rng[1]) // 2


# =============================================================================
# Çizelge kurucu
# =============================================================================
# --- Saat eklerinde ÜNLÜ UYUMU ----------------------------------------------
# "17:15'da" yanlış, "17:15'te" doğru: ek, saatin SÖYLENİŞİNDEKİ son kelimeye
# uyar ("on beş" → beşTE). Kod boyunca elle "'da" yazıldığı için 15, 25, 35,
# 45 ile biten her saat yanlış okunuyordu. Anne bunu fark eder.
_SON_HECE = {          # rakamın son kelimesine göre: (sert mi, ince mi)
    0: ("sıfır", False, False), 1: ("bir", False, True),
    2: ("iki", False, True), 3: ("üç", True, True),
    4: ("dört", True, True), 5: ("beş", True, True),
    6: ("altı", False, False), 7: ("yedi", False, True),
    8: ("sekiz", False, True), 9: ("dokuz", False, False),
    10: ("on", False, False), 20: ("yirmi", False, True),
    30: ("otuz", False, False), 40: ("kırk", True, False),
    50: ("elli", False, True),
}


def _sayi_ozelligi(n: int) -> tuple[bool, bool]:
    """(sert ünsüzle bitiyor mu, ince ünlü mü) — ekin biçimini bunlar belirler."""
    n = int(n)
    if n in _SON_HECE:
        _ad, sert, ince = _SON_HECE[n]
        return sert, ince
    birler = n % 10
    if birler == 0:                       # 60, 70… (dakikada görülmez)
        return _SON_HECE.get(n, ("", False, False))[1:]
    return _sayi_ozelligi(birler)


def saat_eki(minute: int, tur: str = "de") -> str:
    """`_fmt` çıktısına eklenecek kesme işaretli ek.

    tur: "de" (bulunma), "den" (ayrılma), "e" (yönelme), "i" (belirtme).
    Ek, saatin SON söylenen sayısına uyar: dakika 0 ise saate, değilse dakikaya.
    """
    m = int(minute) % 1440
    sayi = m % 60 if m % 60 else m // 60
    sert, ince = _sayi_ozelligi(sayi)
    if tur == "de":
        return "'te" if (sert and ince) else "'ta" if sert else \
               "'de" if ince else "'da"
    if tur == "den":
        return "'ten" if (sert and ince) else "'tan" if sert else \
               "'den" if ince else "'dan"
    if tur == "e":
        return "'e" if ince else "'a"
    if tur == "i":
        return "'i" if ince else "'ı"
    return ""


def saatli(minute: int, tur: str = "de") -> str:
    """"17:15'te" — saat + uyumlu ek, tek parça."""
    return f"{_fmt(minute)}{saat_eki(minute, tur)}"


def _fmt(minute: int) -> str:
    """Dakika → 'HH:MM' (24 saati aşarsa ertesi güne sarar)."""
    minute = int(minute) % (24 * 60)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def build_schedule(bucket_params: dict, wake_minute: int = DEFAULT_WAKE_MIN, *,
                   yas_ay: float | None = None,
                   tek_uyku: bool | None = None) -> list[dict]:
    """Saat saat çizelge üret. `yas_ay` verildiğinde TABLO (Faz Y) tek kaynaktır.

    Mantık (motorun 'uyanıklık süresi' ilkesiyle aynı): sabah uyanışına yaşa uygun
    uyanıklık penceresi eklenerek ilk uyku; her uykudan sonra tekrar pencere kadar
    uyanıklık. Gece yatışı, gece uykusu süresinden türetilen aralığa kırpılır.

    yas_ay yoksa (Faz Y öncesi çağrılar) KB serbest metinleri ayrıştırılır —
    davranış değişmez, yalnız yeni yol tercih edilir.

    Dönen her blok (mobil sözleşmesi):
        {time, end?, type, title, note?, key, start_minute, end_minute}
      time/end : "HH:MM" duvar saati (mobilin gösterdiği alanlar)
      type     : 'wake' | 'nap' | 'sleep' | 'feed' | 'routine'
                 (v1'de yalnız wake/nap/sleep üretilir; feed/routine şemada
                  ayrılmıştır — bu blokları türetecek veri henüz yok.)
      title    : ekranda görünen başlık
      key/start_minute/end_minute : dahili (kaydırma, bildirim penceresi) —
                 mobil bunlara bakmak zorunda değildir.
    """
    bant = bant_coz(bucket_params, yas_ay, tek_uyku)
    if bant is not None:
        cp = yas_bantlari.cizelge_parametreleri(bant)
        return _cizelge_kur(wake_minute, cp["uyku_sayisi"],
                            cp["uyaniklik_penceresi_dk"], cp["uyku_suresi_dk"],
                            yas_bantlari.yatma_araligi(bant, wake_minute))

    # --- Geriye uyumluluk: KB serbest metinleri -----------------------------
    p = bucket_params or {}
    ww = parse_duration_range(p.get("uyaniklik_penceresi")) or DEFAULT_WAKE_WINDOW
    n_naps = parse_count(p.get("uyku_sayisi"))
    if n_naps is None:
        n_naps = 2
    day_sleep = parse_duration_range(p.get("gunduz_uyku_total")) or DEFAULT_DAY_SLEEP
    bed_range = parse_time_range(p.get("yatma_vakti")) or DEFAULT_BEDTIME_RANGE
    nap_len = max(30, _mid(day_sleep) // n_naps) if n_naps > 0 else 0
    return _cizelge_kur(wake_minute, n_naps, _mid(ww), nap_len, bed_range)


def _cizelge_kur(wake_minute: int, n_naps: int, ww: int, nap_len: int,
                 bed_range: tuple[int, int]) -> list[dict]:
    """Uyanış + (pencere, uyku) tekrarı + gece yatışı → blok listesi."""
    blocks: list[dict] = [{
        "key": "wake", "type": "wake",
        "start_minute": wake_minute, "end_minute": wake_minute,
        "title": "Sabah uyanışı",
    }]

    cursor = wake_minute
    for i in range(1, max(0, n_naps) + 1):
        start = cursor + ww
        end = start + nap_len
        blocks.append({
            "key": f"nap_{i}", "type": "nap",
            "start_minute": start, "end_minute": end,
            "title": f"{i}. gündüz uykusu",
            "note": f"Önceki uykudan ~{ww} dakika sonra",
        })
        cursor = end

    bedtime_ham = cursor + ww
    bedtime = max(bed_range[0], min(bed_range[1], bedtime_ham))   # yaş bandına kırp
    gece = {
        "key": "bedtime", "type": "sleep",
        "start_minute": bedtime, "end_minute": wake_minute + 24 * 60,
        "title": "Gece uykusu",
    }
    if bedtime != bedtime_ham:
        gece["note"] = (f"Yaşına uygun yatış saatine "
                        f"({_fmt(bed_range[0])}–{_fmt(bed_range[1])}) getirildi")
    blocks.append(gece)
    return [_with_labels(b) for b in blocks]


def _with_labels(b: dict) -> dict:
    """Dakika alanlarından mobil sözleşmesinin 'time'/'end' alanlarını türet."""
    out = dict(b)
    out["time"] = _fmt(b["start_minute"])
    out["end"] = _fmt(b["end_minute"])
    return out


def headline(baby_name: str, bucket: str | None, schedule: list[dict]) -> str:
    """Tek cümlelik kişisel özet — mobil plan kartının başlığı.

    Örn: "Elif için 9 ay programı — 2 kısa uyku, 19:00 yatış" """
    naps = [b for b in schedule or [] if b.get("type") == "nap"]
    bed = next((b for b in schedule or [] if b.get("key") == "bedtime"), None)
    bant = (bucket or "").replace("_", " ").replace("-", "–").strip() or "yaşına özel"
    parcalar = [f"{len(naps)} kısa uyku"] if naps else ["gündüz uykusuz düzen"]
    if bed is not None:
        parcalar.append(f"{bed.get('time')} yatış")
    return f"{baby_name} için {bant} programı — " + ", ".join(parcalar)


# Faz 6.5R: şema değişikliğinden ÖNCE üretilmiş planlar DB'de duruyor. Onlarda
# bloklar {start, label, type:"night"} biçimindeydi; yeni kod {time, title,
# type:"sleep"} okuyor. Normalize edilmezse:
#   - bağlam özeti "None uyanış" üretir ve gece bloğu düşer,
#   - bildirim penceresi gece bloğunu HİÇ yakalamaz (sessiz kayıp).
# Bu yüzden saklanmış her çizelge OKUNURKEN yükseltilir.
_ESKI_TIP = {"night": "sleep"}
_VARSAYILAN_BASLIK = {"wake": "Sabah uyanışı", "bedtime": "Gece uykusu"}


def normalize_schedule(schedule: list[dict] | None) -> list[dict]:
    """Saklanmış çizelgeyi güncel sözleşmeye yükselt (eski alan adları → yeni).

    Geriye uyumluluk katmanı: zaten güncel biçimdeki bloklar değişmeden geçer."""
    out: list[dict] = []
    for b in schedule or []:
        if not isinstance(b, dict):
            continue
        nb = dict(b)
        sm, em = nb.get("start_minute"), nb.get("end_minute")
        # start → time, label → title, night → sleep
        nb["time"] = nb.get("time") or nb.get("start") or (
            _fmt(sm) if sm is not None else None)
        nb["end"] = nb.get("end") or (_fmt(em) if em is not None else None)
        key = nb.get("key") or ""
        nb["title"] = (nb.get("title") or nb.get("label")
                       or _VARSAYILAN_BASLIK.get(key)
                       or (f"{key.split('_')[-1]}. gündüz uykusu"
                           if key.startswith("nap_") else key or "Uyku"))
        nb["type"] = _ESKI_TIP.get(nb.get("type"), nb.get("type"))
        nb.pop("start", None)          # eski alanlar taşınmaz (karışıklık olmasın)
        nb.pop("label", None)
        out.append(nb)
    return out


def sabit_wake_minute(schedule_template: list[dict] | None) -> int:
    """K1 — şablondaki SABİT sabah uyanış hedefi. Şablon yoksa varsayılan 07:00.

    Bu değer eğitim boyunca DEĞİŞMEZ; gerçek uyanış onu asla güncellemez."""
    for b in normalize_schedule(schedule_template):
        if b.get("key") == "wake" and b.get("start_minute") is not None:
            return int(b["start_minute"])
    return DEFAULT_WAKE_MIN


# =============================================================================
# GÜN İÇİ KAYMA MOTORU v2 (K1–K9)
# =============================================================================
# Eski motor (v1) çizelgenin TAMAMINI 3 günlük ortalama uyanışa göre ±45 dk
# kaydırıyor ve sonucu ertesi güne taban yapıyordu. Kaldırıldı. Yeni motor:
#   • sabah hedefi SABİT (K1) ve şablonda saklanır,
#   • gün planı YALNIZ bugünün kayıtlarından zincirleme kurulur (K2/K3),
#   • kayıt yoksa blok "planlandığı gibi oldu" sayılır (K6) ve işaretlenir,
#   • sonradan kayıt gelirse varsayım bozulur ve zincir yeniden akar (K7),
#   • hesap saftır: DB yok, LLM yok, aynı girdi → aynı çizelge (K8).


def _log_alanlari(lg: Any, tz_offset_min: int) -> dict | None:
    """SleepLog benzeri nesneyi motorun kullandığı düz sözlüğe indir."""
    started = getattr(lg, "started_at", None)
    if started is None:
        return None
    ended = getattr(lg, "ended_at", None)
    bas_gun, bas_dk = _local_minute(started, tz_offset_min)
    bit_gun = bit_dk = None
    if ended is not None:
        bit_gun, bit_dk = _local_minute(ended, tz_offset_min)
    return {
        "id": str(getattr(lg, "id", "") or "") or None,
        "type": getattr(lg, "type", None),
        "bas_gun": bas_gun, "bas_dk": bas_dk,
        "bit_gun": bit_gun, "bit_dk": bit_dk,
        # Gün içi sıralama için: bitiş, bir sonraki güne taştıysa +24 saat.
        "bit_dk_lin": (None if bit_dk is None else
                       bit_dk + 1440 * max(0, (bit_gun - bas_gun).days)),
        "sure_dk": (None if ended is None
                    else max(0, int((ended - started).total_seconds() // 60))),
        # K12.1 — bitiş başlangıçtan ÖNCE mi? `sure_dk` 0'a kırpıldığı için bu
        # bilgi orada kayboluyor; "sıfır süreli" ile "ters kayıt" ayrı sebepler.
        "ters_mi": (ended is not None and ended < started),
    }


# Bir kaydın "uyku" sayıldığı tipler. `sekerleme` eski istemcilerin gönderdiği
# ad; motor açısından normal bir gündüz uykusudur (sınıfı saat belirler).
UYKU_TIPLERI = ("sleep", "nap", "sekerleme")


def kisa_uyku_esigi(bant: dict | None) -> int:
    """Bandın "kısa gündüz uykusu" eşiği (dk).

    İlayda: "Bir saatin altında kalan uykulara kısa gündüz uykusu denir, altı ay
    üzeri bebeklerde. Altı ayın altındaki bebeklerde ise 45 dakikanın
    altındakiler." Değer tabloda (`kisa_uyku_esigi_dk`, v1.4)."""
    if bant:
        esik = bant.get("kisa_uyku_esigi_dk")
        if esik:
            return int(esik)
    return VARSAYILAN_KISA_UYKU_ESIGI_DK


def uyku_tipi_belirle(kayit: dict, bant: dict | None = None) -> str:
    """K19.1 — bir uyku kaydının SINIFI: gündüz mü gece mi.

    TEK KURAL, BAŞLANGIÇ SAATİ:
      • 06:00–19:00 arası başlayan → gündüz uykusu,
      • 19:00–06:00 arası başlayan → gece uykusu,
      • İSTİSNA: 19:00–24:00 arası başlayan, KAPALI ve bandın kısa uyku
        eşiğinden (6 ay altı 45 dk / 6 ay+ 60 dk) kısa kayıt gündüz sayılır —
        bu gece yatışı değil, başarısız bir yatış denemesidir.

    DB'deki `type` sınıfı BELİRLEMEZ: `nap` tipiyle gelen 21:00 kaydı gece
    uykusudur, `sleep` tipiyle gelen 09:50 kaydı gündüz uykusudur. Eski
    istemcilerin `sekerleme` tipi de normal gündüz uykusu gibi işlenir.

    `bant` artık KULLANILIYOR: kısa uyku eşiği yaşa bağlı (v1.4). Bant
    verilmezse 6 ay+ eşiğine düşülür."""
    # Gece yarısını AŞAN kayıt her koşulda gece uykusudur: 16:00'da başlayıp
    # ertesi gün 02:00'de biten bir kayıt takvimsel olarak gündüz uykusu olamaz.
    # Bu, saat kuralının tek fiziksel istisnasıdır.
    if (kayit.get("bit_gun") is not None and kayit.get("bas_gun") is not None
            and kayit["bit_gun"] > kayit["bas_gun"]):
        return GECE_UYKUSU
    bas = kayit["bas_dk"]
    if GUNDUZ_PENCERE_BAS <= bas < GUNDUZ_PENCERE_BIT:
        return GUNDUZ_UYKUSU
    sure = kayit.get("sure_dk")
    if (GUNDUZ_PENCERE_BIT <= bas < AKSAM_ISTISNA_BIT
            and kayit.get("bit_dk") is not None
            and sure is not None and sure < kisa_uyku_esigi(bant)):
        return GUNDUZ_UYKUSU
    return GECE_UYKUSU


def uyku_sinifi_ham(started_at: datetime, ended_at: datetime | None = None,
                    tz_offset_min: int = TZ_OFFSET_MIN,
                    bant: dict | None = None) -> str:
    """K19.2 — ORM satırı / ham datetime için sınıf (GET /logs yanıtı).

    `uyku_tipi_belirle` ile AYNI kuralı uygular; iki yerde ayrı yazılırsa
    mobilin bastığı etiket ile motorun hesabı ayrışır. `bant` verilirse kısa
    uyku eşiği yaşa göre çözülür."""
    bas_gun, bas_dk = _local_minute(started_at, tz_offset_min)
    sure = bit_dk = bit_gun = None
    if ended_at is not None:
        bit_gun, bit_dk = _local_minute(ended_at, tz_offset_min)
        sure = max(0, int((ended_at - started_at).total_seconds() // 60))
    return uyku_tipi_belirle({"bas_dk": bas_dk, "bit_dk": bit_dk,
                              "bas_gun": bas_gun, "bit_gun": bit_gun,
                              "sure_dk": sure}, bant)


def _atlandi_mi(k: dict) -> bool:
    """K7 — 'bu uykuyu hiç yapmadı' kaydı.

    Sıfır süreli `nap` bilinçli olarak "atlandı" sayılır (eski istemciler bu
    biçimi gönderiyor). TERS kayıt (bitiş < başlangıç) buraya GİRMEZ: o bozuk
    bir kayıttır, niyet beyanı değil — K12.1 ile yok sayılır."""
    if k["type"] == ATLANDI_TIPI:
        return True
    if k.get("ters_mi"):
        return False
    return k["type"] == "nap" and k["sure_dk"] == 0


def _yok_say(out: dict, k: dict, kod: str, sebep: str) -> None:
    """Kaydı motordan ÇIKAR ama izini bırak (K-risk R7: sessiz yutma YOK).

    `kod` makine tarafı (mobil/rapor süzer), `sebep` insan tarafıdır."""
    out["yok_sayilan"].append({"id": k["id"], "kod": kod, "sebep": sebep})


def _ortusme_orani(a: dict, b: dict) -> float:
    """K13 — iki uyku kaydının örtüşme oranı: ortak dakika / KISA olanın süresi.

    Payda bilinçli olarak kısa kayıt: 10 dakikalık bir kayıt 2 saatlik bir kaydın
    TAMAMEN içindeyse bu %100 örtüşmedir, %8 değil. Toplam süreye bölmek
    "aynı uykunun iki kaydı"nı eşiğin altında bırakıyordu.

    AÇIK (bitişi olmayan) kayıt bir NOKTA gibi ele alınır: kapalı bir kaydın
    aralığına düşüyorsa tam örtüşme sayılır — K13.2'nin "kapsayan kapalı kayıt
    varsa açık kayıt tamamen yok sayılır" kuralı budur."""
    a_bas, a_bit = a["bas_dk"], a.get("bit_dk_lin")
    b_bas, b_bit = b["bas_dk"], b.get("bit_dk_lin")
    if a_bit is None and b_bit is None:
        return 1.0 if a_bas == b_bas else 0.0
    if a_bit is None:
        return 1.0 if b_bas <= a_bas <= b_bit else 0.0
    if b_bit is None:
        return 1.0 if a_bas <= b_bas <= a_bit else 0.0
    ortak = min(a_bit, b_bit) - max(a_bas, b_bas)
    if ortak <= 0:
        return 0.0
    kisa = min(a_bit - a_bas, b_bit - b_bas)
    return 1.0 if kisa <= 0 else ortak / kisa


def _cakisma_kazanani(a: dict, b: dict) -> tuple[dict, dict]:
    """K13.1 — çakışan iki kayıttan hangisi tutulur: (kazanan, kaybeden).

    Kapalı kayıt açık olana TERCİH EDİLİR (açık kayıt yalnız "şu an sürüyor"
    demektir, K13.2); ikisi de kapalıysa UZUN olan alınır — kısa olan genellikle
    sayacın erken durdurulup yeniden başlatılmış hâlidir."""
    a_acik = a.get("bit_dk_lin") is None
    b_acik = b.get("bit_dk_lin") is None
    if a_acik != b_acik:
        return (b, a) if a_acik else (a, b)
    return (a, b) if (a["sure_dk"] or 0) >= (b["sure_dk"] or 0) else (b, a)


def _cakismalari_coz(kayitlar: list[dict], out: dict) -> list[dict]:
    """K13 — üst üste binen uyku kayıtlarından TEKİNİ tut, diğerini yok say.

    Beta verisindeki 3. hata: anne sayacı başlatıp kapatmıyor, sonra aynı uykuyu
    elle 10:00-11:49 diye giriyor. İki kayıt İKİ AYRI uyku sanılıyor, biri
    yuvayı tüketiyor ve gün bir "hayalet uyku" fazladan görünüyordu."""
    tutulan: list[dict] = []
    for k in sorted(kayitlar, key=lambda x: x["bas_dk"]):
        carpisan = next((t for t in tutulan
                         if _ortusme_orani(t, k) > CAKISMA_ESIGI), None)
        if carpisan is None:
            tutulan.append(k)
            continue
        kazanan, kaybeden = _cakisma_kazanani(carpisan, k)
        if kazanan is not carpisan:
            tutulan[tutulan.index(carpisan)] = kazanan
        _yok_say(out, kaybeden, "cakisma",
                 f"{_fmt(kazanan['bas_dk'])} kaydıyla aynı uykuya ait "
                 f"görünüyor; "
                 f"{'bitişi girilmemiş olan' if kaybeden.get('bit_dk_lin') is None else 'kısa olan'}"
                 f" sayılmadı")
    return sorted(tutulan, key=lambda x: x["bas_dk"])


def gun_kayitlari(logs: Iterable[Any], gun: date, hedef_minute: int,
                  tz_offset_min: int = TZ_OFFSET_MIN, *,
                  nap_sure_dk: int | None = None,
                  gece_sure_dk: int | None = None,
                  now_minute: int | None = None,
                  bant: dict | None = None) -> dict:
    """Ham kayıtları BUGÜNE ait rollere ayır (K2 — yalnız bugünün verisi).

    Dönen: {gece_uykulari, gunduz_uykulari, atlananlar, wake_kayitlari,
            gece_uyanmalari, yok_sayilan}
    `yok_sayilan`: motora giremeyen kayıtlar + sebebi (sessiz yutma YOK, K-risk R7).

    v2.2 — K12/K13 kuralları burada uygulanır:
      • `wake`/`night_wake` HİÇBİR koşulda uyku yuvasına giremez (K12.1). Bu
        ayrım artık tek bir dalda ve en başta yapılıyor; eskiden tip kontrolü
        uyku dallarından SONRA geldiği için sıfır süreli bir uyanma kaydı
        gündüz uykusu listesine düşüp yuvayı tüketebiliyordu.
      • Yuvaya yalnız `sleep`/`nap` ve süresi ≥ UYKU_MIN_SURE_DK olan kayıtlar
        eşlenir; sıfır/negatif/çok kısa olanlar sebebiyle yok sayılır (K12.1).
      • 06:00'dan önceki her uyanma kaydı gece uyanması olarak da SAYILIR
        (K12.2) — ama `wake` kaydı sabah uyanışı adaylığını KAYBETMEZ, çünkü
        K10.1 "06:00 öncesi uyanıp tekrar uyumadı" durumunu yönetiyor.
      • Çakışan uyku kayıtları tekilleştirilir (K13).
      • Aynı anda EN FAZLA BİR açık uyku kaydı sürüyor sayılır; bayat açık
        kayıtlar hesapta kapatılır (K16.2/K17). Bunun için `nap_sure_dk` ve
        `now_minute` gerekir; verilmezse bu adım ATLANIR (eski çağrılar aynen
        çalışır, uydurma bir süre varsayılmaz).
    """
    out = {"gece_uykulari": [], "gunduz_uykulari": [], "atlananlar": [],
           "wake_kayitlari": [], "gece_uyanmalari": [], "yok_sayilan": [],
           "uyaniklik_kanitlari": [], "birlesen": []}
    wake_adaylari: list[dict] = []
    for lg in logs or []:
        k = _log_alanlari(lg, tz_offset_min)
        if k is None:
            continue
        tip = k["type"]

        # --- K12.1 — UYANMA KAYITLARI: asla uyku değildir --------------------
        if tip in ("wake", "night_wake"):
            if k["bas_gun"] != gun:
                continue
            if tip == "night_wake":
                out["gece_uyanmalari"].append(k)
            else:
                wake_adaylari.append(k)     # K19.4 — döngüden SONRA çözülür
            continue

        if tip == "feed":
            continue            # beslenme çizelgeyi etkilemez (v1'de de etkilemiyordu)

        if tip != ATLANDI_TIPI and tip not in UYKU_TIPLERI:
            if k["bas_gun"] == gun:
                _yok_say(out, k, "taninmayan_tip",
                         f"Bu kayıt türü tanınmadı: {tip!r}")
            continue

        # "Atlandı" beyanı sınıflandırmaya girmez (uyku YAPILMAMIŞTIR).
        if k["bas_gun"] == gun and _atlandi_mi(k):
            out["atlananlar"].append(k)
            continue

        # --- K19.1 — SINIF SAATTEN gelir, type'tan DEĞİL --------------------
        if uyku_tipi_belirle(k, bant) == GECE_UYKUSU:
            # Gece uykusu BUGÜNE, bittiği güne göre bağlanır (dün 20:30 → bugün 07:00).
            if k["bit_gun"] == gun:
                out["gece_uykulari"].append(k)
            elif k["bit_dk"] is None and k["bas_gun"] < gun:
                # K12.4/K14.1 — dün başlamış, HÂLÂ AÇIK gece uykusu.
                out["gece_uykulari"].append(dict(k, _devam=True))
            continue            # bugün akşam başlayan açık gece uykusu: yarının

        if k["bas_gun"] != gun:
            continue            # başka güne ait kayıt bugünün planına girmez (K2)

        # --- K12.1 — yuvaya eşlenebilirlik ----------------------------------
        if k["bit_dk"] is None:
            out["gunduz_uykulari"].append(dict(k, _devam=True))   # süren uyku
        elif k["ters_mi"]:
            _yok_say(out, k, "ters_kayit",
                     "Bitiş saati başlangıçtan önce görünüyor; bu kayıt "
                     "güne eklenmedi")
        elif k["sure_dk"] <= 0:
            _yok_say(out, k, "sifir_sure",
                     "Başlangıç ve bitiş saati aynı; süresi olmayan bu "
                     "kayıt güne eklenmedi")
        elif k["sure_dk"] < UYKU_MIN_SURE_DK:
            _yok_say(out, k, "kisa_sure",
                     f"Yalnızca {k['sure_dk']} dakika sürmüş; "
                     f"{UYKU_MIN_SURE_DK} dakikadan kısa kayıtlar uyku "
                     f"sayılmıyor")
        else:
            out["gunduz_uykulari"].append(k)

    # --- K19.4 — `wake` kayıtlarının rolü -----------------------------------
    _wake_kayitlarini_coz(out, wake_adaylari)

    # --- K13 — çakışanları tekilleştir (yuvalara girmeden ÖNCE) -------------
    out["gunduz_uykulari"] = _cakismalari_coz(out["gunduz_uykulari"], out)
    out["gece_uykulari"] = _cakismalari_coz(out["gece_uykulari"], out)

    # K19.1 — gece uykusunun İÇİNDE kalan "gündüz" parçalarını ayıkla
    out["gunduz_uykulari"] = _gece_icindekileri_ayikla(out, gun)

    # --- K16.2/K17 — açık kayıtları çöz -------------------------------------
    _acik_kayitlari_coz(out, nap_sure_dk, now_minute, gece_sure_dk)

    # --- K20 — ardışık parçaları birleştir (yuvalara girmeden ÖNCE) ---------
    out["gunduz_uykulari"] = _parcalari_birlestir(out["gunduz_uykulari"], out,
                                                  bant)

    for anahtar in ("gece_uykulari", "gunduz_uykulari", "atlananlar",
                    "wake_kayitlari", "gece_uyanmalari"):
        out[anahtar].sort(key=lambda x: x["bas_dk"])
    return out


def _kaydi_kapat(k: dict, bit_dk_lin: int, sebep: str) -> None:
    """Açık bir kaydı HESAPTA kapat (DB'ye dokunmaz).

    `bit_dk_lin` gün başlangıcına göre doğrusal dakikadır (24 saati aşabilir);
    `bit_dk` duvar saatine indirgenir."""
    bit_dk_lin = max(k["bas_dk"], int(bit_dk_lin))
    k["bit_dk_lin"] = bit_dk_lin
    k["bit_dk"] = bit_dk_lin % 1440
    k["sure_dk"] = bit_dk_lin - k["bas_dk"]
    k["_devam"] = False
    k["_otomatik_kapandi"] = sebep


def _acik_kayitlari_coz(out: dict, nap_sure_dk: int | None,
                        now_minute: int | None,
                        gece_sure_dk: int | None = None) -> None:
    """K16.2 + K17 — açık uyku kayıtlarını hesapta kapat.

    DB'DEKİ KAYIT DEĞİŞMEZ. Burası "düzeltilmemiş eski veriyi doğru oku"
    katmanıdır; kaydı gerçekten kapatan yol POST /logs/batch (K16.1) ve
    scripts/acik_sayac_kapat.py'dir.

    Sıra önemli:
      1. K16.2 — birden fazla açık kayıt varsa YALNIZ EN YENİSİ sürüyor sayılır.
         Öncekiler min(sonraki açık kaydın başlangıcı, kendi başlangıcı + bandın
         planlanan süresi) ile kapanır. "Yeni kayıt açıldı" bilgisi, bebeğin o
         an uyanık olduğunun kanıtıdır.
      2. K17.2 — açık bir nap'ten SONRA gelen `wake` kaydı onu o saatte kapatır
         (K12.4'ün gece uykusundan nap'e genellenmesi).
      3. K17.1 — hâlâ açık olan kayıt bandın süresinin 2 katını (en az 3 saat)
         aşmışsa "sürüyor" sayılmaz: bitiş = başlangıç + planlanan süre.

    Kapanış sonucu UYKU_MIN_SURE_DK altına düşerse kayıt tümüyle yok sayılır —
    2 dakikalık bir "uyku" yuva tüketmemeli (K12.1 ile aynı eşik)."""
    if nap_sure_dk is None:
        return
    nap_sure_dk = max(1, int(nap_sure_dk))
    bayat_esik = max(2 * nap_sure_dk, K17_NAP_ESIK_MIN_DK)

    # 1) K16.2 — tek açık kayıt
    acik = sorted([k for k in out["gunduz_uykulari"] if k.get("_devam")],
                  key=lambda x: x["bas_dk"])
    for i in range(len(acik) - 1):                 # sonuncusu hariç hepsi
        _kaydi_kapat(acik[i],
                     min(acik[i + 1]["bas_dk"], acik[i]["bas_dk"] + nap_sure_dk),
                     "yeni_kayit")

    # 2) K17.2 — sonraki `wake` kaydı açık nap'i kapatır.
    # Sabah adaylığından ELENEN wake'ler de burada sayılır: bebeğin uyandığını
    # yine de kanıtlıyorlar (K19.4 onları yalnız "sabah uyanışı" rolünden alır).
    wakeler = sorted(w["bas_dk"] for w in out["uyaniklik_kanitlari"])
    for k in out["gunduz_uykulari"]:
        if not k.get("_devam"):
            continue
        sonraki = next((w for w in wakeler if w > k["bas_dk"]), None)
        if sonraki is not None:
            _kaydi_kapat(k, sonraki, "wake")

    # 3) K17.1 — bayat açık kayıt
    if now_minute is not None:
        for k in out["gunduz_uykulari"]:
            if k.get("_devam") and (now_minute - k["bas_dk"]) >= bayat_esik:
                _kaydi_kapat(k, k["bas_dk"] + nap_sure_dk, "bayat")
        # K19.3 — gece uykusunun eşiği bandın GECE ALT SINIRINDAN türer; bant
        # yoksa sabit 14 saate düşülür.
        gece_esik = max(int(gece_sure_dk or 0), K17_GECE_ESIK_DK)
        for g in out["gece_uykulari"]:
            # Açık gece uykusu bir ÖNCEKİ günde başlar; bugünün dakikasına
            # göre geçen süre +24 saattir.
            if g.get("_devam") and (now_minute + 1440 - g["bas_dk"]) >= gece_esik:
                g["_bayat"] = True

    # Kapanış çok kısa kaldıysa kayıt yuvaya giremez.
    kalan = []
    for k in out["gunduz_uykulari"]:
        if (k.get("_otomatik_kapandi") and not k.get("_devam")
                and (k.get("sure_dk") or 0) < UYKU_MIN_SURE_DK):
            _yok_say(out, k, "otomatik_kapanis_kisa",
                     f"{_fmt(k['bas_dk'])} sayacı kapatılmamıştı; "
                     f"kapatıldığında geriye {k.get('sure_dk')} dakika kaldı "
                     f"ve uyku sayılmadı")
            continue
        kalan.append(k)
    out["gunduz_uykulari"] = kalan


def _gece_icindekileri_ayikla(out: dict, gun: date) -> list[dict]:
    """Gece uykusunun ARALIĞINA düşen "gündüz" kaydını ele.

    K19'un saat kuralı sabah 06:00'dan sonra başlayan her uykuyu gündüz sayıyor.
    Ama bebek gece uykusundan 08:25'te uyanmışsa, 06:30-07:15 arasındaki kayıt
    bir gündüz uykusu OLAMAZ — gece uykusunun içinde, gece uyanmasından sonra
    tekrar dalmanın ayrı kaydıdır. Gerçek vakada bu parça 4. bir gündüz uykusu
    üretiyordu (ölçüldü).

    Bu, K13'ün "aynı uyku iki kez kaydedilmiş" kuralının KOVALAR ARASI hâlidir:
    gece kaydı zaten o aralığı kapsıyor, parça tekrar sayılmamalı. Süresi gece
    uykusunun brütüne DAHİL (aynı aralık) olduğu için ayrıca eklenmez.

    Yalnız TAM KAPSANAN kayıtlar elenir; kısmen taşan bir kayıt (ör. 05:30-09:00)
    gerçek bir sabah uykusu olabilir, ona dokunulmaz."""
    geceler = []
    for g in out["gece_uykulari"]:
        bit = g.get("bit_dk_lin")
        if bit is None:
            continue                      # açık gece uykusu: kapsama belirsiz
        # `bas_dk`/`bit_dk_lin` KAYDIN BAŞLADIĞI günün eksenindedir. Bugünün
        # eksenine çekmek için İKİSİ BİRDEN kaydırılır — yalnız birini
        # kaydırmak aralığı 24 saat uzatıp bütün günü yutuyordu (ölçüldü).
        kaydir = 0
        if g.get("bas_gun") is not None and g["bas_gun"] < gun:
            kaydir = 1440 * (gun - g["bas_gun"]).days
        geceler.append((g["bas_dk"] - kaydir, bit - kaydir))
    if not geceler:
        return out["gunduz_uykulari"]

    kalan = []
    for k in out["gunduz_uykulari"]:
        k_bit = k.get("bit_dk_lin")
        if k_bit is None:
            kalan.append(k)
            continue
        icinde = any(gb <= k["bas_dk"] and k_bit <= ge for gb, ge in geceler)
        if icinde:
            _yok_say(out, k, "gece_icinde",
                     f"{_fmt(k['bas_dk'])}-{_fmt(k_bit)} kaydı gece uykusunun "
                     f"içinde kalıyor; ayrı bir gündüz uykusu sayılmadı")
            continue
        kalan.append(k)
    return kalan


def _wake_kayitlarini_coz(out: dict, wake_adaylari: list[dict]) -> None:
    """K19.4 — `wake` kayıtlarının rolünü belirle.

    Yeni mobil `wake` GÖNDERMEYECEK (anne "Uyandı" deyince uyku kaydı kapanıyor).
    Eski istemcilerden (build 16/18) gelmeye devam edecek, o yüzden üç rol ayrı
    ayrı tanımlı:

      • 06:00 ÖNCESİ  → gece uyanması. AYRICA sabah adayı olarak kalır: K10.1'in
        "erken uyandı ve tekrar uyumadı → gün 06:00'dan + şekerleme" yolu bu
        adaya dayanıyor.
      • 06:00 SONRASI + AÇIK gece uykusu var → gece uykusunu o saatte kapatır
        (sabah adayı olur, K12.4).
      • 06:00 SONRASI + gece uykusu kaydı HİÇ YOK → yine sabah adayı olur.
        Spec bu durumu "yok say" diye tanımlıyordu; UYGULANMADI, çünkü o zaman
        günün tek çıpası kaybolur ve "anne sadece Uyandı'ya bastı" senaryosunda
        gün varsayılan hedefe düşer (mevcut G5 senaryosu bunu ölçüyor).
      • 06:00 SONRASI + KAPALI gece uykusu var → GEREKSİZ. Sabah uyanışı zaten
        gece uykusunun bitişinden geliyor; kayıt yok_sayilan'a yazılır.

    Elenen `wake` kayıtları `uyaniklik_kanitlari`nda KALIR: bebeğin o saatte
    uyanık olduğu bilgisi doğrudur ve zincirin geriye düşmesini engeller (K7/E)
    ile açık nap'in kapanmasında (K17.2) kullanılır."""
    acik_gece = any(g.get("_devam") or g.get("bit_dk") is None
                    for g in out["gece_uykulari"])
    kapali_gece = any(g.get("bit_dk") is not None for g in out["gece_uykulari"])

    for w in sorted(wake_adaylari, key=lambda x: x["bas_dk"]):
        if w["bas_dk"] < GUN_BASLANGICI_EN_ERKEN:
            out["gece_uyanmalari"].append(w)
            out["wake_kayitlari"].append(w)
            out["uyaniklik_kanitlari"].append(w)
            continue
        out["uyaniklik_kanitlari"].append(w)
        if acik_gece or not kapali_gece:
            out["wake_kayitlari"].append(w)
        else:
            _yok_say(out, w, "gereksiz_wake",
                     f"{_fmt(w['bas_dk'])} uyanma kaydı kullanılmadı; sabah "
                     f"uyanışı gece uykusunun bitiş saatinden alındı")


def _parca_esigi(onceki: dict, kisa_esik: int) -> int:
    """Bu parçadan sonraki boşluk için geçerli birleştirme eşiği.

    İlk parça bandın "kısa uyku" eşiğinin altında kaldıysa 45 dk, tam bir uyku
    olduysa 15 dk (K20, v1.4)."""
    sure = onceki.get("sure_dk")
    if sure is not None and sure < kisa_esik:
        return PARCA_BIRLESTIRME_KISA_DK
    return PARCA_BIRLESTIRME_DK


def _parcalari_birlestir(kayitlar: list[dict], out: dict,
                         bant: dict | None = None) -> list[dict]:
    """K20 — aralarındaki boşluk ≤ PARCA_BIRLESTIRME_DK olan uykuları BİRLEŞTİR.

    Anne uzun bir uykuyu birden çok kayda bölüyor (14:00-14:36 + 14:36-15:46,
    bitişik) ya da kısa bir uyanıklıktan sonra yeniden uyutuyor (09:36 bitiş →
    09:50 başlangıç). Bunlar kopya DEĞİL (K18), çakışma da DEĞİL (K13) —
    hiçbiri yakalamıyordu ve gün 3 yerine 5 gündüz uykusu görünüyordu.

    Yalnız OKUMA anında yapılır; DB'ye yazılmaz (K20.2). Birleşen kayıtların
    id'leri `out["birlesen"]`e yazılır, oradan adaptation'a taşınır (K20.3).

    AÇIK kayıt birleştirmeye KAPATIR: "sürüyor" bilgisi kaybolmasın diye açık
    kayıt ancak dizinin SONUNDA olabilir ve kendinden öncekini yutabilir.

    Gece uykuları bu işlemden GEÇMEZ: gece parçaları zaten tek bir brüt/net
    toplamda birleşiyor (bkz. _gece_uykusu_ozeti) ve aradaki boşluk gece
    uyanması olarak ayrıca sayılıyor (K20.1'in ikinci cümlesi)."""
    if len(kayitlar) < 2 or PARCA_BIRLESTIRME_DK <= 0:
        return kayitlar

    kisa_esik = kisa_uyku_esigi(bant)
    sirali = sorted(kayitlar, key=lambda k: k["bas_dk"])
    out_list: list[dict] = [sirali[0]]
    for k in sirali[1:]:
        onceki = out_list[-1]
        onceki_bit = onceki.get("bit_dk_lin")
        if onceki_bit is None:          # açık kayıt yutulamaz
            out_list.append(k)
            continue
        bosluk = k["bas_dk"] - onceki_bit
        if bosluk > _parca_esigi(onceki, kisa_esik) or bosluk < 0:
            out_list.append(k)
            continue

        # Birleştir: başlangıç öncekinin, bitiş sonrakinin.
        birlesik = dict(onceki)
        birlesik["_parcalar"] = (onceki.get("_parcalar") or [onceki["id"]]) + [k["id"]]
        if k.get("bit_dk_lin") is None:                 # sonraki AÇIK → birleşik açık
            birlesik["bit_dk"] = None
            birlesik["bit_dk_lin"] = None
            birlesik["sure_dk"] = None
            birlesik["_devam"] = True
        else:
            birlesik["bit_dk_lin"] = max(onceki_bit, k["bit_dk_lin"])
            birlesik["bit_dk"] = birlesik["bit_dk_lin"] % 1440
            birlesik["sure_dk"] = birlesik["bit_dk_lin"] - birlesik["bas_dk"]
            birlesik["_devam"] = False
        # Otomatik kapanış işareti birleşmede anlamını yitirir.
        birlesik.pop("_otomatik_kapandi", None)
        out_list[-1] = birlesik

    for k in out_list:
        if k.get("_parcalar"):
            out["birlesen"].append([i for i in k["_parcalar"] if i])
    return out_list


def gece_uyanma_suresi(k: dict) -> int:
    """K12.2 — bir gece uyanmasının SAYIMDA kullanılacak süresi.

    `ended_at` yoksa GECE_UYANMA_VARSAYILAN_DK varsayılır. Bu varsayım YALNIZ
    hesap içindir: kayıt değiştirilmez, DB'ye hiçbir şey yazılmaz. Eskiden
    süresiz kayıt tümüyle atlanıyordu ve "bütün gece uyandı ama süre girmedim"
    diyen anne sistemde 0 uyanma olarak görünüyordu."""
    return (GECE_UYANMA_VARSAYILAN_DK if k.get("sure_dk") is None
            else max(0, int(k["sure_dk"])))


def sabah_uyanisi(kayitlar: dict, hedef_minute: int) -> dict:
    """K5 + K10 — bugünün sabah uyanışı. ORTALAMA YOK, yalnız bugünün kaydı.

    Aday: bir gece uykusunun bitişi ya da açık bir `wake` kaydı. Birden çok aday
    varsa SON uyanış geçerlidir — erken uyanıp tekrar uyuyan bebekte önceki
    uyanışlar gece bölünmesidir (K10.1, ikinci cümle).

    SAAT SINIRI YOK (K10.2): 09:00'da uyanan bebeğin günü 09:00'dan kurulur.
    Tek mutlak kural K10.1'dir: gün en erken 06:00'da başlar. Bundan önce uyanıp
    TEKRAR UYUMAYAN bebekte `minute` 06:00 olur (ekranda gösterilen), gerçek saat
    `gercek_minute` alanında korunur ve güne 30 dk şekerleme eklenir (K10.3).

    Hiç aday yoksa hedefin kendisi kullanılır (K6, kaynak 'varsayilan').

    K12.3 — 06:00 sonrası, ilk gündüz uykusundan ÖNCEKİ son `wake` kaydı sabah
    uyanışıdır. Gece uykusunun `ended_at`'i varsa öncelik ondadır; ikisi
    çelişiyorsa GEÇ olan alınır (max) — çünkü erken olan, sonrasında tekrar
    uyunmuş bir gece bölünmesidir.

    K12.4 — Gece uykusu AÇIK (ended_at null) ve 06:00 sonrası bir uyanma kaydı
    varsa, gece uykusu O SAATTE kapanmış kabul edilir. Bu yalnız hesapta
    geçerlidir; DB'deki kayıt DEĞİŞTİRİLMEZ.

    Dönen: {minute, gercek_minute, kaynak, zincir_baslangici, erken_uyanma,
            gece_bolunmeleri, uyarilar, wake_note, gece_uykusu}
      minute            : `wake` bloğunda GÖSTERİLECEK saat
      gercek_minute     : kayıttaki gerçek uyanış (erken uyanmada minute'tan farklı)
      zincir_baslangici : gündüz zincirinin başlayacağı dakika
      erken_uyanma      : K10.6 sözlüğü ya da None
      gece_uykusu       : K14.2 brüt/net gece uykusu özeti ya da None
    """
    # Günün ilk gündüz olayı (uyku ya da "atlandı" kaydı): bundan SONRAKİ bir
    # `wake` kaydı sabah uyanışı olamaz — "hâlâ uyanık" işaretidir (K7/E).
    ilk_gunduz = min([u["bas_dk"] for u in kayitlar["gunduz_uykulari"]]
                     + [a["bas_dk"] for a in kayitlar["atlananlar"]],
                     default=None)

    adaylar: list[int] = []
    acik_gece = False
    for g in kayitlar["gece_uykulari"]:
        if g["bit_dk"] is not None:
            adaylar.append(g["bit_dk"])       # K12.3 — önce gece uykusunun bitişi
        else:
            acik_gece = True                  # K12.4 — kapanmamış gece uykusu
    for w in kayitlar["wake_kayitlari"]:
        if ilk_gunduz is None or w["bas_dk"] <= ilk_gunduz:
            adaylar.append(w["bas_dk"])

    # Gece bölünmeleri DAKİKAYA GÖRE tekilleştirilir: aynı an hem gece uyanması
    # kaydından hem "sonradan tekrar uyudu" çıkarımından gelebiliyor.
    bolunme_dk: dict[int, str] = {}
    for gu in kayitlar["gece_uyanmalari"]:
        bolunme_dk[gu["bas_dk"]] = "gece uyanması kaydı"

    uyarilar: list[str] = []

    def _bolunmeler() -> list[dict]:
        return [{"saat": _fmt(m), "dakika": m, "sebep": s}
                for m, s in sorted(bolunme_dk.items())]

    bos = {"gercek_minute": None, "erken_uyanma": None, "wake_note": None,
           "uyarilar": uyarilar}

    if not adaylar:                                   # K6 — kayıt yok
        return dict(bos, minute=hedef_minute, kaynak="varsayilan",
                    zincir_baslangici=hedef_minute,
                    gece_bolunmeleri=_bolunmeler(),
                    gece_uykusu=_gece_uykusu_ozeti(kayitlar, None))

    gercek = max(adaylar)                             # SON uyanış (K10.1)
    # Son uyanıştan ÖNCEKİ her uyanış bir gece bölünmesidir.
    for a in sorted(set(adaylar)):
        if a < gercek:
            bolunme_dk.setdefault(
                a, "sonrasında tekrar uyudu — gece bölünmesi")

    # K12.4 — açık gece uykusu, seçilen uyanış saatinde kapanmış SAYILIR.
    kapanis = gercek if acik_gece else None

    if gercek >= GUN_BASLANGICI_EN_ERKEN:             # K10.2 — normal yol
        return dict(bos, minute=gercek, gercek_minute=gercek, kaynak="kayit",
                    zincir_baslangici=gercek,
                    gece_bolunmeleri=_bolunmeler(),
                    gece_uykusu=_gece_uykusu_ozeti(kayitlar, kapanis))

    # K10.1 — 06:00 öncesi uyandı ve tekrar uyumadı: gün 06:00'dan başlar.
    # v1.4 — erken uyanma ARTIK TEK BAŞINA şekerleme eklemiyor. Gün 06:00'dan
    # kurulur; şekerleme yalnız gündüz uyku açığı kalırsa günün sonunda eklenir
    # (bkz. _sekerleme_yerlestir). Bu uyarı o yüzden şekerlemeden söz etmiyor.
    uyarilar.append("Bebeğiniz çok erken uyandı; gün 06:00'dan başlatıldı.")
    return dict(
        bos,
        minute=GUN_BASLANGICI_EN_ERKEN,
        gercek_minute=gercek,
        kaynak="erken_uyanma",
        zincir_baslangici=GUN_BASLANGICI_EN_ERKEN,
        gece_bolunmeleri=_bolunmeler(),
        gece_uykusu=_gece_uykusu_ozeti(kayitlar, kapanis),
        # K10.5 — wake bloğu gün başlangıcını gösterir, gerçek saat nota yazılır.
        wake_note=(f"Bebeğiniz {saatli(gercek)} uyandı; gün "
                   f"{saatli(GUN_BASLANGICI_EN_ERKEN, 'den')} başlatıldı"),
        # K10.6 — mobilin okuduğu yapılandırılmış iz.
        # `sekerleme_eklendi` artık BURADA karara bağlanmıyor: şekerleme günün
        # sonunda, gündüz açığına göre eklenir. recompute_day gerçek sonucu
        # `adaptation.sekerleme` alanına yazar; bu bayrak geriye uyum için
        # duruyor ve DAİMA False'tur.
        erken_uyanma={"gercek_saat": _fmt(gercek),
                      "gun_baslangici": _fmt(GUN_BASLANGICI_EN_ERKEN),
                      "sekerleme_eklendi": False},
    )


def _gece_uykusu_ozeti(kayitlar: dict, kapanis_dk: int | None) -> dict | None:
    """K14.2 — gece uykusunun brüt/net süresi ve gece uyanma sayımı.

    net = brüt − gece uyanmalarının toplam süresi. v2.1'e kadar yalnız brüt
    vardı; 4 kez uyanıp toplam 80 dakika ayakta kalan bebek "11 saat uyudu"
    olarak raporlanıyordu ve "yeterince uyuyor mu" kararı buna bakıyordu.

    `kapanis_dk`: gece uykusu AÇIK kaldıysa (ended_at null) hesapta kullanılacak
    kapanış dakikası (K12.4). None ve kayıt da açıksa brüt hesaplanamaz.

    Gece uykusu kaydı hiç yoksa None döner — 0 DEĞİL: "veri yok" ile "hiç
    uyumadı" karıştırılmamalı."""
    uyanma_dk = sum(gece_uyanma_suresi(g) for g in kayitlar["gece_uyanmalari"])
    uzun = sum(1 for g in kayitlar["gece_uyanmalari"]
               if gece_uyanma_suresi(g) >= UZUN_UYANMA_MIN_DK)
    sayim = {"uyanma_sayisi": len(kayitlar["gece_uyanmalari"]),
             "uzun_uyanma_sayisi": uzun,
             "uyanma_toplam_dk": uyanma_dk}

    geceler = kayitlar["gece_uykulari"]
    if not geceler:
        return dict(sayim, brut_dk=None, net_dk=None, acik_mi=False) \
            if kayitlar["gece_uyanmalari"] else None

    # Bugüne bağlanan gece uykusu tektir (çakışanlar K13'te elendi); birden çok
    # parça varsa (gece bölünüp tekrar dalmış) süreleri toplanır.
    brut = 0
    acik = False
    for g in geceler:
        bit = g.get("bit_dk_lin")
        if bit is not None:
            brut += max(0, bit - g["bas_dk"])
            continue
        # AÇIK kayıt. gun_kayitlari bugüne yalnız DÜN BAŞLAMIŞ açık gece
        # uykusunu bağlar, dolayısıyla başlangıç bir önceki gündedir: bugünün
        # kapanış dakikasına göre doğrusallaştırmak için +24 saat eklenir.
        acik = True
        if kapanis_dk is None:
            continue
        brut += max(0, kapanis_dk + 1440 - g["bas_dk"])
    if brut <= 0:
        return dict(sayim, brut_dk=None, net_dk=None, acik_mi=acik)
    return dict(sayim, brut_dk=brut, net_dk=max(0, brut - uyanma_dk),
                acik_mi=acik)


def _sablon_penceresi(sablon: list[dict]) -> int | None:
    """Şablonun kodladığı uyanıklık penceresi: ilk uyku başlangıcı − uyanış.

    Şablon `_cizelge_kur` ile kurulduğu için bu fark daima penceredir. Uyku
    içermeyen şablonda (ör. tek bloklu eski kayıt) None döner."""
    wake = next((b for b in sablon if b.get("key") == "wake"), None)
    ilk_nap = next((b for b in sablon if b.get("type") == "nap"), None)
    if wake is None or ilk_nap is None:
        return None
    ww = int(ilk_nap["start_minute"]) - int(wake["start_minute"])
    return ww if ww > 0 else None


def _sablon_naplari(schedule_template: list[dict]) -> list[dict]:
    """Şablondaki gündüz uykusu yuvaları: key + planlanan süre."""
    out = []
    for b in normalize_schedule(schedule_template):
        if b.get("type") == "nap" and b.get("start_minute") is not None:
            out.append({"key": b.get("key") or f"nap_{len(out) + 1}",
                        "title": b.get("title") or f"{len(out) + 1}. gündüz uykusu",
                        "sure_dk": max(1, int(b["end_minute"]) - int(b["start_minute"]))})
    return out


def recompute_day(schedule_template: list[dict], bant: dict | None,
                  sabit_wake: int, todays_logs: Iterable[Any],
                  now_minute: int, *, gun: date | None = None,
                  bucket_params: dict | None = None,
                  tz_offset_min: int = TZ_OFFSET_MIN) -> dict:
    """K2/K3/K4/K6/K7 — BUGÜNÜN çizelgesini şablon + bugünün kayıtlarından kur.

    SAF FONKSİYON: DB'ye dokunmaz, LLM çağırmaz, ağa çıkmaz. Aynı girdi daima
    aynı çıktıyı verir (K8). `now_minute` yalnız ETİKETLEMEYİ etkiler
    ("varsayılan blok" mu, gelecek blok mu) — SAATLERİ etkilemez, böylece gün
    ilerledikçe çizelge kendiliğinden oynamaz.

    Kayıt HİÇ yoksa sonuç şablonun birebir aynısıdır (K6).

    Dönen: {"schedule": [...], "adaptation": {...}}
    """
    sablon = normalize_schedule(schedule_template)
    gun = gun or datetime.now(timezone.utc).date()

    # --- Uyanıklık penceresi -------------------------------------------------
    # ÖNCELİK ŞABLONDUR. Şablon, üretildiği andaki pencereyi zaten kodluyor
    # (ilk uyku - uyanış). Buradan okumak "kayıt yoksa sonuç şablonun aynısıdır"
    # özdeşliğini (K6) HER yolda garanti eder: tablo sürümü değişmiş, KB'den
    # kurulmuş ya da doğum tarihi olmayan eski planlarda da çizelge kendiliğinden
    # oynamaz. Şablondan okunamazsa banda, sonra KB metnine, sonra varsayılana
    # düşülür. (Bebek bant atladıysa şablon zaten yeniden üretilir — bkz. adapt.)
    ww = _sablon_penceresi(sablon)
    if bant is not None:
        if ww is None:
            ww = int(yas_bantlari.cizelge_parametreleri(
                bant)["uyaniklik_penceresi_dk"])
        yatma_lo, yatma_hi = yas_bantlari.yatma_araligi(bant, sabit_wake)
        if bant.get("yatma_vakti_dk"):          # K4 — bandın MUTLAK yatış tavanı
            yatma_hi = min(yatma_hi, int(bant["yatma_vakti_dk"][1]))
    else:
        # Faz Y öncesi çağrı: yatma aralığı KB serbest metninden ayrıştırılır.
        p = bucket_params or {}
        if ww is None:
            ww = _mid(parse_duration_range(p.get("uyaniklik_penceresi"))
                      or DEFAULT_WAKE_WINDOW)
        yatma_lo, yatma_hi = (parse_time_range(p.get("yatma_vakti"))
                              or DEFAULT_BEDTIME_RANGE)

    # K4 tavanı ŞABLONU GERİYE DÖNÜK CEZALANDIRMAZ: şablonun kendi yatışı zaten
    # üretim anında bandın aralığına kırpılmıştı. Bant/KB aralığı o sırada farklı
    # olsaydı bugün şablonun yatışını kırpar ve "kayıt yok → şablon" özdeşliğini
    # bozardık. Bu yüzden aralık şablonun yatışını kapsayacak kadar genişletilir;
    # şablondan SONRAYA düşen her yatış için tavan yine bağlayıcıdır.
    _tpl_bed = next((b["start_minute"] for b in sablon if b.get("key") == "bedtime"),
                    None)
    if _tpl_bed is not None:
        yatma_lo, yatma_hi = min(yatma_lo, _tpl_bed), max(yatma_hi, _tpl_bed)

    # K16.2/K17 — açık kayıtları çözebilmek için bandın planlanan uyku süresi.
    # Bant yoksa ŞABLONUN kendi uyku uzunluğu kullanılır; ikisi de yoksa açık
    # kayıt çözümü atlanır (uydurma süre üretmeyiz).
    _tpl_naplar = _sablon_naplari(sablon)
    if bant is not None:
        _nap_sure = int(yas_bantlari.cizelge_parametreleri(bant)["uyku_suresi_dk"])
        # K19.3 — gece tarafının ölçüsü bandın gece uykusu ALT sınırıdır.
        _gece_sure = (bant.get("gece_uykusu_dk") or [None])[0]
        _gece_sure = int(_gece_sure) if _gece_sure else None
    else:
        _nap_sure = _tpl_naplar[0]["sure_dk"] if _tpl_naplar else None
        _gece_sure = None

    kayitlar = gun_kayitlari(todays_logs, gun, sabit_wake, tz_offset_min,
                             nap_sure_dk=_nap_sure, gece_sure_dk=_gece_sure,
                             now_minute=now_minute, bant=bant)
    sabah = sabah_uyanisi(kayitlar, sabit_wake)

    uyarilar: list[str] = list(sabah["uyarilar"])

    # --- K16.2/K17 — otomatik kapatılan açık kayıtların uyarıları ------------
    # Anne "sayacı kapatmayı unuttum" bilgisini EKRANDA görmeli; aksi hâlde
    # çizelgede nereden çıktığı belirsiz bir uyku bloğu duruyor.
    for _k in kayitlar["gunduz_uykulari"]:
        _sebep = _k.get("_otomatik_kapandi")
        if _sebep == "bayat":
            uyarilar.append(
                f"{saatli(_k['bas_dk'])} başlayan uykunun bitişi girilmemiş; "
                f"sayacı kapatmayı unutmuş olabilirsiniz")
        elif _sebep == "yeni_kayit":
            uyarilar.append(
                f"{_fmt(_k['bas_dk'])} uykusunun bitişi girilmemişti; yeni bir "
                f"kayıt girdiğiniz için {_fmt(_k['bit_dk_lin'])}'da bitmiş "
                f"kabul edildi")
        elif _sebep == "wake":
            uyarilar.append(
                f"{_fmt(_k['bas_dk'])} uykusunun bitişi girilmemişti; uyanma "
                f"kaydınıza göre {_fmt(_k['bit_dk_lin'])}'da bitmiş sayıldı")
    for _g in kayitlar["gece_uykulari"]:
        if _g.get("_bayat"):
            uyarilar.append(
                f"{saatli(_g['bas_dk'])} başlayan gece uykusunun bitişi "
                f"girilmemiş; sabah uyanış saatini eklerseniz plan netleşir")

    # --- K15 — SAĞLAMLIK KURALI ---------------------------------------------
    # Hiçbir gündüz uykusu, sabah uyanışı + bandın MİNİMUM uyanıklık
    # penceresinden önce BAŞLAYAMAZ. Beta verisinde 14,5 aylık bir bebek için
    # 08:00 uyanış → 09:00 uyku üretilmişti; bandın alt sınırı 180 dk olduğu
    # için bu çizelge fiziksel olarak imkânsızdı.
    #
    # Bant yoksa ŞABLONUN penceresi kullanılır — uydurma bir alt sınır
    # koymuyoruz, yoksa kural kendi başına yanlış uyarı üretirdi.
    ww_min_k15 = (int(bant["uyaniklik_penceresi_dk"][0])
                  if (bant is not None and bant.get("uyaniklik_penceresi_dk"))
                  else ww)
    en_erken_uyku = sabah["zincir_baslangici"] + ww_min_k15
    varsayilan: list[str] = []
    atlanan: list[str] = []

    wake_blok = {
        "key": "wake", "type": "wake",
        "start_minute": sabah["minute"], "end_minute": sabah["minute"],
        "title": "Sabah uyanışı",
    }
    if sabah.get("wake_note"):                       # K10.5
        wake_blok["note"] = sabah["wake_note"]
    bloklar: list[dict] = [wake_blok]

    yuvalar = _sablon_naplari(sablon)
    # v1.4 — ŞEKERLEME ARTIK BURADA EKLENMİYOR.
    # Eskiden (K10.3) erken uyanma TEK BAŞINA 30 dk'lık bir blok ekliyordu ve
    # blok GÜNÜN BAŞINA konuyordu. İlayda: "İlla her zaman bir şekerlemeye
    # gerek yok. Gün içerisinde uyku yetersiz kalırsa bir şekerleme yapıyoruz"
    # ve "günün sonunda onu ekleyebiliyoruz". Yani tetikleyici erken uyanma
    # değil GÜNDÜZ UYKU AÇIĞI, yeri de günün sonu. Zincir kurulduktan SONRA
    # `_sekerleme_yerlestir` karar veriyor.
    # Gerçek uykular ve "atlandı" kayıtları TEK bir zaman sıralı olay dizisidir;
    # yuvalara sırayla oturur. Böylece "1. uykuyu atladı, 2.'yi 13:40'ta yaptı"
    # gibi karışık günler de tek kuralla işlenir.
    olaylar = sorted(
        [dict(u, _atlandi=False) for u in kayitlar["gunduz_uykulari"]]
        + [dict(a, _atlandi=True) for a in kayitlar["atlananlar"]],
        key=lambda x: x["bas_dk"])

    # Bebeğin UYANIK olduğu kanıtlanan en geç an — atlanan uyku ve gündüz `wake`
    # kayıtlarından gelir. Zincir bu ânın gerisine düşemez (K7/E senaryosu).
    kanit = sabah["zincir_baslangici"]
    for a in kayitlar["atlananlar"]:
        kanit = max(kanit, a["bas_dk"])
    for w in kayitlar["uyaniklik_kanitlari"]:
        # K19.4 — sabah adaylığından elenen `wake` de bebeğin uyanık olduğunu
        # KANITLAR; zincir onun gerisine düşemez.
        kanit = max(kanit, w["bas_dk"])

    cursor = sabah["zincir_baslangici"]
    sirada = 0                       # tüketilmemiş ilk olay
    for yuva in yuvalar:
        # Şekerlemenin yeri SABİTTİR (06:00 + minimum pencere, K10.3); normal
        # uykular zincirden gelir. K10.4: şekerlemeden sonra zincir NORMAL
        # pencereyle devam eder (nap_1 = şekerleme bitişi + ww).
        aday = (max(yuva["sabit_bas"], kanit) if yuva.get("sabit_bas") is not None
                else max(cursor + ww, kanit))            # bu yuvanın zincir yeri
        # K15 — ZİNCİRDEN gelen blok kuralı ihlal edemez: ileri kaydırılır.
        # `sabit_bas` taşıyan şekerleme bloğu hariç tutulur; onun yeri zaten
        # K10.3 ile bandın minimum penceresine bağlı ve kendi kuralı var.
        if yuva.get("sabit_bas") is None and aday < en_erken_uyku:
            uyarilar.append(
                f"{yuva['title']} çok erkene denk geliyordu ({_fmt(aday)}); "
                f"bebeğinizin bu yaşta en az {ww_min_k15} dakika uyanık "
                f"kalması gerektiği için {saatli(en_erken_uyku, 'e')} alındı.")
            aday = en_erken_uyku
        olay = None
        if sirada < len(olaylar):
            # EŞLEŞTİRME: sıradaki kayıt BU yuvaya mı ait, yoksa daha sonrakine mi?
            # Ölçüt, bir SONRAKİ yuvanın başlayabileceği en erken an: kayıt ondan
            # önce başlıyorsa bu yuvanındır. Konum bazlı eşleştirme yanlıştı —
            # 13:40'taki tek kayıt 1. uykunun yerine geçiyordu (ölçüldü).
            if olaylar[sirada]["bas_dk"] < aday + ww:
                olay = olaylar[sirada]
                sirada += 1

        if olay is not None and olay["_atlandi"]:        # K7 — hiç uyumadı
            atlanan.append(yuva["key"])
            kanit = max(kanit, olay["bas_dk"])
            continue                                     # blok çizelgeye GİRMEZ

        if olay is not None:                             # K3 — gerçek kayıt
            bas = olay["bas_dk"]
            bit = (olay["bit_dk_lin"] if olay.get("bit_dk_lin") is not None
                   else bas + yuva["sure_dk"])           # süren uyku → planlı süre
            # K15 — KAYIT kaydırılmaz: olan olmuştur, çizelge gerçeği yazar.
            # Ama sessiz de geçilmez; anne neden uyarı aldığını görmeli.
            if bas < en_erken_uyku:
                uyarilar.append(
                    f"{yuva['title']}nu {saatli(bas)} kaydetmişsiniz; bu "
                    f"yaşta önerilen en erken saat {_fmt(en_erken_uyku)}. "
                    f"Kaydınız korundu, günün geri kalanı buna göre "
                    f"planlandı.")
            blok = {"key": yuva["key"], "type": "nap",
                    "start_minute": bas, "end_minute": bit,
                    "title": yuva["title"], "kaynak": "kayit"}
            # NOT YALNIZ GERÇEKTEN AÇIK KAYITTA: `_devam` otomatik kapatma
            # sırasında False'a çekiliyor ama savunma amaçlı bitişe de bakılır
            # — kapanmış bir kayda "sürüyor" demek anneyi yanıltır.
            if olay.get("_devam") and olay.get("bit_dk") is None:
                blok["note"] = "Uyku sürüyor; bitiş saati tahmini"
                # X6 — toplam uyku hesabı bu bloğu ŞU ANA kadar sürmüş kabul
                # etmeli; planlanan bitiş yalnız GÖSTERİM içindir. İşaret
                # olmadan 10 dakikadır uyuyan bebek 70 dk uyumuş sayılıyordu.
                blok["devam"] = True
            elif olay.get("_otomatik_kapandi"):
                blok["note"] = {
                    "bayat": "Sayaç kapatılmamış; bitiş saati tahmin edildi",
                    "yeni_kayit": "Sayaç kapatılmamış; yeni kayıt girince kapandı",
                    "wake": "Sayaç kapatılmamış; uyanma kaydıyla kapandı",
                }.get(olay["_otomatik_kapandi"], "Sayaç kendiliğinden kapandı")
                blok["otomatik_kapandi"] = olay["_otomatik_kapandi"]
            bloklar.append(blok)
            cursor = max(cursor, bit)
            kanit = max(kanit, bit)
            continue

        bas = aday                                       # K3 — pencere zinciri
        bit = bas + yuva["sure_dk"]
        blok = {"key": yuva["key"], "type": "nap",
                "start_minute": bas, "end_minute": bit,
                "title": yuva["title"]}
        if bit <= now_minute:                            # K6 — zamanı geçti, kayıt yok
            blok["kaynak"] = "varsayilan"
            # HENÜZ OLMAMIŞ bloğa not yazılmaz: "planlandığı gibi geçti"
            # varsayımı ancak saat geçtikten sonra anlamlıdır. Gelecekteki
            # uykuda not, anneye olmamış bir şeyi olmuş gibi gösteriyordu.
            blok["note"] = f"Önceki uykudan ~{ww} dakika sonra"
            varsayilan.append(yuva["key"])
        else:
            blok["kaynak"] = "plan"
        bloklar.append(blok)
        cursor = bit

    # Şablondan fazla kayıt girildiyse (anne 4. uykuyu da kaydetti) zincire ekle.
    for j, fazla in enumerate(olaylar[sirada:], start=len(yuvalar) + 1):
        if fazla["_atlandi"]:
            continue
        bit = (fazla["bit_dk_lin"] if fazla.get("bit_dk_lin") is not None
               else fazla["bas_dk"] + 30)
        _fazla_blok = {"key": f"nap_{j}", "type": "nap",
                       "start_minute": fazla["bas_dk"], "end_minute": bit,
                       "title": f"{j}. gündüz uykusu", "kaynak": "kayit",
                       "note": "Programda olmayan ek uyku (kaydına göre)"}
        if fazla.get("_devam"):
            _fazla_blok["devam"] = True
        bloklar.append(_fazla_blok)
        cursor = max(cursor, bit)

    # --- v1.4: ŞEKERLEME (K9 + K10.3 tek mekanizma) --------------------------
    # Gündüz toplamı bandın minimumunu tutmuyorsa günün SONUNA ilave uyku.
    bloklar, sekerleme_bilgi, sek_uyari = _sekerleme_yerlestir(
        bloklar, bant, cursor, ww, yatma_lo, yatma_hi, now_minute)
    uyarilar.extend(sek_uyari)
    # ŞEKERLEME ZİNCİR HALKASI DEĞİLDİR: `cursor` ilerletilmez. İlayda:
    # "30 dakika uyutup kalktıktan sonra ÜÇ SAAT SONRA BİLE gece uykusuna
    # geçirebiliyoruz." Şekerlemeden sonra tam uyanıklık penceresi dayatmak
    # yatışı bandın tavanının çok ötesine atıp şekerlemeyi iptal ettiriyordu.
    # Tek kısıt: yatış, şekerleme bitişinden en az `gece_uykusuna_gecis_dk`
    # sonra olmalı.
    en_erken_yatis = None
    if sekerleme_bilgi:
        _gecis = int((bant or {}).get("kestirme_protokolu", {})
                     .get("gece_uykusuna_gecis_dk") or 60)
        en_erken_yatis = sekerleme_bilgi["end_minute"] + _gecis

    # --- K4: yatış zinciri + gece uykusu tavanı ------------------------------
    bloklar, yatis, k4_uyari = _yatisi_yerlestir(
        bloklar, cursor, ww, yatma_lo, yatma_hi, sabit_wake, now_minute,
        en_erken_yatis=en_erken_yatis)
    uyarilar.extend(k4_uyari)
    if yatis.get("kaynak") == "varsayilan":
        varsayilan.append("bedtime")

    schedule = [_with_labels(b) for b in bloklar]

    # Şablondan SAATİ farklı olan her blok "yeniden hesaplanmış"tır — mobil
    # bunları vurgulayabilsin diye anahtarları listelenir.
    sablon_saat = {b.get("key"): b.get("start_minute") for b in sablon}
    yeniden = [b["key"] for b in bloklar
               if sablon_saat.get(b["key"]) != b["start_minute"]]

    adaptation = {
        "hesaplandi_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sabah_uyanis_hedef": _fmt(sabit_wake),
        # K10: erken uyanmada GERÇEK saat yazılır (gösterilen 06:00 değil).
        "sabah_uyanis_gercek": _fmt(sabah["gercek_minute"]
                                    if sabah.get("gercek_minute") is not None
                                    else sabah["minute"]),
        "sabah_uyanis_kaynak": sabah["kaynak"],
        # 'varsayilan' = bugün hiç uyanma kaydı yok; mobil sabah sorusunu
        # ("bebeğiniz saat kaçta uyandı?") bu değere bakarak gösterir.
        # 'kayit' ve 'erken_uyanma' kayıttan gelir, soru gösterilmez.
        "siradaki_blok": siradaki_blok_bilgisi(schedule, now_minute,
                                               sabah["kaynak"]),
        # K10.6 — erken uyanma izi; erken uyanma yoksa None.
        "erken_uyanma": sabah.get("erken_uyanma"),
        # v1.4 — şekerleme eklendiyse gerekçesi ve süresi (mobil kartı bunu
        # gösteriyor). Eklenmediyse None — erken uyanma TEK BAŞINA eklemez.
        "sekerleme": sekerleme_bilgi,
        "varsayilan_bloklar": varsayilan,
        "yeniden_hesaplanan_bloklar": yeniden,
        "atlanan_bloklar": atlanan,
        "yok_sayilan_kayitlar": kayitlar["yok_sayilan"],
        "gece_bolunmeleri": sabah["gece_bolunmeleri"],
        "uyaniklik_penceresi_dk": ww,
        # K15 — çizelgenin uyduğu mutlak alt sınır (mobil "neden bu saat?"
        # sorusunu buradan cevaplayabilir).
        "en_erken_uyku": _fmt(en_erken_uyku),
        "min_uyaniklik_penceresi_dk": ww_min_k15,
        # K12.2/K14.2 — gece uyanma sayımı ve brüt/net gece uykusu.
        "gece_uykusu": sabah.get("gece_uykusu"),
        # v1.4 — regresyon akışının aşaması; adapt() kartı dolduruyorsa
        # mobil buradan "soru mu, devam mı, tıbbi mi" ayrımını okur.
        "regresyon": None,
        # K20.3 — okuma anında birleştirilen parça kayıtların id'leri.
        # DB'de hiçbir şey değişmedi; mobil "bu iki kaydı tek uyku saydık"
        # bilgisini buradan gösterebilir.
        "birlesen_kayitlar": kayitlar.get("birlesen") or [],
        "parca_birlestirme_dk": PARCA_BIRLESTIRME_DK,
        "parca_birlestirme_kisa_dk": PARCA_BIRLESTIRME_KISA_DK,
        "uyarilar": uyarilar,
    }
    return {"schedule": schedule, "adaptation": adaptation}


def _sekerleme_yerlestir(bloklar: list[dict], bant: dict | None, cursor: int,
                         ww: int, yatma_lo: int, yatma_hi: int,
                         now_minute: int) -> tuple[list[dict], dict | None,
                                                   list[str]]:
    """v1.4 — gündüz uyku açığı varsa GÜNÜN SONUNA şekerleme ekle (K9+K10.3).

    TETİKLEYİCİ: günün hesaplanan gündüz toplamı (gerçek kayıtlar + varsayılan
    bloklar) bandın gündüz MİNİMUMUNUN altında. Erken uyanma TEK BAŞINA
    tetiklemez — erken uyanan ama uykusunu dolduran bebeğe blok eklenmez.
    12-18 ay tek uyku varyantında aynı ölçüt "öğle uykusu < 120 dk" demektir
    (bandın gündüz minimumu zaten 120), yalnız pencere 18:00-19:00'a daralır.

    KONUM: son gündüz uykusunun bitişinden İTİBAREN bandın MİNİMUM uyanıklık
    penceresi kadar sonra, tablodaki saat penceresinde (17:00-19:00), gece
    yatışından en az `gece_uykusuna_gecis_dk` (60 dk) önce bitecek şekilde.

    UYANIKLIK PENCERESİ ŞEKERLEMEYE DE UYGULANIR (K15). Eskiden yalnız
    `max(son_uyku_bitişi, 17:00)` alınıyordu; son uyku 16:50'de bittiğinde
    şekerleme 17:00'de başlıyor, hatta 17:10'da biten bir uykudan SIFIR dakika
    sonra başlayabiliyordu. Anneye "uyandır, hemen yatır" talimatı çıkıyordu —
    beta ölçümünde 4 ve 6 aylık iki profilde 22 günde tekrarladı.

    SÜRE: taban 30 dk; şekerleme başlangıcı ile gece yatışı arasında
    `gece_yatisina_kalan_min_dk` (150 dk) ya da fazlası varsa 60 dk
    ("30 dakika minimum süredir; gece uykusunu geciktirmiyorsak bir saat de
    yapabilir").

    Sığmıyorsa blok EKLENMEZ ve sebep uyarıya yazılır — sessizce atlanmaz."""
    uyarilar: list[str] = []
    if bant is None:
        return bloklar, None, uyarilar

    kestirme = yas_bantlari.kestirme_degerlendir(bant, _gunduz_toplam(bloklar))
    if not kestirme.get("gerekli"):
        return bloklar, None, uyarilar

    tek_uyku = (bant.get("varyant") == "tek_uyku")
    pencere = kestirme.get("saat_penceresi") or ["17:00", "19:00"]
    pen_bas, pen_bit = _saat_dk(pencere[0]), _saat_dk(pencere[1])
    if tek_uyku:
        pen_bas = max(pen_bas, SEKERLEME_TEK_UYKU_PENCERE[0])

    naplar = [b for b in bloklar if b["type"] == "nap"]
    son_bitis = max((b["end_minute"] for b in naplar), default=cursor)
    # K15 — bebek son uykusundan kalktıktan sonra en az bandın MİNİMUM
    # uyanıklık penceresi kadar uyanık kalmalı. `ww` şablonun/bandın ÇALIŞMA
    # penceresidir (orta değer olabilir); burada ölçüt bandın ALT sınırıdır:
    # amaç "en erken ne zaman yatırılabilir" sorusuna cevap vermek.
    min_ww = _min_uyaniklik(bant, ww)
    en_erken = son_bitis + min_ww
    bas = max(en_erken, pen_bas)

    # Yatış, şekerleme eklenmemiş hâliyle nereye düşüyordu?
    yatis_tahmini = max(yatma_lo, min(yatma_hi, cursor + ww))
    gecis = int(kestirme.get("gece_uykusuna_gecis_dk") or 60)
    lo = int(kestirme.get("sure_dk_min") or SEKERLEME_DK)
    hi = int(kestirme.get("sure_dk_max") or lo)
    kalan_esik = int(kestirme.get("gece_yatisina_kalan_min_dk") or 150)

    sure = hi if (yatis_tahmini - bas) >= kalan_esik else lo
    if bas >= pen_bit:
        uyarilar.append(
            f"Gündüz uykusu {kestirme['eksik_dk']} dakika eksik kaldı ama "
            f"akşam şekerlemesi için yer kalmadı; gece yatışını yaşına uygun "
            f"en geç saatte tutun. (Son uyku {saatli(son_bitis)} bitti, "
            f"{min_ww} dakika uyanık kalması gerektiği için en erken "
            f"{_fmt(bas)} olabilirdi; şekerleme saatleri "
            f"{pencere[0]}-{pencere[1]} arasında.)")
        return bloklar, None, uyarilar

    # Gece yatışından en az `gecis` dk önce bitmeli. ÖLÇÜT BANDIN TAVANI,
    # şekerlemesiz hesaplanan yatış DEĞİL: şekerleme eklenince yatış zaten
    # ileri kayıyor (İlayda: "30 dakika uyutup kalktıktan sonra üç saat sonra
    # bile gece uykusuna geçirebiliyoruz"). Öngörülen yatışa göre ölçmek, tek
    # uyku varyantında şekerlemeyi imkânsız kılıyordu (18:00 pencere + 19:00
    # öngörü → hiçbir süre sığmıyor).
    en_gec_bitis = min(pen_bit, yatma_hi - gecis)
    if bas + lo > en_gec_bitis:
        uyarilar.append(
            f"Gündüz uykusu {kestirme['eksik_dk']} dakika eksik kaldı ama "
            f"akşam şekerlemesi için yer kalmadı; gece yatışını yaşına uygun "
            f"en geç saatte tutun. (En erken {saatli(bas)} başlayabilirdi, "
            f"gece uykusuna en az {gecis} dakika kalması gerekiyor.)")
        return bloklar, None, uyarilar
    sure = max(lo, min(sure, en_gec_bitis - bas))

    # SON KONTROL — buraya sıfır/kısa uyanıklıkla gelinmemeli. Gelinirse blok
    # ÜRETİLMEZ: yanlış bir talimat vermektense şekerlemesiz gün göstermek
    # daha doğrudur.
    if bas - son_bitis < min_ww:
        uyarilar.append(
            f"Gündüz uykusu {kestirme['eksik_dk']} dakika eksik kaldı ama "
            f"akşam şekerlemesi için yer kalmadı; gece yatışını yaşına uygun "
            f"en geç saatte tutun. (Son uykudan yalnız "
            f"{bas - son_bitis} dakika sonrasına denk geliyordu, bu yaşta en "
            f"az {min_ww} dakika uyanık kalmalı.)")
        return bloklar, None, uyarilar

    blok = {"key": SEKERLEME_KEY, "type": "nap",
            "start_minute": bas, "end_minute": bas + sure,
            "title": sekerleme_basligi(sure),
            "kaynak": "varsayilan" if bas + sure <= now_minute else "plan",
            "note": (f"Gündüz uykusu {kestirme['eksik_dk']} dakika eksik "
                     f"kaldığı için eklendi")}
    bloklar.append(blok)
    uyarilar.append(
        f"Bugünkü gündüz uykusu toplam {kestirme['gerceklesen_dk']} dakika; "
        f"bu yaşta en az {kestirme['min_gunduz_dk']} dakika gerekiyor "
        f"({kestirme['eksik_dk']} dakika eksik). {saatli(bas)} {sure} "
        f"dakikalık şekerleme eklendi.")
    return bloklar, {"start_minute": bas, "end_minute": bas + sure,
                     "sure_dk": sure, "eksik_dk": kestirme["eksik_dk"],
                     "tetik": "tek_uyku_kisa" if tek_uyku else "gunduz_acigi"}, uyarilar


def _min_uyaniklik(bant: dict | None, varsayilan: int) -> int:
    """Bandın MİNİMUM uyanıklık penceresi (dk). Tablo okunamazsa `varsayilan`.

    `cizelge_parametreleri` orta/çalışma değerini verir; K15 için gereken ALT
    sınırdır (bir bloğun en erken başlayabileceği an)."""
    ww = (bant or {}).get("uyaniklik_penceresi_dk")
    if isinstance(ww, (list, tuple)) and ww and ww[0]:
        return int(ww[0])
    return int(varsayilan)


# --- SIRADAKİ BLOK (v2.4.3) -------------------------------------------------
# Mobilin "sıradaki uyku" kartı bu alanı okur ve iki soruya cevap verir:
#   1) Bir sonraki uyku ne zaman başlıyor?
#   2) Bu saate GÜVENİLİR Mİ, yoksa eksik bir kayıt yüzünden tahmin mi?
# Çizelge bir ZİNCİRDİR: her uykunun saati bir öncekinin GERÇEK bitişinden
# türer, zincirin çıpası da sabah uyanışıdır. Halkalardan biri gerçek kayıt
# değilse (ya da kayıt hâlâ açıksa) saat tahmindir — o zaman anneye hangi
# kaydın eksik olduğunu söyleriz ki kartta "uyanma saatini gir" eylemi
# gösterilebilsin. Sessizce tahmin göstermek, anneye olmayan bir kesinlik
# vaat ediyordu.
# DİKKAT: bu BLOK tipleridir. Aynı modüldeki `UYKU_TIPLERI` KAYIT tipleridir
# ("sekerleme" dahil) ve adı çakışırsa kayıt eşlemesi sessizce bozulur —
# bir kez bozuldu, `sekerleme` kayıtları "tanınmayan tip" diye elendi.
UYKU_BLOK_TIPLERI = ("nap", "sleep")


def _gercekten_kapandi(blok: dict) -> bool:
    """Blok GERÇEK bir kayıttan gelip KAPANMIŞ mı?

    Üç durum "hayır"dır: kayıt yok (plan/varsayılan blok), kayıt hâlâ açık
    (`devam`), kaydı sayaç unutulduğu için biz kapattık (`otomatik_kapandi`).
    Üçünde de bitiş saati tahmindir, dolayısıyla sonraki uykunun saati de."""
    return (blok.get("kaynak") == "kayit"
            and not blok.get("devam")
            and not blok.get("otomatik_kapandi"))


def siradaki_blok_bilgisi(cizelge: list[dict], now_minute: int,
                          sabah_kaynak: str | None) -> dict | None:
    """`adaptation.siradaki_blok` gövdesi. Gün bittiyse None.

    guven "kesin" YALNIZ iki koşul birden sağlanırsa:
      - sabah uyanışı gerçek kayıttan geliyorsa (kaynak 'varsayilan' değil),
      - zincirde hemen önceki uyku gerçek kayıtla kapanmışsa.
    Aksi hâlde "tahmini" ve `eksik_kayit` neyin girilmesi gerektiğini söyler.
    Sabah uyanışı zincirin ÇIPASI olduğu için o eksikse önceliklidir: son
    uykunun bitişi girilse bile zincirin başı belirsizdir."""
    uykular = sorted(
        (b for b in cizelge or []
         if b.get("type") in UYKU_BLOK_TIPLERI
         and b.get("start_minute") is not None),
        key=lambda b: b["start_minute"])
    sonraki = next((b for b in uykular if b["start_minute"] > now_minute), None)
    if sonraki is None:                    # gece yatışı da geçti — gün bitti
        return None

    # Zincirde hemen önceki uyku: başlangıcı geçmiş SON blok. `sonraki` zaten
    # başlangıcı geçmemiş İLK blok olduğu için aradaki her blok geçmiştedir.
    onceki = next((b for b in reversed(uykular)
                   if b["start_minute"] <= now_minute), None)

    eksik = None
    if sabah_kaynak == "varsayilan":       # K6 — hiç uyanma kaydı yok
        eksik = "sabah_uyanisi"
    elif onceki is not None and not _gercekten_kapandi(onceki):
        eksik = "son_uyku_bitisi"

    return {
        "key": sonraki.get("key"),
        "time": sonraki.get("time") or _fmt(sonraki["start_minute"]),
        "end": sonraki.get("end") or (_fmt(sonraki["end_minute"])
                                      if sonraki.get("end_minute") is not None
                                      else None),
        "guven": "tahmini" if eksik else "kesin",
        "eksik_kayit": eksik,
    }


def _gelecek_notlarini_temizle(cizelge: list[dict], now_minute: int) -> None:
    """Başlangıcı henüz gelmemiş bloklardan `note` alanını kaldır.

    Gece uykusu (`bedtime`) HARİÇ: onun notu "yaşına uygun saate getirildi"
    gibi bir PLAN açıklamasıdır, olmuş bir olayın anlatımı değil — ve annenin
    "yatış neden 20:00?" sorusunun tek cevabı odur."""
    for b in cizelge:
        if b.get("key") == "bedtime" or b.get("type") == "wake":
            continue
        if b.get("start_minute") is not None and b["start_minute"] > now_minute:
            b.pop("note", None)


def _gunduz_toplam(bloklar: list[dict]) -> int:
    """Çizelgedeki gündüz uykusu toplamı (şekerleme HARİÇ — henüz yok)."""
    return sum(max(0, int(b["end_minute"]) - int(b["start_minute"]))
               for b in bloklar
               if b.get("type") == "nap" and b.get("key") != SEKERLEME_KEY)


def _saat_dk(hhmm: str) -> int:
    """"17:00" → 1020. Bozuk değer gelirse 17:00'a düşer (sessiz 0 olmaz)."""
    try:
        s, d = str(hhmm).split(":")
        return int(s) * 60 + int(d)
    except Exception:
        return 17 * 60


def _yatisi_yerlestir(bloklar: list[dict], cursor: int, ww: int,
                      yatma_lo: int, yatma_hi: int, sabit_wake: int,
                      now_minute: int,
                      en_erken_yatis: int | None = None
                      ) -> tuple[list[dict], dict, list[str]]:
    """K4 — yatış = son uyku bitişi + pencere; bandın tavanını aşamaz.

    TAŞMA SIRASI v1.4'te TERSİNE DÖNDÜ (eski K10.4):
      ① ŞEKERLEME kısaltılır, yetmezse İPTAL edilir,
      ② planlanan son gündüz uykusu bandın MİNİMUMUNA kadar kısaltılır,
      ③ tavanın tamamen ötesine düşen planlı uyku kaldırılır,
      ④ artan taşma için yatış tavana kırpılır.
    Eskiden şekerleme dokunulmazdı ve gerçek uyku kısaltılırdı. Yanlıştı:
    şekerleme zaten AÇIĞI KAPATMAK için eklenen ilave bir bloktur, günü taşıran
    ilk vazgeçilecek şey odur. Gerçek KAYIT olan uykuya hiç dokunulmaz (geçmiş
    değiştirilemez) — kısaltma gerekiyorsa uyarı üretilir, blok korunur.
    Her müdahale `uyarilar`a yazılır; sessiz kırpma YOKTUR."""
    uyarilar: list[str] = []

    def _oynanabilir(b):
        return (b["type"] == "nap" and b["key"] != SEKERLEME_KEY
                and b.get("kaynak") != "kayit")

    # Doğal yatış = son gündüz uykusu + pencere. Şekerleme varsa yatış onun
    # bitişinden en az `gecis` dk sonra olmalı (zincire EKLENMEZ, alt sınır
    # koyar).
    ham = cursor + ww
    if en_erken_yatis is not None:
        ham = max(ham, en_erken_yatis)

    # ① ŞEKERLEME önce gider — ilave bloktur, günü o taşırdıysa vazgeçilir.
    if ham > yatma_hi:
        sek = next((b for b in bloklar if b["key"] == SEKERLEME_KEY), None)
        if sek is not None:
            sure = sek["end_minute"] - sek["start_minute"]
            fazla = ham - yatma_hi
            if fazla >= sure:
                bloklar.remove(sek)
                uyarilar.append(
                    f"Şekerleme eklenmedi; gece yatışını {saatli(yatma_hi, 'den')} "
                    f"sonraya itiyordu")
                kalan = [b for b in bloklar if b["type"] == "nap"]
                ham = (kalan[-1]["end_minute"] if kalan else cursor) + ww
            else:
                sek["end_minute"] -= fazla
                yeni = sek["end_minute"] - sek["start_minute"]
                sek["title"] = sekerleme_basligi(yeni)
                sek["note"] = "Gece yatışı gecikmesin diye kısaltıldı"
                uyarilar.append(
                    f"Şekerleme {sure} dakika yerine {yeni} dakika yapıldı; "
                    f"gece yatışı {saatli(yatma_hi, 'i')} geçmesin diye")
                ham = sek["end_minute"] + ww

    if ham > yatma_hi:
        oynanabilir = [b for b in bloklar if _oynanabilir(b)]
        # Kısaltılamayan GERÇEK kayıt varsa sessiz kalma: anne neden yatışın
        # tavana dayandığını görmeli.
        if not oynanabilir and any(b["type"] == "nap"
                                   and b.get("kaynak") == "kayit"
                                   for b in bloklar):
            uyarilar.append(
                "Gündüz uykusu sizin kaydınızdan geldiği için kısaltılmadı; "
                f"gece yatışı {saatli(yatma_hi, 'e')} alındı")
        son = oynanabilir[-1] if oynanabilir else None
        if son is not None:
            if son["start_minute"] >= yatma_hi:
                # ② Uyku tavanın ötesinde başlıyor: o gün hiç yapılamaz.
                bloklar.remove(son)
                uyarilar.append(
                    f"Son gündüz uykusu çıkarıldı; "
                    f"{saatli(son['start_minute'])} başlaması gece yatışından "
                    f"({_fmt(yatma_hi)}) sonraya denk geliyordu")
                kalan = [b for b in bloklar if b["type"] == "nap"]
                ham = (kalan[-1]["end_minute"] if kalan else cursor) + ww
            else:
                # ① Minimuma kadar kısalt (altına İNMEZ — 10 dk'lık uyku uyku değildir).
                sure = son["end_minute"] - son["start_minute"]
                yeni_sure = max(yas_bantlari.MIN_UYKU_DK, sure - (ham - yatma_hi))
                if yeni_sure < sure:
                    son["end_minute"] = son["start_minute"] + yeni_sure
                    son["note"] = "Gece yatışı çok gecikmesin diye kısaltıldı"
                    uyarilar.append(
                        f"Son gündüz uykusu {sure - yeni_sure} dakika "
                        f"kısaltıldı; gece yatışı {saatli(yatma_hi, 'i')} geçmesin "
                        f"diye")
                    ham = son["end_minute"] + ww

    yatis_dk = max(yatma_lo, min(yatma_hi, ham))
    if ham > yatma_hi:
        uyarilar.append("Bugün gece yatışı, yaşına uygun en geç saate "
                        "denk geldi")
    gece_suresi = (sabit_wake + 1440) - yatis_dk

    yatis = {
        "key": "bedtime", "type": "sleep",
        "start_minute": yatis_dk, "end_minute": sabit_wake + 1440,
        "title": "Gece uykusu",
        "kaynak": "varsayilan" if yatis_dk <= now_minute else "plan",
    }
    if yatis_dk != ham:
        yatis["note"] = (f"Yaşına uygun yatış saatine "
                         f"({_fmt(yatma_lo)}–{_fmt(yatma_hi)}) getirildi")
    yatis["gece_uykusu_dk"] = gece_suresi
    bloklar.append(yatis)
    return bloklar, yatis, uyarilar


# =============================================================================
# Log özeti
# =============================================================================
def _local_minute(dt: datetime, tz_offset_min: int) -> tuple[date, int]:
    """UTC datetime → (yerel tarih, gece yarısından itibaren yerel dakika)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone.utc) + timedelta(minutes=tz_offset_min)
    return local.date(), local.hour * 60 + local.minute


def summarize_logs(logs: Iterable[Any], today: date | None = None,
                   lookback_days: int = LOOKBACK_DAYS,
                   tz_offset_min: int = TZ_OFFSET_MIN) -> dict:
    """Son `lookback_days` günün İSTATİSTİKLERİ — çizelgeyi ARTIK BELİRLEMEZ.

    v2'de (K1/K2) gün planı yalnız BUGÜNÜN kayıtlarından `recompute_day` ile
    kurulur. Bu fonksiyon geriye kalan üç işi besler:
      • regresyon protokolü (self_soothe_fail_nights),
      • chat bağlamı / haftalık görünüm,
      • "yeterince uyuyor mu" karşılaştırmasının ÇOK GÜNLÜK formu.
    Sabah uyanışının ORTALAMASI ARTIK ÜRETİLMEZ — `avg_wake_minute` kaldırıldı;
    onun yerine BUGÜNÜN uyanışı `gunun_uyanisi` alanında döner (K5).

    Değer hesaplanamıyorsa ilgili alan None (çağıran karar verir)."""
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=lookback_days - 1)

    bed_by_day: dict[date, int] = {}
    wake_by_day: dict[date, int] = {}
    naps_by_day: dict[date, list[int]] = {}
    night_wakes_by_day: dict[date, int] = {}
    days_seen: set[date] = set()
    # K12.2 — "kendine dalamama" sinyali görülen AYRI geceler. v2.2'de ölçüt
    # SÜREDEN BAĞIMSIZ hâle geldi: uyanma kaydı olan her gece sayılır.
    # Eskiden ≥20 dk şartı vardı ve `ended_at` girilmemiş kayıt tümüyle
    # atlanıyordu; beta annelerinin verisinde gece uyanmaları bu yüzden HİÇ
    # sayılmadı — 3 kez uyanan bebek "0 uyanma" olarak raporlandı.
    fail_nights: set[date] = set()
    # ≥20 dk süren uyanmanın görüldüğü geceler — artık yalnız ALT METRİK.
    uzun_nights: set[date] = set()
    regression_start = today - timedelta(days=REGRESSION_LOOKBACK_NIGHTS)

    def _gece_uyanmasi_isle(k: dict, d: date, minute: int) -> None:
        """Bir gece uyanmasını hem gecelik sinyale hem günlük sayaca yaz."""
        sure = (GECE_UYANMA_VARSAYILAN_DK if k["sure_dk"] is None
                else k["sure_dk"])
        night_key = d - timedelta(days=1) if minute < 12 * 60 else d
        if regression_start <= night_key <= today:
            fail_nights.add(night_key)
            if sure >= UZUN_UYANMA_MIN_DK:
                uzun_nights.add(night_key)

    for lg in logs:
        k = _log_alanlari(lg, tz_offset_min)
        if k is None:
            continue
        d, minute, typ = k["bas_gun"], k["bas_dk"], k["type"]

        # --- Regresyon sinyali: gece uyanması (pencere: son 3 gece) -------------
        # Bu kontrol LOOKBACK_DAYS penceresinden BAĞIMSIZDIR (kendi penceresi var).
        # K12.2: süre şartı KALKTI; `ended_at` yoksa sayım için 10 dk varsayılır.
        if typ == "night_wake":
            _gece_uyanmasi_isle(k, d, minute)
        elif typ == "wake" and minute < GUN_BASLANGICI_EN_ERKEN:
            # K12.2 — 06:00 öncesi `wake` kaydı da bir gece uyanmasıdır. Mobil
            # bazı akışlarda gece uyanmasını `wake` tipiyle gönderiyor.
            _gece_uyanmasi_isle(k, d, minute)

        # K5: gece uykusunun bitişi sabah uyanışıdır — ama YALNIZ kayıt gerçekten
        # gece uykusuysa (70 dakikalık bir `sleep` gece uykusu olamaz). Bant
        # bilinmediği için burada takvim ölçütü kullanılır: gece yarısını aşmış
        # ya da en az 5 saat sürmüş kayıt.
        if typ == "sleep" and k["bit_dk"] is not None:
            gece_mi = (k["bit_gun"] > k["bas_gun"]) or (k["sure_dk"] or 0) >= 300
            if gece_mi and start <= k["bit_gun"] <= today:
                ed, emin = k["bit_gun"], k["bit_dk"]
                days_seen.add(ed)
                # Aynı gün birden çok gece uykusu varsa SON uyanış geçerlidir (K5).
                wake_by_day[ed] = max(wake_by_day.get(ed, emin), emin)

        if not (start <= d <= today):
            continue
        days_seen.add(d)

        if typ == "wake":
            wake_by_day.setdefault(d, minute)
        elif typ == "sleep":
            m = minute if minute >= BEDTIME_WINDOW[0] else minute + 24 * 60
            if BEDTIME_WINDOW[0] <= m <= BEDTIME_WINDOW[1]:
                bed_by_day[d] = max(bed_by_day.get(d, m), m)
            else:
                naps_by_day.setdefault(d, []).append(k["sure_dk"] or 0)
        elif typ == "nap":
            naps_by_day.setdefault(d, []).append(k["sure_dk"] or 0)
        elif typ == "night_wake":
            night_wakes_by_day[d] = night_wakes_by_day.get(d, 0) + 1
        if typ == "wake" and minute < GUN_BASLANGICI_EN_ERKEN:
            # K12.2 — günlük sayaca da girer (yukarıdaki `wake` dalı sabah
            # uyanışını yazar; bu satır onunla ÇELİŞMEZ, ek bilgidir).
            night_wakes_by_day[d] = night_wakes_by_day.get(d, 0) + 1

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 1) if vals else None

    nap_counts = [len(v) for v in naps_by_day.values()]
    nap_durs = [d for v in naps_by_day.values() for d in v if d > 0]
    # Gün başına gündüz uyku toplamı — yalnız SÜRESİ BİLİNEN uykusu olan günler
    # sayılır (ended_at'i olmayan kayıtlar günü 0 dk göstermesin).
    day_totals = [sum(v) for v in naps_by_day.values() if any(d > 0 for d in v)]

    _bed_avg = (int(round(sum(bed_by_day.values()) / len(bed_by_day)))
                if bed_by_day else None)
    _wake_avg = (int(round(sum(wake_by_day.values()) / len(wake_by_day)))
                 if wake_by_day else None)
    # Gece uykusu = yatıştan ERTESİ sabah uyanışına. _bed_avg gece yarısını
    # aşan yatışlarda zaten +24s kaydırılmış olduğundan burada tekrar eklenmez.
    _night_sleep = (float(_wake_avg + 24 * 60 - _bed_avg)
                    if (_wake_avg is not None and _bed_avg is not None) else None)

    return {
        "days_with_data": len(days_seen),
        # BUGÜNÜN uyanışı (ortalama DEĞİL) — yalnız raporlama için.
        "gunun_uyanisi": wake_by_day.get(today),
        "avg_bedtime_minute": _bed_avg,
        "avg_nap_count": _avg(nap_counts),
        "avg_nap_minutes": _avg(nap_durs),
        "avg_day_sleep_minutes": _avg(day_totals),
        "avg_night_sleep_minutes": _night_sleep,
        # Gece uyanma: kaydı olan günler üzerinden ortalama (0 kayıtlı gün 0 sayılır
        # ancak hiç veri yoksa None).
        "avg_night_wakes": (round(sum(night_wakes_by_day.get(d, 0) for d in days_seen)
                                  / len(days_seen), 1) if days_seen else None),
        # K12.2 — Regresyon protokolü: gece uyanması GÖRÜLEN ayrı gece sayısı
        # (süreye BAKMAZ). Ad geriye dönük uyumluluk için korundu.
        "self_soothe_fail_nights": len(fail_nights),
        # Alt metrik: ≥20 dk süren ("kendine dalamadı") uyanmanın görüldüğü
        # ayrı gece sayısı. Ana sayımı ARTIK kapılamaz, yalnız raporlar.
        "uzun_uyanma_geceleri": len(uzun_nights),
    }


# --- Regresyon aşamaları (v1.4) ---------------------------------------------
# İlayda (S9): "Öncelikle şunu yapıyoruz: kendi uykuya döndü mü çocuk? Bunu
# soruyoruz önce. Kendi dönmüyorsa ve 45 günü geride bıraktıysak... 45 gün
# dolana kadar eğitime YİNE DE DEVAM ediyoruz. 45 günü geride bıraktıysak
# pediatri, fizyoterapi ya da ergoterapi kontrolü rica ediyoruz."
#
# "Programı baştan başlatalım mı?" kartı KALDIRILDI — üç kademeli akış geldi.
REGRESYON_45_GUN = 45

REGRESYON_METINLERI = {
    "kendi_donuyor_mu": (
        "Bebeğiniz gece uyandığında 20 dakika beklerken kendi başına uykuya "
        "dönebiliyor mu?"),
    "devam_45": (
        "Eğitime 45. güne kadar aynı şekilde devam edin, bu dönemde gece "
        "uyanmaları normaldir."),
    "tibbi_yonlendirme": (
        "45 gün doldu ve bebeğiniz hâlâ kendi başına uykuya dönemiyor. "
        "Pediatri kontrolü öneriyoruz; doktorunuza demir, D vitamini ve "
        "magnezyum düzeyleri, uyku apnesi ve geniz eti açısından değerlendirme "
        "isteyebilirsiniz. Gerekirse fizyoterapi/ergoterapi değerlendirmesi de "
        "düşünülebilir."),
}


def egitim_gunu(training_started_at: date | None,
                today: date | None = None) -> int | None:
    """Eğitimin kaçıncı günündeyiz (1'den başlar). Başlangıç yoksa None."""
    if training_started_at is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    return (today - training_started_at).days + 1


# adaptation.regresyon.asama — kart tipinin KISA adı (mobil switch'i için).
REGRESYON_ASAMA = {"kendi_donuyor_mu": "soru", "devam_45": "devam",
                   "tibbi_yonlendirme": "tibbi"}


def regresyon_karti(training_started_at: date | None,
                    kendi_donuyor: bool | None,
                    today: date | None = None) -> dict:
    """v1.4 — regresyon akışının HANGİ kademesindeyiz.

    Kademeler:
      1. `kendi_donuyor_mu` — anneye sorulur (henüz cevap yok).
      2. `devam_45`         — kendi dönmüyor AMA 45 gün dolmadı → eğitime devam.
      3. `tibbi_yonlendirme`— kendi dönmüyor VE 45 gün doldu → tıbbi kontrol.
    Anne "evet, kendi dönüyor" derse kart gösterilmez (çağıran taraf karar
    verir; burada tip None döner).

    Metinler teşhis KOYMAZ — yalnız değerlendirme önerir (danışmanlık sınırı,
    bkz. engine/chatbot.py tıbbi sınır kuralları)."""
    gun = egitim_gunu(training_started_at, today)
    doldu = bool(gun is not None and gun >= REGRESYON_45_GUN)
    if kendi_donuyor is True:
        return {"tip": None, "metin": None, "egitim_gunu": gun,
                "kirkbes_gun_doldu": doldu}
    if kendi_donuyor is None:
        tip = "kendi_donuyor_mu"
    else:
        tip = "tibbi_yonlendirme" if doldu else "devam_45"
    return {"tip": tip, "metin": REGRESYON_METINLERI[tip],
            "egitim_gunu": gun, "kirkbes_gun_doldu": doldu}


def detect_regression(training_completed_at: date | None, log_summary: dict,
                      today: date | None = None) -> tuple[bool, list[str]]:
    """İlayda regresyon protokolü — İKİ koşul birden sağlanmalı.

      1. training_completed_at dolu VE üzerinden ≥13 gün geçmiş
      2. Son 3 gecede, gece uyanması ≥2 AYRI GECEDE görülmüş (K12.2: süre şartı
         YOK — ≥20 dk yalnız `uzun_uyanma_geceleri` alt metriğinde kalır)

    Dönen: (regression_detected, sebepler). Hiçbir şey otomatik üretilmez —
    karar kullanıcınındır (mobil "Programı baştan başlatalım mı?" kartı)."""
    today = today or datetime.now(timezone.utc).date()
    reasons: list[str] = []

    if training_completed_at is None:
        return False, reasons                      # eğitim tamamlanmamış → regresyon yok

    days_since = (today - training_completed_at).days
    cond_1 = days_since >= REGRESSION_MIN_DAYS_AFTER_TRAINING
    fail_nights = int(log_summary.get("self_soothe_fail_nights") or 0)
    cond_2 = fail_nights >= REGRESSION_MIN_NIGHTS

    if cond_1 and cond_2:
        uzun = int(log_summary.get("uzun_uyanma_geceleri") or 0)
        ek = (f" ({uzun} gecede {UZUN_UYANMA_MIN_DK} dk+ sürdü)" if uzun else "")
        reasons.append(
            f"Eğitim {training_completed_at.isoformat()} tarihinde tamamlandı "
            f"({days_since} gün önce) ve son {REGRESSION_LOOKBACK_NIGHTS} gecenin "
            f"{fail_nights}'inde gece uyanması var{ek} — "
            f"kendine dalama becerisinde gerileme (regresyon) sinyali")
    return (cond_1 and cond_2), reasons


# =============================================================================
# Adaptasyon kuralları
# =============================================================================
def _violates_age_band(schedule: list[dict], bucket_params: dict,
                       yas_ay: float | None = None,
                       tek_uyku: bool | None = None) -> str | None:
    """Kaydırılmış çizelge yaş bandına aykırı mı? Aykırıysa sebep metni döner.

    Tablo yolunda (yas_ay verilmişse) ÜÇ kontrol — hepsi yas_bantlari.json'dan:
      1. Gündüz uyku sayısı bandın öngördüğünden farklı mı? (bebek bant atladıysa
         eski çizelge artık geçersizdir → yeniden üretim)
      2. Son uykudan yatışa kadarki uyanıklık, bandın penceresi dışında mı?
      3. Gece uykusu süresi (yatıştan ertesi sabah uyanışına) bandın gece uykusu
         aralığı dışında mı?
    v2 NOTU: bu kontrol artık GÜNLÜK çizelgeye değil, DEĞİŞMEZ ŞABLONA
    uygulanır (bkz. adapt). Bugün bir uykunun atlanmış olması bandı ihlal etmiş
    sayılmaz ve pahalı bir yeniden üretimi tetiklemez; tek tetikleyici bebeğin
    BANT ATLAMASIDIR (ör. 8 aylık 3 uykuluk şablon, 9. ayda 2 uyku bandına düşer).
    Şablonun sabah hedefi sabit olduğu için (K1) çizelge gün gün birikip saat
    etrafında dolanamaz — v1'deki kayma birikmesi yapısal olarak imkânsızdır.

    Tablo yoksa (Faz Y öncesi çağrı) eski KB kontrolü uygulanır."""
    bed = next((b for b in schedule if b["key"] == "bedtime"), None)
    if bed is None:
        return None
    naps = [b for b in schedule if b["type"] == "nap"]
    bant = bant_coz(bucket_params, yas_ay, tek_uyku)

    if bant is not None:
        cp = yas_bantlari.cizelge_parametreleri(bant)
        ww_lo, ww_hi = bant["uyaniklik_penceresi_dk"]
        if len(naps) != cp["uyku_sayisi"]:
            return (f"Çizelgede {len(naps)} gündüz uykusu var; {bant['ad']} bandı "
                    f"{cp['uyku_sayisi']} uyku öngörüyor")
        if naps:
            gap = bed["start_minute"] - naps[-1]["end_minute"]
            if not (ww_lo <= gap <= ww_hi):
                return (f"Son uyku ile yatış arası {gap} dk; {bant['ad']} bandının "
                        f"uyanıklık penceresi {ww_lo}–{ww_hi} dk dışında")
        wake_b = next((b for b in schedule if b["key"] == "wake"), None)
        if wake_b is not None:
            gece_lo, gece_hi = bant["gece_uykusu_dk"]
            gece = (wake_b["start_minute"] + 1440) - bed["start_minute"]
            if not (gece_lo <= gece <= gece_hi):
                return (f"Yatış {_fmt(bed['start_minute'])} ile sabah uyanışı "
                        f"{_fmt(wake_b['start_minute'])} arası {gece} dk; "
                        f"{bant['ad']} bandının gece uykusu {gece_lo}–{gece_hi} dk "
                        "dışında")
        return None

    # --- Geriye uyumluluk: KB serbest metinleri -----------------------------
    p = bucket_params or {}
    bed_range = parse_time_range(p.get("yatma_vakti")) or DEFAULT_BEDTIME_RANGE
    ww = parse_duration_range(p.get("uyaniklik_penceresi")) or DEFAULT_WAKE_WINDOW
    if not (bed_range[0] <= bed["start_minute"] <= bed_range[1]):
        return (f"Kaydırılmış yatış saati {_fmt(bed['start_minute'])}, yaş bandının "
                f"yatma aralığı ({_fmt(bed_range[0])}–{_fmt(bed_range[1])}) dışında")
    last_end = naps[-1]["end_minute"] if naps else None
    if last_end is not None:
        gap = bed["start_minute"] - last_end
        if not (ww[0] <= gap <= ww[1]):
            return (f"Son uyku ile yatış arası {gap} dk; yaş bandının uyanıklık "
                    f"penceresi {ww[0]}–{ww[1]} dk dışında")
    return None


def plan_sablonu(plan_content: dict, bucket_params: dict,
                 yas_ay: float | None = None, tek_uyku: bool | None = None
                 ) -> tuple[list[dict], list[str]]:
    """K1/K2 — planın DEĞİŞMEZ çizelge şablonu.

    Öncelik: content.schedule_template (v2) > content.schedule (v1 planları, ilk
    okumada şablona yükseltilir) > yaş bandından taze türetme.
    Şablon ASLA günlük kayıtlardan güncellenmez; yarının tabanı budur."""
    reasons: list[str] = []
    icerik = plan_content if isinstance(plan_content, dict) else {}
    sablon = normalize_schedule(icerik.get("schedule_template"))
    if not sablon:
        sablon = normalize_schedule(icerik.get("schedule"))
        if sablon:
            reasons.append("v1 planı: mevcut çizelge değişmez şablona yükseltildi")
    if not sablon:
        sablon = build_schedule(bucket_params, DEFAULT_WAKE_MIN,
                                yas_ay=yas_ay, tek_uyku=tek_uyku)
        reasons.append("Planda yapısal çizelge yoktu; yaş bandından türetildi")
    return sablon, reasons


def adapt(plan_content: dict, bucket_params: dict, logs: Iterable[Any], *,
          training_started_at: date | None = None,
          regresyon_kendi_donuyor: bool | None = None,
          today: date | None = None,
          now_minute: int | None = None,
          training_completed_at: date | None = None,
          yas_ay: float | None = None,
          tek_uyku: bool | None = None,
          log_summary: dict | None = None) -> dict:
    """GÜN İÇİ KAYMA MOTORU v2 — kural tabanlı, LLM YOK (K1-K9).

    v1'den farkı: çizelge artık 3 günlük ortalamaya göre ±45 dk KAYDIRILMAZ.
    Sabah hedefi sabittir (K1) ve bugünün çizelgesi bugünün kayıtlarından
    zincirleme kurulur (K2/K3). Kaydırma kavramı ve shift_minutes KALDIRILDI.

    Dönen: {
      schedule: [...],                   # BUGÜNÜN çizelgesi (şablon değişmez)
      schedule_template: [...],          # değişmez şablon (K1) — çağıran saklar
      adaptation: {...},                 # K4 şeması (hesaplandi_at, varsayilan_bloklar…)
      regenerate_required: bool,         # ŞABLON yaş bandına aykırı → tam yeniden üretim
      regression_detected: bool,         # İlayda protokolü (eğitim sonrası geri gidiş)
      regresyon_karti: {...} | None,     # v1.4 üç kademe (soru / devam_45 / tıbbi)
      egitim_baslangic_gunu: int | None, # eğitimin kaçıncı günü
      kirkbes_gun_doldu: bool,           # 45 günlük "devam et" penceresi doldu mu
      kestirme: {...} | None,            # K9 — evrensel 30dk kestirme kuralı
      toplam_uyku: {...} | None,         # K9 — "24 saatte yeterince uyuyor mu?"
      reasons: [str],
    }
    """
    today = today or datetime.now(timezone.utc).date()
    if now_minute is None:
        now_minute = _simdi_dakika(today)
    logs = list(logs or [])
    bant = bant_coz(bucket_params, yas_ay, tek_uyku)
    ozet = log_summary if log_summary is not None else summarize_logs(logs, today=today)

    sablon, reasons = plan_sablonu(plan_content, bucket_params, yas_ay, tek_uyku)

    result = {
        "schedule": sablon,
        "schedule_template": sablon,
        "adaptation": None,
        "regenerate_required": False,
        "regression_detected": False,
        # v1.4 — "Programı baştan başlatalım mı?" KALDIRILDI. Yerine üç
        # kademeli akış: önce anneye "kendi dönüyor mu?" sorulur; dönmüyorsa
        # 45 gün dolana kadar EĞİTİME DEVAM, dolduysa tıbbi yönlendirme.
        "regresyon_karti": None,
        "egitim_baslangic_gunu": egitim_gunu(training_started_at, today),
        "kirkbes_gun_doldu": bool(
            (egitim_gunu(training_started_at, today) or 0) >= REGRESYON_45_GUN),
        "kestirme": None,
        "toplam_uyku": None,
        "reasons": reasons,
    }

    # --- Regresyon katmanı: gün planından BAĞIMSIZ, yalnız BAYRAK -----------
    # Otomatik hiçbir şey üretilmez. v1.4: mobil `regresyon_karti`'nı gösterir,
    # annenin cevabı POST /plans/regresyon-cevap ile geri döner.
    regression, reg_reasons = detect_regression(training_completed_at, ozet, today)
    _kart = None
    if regression:
        result["regression_detected"] = True
        kart = regresyon_karti(training_started_at, regresyon_kendi_donuyor, today)
        _kart = kart if kart["tip"] else None
        result["regresyon_karti"] = _kart
        reasons.extend(reg_reasons)

    # --- Yaş bandı ihlali → TAM YENİDEN ÜRETİM (K8: mevcut haliyle korundu) --
    # DEĞİŞTİ: kontrol artık GÜNLÜK çizelgeye değil ŞABLONA uygulanır. Bugün bir
    # uykunun atlanmış olması bandı ihlal etmez ve pahalı bir yeniden üretimi
    # tetiklememelidir; asıl tetikleyici bebeğin BANT ATLAMASIDIR.
    violation = _violates_age_band(sablon, bucket_params, yas_ay, tek_uyku)
    if violation:
        result["regenerate_required"] = True
        reasons.append(f"{violation} — plan yeniden üretilecek")
        return result

    # --- K2/K3/K4/K6/K7: bugünün çizelgesi ----------------------------------
    gun = recompute_day(sablon, bant, sabit_wake_minute(sablon), logs,
                        now_minute, gun=today, bucket_params=bucket_params)
    result["schedule"] = gun["schedule"]
    result["adaptation"] = gun["adaptation"]
    # Aşama gün hesabının izine de düşer: recompute_day regresyonu bilmez, bu
    # yüzden alanı None kurar ve burada doldurulur.
    if _kart:
        result["adaptation"]["regresyon"] = {
            "asama": REGRESYON_ASAMA[_kart["tip"]], "tip": _kart["tip"],
            "egitim_gunu": _kart["egitim_gunu"],
            "kirkbes_gun_doldu": _kart["kirkbes_gun_doldu"]}
    reasons.extend(gun["adaptation"]["uyarilar"])

    # HENÜZ GELMEMİŞ BLOKLARDA NOT GÖSTERİLMEZ (v2.4.2). Notlar olmuş bir
    # şeyi anlatır ("sayaç kapatılmamış", "kısaltıldı", "sürüyor"); gelecekteki
    # bir uykuya iliştirildiğinde anne olmamış bir olayı okumuş oluyordu.
    # Açıklamalar `adaptation.uyarilar` içinde günün tamamı için duruyor.
    _gelecek_notlarini_temizle(gun["schedule"], now_minute)

    # --- K9: bugünün TOPLAM uykusu bandın ihtiyacını karşılıyor mu? ----------
    # Ölçüt artık 3 günlük ortalama değil, YENİDEN HESAPLANAN GÜNÜN kendisidir.
    if bant is not None:
        gunduz_dk, gece_dk = gun_uyku_toplamlari(gun["schedule"], now_minute)
        kestirme = yas_bantlari.kestirme_degerlendir(bant, gunduz_dk)
        result["kestirme"] = kestirme
        if kestirme["gerekli"]:
            reasons.append(
                f"Gündüz toplam uyku {kestirme['gerceklesen_dk']} dk; "
                f"{bant['ad']} bandının minimumu {kestirme['min_gunduz_dk']} dk "
                f"({kestirme['eksik_dk']} dk eksik) — {kestirme['sure_dk']} dk'lık "
                "ilave kestirme uykusu yaptırılmalı")

        toplam = yas_bantlari.toplam_uyku_degerlendir(bant, gunduz_dk, gece_dk)
        result["toplam_uyku"] = toplam
        if toplam["durum"] == "az":
            hedef_lo, hedef_hi = toplam["hedef_dk"]
            reasons.append(
                f"24 saatlik toplam uyku {toplam['gerceklesen_dk']} dk; "
                f"{bant['ad']} bandının ihtiyacı "
                f"{hedef_lo}{'' if hedef_lo == hedef_hi else '–' + str(hedef_hi)} dk "
                f"({toplam['eksik_dk']} dk eksik)")
    return result


def gun_uyku_toplamlari(schedule: list[dict],
                        now_minute: int | None = None) -> tuple[int, int]:
    """K9 — hesaplanmış günün (gündüz toplam, gece uykusu) dakikaları.

    X6 — HÂLÂ SÜREN bir uyku (`devam=True`) toplamda ŞU ANA kadarki süresiyle
    sayılır, planlanan bitişiyle değil: 10 dakikadır uyuyan bebek "70 dk uyudu"
    sayılıp "yeterince uyudu" denemez. `now_minute` verilmezse planlanan bitiş
    kullanılır (geriye dönük davranış).

    Hiçbir yolda None/NaN dönmez — açık kayıt en kötü ihtimalle 0 dk katkı verir."""
    gunduz = 0
    for b in schedule:
        if b.get("type") != "nap":
            continue
        try:
            bas, bit = int(b["start_minute"]), int(b["end_minute"])
        except (TypeError, ValueError, KeyError):
            continue                       # bozuk blok toplamı çökertmemeli
        if b.get("devam") and now_minute is not None:
            bit = min(bit, max(bas, int(now_minute)))
        gunduz += max(0, bit - bas)
    bed = next((b for b in schedule if b.get("key") == "bedtime"), None)
    gece = int(bed.get("gece_uykusu_dk") or
               (int(bed["end_minute"]) - int(bed["start_minute"]))) if bed else 0
    return gunduz, gece


def _simdi_dakika(gun: date, tz_offset_min: int = TZ_OFFSET_MIN) -> int:
    """`gun` için "şu an" yerel dakikası — K6'nın 'zamanı geçti mi' ölçütü.

    Geçmiş gün → 1439 (gün bitti), gelecek gün → 0 (hiçbir blok geçmedi)."""
    simdi = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_min)
    bugun = simdi.date()
    if gun < bugun:
        return 24 * 60 - 1
    if gun > bugun:
        return 0
    return simdi.hour * 60 + simdi.minute
