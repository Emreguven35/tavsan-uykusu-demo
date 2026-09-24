"""
Uyku sesi üretimi — 10 dk'lık, başı-sonu kusursuz birleşen mono AAC (.m4a).

    python scripts/ses_uret.py                     # eksikleri üret
    python scripts/ses_uret.py --yalniz fan,sssh   # yalnız bunlar
    python scripts/ses_uret.py --zorla             # ara çıktıları yeniden kur
    python scripts/ses_uret.py --dogrula           # üretme, çıktıları ölç

Kaynak klasör: C:\\Users\\Mert KORAL\\tavsan-sesler\\ (SES_KAYNAK env'i ile değişir)
    sesler.csv               slug, kategori, baslik, kaynak, is_free, sira
    ham/{slug}-take{n}.mp3   ElevenLabs çıktıları (ÖNBELLEK — kredi bir kez harcanır)
    ara/{slug}.f32           normalize edilmiş döngü, float32 (miks buradan yapılır)
    cikti/{slug}.m4a         yüklenecek dosya
    cikti/rapor.json         ölçümler

`kaynak` sütunu:
    noise:white|pink|brown          ffmpeg anoisesrc
    sfx:<ingilizce istem>           ElevenLabs Sound Effects, TAKE_SAYISI take
    mix:a+b[@-3]                    iki hazır sesin toplamı (b'ye dB kazancı)

BAŞKA UYGULAMADAN SES ALINMAZ — tamamı burada üretilir.

DÖNGÜ (seamless loop) nasıl kuruluyor:
  1. Take'ler (her biri ≤30 sn) eşit güçlü 2.5 sn crossfade ile zincirlenir,
     sıra döner: t1 t2 t3 t1 t2 … Hedefi XFADE_DONGU kadar AŞAN bir dizi çıkar.
  2. Dizinin FAZLA kuyruğu (N..N+X) başına eşit güçlü crossfade ile bindirilir.
     Böylece out[0] = x[N], yani dosyanın son örneği (x[N-1]) ile ilk örneği
     ardışık iki örnektir — döngü noktası matematiksel olarak dikişsizdir.
  3. Örnek sayısı 1024'ün katıdır (AAC çerçevesi): sona dolgu (padding)
     eklenmez, döngüde sessizlik boşluğu oluşmaz. Baştaki kodlayıcı gecikmesi
     mp4 edit list'iyle işaretlenir (AVPlayer/ExoPlayer uygular).

Doğrulama (her dosya): ffprobe süresi, EBU R128 bütünleşik ses yüksekliği ve
gerçek tepe (m4a'nın KENDİSİ ölçülür, ara float değil), dikiş noktasında
bant enerjisi farkı (içerideki rastgele ardışık pencerelerin dağılımıyla
kıyaslanır), dikişte örnek sıçraması, 5 ms'lik pencerelerde ani ses taraması.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import numpy as np

KOK = Path(__file__).resolve().parent.parent
KAYNAK = Path(os.getenv("SES_KAYNAK") or r"C:\Users\Mert KORAL\tavsan-sesler")
CSV_ADI = "sesler.csv"

SR = 44100
# 600 sn'ye en yakın 1024 katı: 25840 × 1024 = 26 460 160 örnek = 600.0036 sn.
N = 25840 * 1024
XFADE_TAKE = 2.5            # take'ler arası crossfade (sn)
XFADE_DONGU = 3.0           # son → baş crossfade (sn)
KIRP = 0.25                 # take başı/sonundan atılan (sn) — üretim artığı
HEDEF_LUFS = -30.0
TP_TAVAN = -3.0             # dBTP
BITRATE = "96k"

TAKE_SAYISI = 3
SFX_SURE = 30               # ElevenLabs'in izin verdiği en uzun süre (sn)
SFX_MODEL = "eleven_text_to_sound_v2"
SFX_URL = ("https://api.elevenlabs.io/v1/sound-generation"
           "?output_format=mp3_44100_192")


# ---------------------------------------------------------------------------
# ffmpeg yardımcıları
# ---------------------------------------------------------------------------
def _ffmpeg(args: list[str], girdi: bytes | None = None) -> bytes:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
                       input=girdi, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + p.stderr.decode("utf-8", "replace")[-800:])
    return p.stdout


def oku(yol: Path, highpass: bool = True) -> np.ndarray:
    """Herhangi bir ses dosyası → mono 44.1 kHz float32. 20 Hz yüksek geçiren
    süzgeç DC ve duyulmayan alt frekansı atar (kahverengi gürültü kayar)."""
    af = ["-af", "highpass=f=20"] if highpass else []
    ham = _ffmpeg(["-i", str(yol), "-ac", "1", "-ar", str(SR), *af,
                   "-f", "f32le", "-"])
    return np.frombuffer(ham, dtype=np.float32).copy()


def gurultu(renk: str, sure_sn: float, seed: int) -> np.ndarray:
    ham = _ffmpeg(["-f", "lavfi", "-i",
                   f"anoisesrc=color={renk}:sample_rate={SR}:amplitude=0.5"
                   f":duration={sure_sn}:seed={seed}",
                   "-af", "highpass=f=20", "-ac", "1", "-f", "f32le", "-"])
    return np.frombuffer(ham, dtype=np.float32).copy()


def ebur128(girdi: Path | np.ndarray) -> dict:
    """{"lufs": bütünleşik, "tp": gerçek tepe dBTP, "lra": aralık}."""
    if isinstance(girdi, np.ndarray):
        args = ["-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-"]
        veri = girdi.astype(np.float32).tobytes()
    else:
        args, veri = ["-i", str(girdi)], None
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *args,
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       input=veri, capture_output=True)
    err = p.stderr.decode("utf-8", "replace")
    ozet = err[err.rfind("Summary:"):]

    def bul(desen):
        m = re.search(desen, ozet)
        return float(m.group(1)) if m else float("nan")
    return {"lufs": bul(r"I:\s+(-?[\d.]+) LUFS"),
            "lra": bul(r"LRA:\s+(-?[\d.]+) LU"),
            "tp": bul(r"True peak:\s+Peak:\s+(-?[\d.inf]+) dBFS")}


# AAC KENARLARI: kodlayıcı dosyanın başından önce ve sonundan sonra sessizlik
# görür; son çerçevenin nicemleme gürültüsü o "sessizliğe" yayılır. Gürültüde
# duyulmaz ama fan gibi pürüzsüz bir uğultuda döngü noktasında tık bırakır
# (ölçüldü: kenar hatası 0.023, iç kısım 0.0003). Çözüm dairesel bağlam:
# başa döngünün SONUNU, sona döngünün BAŞINI ekleyip kodla, sonra fazlalığı
# yeniden kodlamadan (-c copy) at:
#   • sondaki fazla paketler -frames:a ile kesilir,
#   • baştaki fazla örnekler -itsoffset ile negatif zamana itilir; mp4
#     muxer bunları edit list'e yazar, oynatıcı atlar.
# Çıkış yine tam N örnek decode edilir (dogrula() bunu ölçer).
ON_BAGLAM = 2048
SON_BAGLAM = 4096


def kodla(x: np.ndarray, hedef: Path) -> None:
    hedef.parent.mkdir(parents=True, exist_ok=True)
    uzun = hedef.with_suffix(".uzun.m4a")
    gecici = hedef.with_suffix(".tmp.m4a")
    dairesel = np.concatenate([x[-ON_BAGLAM:], x, x[:SON_BAGLAM]])
    _ffmpeg(["-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
             "-c:a", "aac", "-b:a", BITRATE, str(uzun)],
            girdi=dairesel.astype(np.float32).tobytes())
    paket = len(x) // 1024 + 1 + ON_BAGLAM // 1024      # +1: kodlayıcı gecikmesi
    _ffmpeg(["-itsoffset", f"-{ON_BAGLAM / SR:.10f}", "-i", str(uzun),
             "-c", "copy", "-frames:a", str(paket), "-movflags", "+faststart",
             str(gecici)])
    uzun.unlink(missing_ok=True)
    gecici.replace(hedef)


def kodla_hedefe(x: np.ndarray, hedef: Path) -> None:
    """Kodla, m4a'nın KENDİ ses yüksekliğini ölç, sapma 0.2 LU'yu aşıyorsa
    kazancı düzeltip bir kez daha kodla. AAC 96k üst frekansları kırptığı için
    parlak seslerde (beyaz gürültü) ölçülen değer ~1 dB düşüyor."""
    kodla(x, hedef)
    fark = HEDEF_LUFS - ebur128(hedef)["lufs"]
    if abs(fark) > 0.2:
        kodla((x * np.float32(10 ** (fark / 20))).astype(np.float32), hedef)


def sure_ffprobe(yol: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(yol)],
                       capture_output=True, text=True)
    return float(p.stdout.strip())


# ---------------------------------------------------------------------------
# Döngü kurma
# ---------------------------------------------------------------------------
def _egriler(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Eşit güçlü crossfade: gelen sin, giden cos (ilintisiz sinyalde güç sabit)."""
    t = (np.arange(n, dtype=np.float64) + 0.5) / n
    return np.sin(t * np.pi / 2).astype(np.float32), \
        np.cos(t * np.pi / 2).astype(np.float32)


def zincirle(parcalar: list[np.ndarray], hedef_uzunluk: int) -> np.ndarray:
    """Parçaları dönerek crossfade ile ekle, hedef_uzunluk'a ulaşana dek."""
    xf = int(XFADE_TAKE * SR)
    gir, cik = _egriler(xf)
    out = parcalar[0].copy()
    i = 1
    while len(out) < hedef_uzunluk:
        p = parcalar[i % len(parcalar)]
        i += 1
        if len(p) <= 2 * xf:
            raise ValueError("take crossfade için çok kısa")
        karisim = out[-xf:] * cik + p[:xf] * gir
        out = np.concatenate([out[:-xf], karisim, p[xf:]])
    return out


def dongu_yap(x: np.ndarray) -> np.ndarray:
    """x[N:N+X] kuyruğunu başa bindir → N örneklik kusursuz döngü."""
    xf = int(XFADE_DONGU * SR)
    if len(x) < N + xf:
        raise ValueError(f"döngü için kısa: {len(x)} < {N + xf}")
    out = x[:N].copy()
    gir, cik = _egriler(xf)
    out[:xf] = x[:xf] * gir + x[N:N + xf] * cik
    return out


def yumusak_sinirla(x: np.ndarray, esik_db: float = -9.0,
                    tavan_db: float = -5.0) -> np.ndarray:
    """Eşiğin üstünü tanh ile tavana büker. DURUMSUZ (örnek başına): sıkıştırıcı
    ya da limiter gibi zarf tutmaz, dolayısıyla döngünün başı ile sonu aynı
    davranır — dikiş bozulmaz."""
    t, c = 10 ** (esik_db / 20), 10 ** (tavan_db / 20)
    a = np.abs(x)
    ust = a > t
    y = x.copy()
    y[ust] = np.sign(x[ust]) * (t + (c - t) * np.tanh((a[ust] - t) / (c - t)))
    return y.astype(np.float32)


def normalize(x: np.ndarray) -> tuple[np.ndarray, dict]:
    olc = ebur128(x)
    kazanc_db = HEDEF_LUFS - olc["lufs"]
    y = (x * np.float32(10 ** (kazanc_db / 20))).astype(np.float32)
    return y, {"kazanc_db": round(kazanc_db, 2),
               "tp_tahmini": round(olc["tp"] + kazanc_db, 2)}


# ---------------------------------------------------------------------------
# ElevenLabs SFX
# ---------------------------------------------------------------------------
def _eleven_anahtar() -> str:
    from dotenv import load_dotenv
    load_dotenv(KOK / ".env")
    k = (os.getenv("ELEVENLABS_API_KEY") or "").strip().strip('"')
    if not k:
        raise RuntimeError("ELEVENLABS_API_KEY tanımlı değil")
    return k


def eleven_kredi() -> int | None:
    """Bu dönem harcanan karakter/kredi (character_count)."""
    try:
        r = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": _eleven_anahtar()})
        with urllib.request.urlopen(r, timeout=30) as f:
            return int(json.load(f)["character_count"])
    except Exception as e:                      # rapor için; üretimi durdurmaz
        print(f"   (kredi okunamadı: {e})")
        return None


def sfx_take(istem: str, hedef: Path) -> Path:
    """Tek take üret (önbellekte varsa dokunma)."""
    if hedef.exists() and hedef.stat().st_size > 10_000:
        return hedef
    govde = json.dumps({"text": istem, "duration_seconds": SFX_SURE,
                        "prompt_influence": 0.5, "loop": True,
                        "model_id": SFX_MODEL}).encode("utf-8")
    r = urllib.request.Request(SFX_URL, data=govde, method="POST", headers={
        "xi-api-key": _eleven_anahtar(), "Content-Type": "application/json"})
    for deneme in range(3):
        try:
            with urllib.request.urlopen(r, timeout=300) as f:
                veri = f.read()
            break
        except urllib.error.HTTPError as e:
            govde_hata = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503) and deneme < 2:
                time.sleep(10 * (deneme + 1))
                continue
            raise RuntimeError(f"ElevenLabs {e.code}: {govde_hata}") from None
    hedef.parent.mkdir(parents=True, exist_ok=True)
    hedef.write_bytes(veri)
    return hedef


# ---------------------------------------------------------------------------
# Üretim
# ---------------------------------------------------------------------------
def _ara_yolu(slug: str) -> Path:
    return KAYNAK / "ara" / f"{slug}.f32"


def _ara_oku(slug: str) -> np.ndarray | None:
    p = _ara_yolu(slug)
    if not p.exists():
        return None
    x = np.fromfile(p, dtype=np.float32)
    return x if len(x) == N else None


def uret(satir: dict, zorla: bool) -> np.ndarray:
    """Satırı N örneklik, -30 LUFS'a normalize edilmiş float döngüye çevir."""
    slug, kaynak = satir["slug"], satir["kaynak"]
    if not zorla:
        onceki = _ara_oku(slug)
        if onceki is not None:
            return onceki

    tur, _, deger = kaynak.partition(":")
    xf_fazla = int((XFADE_DONGU + 1) * SR)
    if tur == "noise":
        # Tek uzun üretim: gürültü zaten "take"siz sürekli; seed sabit →
        # yeniden üretim aynı dosyayı verir.
        x = gurultu(deger, (N + xf_fazla) / SR + 1, seed=zlib.crc32(slug.encode()) % 100000)
        x = dongu_yap(x)
    elif tur == "sfx":
        kirp = int(KIRP * SR)
        takeler = []
        for i in range(1, TAKE_SAYISI + 1):
            yol = sfx_take(deger, KAYNAK / "ham" / f"{slug}-take{i}.mp3")
            t = oku(yol)
            takeler.append(t[kirp:len(t) - kirp])
        x = dongu_yap(zincirle(takeler, N + xf_fazla))
    elif tur == "mix":
        m = re.match(r"^([a-z0-9-]+)\+([a-z0-9-]+)(?:@(-?[\d.]+))?$", deger)
        if not m:
            raise ValueError(f"{slug}: miks tanımı anlaşılmadı: {deger}")
        a, b = _ara_oku(m.group(1)), _ara_oku(m.group(2))
        if a is None or b is None:
            raise RuntimeError(f"{slug}: miks bileşeni hazır değil "
                               f"({m.group(1)}, {m.group(2)})")
        kazanc = 10 ** (float(m.group(3) or 0) / 20)
        # İki bileşen de N örneklik kusursuz döngü → toplamları da öyle.
        x = a + b * np.float32(kazanc)
    else:
        raise ValueError(f"{slug}: bilinmeyen kaynak türü: {kaynak}")

    y, bilgi = normalize(x)
    if bilgi["tp_tahmini"] > TP_TAVAN - 1.0:
        # Kalp atışı gibi darbeli seslerde -30 LUFS'ta tepe -3 dBTP'yi aşıyor.
        # Kırpma yerine yumuşak sınırlama; sonra ortalama yeniden -30'a çekilir.
        y, _bilgi = normalize(yumusak_sinirla(y))
    _ara_yolu(slug).parent.mkdir(parents=True, exist_ok=True)
    y.astype(np.float32).tofile(_ara_yolu(slug))
    return y


# ---------------------------------------------------------------------------
# Doğrulama
# ---------------------------------------------------------------------------
_BANTLAR = np.geomspace(50, 16000, 25)
W = 4096


def _bant_db(seg: np.ndarray) -> np.ndarray:
    spek = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    out = []
    for lo, hi in zip(_BANTLAR[:-1], _BANTLAR[1:]):
        out.append(10 * np.log10(spek[(f >= lo) & (f < hi)].sum() + 1e-12))
    return np.array(out)


def dikis_olc(x: np.ndarray) -> dict:
    """Dikiş (son W örnek | ilk W örnek) bant farkı, içerideki ardışık
    pencere çiftlerinin farkıyla kıyaslanır. dikis ≤ ic_p95 → duyulmaz."""
    dikis = float(np.mean(np.abs(_bant_db(x[-W:]) - _bant_db(x[:W]))))
    rng = np.random.default_rng(7)
    ic = []
    for p in rng.integers(W, len(x) - W, 60):
        ic.append(float(np.mean(np.abs(_bant_db(x[p - W:p]) - _bant_db(x[p:p + W])))))
    fark = np.abs(np.diff(x))
    sicrama = float(abs(x[-1] - x[0]))
    return {"dikis_db": round(dikis, 2),
            "ic_medyan_db": round(float(np.median(ic)), 2),
            "ic_p95_db": round(float(np.percentile(ic, 95)), 2),
            "sicrama_orani": round(sicrama / float(np.percentile(fark, 99.9)), 3)}


def tik_tara(x: np.ndarray) -> int:
    """5 ms pencere RMS'i, çevresindeki 1 sn'nin medyanını 12 dB aşan ani
    olay sayısı (ardışık pencereler tek olay sayılır)."""
    p = int(0.005 * SR)
    n = len(x) // p
    rms = 20 * np.log10(np.sqrt(np.mean(x[:n * p].reshape(n, p) ** 2, axis=1)) + 1e-9)
    k = 200                                         # 200 × 5 ms = 1 sn
    olay, onceki = 0, False
    for i in range(n):
        lo, hi = max(0, i - k // 2), min(n, i + k // 2)
        asti = rms[i] > np.median(rms[lo:hi]) + 12
        if asti and not onceki:
            olay += 1
        onceki = asti
    return olay


def dogrula(slug: str) -> dict:
    yol = KAYNAK / "cikti" / f"{slug}.m4a"
    x = oku(yol, highpass=False)                    # m4a'nın KENDİSİ
    olc = ebur128(yol)
    return {"slug": slug, "sure_sn": round(sure_ffprobe(yol), 3),
            "ornek": len(x), "ornek_tam": len(x) == N,
            "lufs": olc["lufs"], "tp": olc["tp"], "lra": olc["lra"],
            **dikis_olc(x), "tik": tik_tara(x),
            "bytes": yol.stat().st_size}


def _tablo(rapor: list[dict]) -> None:
    print(f'\n{"slug":20s} {"süre":>8s} {"LUFS":>6s} {"TP":>6s} {"dikiş":>6s} '
          f'{"iç p95":>6s} {"sıçr.":>5s} {"tık":>4s} {"KB":>6s}  durum')
    print("-" * 92)
    for r in rapor:
        sorun = []
        if abs(r["sure_sn"] - 600) > 1:
            sorun.append("süre")
        if abs(r["lufs"] - HEDEF_LUFS) > 1:
            sorun.append("lufs")
        if r["tp"] > TP_TAVAN:
            sorun.append("tp")
        if r["dikis_db"] > r["ic_p95_db"] or r["sicrama_orani"] > 1:
            sorun.append("dikiş")
        r["sorun"] = sorun
        print(f'{r["slug"]:20s} {r["sure_sn"]:8.3f} {r["lufs"]:6.1f} '
              f'{r["tp"]:6.1f} {r["dikis_db"]:6.2f} {r["ic_p95_db"]:6.2f} '
              f'{r["sicrama_orani"]:5.2f} {r["tik"]:4d} {r["bytes"] // 1024:6d}  '
              f'{"OK" if not sorun else ",".join(sorun)}')


EVET = {"1", "true", "evet", "yes", "e"}


def manifest(klasor: Path = KAYNAK) -> list[dict]:
    """sesler.csv → satırlar (slug, title, category, order_in_category,
    is_free, kaynak). api paketini İÇE AKTARMAZ: api.db yerelde config ister.
    Çıktı biçimi services.sounds.katalog_upsert'ün beklediğidir."""
    with open(klasor / CSV_ADI, encoding="utf-8-sig", newline="") as f:
        ham = list(csv.DictReader(f))
    out = []
    for s in ham:
        slug = (s.get("slug") or "").strip()
        if not slug:
            continue
        out.append({
            "slug": slug,
            "title": (s.get("baslik") or "").strip() or slug,
            "category": (s.get("kategori") or "").strip(),
            "order_in_category": int(s.get("sira") or 0),
            "is_free": (s.get("is_free") or "").strip().lower() in EVET,
            "kaynak": (s.get("kaynak") or "").strip(),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Uyku seslerini üret + doğrula")
    ap.add_argument("--yalniz", default="", help="virgülle slug listesi")
    ap.add_argument("--zorla", action="store_true",
                    help="ara çıktı varsa bile yeniden kur (take önbelleği KALIR)")
    ap.add_argument("--dogrula", action="store_true", help="yalnız ölç")
    a = ap.parse_args()

    satirlar = manifest()
    if a.yalniz:
        istenen = {s.strip() for s in a.yalniz.split(",") if s.strip()}
        satirlar = [s for s in satirlar if s["slug"] in istenen]

    kredi_once = None
    if not a.dogrula and any(s["kaynak"].startswith("sfx:") for s in satirlar):
        kredi_once = eleven_kredi()

    hazir, hatali = [], {}
    for s in satirlar:
        cikti = KAYNAK / "cikti" / f'{s["slug"]}.m4a'
        if a.dogrula:
            if cikti.exists():
                hazir.append(s["slug"])
            continue
        try:
            print(f'{s["slug"]} — {s["kaynak"][:60]}', flush=True)
            y = uret(s, a.zorla)
            if a.zorla or not cikti.exists():
                kodla_hedefe(y, cikti)
            hazir.append(s["slug"])
        except Exception as e:
            hatali[s["slug"]] = str(e)
            print(f"   HATA: {e}", flush=True)

    kredi = None
    if kredi_once is not None:
        sonra = eleven_kredi()
        kredi = None if sonra is None else sonra - kredi_once

    rapor = [dogrula(slug) for slug in hazir]
    _tablo(rapor)
    if kredi is not None:
        print(f"\nElevenLabs kredi harcaması (bu koşu): {kredi}")
    if hatali:
        print("\nÜRETİLEMEYEN:")
        for k, v in hatali.items():
            print(f"   {k}: {v[:200]}")

    rp = KAYNAK / "cikti" / "rapor.json"
    eski = json.loads(rp.read_text("utf-8")) if rp.exists() else {}
    for r in rapor:
        eski[r["slug"]] = r
    rp.write_text(json.dumps(eski, ensure_ascii=False, indent=1), "utf-8")
    return 1 if hatali else 0


if __name__ == "__main__":
    sys.exit(main())
