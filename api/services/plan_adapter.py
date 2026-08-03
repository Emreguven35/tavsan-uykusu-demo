"""
Adaptif plan motoru — KURAL TABANLI, LLM KULLANMAZ (Faz 6.1).

Amaç: uygulamadaki uyudu/uyandı kayıtları (sleep_logs) plana geri beslensin.
Tüm mantık deterministiktir: aynı girdi → aynı çıktı. Bu yüzden birim testle
tam kapsanabilir ve maliyet üretmez (Claude çağrısı YOK).

Akış:
    1. build_schedule()  — yaş bandı parametrelerinden (master_knowledge_base)
       saat saat çizelge kur. KB'de hazır çizelge YOKTUR; uyanıklık penceresi +
       uyku sayısı + gündüz uyku toplamı + yatma vakti aralığından türetilir.
    2. summarize_logs()  — son N günün kayıtlarından gerçek sabah uyanışı, gece
       yatışı, şekerleme sayısı/süresi ve gece uyanma sayısı ortalamalarını çıkar.
    3. adapt()           — plandaki uyanış ile gerçek uyanış arasındaki sapmaya
       göre çizelgeyi kaydır; kaydırma yaş bandına aykırı düşerse yeniden üretim iste.
    4. detect_regression() — İlayda protokolü: eğitim bittikten ≥13 gün sonra
       "kendine dalamama" sinyali görülürse REGRESYON bayrağı kaldır (Faz 6.1R).

İKİ AYRI KATMAN (karıştırılmamalı):
  • Günlük kaydırma  → eğitim DIŞI dönemin günlük ritim yönetimi (kural 1-2).
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

logger = logging.getLogger("tavsan.plan_adapter")

# --- Sabitler (spec) ---------------------------------------------------------
LOOKBACK_DAYS = 3               # "son 3 günün kayıtları"
MIN_SHIFT_MIN = 30              # bu sapmanın ALTINDA kaydırma yapılmaz
MAX_SHIFT_MIN = 45              # kaydırma bu değerde kırpılır (±45dk)
TZ_OFFSET_MIN = 180             # UTC+3 (Europe/Istanbul)

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

# Sabah uyanışı bu pencerede aranır (yerel saat) — gece uyanmalarıyla karışmasın.
MORNING_WINDOW = (4 * 60, 11 * 60)           # 04:00–11:00
# Gece yatışı bu pencerede aranır.
BEDTIME_WINDOW = (16 * 60, 24 * 60 + 2 * 60)  # 16:00–02:00 (ertesi güne taşabilir)


# =============================================================================
# Yaş bandı parametre ayrıştırıcıları
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


def build_schedule(bucket_params: dict, wake_minute: int = DEFAULT_WAKE_MIN) -> list[dict]:
    """Yaş bandı parametrelerinden saat saat çizelge üret.

    Mantık (motorun 'uyanıklık süresi' ilkesiyle aynı): sabah uyanışına yaşa uygun
    uyanıklık penceresi eklenerek ilk uyku; her uykudan sonra tekrar pencere kadar
    uyanıklık. Gece yatışı, yaş bandının yatma_vakti aralığına kırpılır.

    Dönen her blok (mobil sözleşmesi):
        {time, end?, type, title, note?, key, start_minute, end_minute}
      time/end : "HH:MM" duvar saati (mobilin gösterdiği alanlar)
      type     : 'wake' | 'nap' | 'sleep' | 'feed' | 'routine'
                 (v1'de yalnız wake/nap/sleep üretilir; feed/routine şemada
                  ayrılmıştır — KB'de bu blokları türetecek veri henüz yok.)
      title    : ekranda görünen başlık
      key/start_minute/end_minute : dahili (kaydırma, bildirim penceresi) —
                 mobil bunlara bakmak zorunda değildir.
    """
    p = bucket_params or {}
    ww = parse_duration_range(p.get("uyaniklik_penceresi")) or DEFAULT_WAKE_WINDOW
    ww_mid = _mid(ww)
    n_naps = parse_count(p.get("uyku_sayisi"))
    if n_naps is None:
        n_naps = 2
    day_sleep = parse_duration_range(p.get("gunduz_uyku_total")) or DEFAULT_DAY_SLEEP
    bed_range = parse_time_range(p.get("yatma_vakti")) or DEFAULT_BEDTIME_RANGE

    blocks: list[dict] = [{
        "key": "wake", "type": "wake",
        "start_minute": wake_minute, "end_minute": wake_minute,
        "title": "Sabah uyanışı",
    }]

    cursor = wake_minute
    if n_naps > 0:
        nap_len = max(30, _mid(day_sleep) // n_naps)     # tek uyku min 30dk
        for i in range(1, n_naps + 1):
            start = cursor + ww_mid
            end = start + nap_len
            blocks.append({
                "key": f"nap_{i}", "type": "nap",
                "start_minute": start, "end_minute": end,
                "title": f"{i}. gündüz uykusu",
                "note": f"Uyanıklık penceresi ~{ww_mid} dk sonra",
            })
            cursor = end

    bedtime_ham = cursor + ww_mid
    bedtime = max(bed_range[0], min(bed_range[1], bedtime_ham))   # yaş bandına kırp
    gece = {
        "key": "bedtime", "type": "sleep",
        "start_minute": bedtime, "end_minute": wake_minute + 24 * 60,
        "title": "Gece uykusu",
    }
    if bedtime != bedtime_ham:
        gece["note"] = (f"Yaş bandının yatma aralığına "
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


def shift_schedule(schedule: list[dict], minutes: int) -> list[dict]:
    """Çizelgenin TAMAMINI verilen dakika kadar kaydır (blok süreleri korunur)."""
    out = []
    for b in schedule:
        nb = dict(b)
        nb["start_minute"] = b["start_minute"] + minutes
        nb["end_minute"] = b["end_minute"] + minutes
        out.append(_with_labels(nb))
    return out


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
    """Son `lookback_days` günün kayıtlarından gerçek davranış ortalamaları.

    Dönen: {
      days_with_data, avg_wake_minute, avg_bedtime_minute,
      avg_nap_count, avg_nap_minutes, avg_night_wakes
    }
    Değer hesaplanamıyorsa ilgili alan None (çağıran karar verir)."""
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=lookback_days - 1)

    wake_by_day: dict[date, int] = {}
    bed_by_day: dict[date, int] = {}
    naps_by_day: dict[date, list[int]] = {}
    night_wakes_by_day: dict[date, int] = {}
    days_seen: set[date] = set()
    # "Kendine dalamama" sinyali görülen AYRI geceler (İlayda regresyon protokolü).
    # Gece anahtarı: 12:00'den önceki uyanmalar BİR ÖNCEKİ günün gecesine sayılır.
    fail_nights: set[date] = set()
    regression_start = today - timedelta(days=REGRESSION_LOOKBACK_NIGHTS)

    for lg in logs:
        started = getattr(lg, "started_at", None)
        if started is None:
            continue
        d, minute = _local_minute(started, tz_offset_min)
        typ = getattr(lg, "type", None)
        ended = getattr(lg, "ended_at", None)

        # --- Regresyon sinyali: uzun süren gece uyanması (pencere: son 3 gece) ---
        # Bu kontrol LOOKBACK_DAYS penceresinden BAĞIMSIZDIR (kendi penceresi var).
        if typ == "night_wake" and ended is not None:
            dur = (ended - started).total_seconds() / 60.0
            if dur >= SELF_SOOTHE_FAIL_MIN:
                night_key = d - timedelta(days=1) if minute < 12 * 60 else d
                if regression_start <= night_key <= today:
                    fail_nights.add(night_key)

        # Gece uykusunun BİTİŞİ sabah uyanışını verir → bitişin gününe yazılır.
        if typ == "sleep" and ended is not None:
            ed, emin = _local_minute(ended, tz_offset_min)
            if MORNING_WINDOW[0] <= emin <= MORNING_WINDOW[1] and start <= ed <= today:
                days_seen.add(ed)
                # Aynı gün birden çok kayıt varsa en ERKEN uyanışı al.
                wake_by_day[ed] = min(wake_by_day.get(ed, emin), emin)

        if not (start <= d <= today):
            continue
        days_seen.add(d)

        if typ == "wake" and MORNING_WINDOW[0] <= minute <= MORNING_WINDOW[1]:
            wake_by_day[d] = min(wake_by_day.get(d, minute), minute)
        elif typ == "sleep":
            m = minute if minute >= BEDTIME_WINDOW[0] else minute + 24 * 60
            if BEDTIME_WINDOW[0] <= m <= BEDTIME_WINDOW[1]:
                bed_by_day[d] = max(bed_by_day.get(d, m), m)
        elif typ == "nap":
            dur = 0
            if ended is not None:
                dur = max(0, int((ended - started).total_seconds() // 60))
            naps_by_day.setdefault(d, []).append(dur)
        elif typ == "night_wake":
            night_wakes_by_day[d] = night_wakes_by_day.get(d, 0) + 1

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 1) if vals else None

    nap_counts = [len(v) for v in naps_by_day.values()]
    nap_durs = [d for v in naps_by_day.values() for d in v if d > 0]

    return {
        "days_with_data": len(days_seen),
        "avg_wake_minute": (int(round(sum(wake_by_day.values()) / len(wake_by_day)))
                            if wake_by_day else None),
        "avg_bedtime_minute": (int(round(sum(bed_by_day.values()) / len(bed_by_day)))
                               if bed_by_day else None),
        "avg_nap_count": _avg(nap_counts),
        "avg_nap_minutes": _avg(nap_durs),
        # Gece uyanma: kaydı olan günler üzerinden ortalama (0 kayıtlı gün 0 sayılır
        # ancak hiç veri yoksa None).
        "avg_night_wakes": (round(sum(night_wakes_by_day.get(d, 0) for d in days_seen)
                                  / len(days_seen), 1) if days_seen else None),
        # Regresyon protokolü: ≥20dk süren gece uyanmasının görüldüğü AYRI gece sayısı.
        "self_soothe_fail_nights": len(fail_nights),
    }


def detect_regression(training_completed_at: date | None, log_summary: dict,
                      today: date | None = None) -> tuple[bool, list[str]]:
    """İlayda regresyon protokolü — İKİ koşul birden sağlanmalı.

      1. training_completed_at dolu VE üzerinden ≥13 gün geçmiş
      2. Son 3 gecede, ≥20dk süren gece uyanması (kendine dalamama) ≥2 GECEDE görülmüş

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
        reasons.append(
            f"Eğitim {training_completed_at.isoformat()} tarihinde tamamlandı "
            f"({days_since} gün önce) ve son {REGRESSION_LOOKBACK_NIGHTS} gecenin "
            f"{fail_nights}'inde {SELF_SOOTHE_FAIL_MIN} dk+ süren gece uyanması var — "
            f"kendine dalama becerisinde gerileme (regresyon) sinyali")
    return (cond_1 and cond_2), reasons


# =============================================================================
# Adaptasyon kuralları
# =============================================================================
def _violates_age_band(schedule: list[dict], bucket_params: dict) -> str | None:
    """Kaydırılmış çizelge yaş bandına aykırı mı? Aykırıysa sebep metni döner.

    İki kontrol:
      1. Gece yatışı, yaş bandının yatma_vakti aralığının DIŞINDA mı?
      2. Son uykudan yatışa kadarki uyanıklık, yaş bandının penceresi dışında mı?
    """
    p = bucket_params or {}
    bed_range = parse_time_range(p.get("yatma_vakti")) or DEFAULT_BEDTIME_RANGE
    ww = parse_duration_range(p.get("uyaniklik_penceresi")) or DEFAULT_WAKE_WINDOW

    bed = next((b for b in schedule if b["key"] == "bedtime"), None)
    if bed is None:
        return None
    if not (bed_range[0] <= bed["start_minute"] <= bed_range[1]):
        return (f"Kaydırılmış yatış saati {_fmt(bed['start_minute'])}, yaş bandının "
                f"yatma aralığı ({_fmt(bed_range[0])}–{_fmt(bed_range[1])}) dışında")

    naps = [b for b in schedule if b["type"] == "nap"]
    last_end = naps[-1]["end_minute"] if naps else None
    if last_end is not None:
        gap = bed["start_minute"] - last_end
        if not (ww[0] <= gap <= ww[1]):
            return (f"Son uyku ile yatış arası {gap} dk; yaş bandının uyanıklık "
                    f"penceresi {ww[0]}–{ww[1]} dk dışında")
    return None


def adapt(plan_content: dict, bucket_params: dict, log_summary: dict,
          training_completed_at: date | None = None,
          today: date | None = None) -> dict:
    """Kural tabanlı adaptasyon + regresyon tespiti.

    Dönen: {
      adjusted: bool,                    # çizelge kaydırıldı mı
      shift_minutes: int,                # uygulanan kaydırma (kırpılmış)
      regenerate_required: bool,         # yaş bandı ihlali → çağıran TAM YENİDEN ÜRETİM yapar
      regression_detected: bool,         # İlayda protokolü (eğitim sonrası geri gidiş)
      restart_program_suggested: bool,   # kullanıcıya sorulacak ÖNERİ — otomatik üretim YOK
      reasons: [str],
      schedule: [...]                    # sonuç çizelge (kaydırılmış veya orijinal)
    }

    NOT (Faz 6.1R): "gece uyanma ortalaması +2 → yeniden üretim öner" kuralı
    KALDIRILDI; yerine İlayda'nın regresyon protokolü geldi (detect_regression).
    """
    reasons: list[str] = []

    # Temel çizelge: plan içinde varsa onu kullan, yoksa yaş bandından türet
    # (Faz 5R öncesi planlarda 'schedule' yoktur — geriye uyumluluk).
    schedule = plan_content.get("schedule") if isinstance(plan_content, dict) else None
    schedule = normalize_schedule(schedule)          # eski şema → güncel sözleşme
    if not schedule:
        schedule = build_schedule(bucket_params, DEFAULT_WAKE_MIN)
        reasons.append("Planda yapısal çizelge yoktu; yaş bandından türetildi")

    wake_block = next((b for b in schedule if b["key"] == "wake"), None)
    plan_wake = wake_block["start_minute"] if wake_block else DEFAULT_WAKE_MIN
    actual_wake = log_summary.get("avg_wake_minute")

    result = {
        "adjusted": False,
        "shift_minutes": 0,
        "regenerate_required": False,
        "regression_detected": False,
        "restart_program_suggested": False,
        "reasons": reasons,
        "schedule": schedule,
    }

    # --- Regresyon katmanı: kaydırmadan BAĞIMSIZ, yalnız BAYRAK -------------
    # Otomatik hiçbir şey üretilmez; mobil kullanıcıya "Programı baştan başlatmak
    # ister misiniz?" kartını gösterir, onay gelirse /plans/generate çağırır.
    regression, reg_reasons = detect_regression(training_completed_at, log_summary, today)
    if regression:
        result["regression_detected"] = True
        result["restart_program_suggested"] = True     # kart kullanıcıya gösterilir
        reasons.extend(reg_reasons)

    # --- Kural 1: uyanış sapmasına göre kaydırma -----------------------------
    if actual_wake is None:
        reasons.append("Kayıtlarda sabah uyanışı bulunamadı; kaydırma yapılmadı")
        return result

    delta = actual_wake - plan_wake
    if abs(delta) < MIN_SHIFT_MIN:
        reasons.append(
            f"Gerçek uyanış {_fmt(actual_wake)}, plandaki {_fmt(plan_wake)} "
            f"(sapma {delta:+d} dk) — {MIN_SHIFT_MIN} dk eşiğinin altında, kaydırma yok")
        return result

    shift = max(-MAX_SHIFT_MIN, min(MAX_SHIFT_MIN, delta))
    shifted = shift_schedule(schedule, shift)

    # --- Kural 2: kaydırma yaş bandına aykırıysa → TAM YENİDEN ÜRETİM --------
    violation = _violates_age_band(shifted, bucket_params)
    if violation:
        result["regenerate_required"] = True
        reasons.append(f"{violation} — kaydırma yerine plan yeniden üretilecek")
        return result

    clip_note = "" if shift == delta else f" (sapma {delta:+d} dk, ±{MAX_SHIFT_MIN} dk sınırına kırpıldı)"
    reasons.append(
        f"Gerçek uyanış {_fmt(actual_wake)}, plandaki {_fmt(plan_wake)} — "
        f"çizelge {shift:+d} dk kaydırıldı{clip_note}")
    result.update({"adjusted": True, "shift_minutes": shift, "schedule": shifted})
    return result
