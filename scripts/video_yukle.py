"""
Eğitim videosu yükleme betiği — TEK KOMUT, BUILD GEREKTİRMEZ.

    python scripts/video_yukle.py                 # yeni/değişmiş olanları yükle
    python scripts/video_yukle.py --kuru-kosu     # ne yapacağını göster, dokunma
    python scripts/video_yukle.py --yerel         # prod yerine yerel depoya yaz

AKIŞ (yeni video eklemek):
    1. Dosyayı C:\\Users\\Mert KORAL\\tavsan-videolar\\ klasörüne at.
    2. videolar.csv'ye bir satır ekle (dosya, slug, baslik, kategori,
       asama_etiketleri, aciklama, sira).
    3. Bu betiği çalıştır. Bitti — deploy YOK, build YOK.

NE YAPAR:
    • CSV'yi okur, kataloğu (education_videos) karşılaştırır,
    • yeni/eksik videoyu sıkıştırır + poster üretir (scripts/video_isle.py),
    • dosyaları Railway volume'una yükler (railway ssh üzerinden),
    • education_videos'a slug bazlı upsert eder (süre ffprobe'dan).
    Var olan dosya ATLANIR; betik istediğin kadar tekrar çalıştırılabilir.

NEDEN `railway ssh + base64`: volume'a dışarıdan yazan bir uç YOK ve olmamalı
(yükleme ucu açmak, kimlik doğrulaması ne olursa olsun, diske yazma yüzeyi
açmaktır). Betik dosyayı parça parça stdin'den konteynere akıtır; sunucuda
kalıcı bir yükleme ucu bırakmaz.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from scripts import video_isle                                   # noqa: E402

# Konteynerdeki venv — `python` nix profilindekini bulur ve sqlalchemy görmez.
UZAK_PYTHON = "/opt/venv/bin/python"
# Konteynerde volume kökü. MEDIA_ROOT env'i ile aynı olmalı.
UZAK_MEDIA_ROOT_VARSAYILAN = "/data/media"
PARCA = 3 * 1024 * 1024          # base64 öncesi parça boyutu


# ---------------------------------------------------------------------------
# Uzak taraf (railway ssh)
# ---------------------------------------------------------------------------
def _railway_yolu() -> str:
    """`railway` çalıştırılabiliri. Windows'ta bu bir .cmd kabuğudur ve
    subprocess `["railway", ...]` ile onu BULAMAZ (WinError 2)."""
    import shutil
    for ad in ("railway", "railway.cmd", "railway.exe"):
        bulunan = shutil.which(ad)
        if bulunan:
            return bulunan
    raise RuntimeError("railway CLI bulunamadı — `npm i -g @railway/cli` "
                       "ya da `railway login` yapılmış mı?")


def _railway(komut: str, girdi: bytes | None = None,
             zaman_asimi: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([_railway_yolu(), "ssh", komut], input=girdi,
                          capture_output=True, timeout=zaman_asimi)


def _uzak_python(kod: str, zaman_asimi: int = 600) -> str:
    """Konteynerde Python kodu çalıştır (base64 ile — tırnak cehennemi yok)."""
    b64 = base64.b64encode(kod.encode("utf-8")).decode()
    komut = (f"{UZAK_PYTHON} -c \"import base64;"
             f"exec(compile(base64.b64decode('{b64}').decode('utf-8'),"
             f"'uzak','exec'))\"")
    p = _railway(komut, zaman_asimi=zaman_asimi)
    cikti = (p.stdout or b"").decode("utf-8", "replace")
    if p.returncode != 0:
        hata = (p.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError(f"railway ssh hatası:\n{cikti[-800:]}\n{hata[-800:]}")
    return cikti


def uzak_dosya_var_mi(yollar: list[str]) -> dict[str, int]:
    """Volume'da var olan dosyalar → {depo_yolu: boyut}."""
    kod = f'''
import json, os
kok = os.getenv("MEDIA_ROOT") or {UZAK_MEDIA_ROOT_VARSAYILAN!r}
out = {{}}
for y in {yollar!r}:
    p = os.path.join(kok, y)
    if os.path.exists(p):
        out[y] = os.path.getsize(p)
print("JSON" + json.dumps(out))
'''
    return _cikti_json(_uzak_python(kod))


def uzak_yukle(yerel: Path, depo_yolu: str) -> int:
    """Dosyayı parça parça volume'a yaz. Önce .tmp'ye, sonra taşı."""
    kod = f'''
import base64, os, sys
kok = os.getenv("MEDIA_ROOT") or {UZAK_MEDIA_ROOT_VARSAYILAN!r}
hedef = os.path.join(kok, {depo_yolu!r})
os.makedirs(os.path.dirname(hedef), exist_ok=True)
gecici = hedef + ".tmp"
with open(gecici, "wb") as f:
    for satir in sys.stdin:
        satir = satir.strip()
        if satir:
            f.write(base64.b64decode(satir))
os.replace(gecici, hedef)
print("JSON{{\\"boyut\\": %d}}" % os.path.getsize(hedef))
'''
    b64 = base64.b64encode(kod.encode("utf-8")).decode()
    komut = (f"{UZAK_PYTHON} -c \"import base64;"
             f"exec(compile(base64.b64decode('{b64}').decode('utf-8'),"
             f"'uzak','exec'))\"")
    govde = bytearray()
    with yerel.open("rb") as f:
        while True:
            blok = f.read(PARCA)
            if not blok:
                break
            govde += base64.b64encode(blok) + b"\n"
    p = _railway(komut, girdi=bytes(govde), zaman_asimi=1800)
    cikti = (p.stdout or b"").decode("utf-8", "replace")
    if p.returncode != 0:
        raise RuntimeError(f"yükleme hatası ({depo_yolu}):\n{cikti[-500:]}\n"
                           f"{(p.stderr or b'').decode('utf-8', 'replace')[-500:]}")
    return int(_cikti_json(cikti).get("boyut") or 0)


def uzak_katalog_upsert(satirlar: list[dict], sureler: dict[str, int]) -> dict:
    """education_videos'u konteynerin KENDİ kodu ve DB'si ile upsert et."""
    kod = f'''
import json, sys
sys.path.insert(0, "/app")
from api.db import SessionLocal
from api.services import education
db = SessionLocal()
try:
    sonuc = education.katalog_upsert(db, {satirlar!r}, {sureler!r})
finally:
    db.close()
print("JSON" + json.dumps(sonuc))
'''
    return _cikti_json(_uzak_python(kod))


def _cikti_json(cikti: str) -> dict:
    for satir in reversed(cikti.splitlines()):
        if satir.startswith("JSON"):
            return json.loads(satir[4:])
    raise RuntimeError(f"uzak taraftan JSON gelmedi:\n{cikti[-500:]}")


# ---------------------------------------------------------------------------
# Yerel taraf
# ---------------------------------------------------------------------------
def yerel_katalog_upsert(satirlar: list[dict], sureler: dict[str, int]) -> dict:
    from api.db import SessionLocal
    from api.services import education
    db = SessionLocal()
    try:
        return education.katalog_upsert(db, satirlar, sureler)
    finally:
        db.close()


def yerel_yukle(yerel: Path, depo_yolu: str) -> int:
    from api.services import storage
    return storage.depo().yaz(depo_yolu, yerel.read_bytes())


def yerel_dosya_var_mi(yollar: list[str]) -> dict[str, int]:
    from api.services import storage
    d = storage.depo()
    return {y: d.boyut(y) for y in yollar if d.var_mi(y)}


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Eğitim videolarını sıkıştır, yükle, kataloğa işle")
    ap.add_argument("--kuru-kosu", action="store_true",
                    help="hiçbir şey üretme/yükleme, yalnız planı göster")
    ap.add_argument("--yerel", action="store_true",
                    help="Railway yerine YEREL depoya ve yerel DB'ye yaz")
    ap.add_argument("--zorla", action="store_true",
                    help="volume'da dosya olsa bile yeniden yükle")
    a = ap.parse_args()

    from api.services import storage

    satirlar = video_isle.manifest_oku()
    eksik = [s for s in satirlar if not s["_var"]]
    islenecek = [s for s in satirlar if s["_var"]]
    if eksik:
        print("UYARI — CSV'de olup klasörde bulunamayan dosyalar:")
        for s in eksik:
            print(f'   {s["slug"]}: {s.get("dosya") or "(boş)"}')

    yollar = []
    for s in islenecek:
        yollar += [storage.video_yolu(s["slug"]), storage.poster_yolu(s["slug"])]

    var_mi = yerel_dosya_var_mi if a.yerel else uzak_dosya_var_mi
    yukle = yerel_yukle if a.yerel else uzak_yukle
    upsert = yerel_katalog_upsert if a.yerel else uzak_katalog_upsert

    print(f"Depo: {'YEREL' if a.yerel else 'Railway volume'} · "
          f"{len(islenecek)} video manifestte")
    mevcut = var_mi(yollar)

    gerekli = []
    for s in islenecek:
        v, p = storage.video_yolu(s["slug"]), storage.poster_yolu(s["slug"])
        if a.zorla or v not in mevcut or p not in mevcut:
            gerekli.append(s)

    print(f"Depoda var: {len(mevcut)}/{len(yollar)} dosya · "
          f"yüklenecek video: {len(gerekli)}")

    if a.kuru_kosu:
        print("\nKURU KOŞU — yapılacaklar:")
        for s in islenecek:
            v = storage.video_yolu(s["slug"])
            durum = "YÜKLENİR" if s in gerekli else "atlanır (depoda var)"
            print(f'   {s["slug"]:30s} {durum}')
        print("\nKatalog upsert TÜM satırlar için koşardı "
              f"({len(islenecek)} satır).")
        return 0

    sureler: dict[str, int] = {}
    for i, s in enumerate(gerekli, 1):
        print(f'[{i}/{len(gerekli)}] {s["slug"]} — sıkıştırılıyor…', flush=True)
        r = video_isle.isle(s["_yol"], s["slug"])
        sureler[s["slug"]] = int(round(r["sure_sn"]))
        print(f'    {r["orijinal_mb"]:.1f}M → {r["cikis_mb"]:.1f}M ({r["yontem"]}) '
              f'· yükleniyor…', flush=True)
        yukle(r["video"], storage.video_yolu(s["slug"]))
        yukle(r["poster"], storage.poster_yolu(s["slug"]))
        print("    yüklendi.", flush=True)

    # Süresi bilinmeyen (bu turda yüklenmemiş) videolar için ffprobe'u yerelden
    # oku — katalogdaki duration_sec 0'da kalmasın.
    for s in islenecek:
        if s["slug"] not in sureler:
            hedef = video_isle.CIKTI_KLASOR / f'{s["slug"]}.mp4'
            kaynak = hedef if hedef.exists() else s["_yol"]
            try:
                sureler[s["slug"]] = int(round(
                    video_isle.video_bilgisi(kaynak)["sure_sn"]))
            except Exception as e:                  # ffmpeg yoksa süreyi atla
                print(f'    (süre okunamadı: {s["slug"]}: {e})')

    kayitlar = [{k: s[k] for k in ("slug", "title", "description", "category",
                                   "order_in_category", "stage_tags")}
                for s in _kayitlara_cevir(islenecek)]
    sonuc = upsert(kayitlar, sureler)
    print(f'\nKatalog: {len(sonuc["eklenen"])} eklendi, '
          f'{len(sonuc["guncellenen"])} güncellendi.')
    if sonuc["eklenen"]:
        print("   eklenen:", ", ".join(sonuc["eklenen"]))
    if sonuc["guncellenen"]:
        print("   güncellenen:", ", ".join(sonuc["guncellenen"]))
    return 0


def _kayitlara_cevir(satirlar: list[dict]) -> list[dict]:
    """CSV satırı → katalog_upsert'ün beklediği biçim."""
    from api.services.education import GENEL
    out = []
    for s in satirlar:
        out.append({
            "slug": s["slug"],
            "title": (s.get("baslik") or "").strip(),
            "description": (s.get("aciklama") or "").strip() or None,
            "category": (s.get("kategori") or "").strip(),
            "order_in_category": int(s.get("sira") or 0),
            "stage_tags": s["etiketler"] or [GENEL],
        })
    return out


if __name__ == "__main__":
    sys.exit(main())
