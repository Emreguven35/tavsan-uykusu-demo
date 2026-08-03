"""
Adaptif plan motoru testleri (Faz 6.1 + 6.1R) — LLM/DB/ağ YOK, tamamen deterministik.

İKİ AYRI KATMAN test edilir:
  A) Günlük ritim kaydırma (eğitim dışı dönem)
  B) Regresyon protokolü (İlayda — eğitim tamamlandıktan sonra)

Sabit log fixture'larıyla kural kapsamı:
  1. Sapma < 30dk            → kaydırma YOK
  2. Sapma +30dk             → +30 kaydırma
  3. Sapma +90dk             → +45'e kırpılır (üst sınır)
  4. Sapma -60dk             → -45'e kırpılır (alt sınır)
  5. Yaş bandı ihlali        → regenerate_required (kaydırma YAPILMAZ)
  6. REGRESYON: 13 gün sınırı (12→false, 13→true), dalamama eşiği (1 gece→false,
     2 gece→true), training_completed_at boş→asla, 20dk eşiği, ended_at yoksa sayılmaz
  7. adapt() regresyonu bayrak olarak döner; kaydırmayla aynı anda olabilir;
     45-15-45 protokol sabiti
  8. Uyanış kaydı yok        → kaydırma yok, açıklayıcı reason
  9. Çizelge kurucu          → uyanıklık penceresi mantığı + yatma aralığına kırpma
 10. Parser'lar              → KB'nin tutarsız metin biçimleri
 11. Log özeti               → gece uykusu bitişi = sabah uyanışı, şekerleme süresi
 12. shift_schedule          → blok süreleri korunur
 13. Geriye uyumluluk        → eski planda schedule yoksa türetilir

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
# 1) Sapma eşiğin altında → kaydırma yok
# =============================================================================
plan = plan_with_schedule(7 * 60)                       # plan uyanış 07:00
summary = pa.summarize_logs(wake_logs(7, 15), today=TODAY)   # gerçek 07:15 (+15dk)
r = pa.adapt(plan, BUCKET_8AY, summary, today=TODAY)
check("1) Sapma +15dk (<30) → kaydırma YOK",
      r["adjusted"] is False and r["shift_minutes"] == 0,
      f"adjusted={r['adjusted']} shift={r['shift_minutes']} reasons={r['reasons']}")

# =============================================================================
# 2) Sapma tam +30dk → kaydırılır
# =============================================================================
summary = pa.summarize_logs(wake_logs(7, 30), today=TODAY)   # 07:30
r = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, summary, today=TODAY)
check("2) Sapma +30dk → +30 kaydırma",
      r["adjusted"] is True and r["shift_minutes"] == 30,
      f"shift={r['shift_minutes']} bedtime={[b for b in r['schedule'] if b['key']=='bedtime']}")

# İlk blok gerçekten kaydı mı?
_wake = next(b for b in r["schedule"] if b["key"] == "wake")
check("2b) Kaydırma çizelgeye yansıdı (wake 07:30)",
      _wake["time"] == "07:30", f"wake={_wake['time']}")

# =============================================================================
# 3) Büyük pozitif sapma → +45'e kırpılır
# =============================================================================
summary = pa.summarize_logs(wake_logs(8, 30), today=TODAY)   # 08:30 = +90dk
r3 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, summary, today=TODAY)
# Kırpma İHLAL KONTROLÜNDEN ÖNCE uygulanır: +90 → +45; +45'lik yatış 19:45 olur ve
# 18:00-20:00 aralığında kalır → kaydırma geçerli, yeniden üretim gerekmez.
_bed3 = next(b for b in r3["schedule"] if b["key"] == "bedtime")
check("3) Sapma +90dk → +45'e kırpılır (yatış 19:45, sınır içinde)",
      r3["adjusted"] is True and r3["shift_minutes"] == 45
      and r3["regenerate_required"] is False and _bed3["time"] == "19:45",
      f"shift={r3['shift_minutes']} bed={_bed3['time']} "
      f"required={r3['regenerate_required']}")
check("3b) Kırpma sebebi raporlanır",
      any("kırpıldı" in s for s in r3["reasons"]), f"reasons={r3['reasons']}")

# =============================================================================
# 4) Negatif sapma → -45'e kırpılır (alt sınır)
# =============================================================================
summary = pa.summarize_logs(wake_logs(6, 0), today=TODAY)    # 06:00 = -60dk
r4 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, summary, today=TODAY)
_bed4 = next(b for b in r4["schedule"] if b["key"] == "bedtime")
_wake4 = next(b for b in r4["schedule"] if b["key"] == "wake")
check("4) Sapma -60dk → -45'e kırpılır (uyanış 06:15, yatış 18:15)",
      r4["adjusted"] is True and r4["shift_minutes"] == -45
      and _wake4["time"] == "06:15" and _bed4["time"] == "18:15",
      f"shift={r4['shift_minutes']} wake={_wake4['time']} bed={_bed4['time']}")

# =============================================================================
# 5) Yaş bandı ihlali → regenerate_required, kaydırma YAPILMAZ
# =============================================================================
# Yatışı zaten 19:45'e yakın bir plan kur (geç uyanış) + ileri sapma ver →
# kaydırılmış yatış 20:00 sınırını aşar.
late_plan = {"schedule": pa.build_schedule(BUCKET_8AY, 9 * 60),   # 09:00 uyanış
             "baseline_night_wakes": 2}
_late_bed = next(b for b in late_plan["schedule"] if b["key"] == "bedtime")
summary = pa.summarize_logs(wake_logs(10, 0), today=TODAY)        # +60dk sapma
r5 = pa.adapt(late_plan, BUCKET_8AY, summary, today=TODAY)
check("5) Yaş bandı ihlali → regenerate_required, kaydırma yok",
      r5["regenerate_required"] is True and r5["adjusted"] is False,
      f"base_bedtime={_late_bed['time']} required={r5['regenerate_required']} "
      f"adjusted={r5['adjusted']} reasons={r5['reasons']}")

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
r7 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, s_2fail,
              training_completed_at=DONE_13, today=TODAY)
check("7) adapt: regression_detected + restart_program_suggested",
      r7["regression_detected"] is True and r7["restart_program_suggested"] is True
      and r7["regenerate_required"] is False,
      f"det={r7['regression_detected']} restart={r7['restart_program_suggested']} "
      f"required={r7['regenerate_required']}")

r7b = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, s_1fail,
               training_completed_at=DONE_13, today=TODAY)
check("7b) Eşik altı → bayrak YOK",
      r7b["regression_detected"] is False and r7b["restart_program_suggested"] is False,
      f"det={r7b['regression_detected']}")

# 7c) Regresyon, günlük kaydırmadan BAĞIMSIZ katmandır (ikisi birlikte olabilir)
s_shift_and_reg = pa.summarize_logs(wake_logs(7, 40) + fail_night_logs(2), today=TODAY)
r7c = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, s_shift_and_reg,
               training_completed_at=DONE_13, today=TODAY)
check("7c) Kaydırma + regresyon aynı anda raporlanır",
      r7c["adjusted"] is True and r7c["shift_minutes"] == 40
      and r7c["regression_detected"] is True,
      f"adjusted={r7c['adjusted']} shift={r7c['shift_minutes']} "
      f"det={r7c['regression_detected']}")

# 7d) 45-15-45 protokolü sabiti doğru
_p = pa.NIGHT_WAKE_PROTOCOL
check("7d) 45-15-45 gece direnme protokolü sabiti",
      _p["resist_minutes"] == 45 and _p["routine_minutes"] == 15
      and _p["repeat"] is True and "45 dk" in _p["aciklama"], str(_p))

# =============================================================================
# 8) Uyanış kaydı yok → kaydırma yok + açıklayıcı sebep
# =============================================================================
logs8 = [FakeLog("feed", _utc(1, 13))]                      # yalnız besleme kaydı
summary8 = pa.summarize_logs(logs8, today=TODAY)
r8 = pa.adapt(plan_with_schedule(7 * 60), BUCKET_8AY, summary8, today=TODAY)
check("8) Uyanış kaydı yok → kaydırma yok + sebep",
      r8["adjusted"] is False and summary8["avg_wake_minute"] is None
      and any("uyanış" in s.lower() for s in r8["reasons"]),
      f"avg_wake={summary8['avg_wake_minute']} reasons={r8['reasons']}")

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
check("11) Gece uykusu bitişi → sabah uyanışı (06:40)",
      summary11["avg_wake_minute"] == 6 * 60 + 40,
      f"avg_wake={summary11['avg_wake_minute']} (beklenen {6*60+40})")

logs11b = wake_logs(7) + [FakeLog("nap", _utc(1, 10), _utc(1, 11, 30))]
summary11b = pa.summarize_logs(logs11b, today=TODAY)
check("11b) Şekerleme süresi ortalaması (90dk)",
      summary11b["avg_nap_minutes"] == 90.0,
      f"avg_nap={summary11b['avg_nap_minutes']} count={summary11b['avg_nap_count']}")

# =============================================================================
# 12) shift_schedule blok sürelerini korur
# =============================================================================
orig = pa.build_schedule(BUCKET_8AY, 7 * 60)
moved = pa.shift_schedule(orig, 30)
_durs_ok = all(o["end_minute"] - o["start_minute"] == m["end_minute"] - m["start_minute"]
               for o, m in zip(orig, moved))
_shift_ok = all(m["start_minute"] - o["start_minute"] == 30 for o, m in zip(orig, moved))
check("12) shift_schedule: süreler korunur, hepsi eşit kayar",
      _durs_ok and _shift_ok, f"durs_ok={_durs_ok} shift_ok={_shift_ok}")

# =============================================================================
# 13) Geriye uyumluluk: eski planda 'schedule' yoksa yaş bandından türetilir
# =============================================================================
old_plan = {"markdown": "# eski plan", "bucket": "8_ay"}     # Faz 5R öncesi içerik
summary13 = pa.summarize_logs(wake_logs(7, 45), today=TODAY)
r13 = pa.adapt(old_plan, BUCKET_8AY, summary13, today=TODAY)
check("13) Eski plan (schedule yok) → çizelge türetilir, motor çalışır",
      len(r13["schedule"]) > 0 and any("türetildi" in s for s in r13["reasons"]),
      f"blocks={len(r13['schedule'])} reasons={r13['reasons'][:1]}")

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

# Kaydırma sonrası headline'daki yatış saati de kayar
_moved = pa.shift_schedule(_sch, 30)
_hl2 = pa.headline("Elif", "9_ay", _moved)
check("14f) Kaydırma headline'a yansır", _hl2 != _hl and "19:30" in _hl2, _hl2)

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("PLAN ADAPTER TEST SONUÇLARI (Faz 6.1)")
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
