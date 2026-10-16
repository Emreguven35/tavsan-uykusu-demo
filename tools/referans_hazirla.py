"""
Anne Sesi referans kayıtlarını hazırla (ANNE_SESI_STS).

Uzman kayıtları telefondan gelir (m4a / wav / mp3). Dosyaları
data/anne_sesi_referans/ klasörüne bırakın ve çalıştırın:

    python tools/referans_hazirla.py            # hazırla + listele
    python tools/referans_hazirla.py --liste    # yalnız listele
    python tools/referans_hazirla.py --zorla    # hepsini yeniden hazırla

Her ham dosya için:
  1. Dosya adı içerik kimliğiyle eşleştirilir. Kabul edilen adlar:
     kimlik ("ninni_dandini.m4a"), başlık ("Dandini Dastana.m4a") ya da
     kimliğin önek-siz hâliyle başlayan ad ("dandini dastana son.m4a").
     Türkçe harf, boşluk, büyük/küçük harf önemsiz. Eşleşmeyen dosyaya
     dokunulmaz (--hepsi ile adı kimlik sayılarak hazırlanır).
  2. Ham dosya ham/ alt klasörüne TAŞINIR (silinmez, üzerine yazılmaz).
  3. ffmpeg ile: baş ve sondaki sessizlik kırpılır, ses iki geçişli
     loudnorm ile normalize edilir (−18 LUFS, tepe −2 dBTP), 44,1 kHz mono
     128 kbps MP3'e çevrilir → {kimlik}.mp3. Dosyaya "tavsan-referans-v1"
     etiketi yazılır; etiketli MP3 bir daha işlenmez.
  4. Paketteki her içerik için süre ve uyarılar listelenir.

Gerekli: ffmpeg ve ffprobe PATH'te (ya da --ffmpeg / --ffprobe).
Uygulama ham/ klasörünü OKUMAZ; yalnız {kimlik}.mp3 dosyalarını kullanır.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KLASOR = ROOT / "data" / "anne_sesi_referans"
KATALOG = ROOT / "data" / "stories.json"
ETIKET = "tavsan-referans-v1"
UZANTILAR = (".m4a", ".wav", ".mp3")

# Hedefler (bkz. data/anne_sesi_referans/README.md, docs/uzman_kayit_listesi.md)
PARCA_SN = 270            # services/voice_sts.PARCA_SN — üstü bölünür
NINNI_HEDEF_SN = 150
MASAL_HEDEF_SN = 270
MASAL_UST_SN = 360
EN_KISA_SN = 5

_TR = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")


def normalize(ad: str) -> str:
    ad = ad.translate(_TR).lower()
    return re.sub(r"[^a-z0-9]+", "_", ad).strip("_")


def katalog() -> list[dict]:
    d = json.loads(KATALOG.read_text(encoding="utf-8"))
    return [dict(x, tur="ninni") for x in d.get("ninniler", [])] + \
           [dict(x, tur="masal") for x in d.get("masallar", [])]


def paket_kimlikleri(icerikler: list[dict]) -> list[str]:
    """Paketteki kimlikler: VOICE_PAKET_* ayarı varsa o, yoksa uygulamanın
    varsayılanı (services/voice_paket)."""
    try:
        sys.path.insert(0, str(ROOT))
        from api.services import voice_paket
        liste = voice_paket.paket_listesi()
        return liste["ninniler"] + liste["masallar"]
    except Exception:
        return [x["id"] for x in icerikler]


def eslestir(dosya_adi: str, icerikler: list[dict]) -> str | None:
    kok = normalize(Path(dosya_adi).stem)
    for x in icerikler:
        if kok == x["id"] or kok == normalize(x.get("title", "")):
            return x["id"]
    adaylar = []
    for x in icerikler:
        oneksiz = x["id"].split("_", 1)[1] if "_" in x["id"] else x["id"]
        if (kok == oneksiz or kok.startswith(oneksiz + "_")
                or kok.startswith(x["id"] + "_")):          # "ninni_dandini (1)"
            adaylar.append(x["id"])
    return adaylar[0] if len(adaylar) == 1 else None


def _calistir(komut: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(komut, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def sure(ffprobe: str, yol: Path) -> float | None:
    r = _calistir([ffprobe, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1:nk=1", str(yol)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def etiketli_mi(ffprobe: str, yol: Path) -> bool:
    r = _calistir([ffprobe, "-v", "error", "-show_entries", "format_tags=comment",
                   "-of", "default=nw=1:nk=1", str(yol)])
    return ETIKET in r.stdout


def _sessizlik(esik_db: float) -> str:
    """Baş ve sondaki sessizliği kırp; kenarlarda 0,3 sn nefes payı kalır."""
    tek = (f"silenceremove=start_periods=1:start_duration=0:"
           f"start_threshold={esik_db}dB:start_silence=0.3")
    return f"{tek},areverse,{tek},areverse"


def hazirla(ffmpeg: str, kaynak: Path, hedef: Path, esik_db: float) -> None:
    """İki geçiş: önce ölç (loudnorm print_format=json), sonra doğrusal uygula."""
    on = _sessizlik(esik_db)
    olc = _calistir([ffmpeg, "-hide_banner", "-nostats", "-i", str(kaynak), "-vn",
                     "-af", f"{on},loudnorm=I=-18:TP=-2:LRA=11:print_format=json",
                     "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", olc.stderr, re.S)
    if olc.returncode != 0 or not m:
        raise RuntimeError(f"ölçüm başarısız: {olc.stderr.strip()[-300:]}")
    o = json.loads(m.group(0))
    norm = ("loudnorm=I=-18:TP=-2:LRA=11:"
            f"measured_I={o['input_i']}:measured_TP={o['input_tp']}:"
            f"measured_LRA={o['input_lra']}:measured_thresh={o['input_thresh']}:"
            f"offset={o['target_offset']}:linear=true")
    gecici = hedef.with_name(hedef.stem + ".hazirlaniyor.mp3")
    r = _calistir([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(kaynak), "-vn", "-af", f"{on},{norm}",
                   "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k",
                   "-map_metadata", "-1", "-metadata", f"comment={ETIKET}",
                   "-id3v2_version", "3", str(gecici)])
    if r.returncode != 0 or not gecici.exists():
        raise RuntimeError(f"dönüştürme başarısız: {r.stderr.strip()[-300:]}")
    os.replace(gecici, hedef)


def _hama_tasi(dosya: Path, ham: Path, kimlik: str) -> Path:
    ham.mkdir(exist_ok=True)
    hedef = ham / f"{kimlik}{dosya.suffix.lower()}"
    if hedef.exists():                      # üzerine yazma: eskisi saklanır
        hedef = ham / f"{kimlik}.{time.strftime('%Y%m%d-%H%M%S')}{dosya.suffix.lower()}"
    shutil.move(str(dosya), str(hedef))
    return hedef


def _sure_yaz(sn: float | None) -> str:
    if sn is None:
        return "?"
    return f"{int(sn // 60)}:{int(round(sn % 60)):02d}"


def calistir(klasor: Path, ffmpeg: str, ffprobe: str, zorla: bool = False,
             hepsi: bool = False, liste: bool = False, esik_db: float = -50.0) -> dict:
    icerikler = katalog()
    ham = klasor / "ham"
    sonuc = {"hazirlanan": [], "eslesmeyen": [], "hata": [], "satirlar": []}

    if not liste:
        # 1) Klasöre bırakılmış ham dosyaları ham/ altına al.
        for dosya in sorted(p for p in klasor.iterdir() if p.is_file()):
            if dosya.suffix.lower() not in UZANTILAR or ".hazirlaniyor." in dosya.name:
                continue
            if dosya.suffix.lower() == ".mp3" and etiketli_mi(ffprobe, dosya):
                continue                    # zaten hazırlanmış çıktı
            kimlik = eslestir(dosya.name, icerikler)
            if kimlik is None and hepsi:
                kimlik = normalize(dosya.stem)
            if kimlik is None:
                sonuc["eslesmeyen"].append(dosya.name)
                continue
            _hama_tasi(dosya, ham, kimlik)

        # 2) ham/ altındaki her kimliğin EN YENİ kaydını hazırla.
        enyeni: dict[str, Path] = {}
        for p in (sorted(ham.iterdir()) if ham.exists() else []):
            if p.is_file() and p.suffix.lower() in UZANTILAR:
                k = p.name.split(".", 1)[0]
                if k not in enyeni or p.stat().st_mtime > enyeni[k].stat().st_mtime:
                    enyeni[k] = p
        for kimlik, kaynak in sorted(enyeni.items()):
            hedef = klasor / f"{kimlik}.mp3"
            if (not zorla and hedef.exists()
                    and hedef.stat().st_mtime >= kaynak.stat().st_mtime):
                continue
            try:
                hazirla(ffmpeg, kaynak, hedef, esik_db)
                sonuc["hazirlanan"].append(kimlik)
            except Exception as e:
                sonuc["hata"].append((kimlik, str(e)))

    # 3) Liste: paketteki her içerik + pakette olmayan hazır dosyalar.
    basliklar = {x["id"]: (x["tur"], x.get("title", "")) for x in icerikler}
    kimlikler = paket_kimlikleri(icerikler)
    kimlikler += sorted({p.stem for p in klasor.glob("*.mp3")} - set(kimlikler))
    for kimlik in kimlikler:
        tur, baslik = basliklar.get(kimlik, ("?", "(katalogda yok)"))
        yol = klasor / f"{kimlik}.mp3"
        sn = sure(ffprobe, yol) if yol.exists() else None
        uyari = []
        if not yol.exists():
            uyari.append("KAYIT YOK → TTS")
        elif sn is not None:
            if sn < EN_KISA_SN:
                uyari.append("çok kısa — kırpma sesi yemiş olabilir")
            if sn > PARCA_SN:
                uyari.append(f"{PARCA_SN // 60}:{PARCA_SN % 60:02d} üstü → parçalara bölünecek")
            if tur == "ninni" and sn > NINNI_HEDEF_SN:
                uyari.append("ninni hedefi 2:30")
            if tur == "masal" and sn > MASAL_UST_SN:
                uyari.append("masal üst sınırı 6:00")
        sonuc["satirlar"].append({"kimlik": kimlik, "tur": tur, "baslik": baslik,
                                  "sure_sn": sn, "uyari": uyari})
    return sonuc


def _yazdir(sonuc: dict) -> None:
    for k in sonuc["hazirlanan"]:
        print(f"hazırlandı: {k}.mp3")
    for ad in sonuc["eslesmeyen"]:
        print(f"EŞLEŞMEDİ (dokunulmadı): {ad} — adı içerik kimliği ya da başlık olmalı")
    for k, e in sonuc["hata"]:
        print(f"HATA: {k}: {e}")
    print()
    print(f"{'kimlik':<34} {'tür':<6} {'süre':>6}  başlık / uyarı")
    toplam = 0.0
    for s in sonuc["satirlar"]:
        toplam += s["sure_sn"] or 0
        print(f"{s['kimlik']:<34} {s['tur']:<6} {_sure_yaz(s['sure_sn']):>6}  "
              f"{s['baslik']}" + (f"  [{'; '.join(s['uyari'])}]" if s["uyari"] else ""))
    print(f"\nToplam: {_sure_yaz(toplam)} — STS kredisi ≈ {round(toplam / 60 * 1000):,} "
          f"/ anne (dakikada 1.000)".replace(",", "."))


def main(argv=None) -> int:
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--klasor", type=Path, default=KLASOR)
    a.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    a.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    a.add_argument("--zorla", action="store_true", help="hazır olanları da yeniden işle")
    a.add_argument("--hepsi", action="store_true",
                   help="eşleşmeyen dosyayı adını kimlik sayarak hazırla")
    a.add_argument("--liste", action="store_true", help="yalnız listele, dosyalara dokunma")
    a.add_argument("--esik", type=float, default=-50.0,
                   help="sessizlik eşiği dB (varsayılan -50; gürültülü kayıtta -40)")
    g = a.parse_args(argv)
    for arac in (g.ffmpeg, g.ffprobe):
        if not shutil.which(arac) and not Path(arac).exists():
            print(f"{arac} bulunamadı — ffmpeg kurun ya da --ffmpeg/--ffprobe verin")
            return 2
    g.klasor.mkdir(parents=True, exist_ok=True)
    sonuc = calistir(g.klasor, g.ffmpeg, g.ffprobe, zorla=g.zorla, hepsi=g.hepsi,
                     liste=g.liste, esik_db=g.esik)
    _yazdir(sonuc)
    return 1 if sonuc["hata"] else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
