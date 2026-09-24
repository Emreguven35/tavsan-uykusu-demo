"""
Uyku sesi yükleme betiği — TEK KOMUT, BUILD GEREKTİRMEZ (video_yukle.py'nin ikizi).

    python scripts/ses_yukle.py                 # yeni/eksik olanları yükle
    python scripts/ses_yukle.py --kuru-kosu     # ne yapacağını göster, dokunma
    python scripts/ses_yukle.py --yerel         # prod yerine yerel depoya yaz

AKIŞ (yeni ses eklemek):
    1. C:\\Users\\Mert KORAL\\tavsan-sesler\\sesler.csv'ye satır ekle.
    2. python scripts/ses_uret.py   → cikti/{slug}.m4a (+ doğrulama tablosu)
    3. python scripts/ses_yukle.py  → volume'a yükle + sleep_sounds upsert.

KATALOĞA YALNIZ DOSYASI OLAN SES YAZILIR: CSV'de olup henüz üretilmemiş satır
atlanır (mobilde çalmayan kart olmasın). Yükleme yolu video betiğiyle aynı
(`railway ssh` + base64 stdin) — gerekçe orada.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from scripts import ses_uret                                     # noqa: E402
from scripts.video_yukle import (_cikti_json, _uzak_python,     # noqa: E402
                                 uzak_dosya_var_mi, uzak_yukle,
                                 yerel_dosya_var_mi, yerel_yukle)

KATALOG_ALANLARI = ("slug", "title", "category", "order_in_category", "is_free")


def uzak_katalog_upsert(satirlar: list[dict], olculer: dict) -> dict:
    """sleep_sounds'u konteynerin KENDİ kodu ve DB'si ile upsert et. Veri
    stdin'den gider (Windows komut satırı sınırı — bkz. video_yukle)."""
    kod = '''
import json, sys
sys.path.insert(0, "/app")
from api.db import SessionLocal
from api.services import sounds
veri = json.loads(sys.stdin.read())
db = SessionLocal()
try:
    sonuc = sounds.katalog_upsert(db, veri["satirlar"], veri["olculer"])
finally:
    db.close()
print("JSON" + json.dumps(sonuc))
'''
    veri = json.dumps({"satirlar": satirlar, "olculer": olculer},
                      ensure_ascii=False).encode("utf-8")
    return _cikti_json(_uzak_python(kod, girdi=veri))


def yerel_katalog_upsert(satirlar: list[dict], olculer: dict) -> dict:
    from api.db import SessionLocal
    from api.services import sounds
    db = SessionLocal()
    try:
        return sounds.katalog_upsert(db, satirlar, olculer)
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Uyku seslerini yükle + kataloğa işle")
    ap.add_argument("--kuru-kosu", action="store_true")
    ap.add_argument("--yerel", action="store_true",
                    help="Railway yerine YEREL depoya ve yerel DB'ye yaz")
    ap.add_argument("--zorla", action="store_true",
                    help="volume'da dosya olsa bile yeniden yükle")
    a = ap.parse_args()

    from api.services import storage

    satirlar = ses_uret.manifest()
    cikti = ses_uret.KAYNAK / "cikti"
    hazir = [s for s in satirlar if (cikti / f'{s["slug"]}.m4a').exists()]
    eksik = [s["slug"] for s in satirlar if s not in hazir]
    if eksik:
        print("Üretilmemiş (atlanır, kataloğa YAZILMAZ):", ", ".join(eksik))

    var_mi = yerel_dosya_var_mi if a.yerel else uzak_dosya_var_mi
    yukle = yerel_yukle if a.yerel else uzak_yukle
    upsert = yerel_katalog_upsert if a.yerel else uzak_katalog_upsert

    yollar = [storage.uyku_sesi_yolu(s["slug"]) for s in hazir]
    print(f"Depo: {'YEREL' if a.yerel else 'Railway volume'} · "
          f"{len(hazir)} ses hazır")
    mevcut = var_mi(yollar)
    olculer = {}
    for s in hazir:
        dosya = cikti / f'{s["slug"]}.m4a'
        olculer[s["slug"]] = {"duration_sec": int(round(ses_uret.sure_ffprobe(dosya))),
                              "bytes": dosya.stat().st_size}
    gerekli = [s for s in hazir
               if a.zorla or mevcut.get(storage.uyku_sesi_yolu(s["slug"]))
               != olculer[s["slug"]]["bytes"]]

    if a.kuru_kosu:
        print("\nKURU KOŞU:")
        for s in hazir:
            durum = "YÜKLENİR" if s in gerekli else "atlanır (depoda aynı boyutta)"
            print(f'   {s["slug"]:22s} {olculer[s["slug"]]["bytes"] // 1024:6d} KB  {durum}')
        print(f"\nKatalog upsert {len(hazir)} satır için koşardı.")
        return 0

    for i, s in enumerate(gerekli, 1):
        print(f'[{i}/{len(gerekli)}] {s["slug"]} yükleniyor…', flush=True)
        yukle(cikti / f'{s["slug"]}.m4a', storage.uyku_sesi_yolu(s["slug"]))

    kayitlar = [{k: s[k] for k in KATALOG_ALANLARI} for s in hazir]
    sonuc = upsert(kayitlar, olculer)
    print(f'\nKatalog: {len(sonuc["eklenen"])} eklendi, '
          f'{len(sonuc["guncellenen"])} güncellendi.')
    for k in ("eklenen", "guncellenen"):
        if sonuc[k]:
            print(f"   {k}:", ", ".join(sonuc[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
