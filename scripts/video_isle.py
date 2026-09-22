"""
Eğitim videosu ön işleme — sıkıştırma, poster, süre. AĞ YOK, DB YOK.

`scripts/video_yukle.py` bunu kullanır; tek başına da çalıştırılabilir:

    python scripts/video_isle.py              # tümünü işle, tablo bas
    python scripts/video_isle.py --kuru-kosu  # sadece planı göster

TASARIM KARARLARI (kaynak videolar ölçülerek alındı, 2026-09-22):

1. **Ölçekleme YOK.** Kaynaklar 576x1024 (dikey / Reels formatı), 1080p değil.
   `scale=-2:1080` bunları BÜYÜTÜRDÜ: dosya şişer, tek piksel detay eklenmez.
   Kaynak yüksekliği HEDEF_YUKSEKLIK'in üzerindeyse küçültme yapılır, altındaysa
   dokunulmaz.

2. **Yeniden kodlama dosyayı büyütüyorsa yapılmaz.** Kaynaklar zaten ~0.9-1.3
   Mbps'e sıkıştırılmış h264. CRF 20 ile yeniden kodlamak bazılarında dosyayı
   BÜYÜTÜP üstüne bir nesil daha kalite kaybı bindiriyor. Bu durumda video ve
   ses akışı OLDUĞU GİBİ kopyalanır (`-c copy`) ve yalnız `+faststart` eklenir —
   kayıpsızdır ve akış için gereken tek şey zaten odur.

3. **Poster kaynak en/boy oranını korur.** Dikey videoya 1280x720 poster
   basılamaz (ya bozar ya siyah bant ekler). Poster kaynak çözünürlüğünde
   üretilir; kaynak HEDEF_YUKSEKLIK'ten büyükse oranı koruyarak küçültülür.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KAYNAK_KLASOR = Path(os.getenv("VIDEO_KAYNAK")
                     or r"C:\Users\Mert KORAL\tavsan-videolar")
CIKTI_KLASOR = KAYNAK_KLASOR / "cikti"
CSV_ADI = "videolar.csv"

HEDEF_YUKSEKLIK = 1080          # bunun ÜZERİ küçültülür, altına dokunulmaz
CRF = 20
PRESET = "slow"
SES_BITRATE = "128k"
POSTER_SANIYE = 3
POSTER_KALITE = 2               # ffmpeg -q:v 2 ≈ jpeg q90


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe bulma — winget kurulumu PATH'i ancak yeni oturumda günceller
# ---------------------------------------------------------------------------
def _arac(ad: str) -> str:
    bulunan = shutil.which(ad)
    if bulunan:
        return bulunan
    kokler = [Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
              / "Packages"]
    for kok in kokler:
        if not kok.exists():
            continue
        for yol in kok.rglob(f"{ad}.exe"):
            return str(yol)
    raise RuntimeError(
        f"{ad} bulunamadı. Kurulum: winget install --id Gyan.FFmpeg --exact")


FFMPEG = None
FFPROBE = None


def araclari_hazirla() -> None:
    global FFMPEG, FFPROBE
    if FFMPEG is None:
        FFMPEG, FFPROBE = _arac("ffmpeg"), _arac("ffprobe")


def _kos(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def video_bilgisi(yol: Path) -> dict:
    """{'sure_sn': float, 'genislik': int, 'yukseklik': int, 'boyut': int}"""
    araclari_hazirla()
    p = _kos([FFPROBE, "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height",
              "-show_entries", "format=duration",
              "-of", "json", str(yol)])
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe hatası ({yol.name}): {p.stderr.strip()[:200]}")
    d = json.loads(p.stdout)
    akis = (d.get("streams") or [{}])[0]
    return {
        "sure_sn": float((d.get("format") or {}).get("duration") or 0),
        "genislik": int(akis.get("width") or 0),
        "yukseklik": int(akis.get("height") or 0),
        "boyut": yol.stat().st_size,
    }


def _kodla(kaynak: Path, hedef: Path, bilgi: dict) -> None:
    argv = [FFMPEG, "-y", "-i", str(kaynak),
            "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF)]
    if bilgi["yukseklik"] > HEDEF_YUKSEKLIK:      # yalnız KÜÇÜLTME
        argv += ["-vf", f"scale=-2:{HEDEF_YUKSEKLIK}"]
    argv += ["-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-c:a", "aac", "-b:a", SES_BITRATE, str(hedef)]
    p = _kos(argv)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg hatası ({kaynak.name}): {p.stderr.strip()[-400:]}")


def _kayipsiz_kopyala(kaynak: Path, hedef: Path) -> None:
    """Akışlara DOKUNMADAN yeniden paketle — tek kazanç +faststart."""
    p = _kos([FFMPEG, "-y", "-i", str(kaynak), "-c", "copy",
              "-movflags", "+faststart", str(hedef)])
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg remux hatası ({kaynak.name}): "
                           f"{p.stderr.strip()[-400:]}")


def _poster(kaynak: Path, hedef: Path, bilgi: dict) -> None:
    an = min(POSTER_SANIYE, max(0.0, bilgi["sure_sn"] - 0.5))
    argv = [FFMPEG, "-y", "-ss", f"{an:.2f}", "-i", str(kaynak), "-frames:v", "1"]
    if bilgi["yukseklik"] > HEDEF_YUKSEKLIK:      # oran KORUNUR
        argv += ["-vf", f"scale=-2:{HEDEF_YUKSEKLIK}"]
    argv += ["-q:v", str(POSTER_KALITE), str(hedef)]
    p = _kos(argv)
    if p.returncode != 0:
        raise RuntimeError(f"poster hatası ({kaynak.name}): {p.stderr.strip()[-400:]}")


def isle(kaynak: Path, slug: str, cikti_klasor: Path = CIKTI_KLASOR,
         zorla: bool = False) -> dict:
    """Tek videoyu işle. Çıktı varsa ATLA (idempotent).

    Dönen: {slug, dosya, orijinal_mb, cikis_mb, sure_sn, yontem, video, poster}
    """
    araclari_hazirla()
    cikti_klasor.mkdir(parents=True, exist_ok=True)
    hedef = cikti_klasor / f"{slug}.mp4"
    poster = cikti_klasor / f"{slug}.jpg"
    bilgi = video_bilgisi(kaynak)

    if hedef.exists() and poster.exists() and not zorla:
        return {"slug": slug, "dosya": kaynak.name,
                "orijinal_mb": bilgi["boyut"] / 1e6,
                "cikis_mb": hedef.stat().st_size / 1e6,
                "sure_sn": bilgi["sure_sn"], "yontem": "atlandı",
                "cozunurluk": f'{bilgi["genislik"]}x{bilgi["yukseklik"]}',
                "video": hedef, "poster": poster}

    gecici = cikti_klasor / f"{slug}.kodlaniyor.mp4"
    _kodla(kaynak, gecici, bilgi)
    kodlanmis = gecici.stat().st_size

    if kodlanmis >= bilgi["boyut"]:
        # Yeniden kodlama KAZANÇ SAĞLAMADI → kayıpsız yola dön.
        gecici.unlink(missing_ok=True)
        _kayipsiz_kopyala(kaynak, hedef)
        yontem = "kayıpsız (yeniden kodlama büyütüyordu)"
    else:
        gecici.replace(hedef)
        yontem = f"x264 crf{CRF}"

    _poster(kaynak, poster, bilgi)
    return {"slug": slug, "dosya": kaynak.name,
            "orijinal_mb": bilgi["boyut"] / 1e6,
            "cikis_mb": hedef.stat().st_size / 1e6,
            "sure_sn": bilgi["sure_sn"], "yontem": yontem,
            "cozunurluk": f'{bilgi["genislik"]}x{bilgi["yukseklik"]}',
            "video": hedef, "poster": poster}


def manifest_oku(klasor: Path = KAYNAK_KLASOR) -> list[dict]:
    """videolar.csv → satır sözlükleri. Eksik dosyalar `_var` ile işaretlenir."""
    yol = klasor / CSV_ADI
    if not yol.exists():
        raise SystemExit(f"{yol} yok — önce manifesti oluşturun.")
    with open(yol, encoding="utf-8-sig", newline="") as f:
        satirlar = list(csv.DictReader(f))
    for s in satirlar:
        s["_yol"] = klasor / (s.get("dosya") or "")
        s["_var"] = bool(s.get("dosya")) and s["_yol"].exists()
        s["etiketler"] = [e.strip() for e in
                          (s.get("asama_etiketleri") or "").split(",") if e.strip()]
        s["sira"] = int(s.get("sira") or 0)
    return satirlar


def _tablo(sonuclar: list[dict]) -> None:
    print(f'\n{"dosya":58s} {"çözünürlük":>11s} {"orijinal":>9s} '
          f'{"çıkış":>8s} {"süre":>7s}  yöntem')
    print("-" * 118)
    t_o = t_c = 0.0
    for r in sonuclar:
        ad = r["dosya"]
        if len(ad) > 57:
            ad = ad[:54] + "..."
        dk, sn = divmod(int(r["sure_sn"]), 60)
        print(f'{ad:58s} {r["cozunurluk"]:>11s} {r["orijinal_mb"]:8.1f}M '
              f'{r["cikis_mb"]:7.1f}M {dk:4d}:{sn:02d}  {r["yontem"]}')
        t_o += r["orijinal_mb"]
        t_c += r["cikis_mb"]
    print("-" * 118)
    kazanc = (1 - t_c / t_o) * 100 if t_o else 0
    print(f'{"TOPLAM":58s} {"":>11s} {t_o:8.1f}M {t_c:7.1f}M '
          f'          kazanç %{kazanc:.1f}')


def main() -> int:
    ap = argparse.ArgumentParser(description="Eğitim videolarını sıkıştır + poster üret")
    ap.add_argument("--kuru-kosu", action="store_true",
                    help="hiçbir şey üretme, yalnız planı göster")
    ap.add_argument("--zorla", action="store_true",
                    help="çıktı varsa bile yeniden üret")
    a = ap.parse_args()

    satirlar = manifest_oku()
    eksik = [s for s in satirlar if not s["_var"]]
    if eksik:
        print("UYARI — CSV'de olup klasörde bulunamayan dosyalar:")
        for s in eksik:
            print(f'   {s["slug"]}: {s.get("dosya") or "(boş)"}')

    islenecek = [s for s in satirlar if s["_var"]]
    if a.kuru_kosu:
        print(f"\nKURU KOŞU — {len(islenecek)} video işlenecekti:")
        for s in islenecek:
            hedef = CIKTI_KLASOR / f'{s["slug"]}.mp4'
            print(f'   {s["slug"]:30s} {"ATLANIR" if hedef.exists() else "işlenir"}')
        return 0

    sonuclar = []
    for i, s in enumerate(islenecek, 1):
        print(f'[{i}/{len(islenecek)}] {s["slug"]} …', flush=True)
        sonuclar.append(isle(s["_yol"], s["slug"], zorla=a.zorla))
    _tablo(sonuclar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
