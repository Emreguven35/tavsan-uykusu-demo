"""
Gün içi kayma motoru testleri (v2 / K1-K9) — LLM/DB/ağ YOK, tamamen deterministik.

İKİ AYRI KATMAN test edilir:
  A) Gün içi yeniden hesaplama (sabit sabah hedefi + bugünün kayıtları)
  B) Regresyon protokolü (İlayda — eğitim tamamlandıktan sonra)

v1'DEN FARKLAR (bu dosyada güncellenen kontroller):
  • 30 dk'lık ölü bant ve ±45 dk'lık kaydırma tavanı KALDIRILDI (1-4).
  • Yaş bandı ihlali kontrolü artık GÜNLÜK çizelgeye değil ŞABLONA uygulanır (5).
  • shift_schedule kaldırıldı; yerine recompute_day özdeşlik/idempotans testi (12).
  • summarize_logs artık `avg_wake_minute` üretmez (11).
Senaryo kapsamının tamamı için: tests/test_gun_ici_kayma.py

Sabit log fixture'larıyla kural kapsamı:
  1. Küçük sapma da güne yansır (ölü bant yok)  → K2
  2. Uyanış 07:30 → zincir 07:30'dan             → K2/K3
  3. +90 dk sapma kırpılmaz; yatış tavanı korunur → K4
  4. Erken uyanış → gün erkene, yatış bandda      → K2/K4
  5. ŞABLON yaş bandına aykırı → regenerate_required; atlanan uyku tetiklemez
  6. REGRESYON: 13 gün sınırı (12→false, 13→true), dalamama eşiği (1 gece→false,
     2 gece→true), training_completed_at boş→asla, 20dk eşiği, ended_at yoksa sayılmaz
  7. adapt() regresyonu bayrak olarak döner; gün hesabıyla aynı anda olabilir;
     45-15-45 protokol sabiti
  8. Uyanış kaydı yok → çizelge şablonun aynısı, bloklar 'varsayilan'  → K6
  9. Çizelge kurucu   → uyanıklık penceresi mantığı + yatma aralığına kırpma
 10. Parser'lar       → KB'nin tutarsız metin biçimleri
 11. Log özeti        → gece uykusu bitişi = BUGÜNÜN uyanışı (ortalama yok) → K5
 12. recompute_day    → kayıt yoksa şablonla özdeş, idempotent            → K6/K8
 13. Geriye uyumluluk → eski planda schedule yoksa türetilir; v1 planı şablona
                        yükseltilir                                       → K1

Çalıştırma: python tests/test_plan_adapter.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.services import plan_adapter as pa   # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --- Fixture'lar -------------------------------------------------------------
class FakeLog:
    """SleepLog benzeri (ORM'siz) — motor yalnız type/started_at/ended_at okur."""

    def __init__(self, type_, started_at, ended_at=None):
        self.type = type_
        self.started_at = started_at
        self.ended_at = ended_at


TODAY = datetime(2026, 8, 3, tzinfo=timezone.utc).date()
TZ = pa.TZ_OFFSET_MIN            # +180dk (UTC+3)


def _utc(day_offset: int, local_h: int, local_m: int = 0) -> datetime:
    """Yerel (UTC+3) duvar saatini UTC datetime'a çevir."""
    base = datetime(2026, 8, 3, tzinfo=timezone.utc) - timedelta(days=day_offset)
    return base + timedelta(hours=local_h, minutes=local_m) - timedelta(minutes=TZ)


def wake_logs(local_hour: int, local_min: int = 0, days: int = 3) -> list[FakeLog]:
    """Her gün verilen yerel saatte sabah uyanışı ('wake' kaydı)."""
    return [FakeLog("wake", _utc(d, local_hour, local_min)) for d in range(days)]


# 8 aylık bebek bandı (gerçek KB değerleri) — WW 2.5-3.5 Saat, 2-3 uyku,
# gündüz 2.5-3.5 Saat, yatma 18:00-20:00
BUCKET_8AY = {
    "uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "2.5 - 3.5 Saat"},
    "uyku_sayisi": {"RESMI_DEGER": "2-3"},
    "gunduz_uyku_total": "2.5-3.5 Saat",
    "yatma_vakti": "18:00 - 20:00",
}


def plan_with_schedule(wake_minute: int = 7 * 60, baseline_nw: int | None = 2) -> dict:
    return {
        "schedule": pa.build_schedule(BUCKET_8AY, wake_minute),
        "baseline_night_wakes": baseline_nw,
    }


# =============================================================================
# 1) Küçük sapma da güne yansır — v1'deki 30 dk ÖLÜ BANT kaldırıldı (K2)
# =============================================================================
# v1: |sapma| < 30 dk → hiçbir şey yapılmaz. v2: gün gerçek uyanıştan hesaplanır,
# eşik yoktur; plan gerçeğin 15 dk gerisinde donmaz.
NOW = 23 * 60                                    # gün bitti → K6 etiketlemesi net
plan = plan_with_schedule(7 * 60)                # şablon uyanış 07:00
r = pa.adapt(plan, BUCKET_8AY, wake_logs(7, 15), today=TODAY, now_minute=NOW)
_wake = next(b for b in r["schedule"] if b["key"] == "wake")
check("1) Gerçek uyanış 07:15 → gün 07:15'ten hesaplanır (ölü bant yok)",
      _wake["time"] == "07:15"
      and r["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
      f"wake={_wake['time']} kaynak={r['adaptation']['sabah_uyanis_kaynak']}")
check("1b) ŞABLON değişmedi (K1)",
      pa.sabit_wake_minute(r["schedule_template"]) == 7 * 60,
      pa.sabit_wake_minute(r["schedule_template"]))

# =============================================================================
# 2) Uyanış 07:30 → tüm gün zinciri 07:30'dan akar
# =============================================================================
r = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, wake_logs(7, 30),
             today=TODAY, now_minute=NOW)
_wake = next(b for b in r["schedule"] if b["key"] == "wake")
_nap1 = next(b for b in r["schedule"] if b["key"] == "nap_1")
check("2) Uyanış 07:30 → wake 07:30, ilk uyku 07:30+pencere",
      _wake["time"] == "07:30" and _nap1["time"] == "10:30",   # pencere 180 dk (KB)
      f"wake={_wake['time']} nap_1={_nap1['time']}")
check("2b) yeniden_hesaplanan_bloklar dolu",
      "nap_1" in r["adaptation"]["yeniden_hesaplanan_bloklar"],
      r["adaptation"]["yeniden_hesaplanan_bloklar"])

# =============================================================================
# 3) Büyük sapma ARTIK KIRPILMAZ (v1'deki ±45 dk tavanı kaldırıldı)
# =============================================================================
r3 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, wake_logs(8, 30),
              today=TODAY, now_minute=NOW)
_wake3 = next(b for b in r3["schedule"] if b["key"] == "wake")
_bed3 = next(b for b in r3["schedule"] if b["key"] == "bedtime")
check("3) Uyanış 08:30 (+90dk) → kırpma YOK, gün 08:30'dan",
      _wake3["time"] == "08:30" and r3["regenerate_required"] is False,
      f"wake={_wake3['time']} required={r3['regenerate_required']}")
# K4 iki müdahaleden birini yapar: son uykuyu kısaltır/kaldırır, ya da yatışı
# tavana kırpar. Hangisi olursa olsun tavan aşılmaz ve uyarı yazılır.
_k4_uyari = [s for s in r3["reasons"]
             if "sınırına dayandı" in s or "kısaltıldı" in s or "kaldırıldı" in s]
check("3b) Yatış bandın TAVANINI aşmadı (K4) ve müdahale uyarıya yazıldı",
      _bed3["start_minute"] <= 20 * 60 and _k4_uyari,
      f"bed={_bed3['time']} reasons={r3['reasons']}")

# =============================================================================
# 4) Erken uyanış → gün erkene alınır, yatış da erkene (K2/K4)
# =============================================================================
r4 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, wake_logs(6, 0),
              today=TODAY, now_minute=NOW)
_bed4 = next(b for b in r4["schedule"] if b["key"] == "bedtime")
_wake4 = next(b for b in r4["schedule"] if b["key"] == "wake")
check("4) Uyanış 06:00 (-60dk) → kırpma yok, wake 06:00",
      _wake4["time"] == "06:00", f"wake={_wake4['time']} bed={_bed4['time']}")
check("4b) Yatış bandın yatma aralığında kaldı",
      18 * 60 <= _bed4["start_minute"] <= 20 * 60, _bed4["time"])

# =============================================================================
# 5) Yaş bandı ihlali → regenerate_required (kontrol artık ŞABLONA uygulanır)
# =============================================================================
# 09:00 uyanışlı şablon: son uyku ile yatış arası, bandın uyanıklık penceresi
# aralığının DIŞINDA kalıyor → şablon geçersiz, plan yeniden üretilmeli.
late_plan = {"schedule": pa.build_schedule(BUCKET_8AY, 9 * 60),   # 09:00 uyanış
             "baseline_night_wakes": 2}
_late_bed = next(b for b in late_plan["schedule"] if b["key"] == "bedtime")
r5 = pa.adapt(late_plan, BUCKET_8AY, wake_logs(10, 0), today=TODAY, now_minute=NOW)
check("5) Şablon yaş bandına aykırı → regenerate_required",
      r5["regenerate_required"] is True and r5["adaptation"] is None,
      f"base_bedtime={_late_bed['time']} required={r5['regenerate_required']} "
      f"reasons={r5['reasons']}")
check("5b) Bugünün uykusu atlansa bile yeniden üretim TETİKLENMEZ",
      pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY,
               wake_logs(7) + [FakeLog(pa.ATLANDI_TIPI, _utc(0, 10))],
               today=TODAY, now_minute=NOW)["regenerate_required"] is False,
      "atlanan uyku bant ihlali sayılmamalı")

# =============================================================================
# 6) REGRESYON PROTOKOLÜ (İlayda, Faz 6.1R)
# =============================================================================
# Yardımcı: n gecede, `dur_min` dakika süren birer gece uyanması üret.
def fail_night_logs(nights: int, dur_min: int = 25) -> list[FakeLog]:
    out = []
    for d in range(nights):
        start = _utc(d, 2)                                  # yerel 02:00
        out.append(FakeLog("night_wake", start,
                           start + timedelta(minutes=dur_min)))
    return out


DONE_13 = TODAY - timedelta(days=13)          # eğitim tam 13 gün önce bitti
DONE_12 = TODAY - timedelta(days=12)          # 12 gün → koşul-1 sağlanmaz

s_2fail = pa.summarize_logs(wake_logs(7, 10) + fail_night_logs(2), today=TODAY)
s_1fail = pa.summarize_logs(wake_logs(7, 10) + fail_night_logs(1), today=TODAY)

# 6a) 13 gün + 2 gece sinyal → REGRESYON
det, why = pa.detect_regression(DONE_13, s_2fail, today=TODAY)
check("6a) 13 gün + 2 gece dalamama → regression_detected",
      det is True and len(why) == 1,
      f"detected={det} fail_nights={s_2fail['self_soothe_fail_nights']} why={why}")

# 6b) 12 gün sınırı → koşul-1 sağlanmaz
det12, _ = pa.detect_regression(DONE_12, s_2fail, today=TODAY)
check("6b) 12 gün (sınır altı) → regresyon YOK",
      det12 is False, f"detected={det12}")

# 6c) 1 gece sinyal → koşul-2 sağlanmaz
det1, _ = pa.detect_regression(DONE_13, s_1fail, today=TODAY)
check("6c) 13 gün ama 1 gece dalamama → regresyon YOK",
      det1 is False, f"detected={det1} fail_nights={s_1fail['self_soothe_fail_nights']}")

# 6d) training_completed_at boş → HİÇBİR ZAMAN regresyon
det_none, _ = pa.detect_regression(None, s_2fail, today=TODAY)
check("6d) training_completed_at boş → regresyon YOK",
      det_none is False, f"detected={det_none}")

# 6e) Kısa gece uyanmaları (20dk altı) sinyal SAYILMAZ
s_short = pa.summarize_logs(wake_logs(7, 10) + fail_night_logs(3, dur_min=15),
                            today=TODAY)
det_short, _ = pa.detect_regression(DONE_13, s_short, today=TODAY)
check("6e) 15dk'lık uyanmalar (eşik altı) → sinyal sayılmaz",
      det_short is False and s_short["self_soothe_fail_nights"] == 0,
      f"detected={det_short} fail_nights={s_short['self_soothe_fail_nights']}")

# 6f) Süresi olmayan (ended_at=None) gece uyanması sinyal sayılmaz
s_noend = pa.summarize_logs(wake_logs(7, 10) + [FakeLog("night_wake", _utc(d, 2))
                                                for d in range(3)], today=TODAY)
det_noend, _ = pa.detect_regression(DONE_13, s_noend, today=TODAY)
check("6f) ended_at olmayan gece uyanması → sinyal sayılmaz",
      det_noend is False, f"fail_nights={s_noend['self_soothe_fail_nights']}")

# =============================================================================
# 7) adapt() regresyonu bayrak olarak döner; OTOMATİK ÜRETİM YOK
# =============================================================================
_loglar_2fail = wake_logs(7, 10) + fail_night_logs(2)
_loglar_1fail = wake_logs(7, 10) + fail_night_logs(1)

r7 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, _loglar_2fail,
              training_completed_at=DONE_13, today=TODAY, now_minute=NOW)
check("7) adapt: regression_detected + restart_program_suggested",
      r7["regression_detected"] is True and r7["restart_program_suggested"] is True
      and r7["regenerate_required"] is False,
      f"det={r7['regression_detected']} restart={r7['restart_program_suggested']} "
      f"required={r7['regenerate_required']}")

r7b = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, _loglar_1fail,
               training_completed_at=DONE_13, today=TODAY, now_minute=NOW)
check("7b) Eşik altı → bayrak YOK",
      r7b["regression_detected"] is False and r7b["restart_program_suggested"] is False,
      f"det={r7b['regression_detected']}")

# 7c) Regresyon, gün içi hesaplamadan BAĞIMSIZ katmandır (ikisi birlikte olabilir)
r7c = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY,
               wake_logs(7, 40) + fail_night_logs(2),
               training_completed_at=DONE_13, today=TODAY, now_minute=NOW)
_wake7c = next(b for b in r7c["schedule"] if b["key"] == "wake")
check("7c) Gün hesabı + regresyon aynı anda raporlanır",
      _wake7c["time"] == "07:40" and r7c["regression_detected"] is True,
      f"wake={_wake7c['time']} det={r7c['regression_detected']}")

# 7d) 45-15-45 protokolü sabiti doğru
_p = pa.NIGHT_WAKE_PROTOCOL
check("7d) 45-15-45 gece direnme protokolü sabiti",
      _p["resist_minutes"] == 45 and _p["routine_minutes"] == 15
      and _p["repeat"] is True and "45 dk" in _p["aciklama"], str(_p))

# =============================================================================
# 8) Uyanış kaydı yok → kaydırma yok + açıklayıcı sebep
# =============================================================================
logs8 = [FakeLog("feed", _utc(1, 13))]                      # yalnız besleme kaydı
r8 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, logs8, today=TODAY,
              now_minute=NOW)
_sablon8 = plan_with_schedule(7 * 60)["schedule"]
check("8) Uyanış kaydı yok → çizelge ŞABLONUN AYNISI, kaynak 'varsayilan' (K6)",
      r8["adaptation"]["sabah_uyanis_kaynak"] == "varsayilan"
      and [(b["key"], b["start_minute"]) for b in r8["schedule"]]
      == [(b["key"], b["start_minute"]) for b in _sablon8],
      f"kaynak={r8['adaptation']['sabah_uyanis_kaynak']} "
      f"varsayilan={r8['adaptation']['varsayilan_bloklar']}")
check("8b) Zamanı geçen bloklar 'varsayilan' işaretlendi (K6)",
      set(r8["adaptation"]["varsayilan_bloklar"]) >= {"nap_1", "nap_2"},
      r8["adaptation"]["varsayilan_bloklar"])

# =============================================================================
# 9) Çizelge kurucu — uyanıklık penceresi + yatma aralığına kırpma
# =============================================================================
sch = pa.build_schedule(BUCKET_8AY, 7 * 60)
_w = next(b for b in sch if b["key"] == "wake")
_naps = [b for b in sch if b["type"] == "nap"]
_bed = next(b for b in sch if b["key"] == "bedtime")
_bed_ok = 18 * 60 <= _bed["start_minute"] <= 20 * 60
# İlk uyku = uyanış + pencere ortası (2.5-3.5 Saat → 180dk) = 10:00
_first_ok = _naps and _naps[0]["time"] == "10:00"
check("9) Çizelge: uyanış+pencere ilk uyku, yatış 18:00-20:00 aralığında",
      _w["time"] == "07:00" and _first_ok and _bed_ok and len(_naps) == 2,
      f"wake={_w['time']} naps={[(n['time'],n['end']) for n in _naps]} bed={_bed['time']}")

# =============================================================================
# 10) Parser'lar — KB'nin tutarsız metin biçimleri
# =============================================================================
_cases = [
    ("40-60 Dakika", (40, 60)),
    ("90 Dakika", (90, 90)),
    ("1.5 - 2.5 Saat", (90, 150)),
    ("2.25 - 3.5 Saat", (135, 210)),
    ("5 Saat 30 Dakika - 7 Saat", (330, 420)),
    ("2.5 - 3.5 Saat (görsel) / 3-4 Saat (resmi)", (150, 210)),
    ("tüm gün (öğlen uykusu önerilir)", None),
    (None, None),
]
_bad = [(raw, pa.parse_duration_range(raw), exp)
        for raw, exp in _cases if pa.parse_duration_range(raw) != exp]
check("10) parse_duration_range tüm KB biçimleri", not _bad, f"hatalar={_bad}")

_t = pa.parse_time_range("18:00 - 20:00")
_c = [pa.parse_count("3-4"), pa.parse_count("4+"), pa.parse_count({"RESMI_DEGER": "2"})]
check("10b) parse_time_range + parse_count",
      _t == (1080, 1200) and _c == [3, 4, 2], f"time={_t} counts={_c}")

# =============================================================================
# 11) Log özeti — gece uykusunun BİTİŞİ sabah uyanışıdır
# =============================================================================
logs11 = []
for d in range(3):
    # Gece 20:00'de başlayıp ertesi sabah 06:40'ta biten uyku
    logs11.append(FakeLog("sleep", _utc(d + 1, 20), _utc(d, 6, 40)))
summary11 = pa.summarize_logs(logs11, today=TODAY)
check("11) Gece uykusu bitişi → BUGÜNÜN uyanışı (06:40, ortalama DEĞİL)",
      summary11["gunun_uyanisi"] == 6 * 60 + 40,
      f"gunun_uyanisi={summary11['gunun_uyanisi']} (beklenen {6*60+40})")
check("11c) avg_wake_minute artık ÜRETİLMEZ (K5 — ortalama kaldırıldı)",
      "avg_wake_minute" not in summary11, list(summary11))

logs11b = wake_logs(7) + [FakeLog("nap", _utc(1, 10), _utc(1, 11, 30))]
summary11b = pa.summarize_logs(logs11b, today=TODAY)
check("11b) Şekerleme süresi ortalaması (90dk)",
      summary11b["avg_nap_minutes"] == 90.0,
      f"avg_nap={summary11b['avg_nap_minutes']} count={summary11b['avg_nap_count']}")

# =============================================================================
# 12) recompute_day SAF ve ÖZDEŞ: kayıt yoksa sonuç şablonun aynısıdır (K6/K8)
# =============================================================================
orig = pa.build_schedule(BUCKET_8AY, 7 * 60)
_bos = pa.recompute_day(orig, None, 7 * 60, [], now_minute=0, gun=TODAY,
                        bucket_params=BUCKET_8AY)
check("12) Kayıt yokken çizelge ŞABLONLA BİREBİR aynı",
      [(b["key"], b["start_minute"], b["end_minute"]) for b in _bos["schedule"]]
      == [(b["key"], b["start_minute"], b["end_minute"]) for b in orig],
      f"{[(b['key'], b['time']) for b in _bos['schedule']]}")
_tekrar = pa.recompute_day(orig, None, 7 * 60, wake_logs(7, 25), now_minute=NOW,
                           gun=TODAY, bucket_params=BUCKET_8AY)
_tekrar2 = pa.recompute_day(orig, None, 7 * 60, wake_logs(7, 25), now_minute=NOW,
                            gun=TODAY, bucket_params=BUCKET_8AY)
check("12b) İdempotent: aynı girdi → aynı çizelge (K8)",
      _tekrar["schedule"] == _tekrar2["schedule"], "")
# Gündüz uykularının SÜRESİ şablondan gelir. Gece bloğunun süresi kasten
# değişkendir: bitişi SABİT sabah hedefidir (K1), başlangıcı zincire bağlıdır.
check("12c) Gündüz uykularının süreleri şablondan korunur",
      [b["end_minute"] - b["start_minute"]
       for b in _tekrar["schedule"] if b["type"] == "nap"]
      == [b["end_minute"] - b["start_minute"] for b in orig if b["type"] == "nap"],
      f"{[b['end_minute'] - b['start_minute'] for b in _tekrar['schedule']]}")
_bed12 = next(b for b in _tekrar["schedule"] if b["key"] == "bedtime")
check("12d) Gece bloğu SABİT sabah hedefinde biter (K1)",
      _bed12["end_minute"] == 7 * 60 + 1440, _bed12["end"])

# =============================================================================
# 13) Geriye uyumluluk: eski planda 'schedule' yoksa yaş bandından türetilir
# =============================================================================
old_plan = {"markdown": "# eski plan", "bucket": "8_ay"}     # Faz 5R öncesi içerik
r13 = pa.adapt(old_plan, BUCKET_8AY, wake_logs(7, 45), today=TODAY, now_minute=NOW)
check("13) Eski plan (schedule yok) → şablon türetilir, motor çalışır",
      len(r13["schedule"]) > 0 and any("türetildi" in s for s in r13["reasons"]),
      f"blocks={len(r13['schedule'])} reasons={r13['reasons'][:1]}")
check("13b) v1 planı (schedule var, schedule_template yok) → şablona yükseltilir",
      any("şablona yükseltildi" in s
          for s in pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, [],
                            today=TODAY, now_minute=NOW)["reasons"]),
      pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, [], today=TODAY,
               now_minute=NOW)["reasons"])

# =============================================================================
# 14) Çizelge şeması (mobil sözleşmesi) + headline
# =============================================================================
_sch = pa.build_schedule(BUCKET_8AY, 7 * 60)
_zorunlu = {"time", "type", "title", "key", "start_minute", "end_minute"}
_eksik = [b["key"] for b in _sch if not _zorunlu.issubset(b.keys())]
check("14) Her blokta zorunlu alanlar var (time/type/title)",
      not _eksik, f"eksik={_eksik} ornek={_sch[0]}")

_gecerli_tipler = {"wake", "nap", "sleep", "feed", "routine"}
_kotu = [b["type"] for b in _sch if b["type"] not in _gecerli_tipler]
check("14b) type değerleri sözleşmedeki enum içinde",
      not _kotu, f"gecersiz={_kotu}")

check("14c) time 'HH:MM' biçiminde",
      all(len(b["time"]) == 5 and b["time"][2] == ":" for b in _sch),
      str([b["time"] for b in _sch]))

check("14d) Gece uykusu tipi 'sleep'",
      next(b for b in _sch if b["key"] == "bedtime")["type"] == "sleep",
      str(next(b for b in _sch if b["key"] == "bedtime")))

_hl = pa.headline("Elif", "9_ay", _sch)
check("14e) headline tek cümlelik kişisel özet",
      "Elif" in _hl and "kısa uyku" in _hl and "yatış" in _hl, _hl)
print(f"       headline: {_hl}")

# Gün yeniden hesaplanınca headline'daki yatış saati de değişir
_gec = pa.recompute_day(_sch, None, 7 * 60, wake_logs(8, 0), now_minute=NOW,
                        gun=TODAY, bucket_params=BUCKET_8AY)["schedule"]
_hl2 = pa.headline("Elif", "9_ay", _gec)
check("14f) Yeniden hesaplanan gün headline'a yansır",
      _hl2 != _hl and next(b for b in _gec if b["key"] == "bedtime")["time"] in _hl2,
      _hl2)

# =============================================================================
# 15) GERİYE UYUMLULUK: şema değişikliğinden ÖNCE üretilmiş planlar (Faz 6.5R)
# =============================================================================
# Gerçek production kaydından alınmış blok biçimi: start/label/type="night",
# time/title YOK. Normalize edilmezse bağlam "None uyanış" üretir ve gece bloğu
# bildirimlerde HİÇ yakalanmaz.
ESKI_SCHEDULE = [
    {"end": "07:00", "key": "wake", "type": "wake", "label": "Sabah uyanış",
     "start": "07:00", "end_minute": 420, "start_minute": 420},
    {"end": "10:45", "key": "nap_1", "type": "nap", "label": "1. gündüz uykusu",
     "start": "09:30", "end_minute": 645, "start_minute": 570},
    {"end": "07:00", "key": "bedtime", "type": "night", "label": "Gece uykusu",
     "start": "19:00", "end_minute": 1860, "start_minute": 1140},
]

_norm = pa.normalize_schedule(ESKI_SCHEDULE)
check("15) Eski 'start' → 'time' taşındı",
      [b["time"] for b in _norm] == ["07:00", "09:30", "19:00"],
      str([b.get("time") for b in _norm]))
check("15b) Eski 'label' → 'title' taşındı",
      [b["title"] for b in _norm] == ["Sabah uyanış", "1. gündüz uykusu", "Gece uykusu"],
      str([b.get("title") for b in _norm]))
check("15c) type 'night' → 'sleep'",
      _norm[-1]["type"] == "sleep", str(_norm[-1]["type"]))
check("15d) Eski alanlar (start/label) taşınmaz",
      all("start" not in b and "label" not in b for b in _norm), str(_norm[0]))
check("15e) Güncel biçim normalize'dan DEĞİŞMEDEN geçer",
      pa.normalize_schedule(_sch) == _sch, "")
check("15f) Boş/None güvenli", pa.normalize_schedule(None) == [], "")

# Eski planla adapt çalışıyor mu? Fixture, GEÇERLİ bir çizelgenin eski biçime
# "düşürülmüş" hâlidir — böylece kaydırma dalı gerçekten koşar (yukarıdaki kısa
# ESKI_SCHEDULE tek nap içerdiği için uyanıklık penceresi ihlaline düşerdi).
def _eski_bicime_dusur(sch):
    out = []
    for b in sch:
        nb = {k: v for k, v in b.items() if k not in ("time", "title")}
        nb["start"] = b["time"]
        nb["label"] = b["title"]
        nb["type"] = "night" if b["type"] == "sleep" else b["type"]
        out.append(nb)
    return out


_eski_plan = {"schedule": _eski_bicime_dusur(pa.build_schedule(BUCKET_8AY, 7 * 60))}
_r15 = pa.adapt(_eski_plan, BUCKET_8AY, wake_logs(7, 40), today=TODAY,
                now_minute=NOW)
_wake15 = next(b for b in _r15["schedule"] if b["key"] == "wake")
check("15g) Eski planla adapt: çizelge yükseltilir ve gün yeniden hesaplanır",
      _wake15["time"] == "07:40"
      and all(b.get("time") and b.get("title") for b in _r15["schedule"])
      and all(b["type"] != "night" for b in _r15["schedule"]),
      f"wake={_wake15['time']} "
      f"blok={_r15['schedule'][0] if _r15['schedule'] else None}")

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("PLAN ADAPTER TEST SONUÇLARI (gün içi kayma motoru v2 — K1-K9)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    print(f"[{mark}] {name}")
    if detail and not ok:
        print(f"       {detail}")
print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} gecti")
print("=" * 74)
sys.exit(0 if passed == len(results) else 1)
