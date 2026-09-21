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
Regresyon hiçbir şeyi otomatik üretmez; mobil kullanıcıya "Programı baştan
başlatmak ister misiniz?" kartını gösterir, onaylanırsa mobil POST /plans/generate
çağırır ve training_started_at'i bugüne PATCH'ler.

ZAMAN DİLİMİ: sleep_logs UTC saklanır, plan çizelgesi ise yerel duvar saatidir.
Kullanıcı bazlı timezone alanı henüz YOK; TZ_OFFSET_MIN varsayılanı Türkiye
(UTC+3, DST yok). Çok ülkeli sürümde users tablosuna timezone eklenmelidir.
"""
from __future__ import annotations

import logging
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
SEKERLEME_DK = 30
SEKERLEME_KEY = "sekerleme"
SEKERLEME_BASLIK = "Şekerleme (30 dk)"

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
            "note": f"Uyanıklık penceresi ~{ww} dk sonra",
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
        gece["note"] = (f"Yaş bandının yatış aralığına "
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


def _gece_uykusu_mu(k: dict, hedef_minute: int, gun: date | None = None) -> bool:
    """K5 — bu `sleep` kaydı gerçekten GECE uykusu mu?

    Ölçüt BAŞLANGIÇ saatidir, süre değil: gece uykusu sabah hedefinden ÖNCE
    başlar. İki biçimi vardır ve ikisi de takvimden türer, uydurma duvar
    saatinden değil:
      1. Bir ÖNCEKİ yerel günde başlamış (gece uykusu gece yarısını aşar), ya da
      2. Aynı gün ama sabah hedefinden ÖNCE başlamış — gece uyanmasından sonra
         tekrar dalmış bebeğin gece PARÇASI (ör. 05:00-07:10).
    09:30'da başlayan 70 dakikalık bir `sleep` kaydı ikisini de sağlamaz →
    gündüz uykusu olarak işlenir (süre ölçütü bu parçayı yanlış eliyordu).

    K12.4 — ÜÇÜNCÜ biçim: HENÜZ BİTMEMİŞ (ended_at null) ve BUGÜNDEN ÖNCE
    başlamış kayıt. Eskiden bu düşüyordu: bitişi olmadığı için (1) sağlanmıyor,
    başlangıcı akşam olduğu için (2) de sağlanmıyordu; sonra `bas_gun != gun`
    filtresine takılıp TAMAMEN görünmez oluyordu. Sonucu: anne akşam sayacı
    başlatıp sabah durdurmayınca gece uykusu hiç olmamış sayılıyor ve sabah
    uyanışı varsayılan hedefte kalıyordu (beta verisinde ölçülen 4. hata)."""
    if k["type"] != "sleep":
        return False
    if k["bit_gun"] is not None and k["bit_gun"] > k["bas_gun"]:
        return True
    if k["bit_dk"] is None and gun is not None and k["bas_gun"] < gun:
        return True
    return k["bas_dk"] < hedef_minute


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
                 f"cakisma: {_fmt(kazanan['bas_dk'])} kaydıyla ayni uyku "
                 f"({'acik sayac kaydi' if kaybeden.get('bit_dk_lin') is None else 'kisa olan'} "
                 f"yok sayildi)")
    return sorted(tutulan, key=lambda x: x["bas_dk"])


def gun_kayitlari(logs: Iterable[Any], gun: date, hedef_minute: int,
                  tz_offset_min: int = TZ_OFFSET_MIN) -> dict:
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
    """
    out = {"gece_uykulari": [], "gunduz_uykulari": [], "atlananlar": [],
           "wake_kayitlari": [], "gece_uyanmalari": [], "yok_sayilan": []}
    for lg in logs or []:
        k = _log_alanlari(lg, tz_offset_min)
        if k is None:
            continue
        tip = k["type"]

        # --- K12.1 — UYANMA KAYITLARI: asla uyku değildir --------------------
        if tip in ("wake", "night_wake"):
            if k["bas_gun"] != gun:
                continue
            # `night_wake` tipi zaten gece uyanmasıdır; `wake` ise YALNIZ 06:00
            # öncesindeyse gece uyanması sayılır (K12.2). 06:00 öncesi `wake`
            # kaydı AYRICA wake_kayitlari'nda kalır: K10.1'in "erken uyandı,
            # tekrar uyumadı → gün 06:00'dan" yolu bu adaya ihtiyaç duyuyor.
            if tip == "night_wake" or k["bas_dk"] < GUN_BASLANGICI_EN_ERKEN:
                out["gece_uyanmalari"].append(k)
            if tip == "wake":
                out["wake_kayitlari"].append(k)
            continue

        if tip == "feed":
            continue            # beslenme çizelgeyi etkilemez (v1'de de etkilemiyordu)

        if tip not in ("nap", "sleep", ATLANDI_TIPI):
            if k["bas_gun"] == gun:
                _yok_say(out, k, "taninmayan_tip",
                         f"tanınmayan kayıt tipi: {tip!r}")
            continue

        # Gece uykusu BUGÜNE, bittiği güne göre bağlanır (dün 20:30 → bugün 07:00).
        if tip == "sleep" and _gece_uykusu_mu(k, hedef_minute, gun):
            if k["bit_gun"] == gun:
                out["gece_uykulari"].append(k)
            elif k["bit_dk"] is None and k["bas_gun"] < gun:
                # K12.4/K14.1 — dün başlamış, HÂLÂ AÇIK gece uykusu. Bugünün
                # kaydıdır: sabah uyanışı bunun kapanışından türer.
                out["gece_uykulari"].append(dict(k, _devam=True))
            continue            # bugün akşam başlayan açık gece uykusu: yarının

        if k["bas_gun"] != gun:
            continue            # başka güne ait kayıt bugünün planına girmez (K2)

        if _atlandi_mi(k):
            out["atlananlar"].append(k)
            continue

        # --- K12.1 — yuvaya eşlenebilirlik ----------------------------------
        if k["bit_dk"] is None:
            out["gunduz_uykulari"].append(dict(k, _devam=True))   # süren uyku
        elif k["ters_mi"]:
            _yok_say(out, k, "ters_kayit",
                     "bitiş saati başlangıçtan önce — kayıt yuvaya eşlenmedi")
        elif k["sure_dk"] <= 0:
            _yok_say(out, k, "sifir_sure",
                     "sıfır süreli uyku kaydı (başlangıç = bitiş) — "
                     "yuvaya eşlenmedi")
        elif k["sure_dk"] < UYKU_MIN_SURE_DK:
            _yok_say(out, k, "kisa_sure",
                     f"{k['sure_dk']} dk — {UYKU_MIN_SURE_DK} dk altındaki "
                     "uyku kaydı yuvaya eşlenmez")
        else:
            out["gunduz_uykulari"].append(k)

    # --- K13 — çakışanları tekilleştir (yuvalara girmeden ÖNCE) -------------
    out["gunduz_uykulari"] = _cakismalari_coz(out["gunduz_uykulari"], out)
    out["gece_uykulari"] = _cakismalari_coz(out["gece_uykulari"], out)

    for anahtar in ("gece_uykulari", "gunduz_uykulari", "atlananlar",
                    "wake_kayitlari", "gece_uyanmalari"):
        out[anahtar].sort(key=lambda x: x["bas_dk"])
    return out


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
    uyarilar.append("Erken uyanma: gün 06:00'dan başlatıldı, "
                    "30 dk şekerleme eklendi.")
    return dict(
        bos,
        minute=GUN_BASLANGICI_EN_ERKEN,
        gercek_minute=gercek,
        kaynak="erken_uyanma",
        zincir_baslangici=GUN_BASLANGICI_EN_ERKEN,
        gece_bolunmeleri=_bolunmeler(),
        gece_uykusu=_gece_uykusu_ozeti(kayitlar, kapanis),
        # K10.5 — wake bloğu gün başlangıcını gösterir, gerçek saat nota yazılır.
        wake_note=(f"Bebeğiniz {_fmt(gercek)}'da uyandı, "
                   f"gün {_fmt(GUN_BASLANGICI_EN_ERKEN)} kabul edildi"),
        # K10.6 — mobilin okuduğu yapılandırılmış iz.
        erken_uyanma={"gercek_saat": _fmt(gercek),
                      "gun_baslangici": _fmt(GUN_BASLANGICI_EN_ERKEN),
                      "sekerleme_eklendi": True},
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

    kayitlar = gun_kayitlari(todays_logs, gun, sabit_wake, tz_offset_min)
    sabah = sabah_uyanisi(kayitlar, sabit_wake)

    uyarilar: list[str] = list(sabah["uyarilar"])

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
    # K10.3 — Erken uyanmada güne EK 30 dk şekerleme yuvası eklenir. Başlangıcı
    # zincire değil, bandın MİNİMUM uyanıklık penceresine bağlıdır:
    # 06:00 + pencere_alt_sinir (8 ay: 120 dk → 08:00). Bant çözülemiyorsa
    # şablonun penceresinin altına inilmez (uydurma sayı yok).
    if sabah["kaynak"] == "erken_uyanma":
        ww_min = (int(bant["uyaniklik_penceresi_dk"][0]) if bant is not None
                  else min(ww, _mid(DEFAULT_WAKE_WINDOW)))
        yuvalar = [{"key": SEKERLEME_KEY, "title": SEKERLEME_BASLIK,
                    "sure_dk": SEKERLEME_DK,
                    "sabit_bas": sabah["zincir_baslangici"] + ww_min,
                    "ek_blok": True}] + yuvalar
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
    for w in kayitlar["wake_kayitlari"]:
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
                f"{yuva['title']} minimum uyanıklık penceresinden erken "
                f"düşüyordu ({_fmt(aday)}); {_fmt(en_erken_uyku)}'a kaydırıldı "
                f"(sabah uyanışı + {ww_min_k15} dk).")
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
                    f"{yuva['title']} {_fmt(bas)}'da kaydedildi — minimum "
                    f"uyanıklık penceresinden erken (en erken "
                    f"{_fmt(en_erken_uyku)}). Kayıt korundu, sonraki bloklar "
                    f"bu kayda göre kuruldu.")
            blok = {"key": yuva["key"], "type": "nap",
                    "start_minute": bas, "end_minute": bit,
                    "title": yuva["title"], "kaynak": "kayit"}
            if olay.get("_devam"):
                blok["note"] = "Uyku sürüyor — bitiş planlanan süreyle tahmin edildi"
            bloklar.append(blok)
            cursor = max(cursor, bit)
            kanit = max(kanit, bit)
            continue

        bas = aday                                       # K3 — pencere zinciri
        bit = bas + yuva["sure_dk"]
        blok = {"key": yuva["key"], "type": "nap",
                "start_minute": bas, "end_minute": bit,
                "title": yuva["title"],
                "note": f"Uyanıklık penceresi ~{ww} dk sonra"}
        if bit <= now_minute:                            # K6 — zamanı geçti, kayıt yok
            blok["kaynak"] = "varsayilan"
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
        bloklar.append({"key": f"nap_{j}", "type": "nap",
                        "start_minute": fazla["bas_dk"], "end_minute": bit,
                        "title": f"{j}. gündüz uykusu", "kaynak": "kayit",
                        "note": "Şablonda olmayan ilave uyku kaydı"})
        cursor = max(cursor, bit)

    # --- K4: yatış zinciri + gece uykusu tavanı ------------------------------
    bloklar, yatis, k4_uyari = _yatisi_yerlestir(
        bloklar, cursor, ww, yatma_lo, yatma_hi, sabit_wake, now_minute)
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
        # K10.6 — erken uyanma izi; erken uyanma yoksa None.
        "erken_uyanma": sabah.get("erken_uyanma"),
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
        "uyarilar": uyarilar,
    }
    return {"schedule": schedule, "adaptation": adaptation}


def _yatisi_yerlestir(bloklar: list[dict], cursor: int, ww: int,
                      yatma_lo: int, yatma_hi: int, sabit_wake: int,
                      now_minute: int) -> tuple[list[dict], dict, list[str]]:
    """K4 — yatış = son uyku bitişi + pencere; bandın tavanını aşamaz.

    Tavan aşılıyorsa sıra (K10.4): ① son gündüz uykusu bandın izin verdiği
    MİNİMUMA kadar kısaltılır, ② tavanın tamamen ötesine düşen uyku kaldırılır,
    ③ artan taşma için yatış tavana kırpılır. Gerçek KAYIT olan uykuya
    dokunulmaz (geçmiş değiştirilemez) ve ŞEKERLEME ASLA kaldırılmaz/kısaltılmaz
    — o, erken uyanmanın telafisidir (K10.3/K10.4).
    Her müdahale `uyarilar`a yazılır; sessiz kırpma YOKTUR."""
    uyarilar: list[str] = []
    # Şekerleme (K10.3) ve gerçek kayıtlar müdahale dışıdır.
    def _oynanabilir(b):
        return (b["type"] == "nap" and b["key"] != SEKERLEME_KEY
                and b.get("kaynak") != "kayit")

    ham = cursor + ww
    if ham > yatma_hi:
        oynanabilir = [b for b in bloklar if _oynanabilir(b)]
        son = oynanabilir[-1] if oynanabilir else None
        if son is not None:
            if son["start_minute"] >= yatma_hi:
                # ② Uyku tavanın ötesinde başlıyor: o gün hiç yapılamaz.
                bloklar.remove(son)
                uyarilar.append(
                    f"Son gündüz uykusu kaldırıldı — başlangıcı ({_fmt(son['start_minute'])}) "
                    f"bandın yatış tavanının ({_fmt(yatma_hi)}) ötesindeydi")
                kalan = [b for b in bloklar if b["type"] == "nap"]
                ham = (kalan[-1]["end_minute"] if kalan else cursor) + ww
            else:
                # ① Minimuma kadar kısalt (altına İNMEZ — 10 dk'lık uyku uyku değildir).
                sure = son["end_minute"] - son["start_minute"]
                yeni_sure = max(yas_bantlari.MIN_UYKU_DK, sure - (ham - yatma_hi))
                if yeni_sure < sure:
                    son["end_minute"] = son["start_minute"] + yeni_sure
                    son["note"] = "Yatış saati bandın sınırına sığsın diye kısaltıldı"
                    uyarilar.append(
                        f"Son gündüz uykusu {sure - yeni_sure} dk kısaltıldı — yatış "
                        f"bandın tavanını ({_fmt(yatma_hi)}) aşmasın diye")
                    ham = son["end_minute"] + ww

    yatis_dk = max(yatma_lo, min(yatma_hi, ham))
    if ham > yatma_hi:
        uyarilar.append("Bugün yatış saati bandın sınırına dayandı")
    gece_suresi = (sabit_wake + 1440) - yatis_dk

    yatis = {
        "key": "bedtime", "type": "sleep",
        "start_minute": yatis_dk, "end_minute": sabit_wake + 1440,
        "title": "Gece uykusu",
        "kaynak": "varsayilan" if yatis_dk <= now_minute else "plan",
    }
    if yatis_dk != ham:
        yatis["note"] = (f"Yaş bandının yatış aralığına "
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
      restart_program_suggested: bool,   # kullanıcıya sorulacak ÖNERİ — otomatik üretim YOK
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
        "restart_program_suggested": False,
        "kestirme": None,
        "toplam_uyku": None,
        "reasons": reasons,
    }

    # --- Regresyon katmanı: gün planından BAĞIMSIZ, yalnız BAYRAK -----------
    # Otomatik hiçbir şey üretilmez; mobil kullanıcıya "Programı baştan başlatmak
    # ister misiniz?" kartını gösterir, onay gelirse /plans/generate çağırır.
    regression, reg_reasons = detect_regression(training_completed_at, ozet, today)
    if regression:
        result["regression_detected"] = True
        result["restart_program_suggested"] = True     # kart kullanıcıya gösterilir
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
    reasons.extend(gun["adaptation"]["uyarilar"])

    # --- K9: bugünün TOPLAM uykusu bandın ihtiyacını karşılıyor mu? ----------
    # Ölçüt artık 3 günlük ortalama değil, YENİDEN HESAPLANAN GÜNÜN kendisidir.
    if bant is not None:
        gunduz_dk, gece_dk = gun_uyku_toplamlari(gun["schedule"])
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


def gun_uyku_toplamlari(schedule: list[dict]) -> tuple[int, int]:
    """K9 — hesaplanmış günün (gündüz toplam, gece uykusu) dakikaları."""
    gunduz = sum(max(0, int(b["end_minute"]) - int(b["start_minute"]))
                 for b in schedule if b.get("type") == "nap")
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
