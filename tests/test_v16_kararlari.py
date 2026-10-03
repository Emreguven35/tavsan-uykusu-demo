"""
v1.6 kararları (2026-09-30) — LLM/DB/ağ YOK, tamamen deterministik.

  A) Geç yatış istisnası (bant tavanını aşıp 24:00'e kadar + "alışma evresi")
     YALNIZ egitim_plani'nda. egitim_bekleme'de yatış bant tavanında kalır,
     gerekirse son uyku kısaltılır (eski K4).
  B) Sabah hedefi farkı uyarı (Dikkat kartı) DEĞİL: sabah bloğunun notu +
     adaptation.notlar; reasons/uyarilar'a girmez.
  C) Tolerans kural başına: katı 06:00'da 30 dk (06:30 sonrası not),
     "08:00'e kadar" kurallarında 0 (yalnız 08:00 sonrası not).
  D) gec_yatis.mutlak_tavan 1440 dk → "24:00"; blok saatleri 00:xx sarmaya
     devam eder.
  E) asama_gecisi kuralı config'te (İlayda 4. cevap) — motor henüz okumuyor.

Çalıştırma: python tests/test_v16_kararlari.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.services import plan_adapter as pa   # noqa: E402
from engine import yas_bantlari               # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


class FakeLog:
    def __init__(self, type_, started_at, ended_at=None, notes=None):
        self.type = type_
        self.started_at = started_at
        self.ended_at = ended_at
        self.notes = notes


TODAY = datetime(2026, 8, 3, tzinfo=timezone.utc).date()
TZ = pa.TZ_OFFSET_MIN
NOW = 23 * 60


def _utc(local_h: int, local_m: int = 0) -> datetime:
    base = datetime(2026, 8, 3, tzinfo=timezone.utc)
    return base + timedelta(hours=local_h, minutes=local_m) - timedelta(minutes=TZ)


def uyanis(h: int, m: int = 0) -> list[FakeLog]:
    return [FakeLog("wake", _utc(h, m))]


BUCKET_8AY = {
    "uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "2.5 - 3.5 Saat"},
    "uyku_sayisi": {"RESMI_DEGER": "2-3"},
    "gunduz_uyku_total": "2.5-3.5 Saat",
    "yatma_vakti": "18:00 - 20:00",
}


def plan(wake_minute: int = 7 * 60, **ek) -> dict:
    return {"schedule": pa.build_schedule(BUCKET_8AY, wake_minute), **ek}


def blok(r: dict, key: str) -> dict:
    return next(b for b in r["schedule"] if b["key"] == key)


# =============================================================================
# A) Geç yatış istisnası yalnız egitim_plani
# =============================================================================
# 08:30 uyanış (+90 dk) günü kaydırır; doğal yatış bandın üst ucunu (20:00) aşar.
r_egitim = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, uyanis(8, 30),
                    today=TODAY, now_minute=NOW)
r_bekleme = pa.adapt(plan(type="egitim_bekleme", uygun_mu=False), BUCKET_8AY,
                     uyanis(8, 30), today=TODAY, now_minute=NOW)
r_tipsiz_uygun = pa.adapt(plan(), BUCKET_8AY, uyanis(8, 30), today=TODAY,
                          now_minute=NOW)
r_tipsiz_bekleme = pa.adapt(plan(uygun_mu=False), BUCKET_8AY, uyanis(8, 30),
                            today=TODAY, now_minute=NOW)
r_param = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, uyanis(8, 30),
                   today=TODAY, now_minute=NOW, plan_tipi="egitim_bekleme")

b_eg, b_bk = blok(r_egitim, "bedtime"), blok(r_bekleme, "bedtime")
check("A1) egitim_plani: yatış bant üst ucunu (20:00) aşabiliyor + gec_yatis izi",
      b_eg["start_minute"] > 20 * 60 and b_eg.get("gec_yatis"),
      f"bed={b_eg['time']} gec={b_eg.get('gec_yatis')}")
check("A2) egitim_plani: 'alışma evresi' uyarısı var",
      any("alışma evresine özgü" in s for s in r_egitim["reasons"]),
      str(r_egitim["reasons"]))
check("A3) egitim_bekleme: yatış bant tavanında (≤ 20:00), gec_yatis YOK",
      b_bk["start_minute"] <= 20 * 60 and "gec_yatis" not in b_bk,
      f"bed={b_bk['time']} blok={b_bk}")
check("A4) egitim_bekleme: 'alışma evresi' uyarısı YOK",
      not any("alışma evresine özgü" in s for s in r_bekleme["reasons"]),
      str(r_bekleme["reasons"]))
_bk_naps = [b for b in r_bekleme["schedule"] if b["type"] == "nap"]
_eg_naps = [b for b in r_egitim["schedule"] if b["type"] == "nap"]
check("A5) egitim_bekleme: son uyku kısaltıldı ya da kaldırıldı (eski K4)",
      sum(b["end_minute"] - b["start_minute"] for b in _bk_naps)
      < sum(b["end_minute"] - b["start_minute"] for b in _eg_naps),
      f"bekleme={[(b['time'], b['end']) for b in _bk_naps]} "
      f"egitim={[(b['time'], b['end']) for b in _eg_naps]}")
check("A6) Tipsiz + uygun plan → egitim_plani sayılır (istisna geçerli)",
      blok(r_tipsiz_uygun, "bedtime").get("gec_yatis") is not None)
check("A7) Tipsiz + uygun_mu=False → egitim_bekleme sayılır (istisna yok)",
      "gec_yatis" not in blok(r_tipsiz_bekleme, "bedtime"))
check("A8) plan_tipi parametresi içerikteki type'ı ezer",
      "gec_yatis" not in blok(r_param, "bedtime"))
check("A9) Tablo: istisna yalnız egitim_plani'nda geçerli",
      [yas_bantlari.gece_yatisi_mutlak_tavan_gecerli_mi(t) for t in
       ("egitim_plani", "egitim_bekleme", "yenidogan_ritim", None)]
      == [True, False, False, False])

# =============================================================================
# D) mutlak_tavan "24:00"; blok saatleri sarmaya devam eder
# =============================================================================
check("D1) gec_yatis.mutlak_tavan = '24:00' (eskiden '00:00')",
      (b_eg.get("gec_yatis") or {}).get("mutlak_tavan") == "24:00",
      str(b_eg.get("gec_yatis")))
check("D2) _fmt_sinir: 1440→24:00, 1200→20:00",
      pa._fmt_sinir(1440) == "24:00" and pa._fmt_sinir(1200) == "20:00")
check("D3) Blok saatleri gece yarısı sonrası 00:xx (sarma korunuyor)",
      pa._fmt(1450) == "00:10" and pa._fmt(1440) == "00:00")
check("D4) Gece bloğunun bitişi ertesi sabah (HH:MM, sarılmış)",
      b_eg.get("end") == "07:00", str(b_eg.get("end")))


# =============================================================================
# B + C) Sabah hedefi farkı → not; tolerans kural başına
# =============================================================================
def gun(sablon_wake: int, yas_ay: float, h: int, m: int,
        tek_uyku: bool | None = None) -> dict:
    sablon = pa.plan_sablonu(plan(sablon_wake), BUCKET_8AY)[0]
    return pa.recompute_day(sablon, None, sablon_wake, uyanis(h, m), NOW,
                            gun=TODAY, bucket_params=BUCKET_8AY,
                            sabah_hedefi=yas_bantlari.sabah_hedefi(yas_ay, tek_uyku))


def notlu(r: dict) -> bool:
    return bool(r["adaptation"]["notlar"])


h8 = yas_bantlari.sabah_hedefi(8)
h95 = yas_bantlari.sabah_hedefi(9.5)
check("C0) Tolerans tablodan: 8 ay 06:00/30 dk, 9.5 ay 08:00/0 dk, 12 ay tek uyku 0",
      (h8["hedef"], h8["uyari_toleransi_dk"]) == ("06:00", 30)
      and (h95["en_gec"], h95["uyari_toleransi_dk"]) == ("08:00", 0)
      and yas_bantlari.sabah_hedefi(12, True)["uyari_toleransi_dk"] == 0
      and yas_bantlari.sabah_hedefi(12, False)["uyari_toleransi_dk"] == 30)

r_0625 = gun(6 * 60, 8, 6, 25)
r_0630 = gun(6 * 60, 8, 6, 30)
r_0645 = gun(6 * 60, 8, 6, 45)
check("C1) 8 ay (katı 06:00): 06:25 kalkış → not YOK", not notlu(r_0625),
      str(r_0625["adaptation"]["notlar"]))
check("C2) 8 ay: 06:30 kalkış (tam sınır) → not YOK", not notlu(r_0630))
check("C3) 8 ay: 06:45 kalkış → not VAR", notlu(r_0645),
      str(r_0645["adaptation"]))

r_0759 = gun(7 * 60, 9.5, 7, 59)
r_0800 = gun(7 * 60, 9.5, 8, 0)
r_0810 = gun(7 * 60, 9.5, 8, 10)
check("C4) 9.5 ay (08:00'e kadar): 07:59 → not YOK", not notlu(r_0759))
check("C5) 9.5 ay: 08:00 → not YOK (tolerans 0, sınır dahil)", not notlu(r_0800))
check("C6) 9.5 ay: 08:10 → not VAR (08:00 sonrası)", notlu(r_0810),
      str(r_0810["adaptation"]["notlar"]))
r_tek = gun(7 * 60, 13, 8, 10, tek_uyku=True)
check("C7) Tek uyku: 08:10 → not VAR, 07:45 → YOK",
      notlu(r_tek) and not notlu(gun(7 * 60, 13, 7, 45, tek_uyku=True)))

_not = r_0645["adaptation"]["notlar"][0]
check("B1) Not sabah bloğunun note alanında",
      _not in (blok(r_0645, "wake").get("note") or ""),
      str(blok(r_0645, "wake")))
check("B2) Not uyarilar'da (Dikkat kartı) YOK",
      not any("sabah hedefi" in u for u in r_0645["adaptation"]["uyarilar"]),
      str(r_0645["adaptation"]["uyarilar"]))
check("B3) Günde bir kez: notlar listesinde tek cümle",
      len(r_0645["adaptation"]["notlar"]) == 1)
check("B4) Not metni saati ve hedefi söylüyor",
      "06:45" in _not and "06:00" in _not, _not)

# adapt() yolu: reasons'a girmiyor, wake notu gelecek-not temizliğinden sağ çıkıyor.
# Şablon tablodan (yas_ay=8) kurulur; aksi hâlde bant ihlali yeniden üretim ister.
r_ad = pa.adapt({"type": "egitim_plani",
                 "schedule": pa.build_schedule(BUCKET_8AY, 6 * 60, yas_ay=8)},
                BUCKET_8AY, uyanis(6, 45), today=TODAY, now_minute=8 * 60, yas_ay=8)
check("B4b) adapt(): yeniden üretim istenmedi (şablon banda uygun)",
      r_ad["regenerate_required"] is False, str(r_ad["reasons"]))
check("B5) adapt(): sabah notu reasons'a girmiyor",
      not any("sabah hedefi" in s for s in r_ad["reasons"]), str(r_ad["reasons"]))
check("B6) adapt(): wake bloğunda not + adaptation.notlar dolu",
      "sabah hedefi" in (blok(r_ad, "wake").get("note") or "")
      and (r_ad["adaptation"] or {}).get("notlar"),
      f"wake={blok(r_ad, 'wake')} notlar={(r_ad['adaptation'] or {}).get('notlar')}")

# =============================================================================
# E) Aşama geçişi kuralı config'te (uygulanmadı)
# =============================================================================
_ag = yas_bantlari._tablo()["evrensel_kurallar"].get("asama_gecisi") or {}
check("E1) asama_gecisi: 7 gün, 7 gün mola, sonra program en baştan",
      _ag.get("asamada_en_fazla_gun") == 7
      and _ag.get("ilerlenemezse") == {"mola_gun": 7, "sonra": "program_bastan"},
      str(_ag))
check("E2) asama_gecisi 'HENÜZ UYGULANMADI' işaretli",
      "HENÜZ UYGULANMADI" in (_ag.get("uygulama_durumu") or ""))

# =============================================================================
# F) K20 motor dışında da: summarize_logs + sohbet bağlamı
# =============================================================================
import os                                     # noqa: E402
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.setdefault("ENVIRONMENT", "development")
from api.services import baby_context as bc   # noqa: E402

GUN = TODAY
parcali = [FakeLog("nap", _utc(14, 0), _utc(14, 36)),
           FakeLog("nap", _utc(14, 36), _utc(15, 46)),        # bitişik parça
           FakeLog("sleep", _utc(10, 0), _utc(11, 10))]       # gündüz, tipi sleep
oz = pa.summarize_logs(parcali, today=GUN)
check("F1) summarize_logs: bitişik parçalar tek uyku (2 uyku, 3 değil)",
      oz["avg_nap_count"] == 2.0, str(oz))
check("F2) summarize_logs: birleşik süre 106 dk ortalamaya giriyor",
      oz["avg_nap_minutes"] == round((70 + 106) / 2, 1), str(oz["avg_nap_minutes"]))

g_uyku = bc._gunduz_uykulari(parcali, GUN, TZ)
check("F3) Sohbet bağlamı: gündüz 'sleep' kaydı sayılıyor + parçalar birleşik",
      g_uyku == [(10 * 60, 11 * 60 + 10), (14 * 60, 15 * 60 + 46)], str(g_uyku))
_oz = bc._gun_ozeti("bugün", parcali, TZ, None, g_uyku)
check("F4) Sohbet bağlamı metni: 'şekerleme 2 (10:00-11:10, 14:00-15:46)'",
      "şekerleme 2 (10:00-11:10, 14:00-15:46)" in (_oz or ""), str(_oz))
_eski = bc._gun_ozeti("bugün", [FakeLog("sleep", _utc(11, 2), _utc(13, 6))],
                      TZ, None, bc._gunduz_uykulari(
                          [FakeLog("sleep", _utc(11, 2), _utc(13, 6))], GUN, TZ))
check("F5) Prod vakası: 11:02-13:06 'sleep' artık şekerleme (yatış DEĞİL)",
      "şekerleme 1 (11:02-13:06)" in (_eski or "") and "yatış" not in (_eski or ""),
      str(_eski))
_aksam = [FakeLog("sleep", _utc(19, 40), _utc(23, 50))]
check("F6) Akşam 19:40 gece uykusu hâlâ yatış olarak yazılıyor",
      "gece yatış 19:40" in (bc._gun_ozeti("bugün", _aksam, TZ, None,
                                           bc._gunduz_uykulari(_aksam, GUN, TZ)) or ""))

# =============================================================================
# G) K21 — günlerce açık kalıp sonra durdurulmuş (KAPALI gelen) sayaç
# =============================================================================
# Prod 2026-09-30: 18:50'de başlayıp 2 gün sonra 21:21'de durdurulan kayıt günü
# 21:21'den kuruyordu (1. gündüz uykusu 23:51) ve gerçek 07:10 uyanış kaydını
# "gereksiz" diye atıyordu.
def _gun_gece(bas_gun_once: int, bh: int, bm: int, eh: int, em: int) -> datetime:
    return _utc(bh, bm) - timedelta(days=bas_gun_once), _utc(eh, em)

_b, _e = _gun_gece(2, 18, 50, 21, 21)
_dev = [FakeLog("sleep", _b, _e)]
r_dev = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, _dev, today=TODAY, now_minute=21 * 60 + 30)
check("G1) 2 günlük sayaç (21:21'de durdu) sabah uyanışı DEĞİL",
      r_dev["adaptation"]["sabah_uyanis_gercek"] != "21:21"
      and blok(r_dev, "nap_1")["start_minute"] < 12 * 60,
      f"sabah={r_dev['adaptation']['sabah_uyanis_gercek']} nap_1={blok(r_dev, 'nap_1')['time']}")
check("G2) Kayıt 'asiri_uzun' sebebiyle yok sayıldı (iz bırakıldı)",
      any(y["kod"] == "asiri_uzun" for y in r_dev["adaptation"]["yok_sayilan_kayitlar"]),
      str(r_dev["adaptation"]["yok_sayilan_kayitlar"]))
r_dev2 = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY,
                  _dev + [FakeLog("wake", _utc(7, 10), _utc(7, 10))],
                  today=TODAY, now_minute=9 * 60)
check("G3) Gerçek 07:10 uyanış kaydı kullanılıyor (atılmıyor)",
      r_dev2["adaptation"]["sabah_uyanis_gercek"] == "07:10"
      and r_dev2["adaptation"]["sabah_uyanis_kaynak"] == "kayit"
      and not any(y["kod"] == "gereksiz_wake"
                  for y in r_dev2["adaptation"]["yok_sayilan_kayitlar"]),
      str(r_dev2["adaptation"]))
_b, _e = _gun_gece(1, 8, 12, 6, 52)
r_sabah = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, [FakeLog("sleep", _b, _e)],
                   today=TODAY, now_minute=9 * 60)
check("G4) Sabah (06:52) durdurulan 22 saatlik sayaç eskisi gibi: bitiş sabah uyanışı",
      r_sabah["adaptation"]["sabah_uyanis_gercek"] == "06:52"
      and r_sabah["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
      str(r_sabah["adaptation"]["sabah_uyanis_gercek"]))
_b, _e = _gun_gece(1, 15, 25, 7, 10)                        # 15 sa 45 dk — sınır altı
r_sinir = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, [FakeLog("nap", _b, _e)],
                   today=TODAY, now_minute=9 * 60)
check("G5) 16 saatin altındaki uzun gece (15:25→07:10) eskisi gibi gece uykusu",
      r_sinir["adaptation"]["sabah_uyanis_gercek"] == "07:10"
      and not r_sinir["adaptation"]["yok_sayilan_kayitlar"],
      str(r_sinir["adaptation"]["yok_sayilan_kayitlar"]))
_b, _e = _gun_gece(1, 20, 30, 6, 45)
r_normal = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, [FakeLog("sleep", _b, _e)],
                    today=TODAY, now_minute=9 * 60)
check("G6) Normal gece (20:30→06:45) değişmedi",
      r_normal["adaptation"]["sabah_uyanis_gercek"] == "06:45")
_oz_dev = pa.summarize_logs(_dev, today=TODAY)
check("G7) summarize_logs: 2 günlük sayaç ne sabah uyanışı ne gündüz uykusu",
      _oz_dev["gunun_uyanisi"] is None and _oz_dev["avg_nap_count"] is None, str(_oz_dev))

# =============================================================================
# H) K21.2 — SABAH biten 16 saat üstü kayıt (prod 2026-10-03: 03:42 → ertesi 07:37)
# =============================================================================
_b, _e = _gun_gece(1, 3, 42, 7, 37)                          # 27 sa 55 dk
_uzun = [FakeLog("sleep", _b, _e)]
r_uz = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY, _uzun, today=TODAY,
                now_minute=9 * 60)
_ad = r_uz["adaptation"]
check("H1) Bitiş (07:37) sabah uyanışı olarak kullanılıyor",
      _ad["sabah_uyanis_gercek"] == "07:37" and _ad["sabah_uyanis_kaynak"] == "kayit",
      str(_ad["sabah_uyanis_gercek"]))
check("H2) Gece uykusu süresi hesaplanmıyor (brüt/net yok)",
      not _ad["gece_uykusu"] or _ad["gece_uykusu"].get("brut_dk") is None,
      str(_ad["gece_uykusu"]))
check("H3) adaptation.asiri_uzun_kayitlar dolu (27 sa 55 dk) + uyarı",
      [x["sure_dk"] for x in _ad["asiri_uzun_kayitlar"]] == [27 * 60 + 55]
      and any("saymadık" in u for u in (r_uz.get("uyarilar") or []) + list(_ad.get("uyarilar") or [])),
      f'{_ad["asiri_uzun_kayitlar"]} / {r_uz.get("uyarilar")}')
_gk = pa.gun_kayitlari(_uzun, TODAY, pa.DEFAULT_WAKE_MIN)
_gk_dun = pa.gun_kayitlari(_uzun, TODAY - timedelta(days=1), pa.DEFAULT_WAKE_MIN)
check("H4) gun_kayitlari: bugüne _asiri_uzun işaretli, başladığı güne hiç bağlanmıyor",
      [bool(g.get("_asiri_uzun")) for g in _gk["gece_uykulari"]] == [True]
      and not _gk_dun["gece_uykulari"] and not _gk_dun["gunduz_uykulari"],
      f'{_gk["gece_uykulari"]} / {_gk_dun}')
# Gündüz saatinde başlayıp ertesi sabah biten sayaç başladığı güne 18 saatlik
# "şekerleme" olarak da yazılmamalı.
_b, _e = _gun_gece(1, 14, 20, 8, 25)
_gk2 = pa.gun_kayitlari([FakeLog("nap", _b, _e)], TODAY - timedelta(days=1),
                        pa.DEFAULT_WAKE_MIN)
check("H5) 14:20 → ertesi 08:25 başladığı güne gündüz uykusu olarak girmiyor",
      not _gk2["gunduz_uykulari"], str(_gk2["gunduz_uykulari"]))
# Aynı gece için doğru kayıt da varsa aşırı uzun kayıt ATILIR (K13 onu tutardı).
_b2, _e2 = _gun_gece(1, 20, 0, 7, 0)
r_iki = pa.adapt(plan(type="egitim_plani"), BUCKET_8AY,
                 _uzun + [FakeLog("sleep", _b2, _e2)], today=TODAY, now_minute=9 * 60)
check("H6) Doğru gece kaydı varken aşırı uzun kayıt yok sayılır, gece 11 sa",
      r_iki["adaptation"]["gece_uykusu"]["brut_dk"] == 11 * 60
      and any(y["kod"] == "asiri_uzun" for y in r_iki["adaptation"]["yok_sayilan_kayitlar"]),
      str(r_iki["adaptation"]["gece_uykusu"]))
_oz_uz = pa.summarize_logs(_uzun, today=TODAY)
check("H7) summarize_logs: yalnız uyanış (07:37), gündüz uykusu yok",
      _oz_uz["gunun_uyanisi"] == 7 * 60 + 37 and _oz_uz["avg_nap_count"] is None,
      str(_oz_uz))

# =============================================================================
print("=" * 78)
print("v1.6 KARARLARI")
print("=" * 78)
gecen = 0
for ad, ok, detay in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {ad}")
    if not ok and detay:
        print(f"         → {detay[:400]}")
    gecen += ok
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
print("=" * 78)
sys.exit(0 if gecen == len(results) else 1)
