"""
TEK SEFERLİK GERÇEK speech-to-speech denemesi (ANNE_SESI_STS öncesi kalite/izin
kontrolü). Bir referans kaydını verilen test sesine dönüştürür, çıktıyı dosyaya
yazar ve harcanan krediyi gösterir. Uygulamanın veritabanına DOKUNMAZ.

    python tools/sts_deneme.py data/anne_sesi_referans/ninni_dandini.mp3 \\
        --voice-id <TEST_VOICE_ID> --sn 30

  --gecici-klon <ses_dosyası>
             --voice-id yerine: bu örnekten (ör. 1 dk'lık mp3/m4a/wav) geçici bir
             klon açar, denemeden sonra (hata olsa da) siler. Hesapta 1 boş
             klon slotu gerekir.
  --sn       referansın ilk kaç saniyesi gönderilsin (varsayılan 30; 0 = tamamı,
             en fazla 270 — tek çağrı, parçalama yok)
  --cikti    çıktı dosyası (varsayılan: sts_deneme_{dosya}_{zaman}.mp3)
  --env      anahtarın okunacağı .env (varsayılan: ELEVENLABS_API_KEY ortam
             değişkeni, yoksa repo kökündeki .env)
  --evet     onay sorma

Anahtar hiçbir yerde yazdırılmaz. Referans MP3 olmalı
(m4a/wav için önce: python tools/referans_hazirla.py).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests                                           # noqa: E402

from api.services import mp3, voice, voice_sts            # noqa: E402

ABONELIK_URL = "https://api.elevenlabs.io/v1/user/subscription"


def anahtar_bul(env_yolu: Path | None) -> str | None:
    if env_yolu is None and os.getenv("ELEVENLABS_API_KEY"):
        return os.getenv("ELEVENLABS_API_KEY")
    yol = env_yolu or (ROOT / ".env")
    if not yol.exists():
        return None
    for satir in yol.read_text(encoding="utf-8").splitlines():
        satir = satir.strip()
        if satir.startswith("ELEVENLABS_API_KEY="):
            return satir.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def kullanilan_kredi(anahtar: str) -> int | None:
    try:
        r = requests.get(ABONELIK_URL, headers={"xi-api-key": anahtar}, timeout=20)
        return int(r.json()["character_count"]) if r.status_code == 200 else None
    except Exception:
        return None


def _hata(r) -> str:
    try:
        d = r.json().get("detail")
        if isinstance(d, dict):
            return f"{d.get('status', '')}: {d.get('message', '')}"[:300]
        return json.dumps(d, ensure_ascii=False)[:300]
    except Exception:
        return (r.text or "")[:300]


def gecici_klon_ac(anahtar: str, ornek: Path) -> str | None:
    """Örnekten geçici klon açar; voice_id ya da None döner."""
    os.environ["ELEVENLABS_API_KEY"] = anahtar     # yalnız bu süreç; voice.py env'den okur
    tur = mimetypes.guess_type(ornek.name)[0] or "audio/mpeg"
    sonuc = voice.clone_voice(f"sts_deneme_gecici_{time.strftime('%Y%m%d-%H%M%S')}",
                              ornek.read_bytes(), ornek.name, tur)
    if not sonuc.get("ok"):
        print(f"Geçici klon açılamadı ({sonuc.get('reason', '')}): {sonuc.get('error')}")
        return None
    print(f"Geçici klon açıldı: {sonuc['voice_id']}")
    return sonuc["voice_id"]


def gecici_klonu_sil(voice_id: str) -> None:
    sonuc = voice.delete_voice(voice_id)
    if sonuc.get("ok"):
        print(f"Geçici klon silindi: {voice_id}")
    else:
        print(f"UYARI: geçici klon SİLİNEMEDİ ({sonuc.get('status')}): {sonuc.get('error')}")
        print(f"ElevenLabs panelinden elle silin: {voice_id}")


def main(argv=None) -> int:
    a = argparse.ArgumentParser(description="Tek seferlik gerçek STS denemesi")
    a.add_argument("referans", type=Path)
    ses = a.add_mutually_exclusive_group(required=True)
    ses.add_argument("--voice-id")
    ses.add_argument("--gecici-klon", type=Path, metavar="SES_DOSYASI")
    a.add_argument("--sn", type=float, default=30.0)
    a.add_argument("--cikti", type=Path)
    a.add_argument("--env", type=Path)
    a.add_argument("--evet", action="store_true")
    g = a.parse_args(argv)

    if not g.referans.is_file() or g.referans.suffix.lower() != ".mp3":
        print("Referans bir MP3 dosyası olmalı (önce tools/referans_hazirla.py).")
        return 2
    anahtar = anahtar_bul(g.env)
    if not anahtar:
        print("ELEVENLABS_API_KEY bulunamadı (ortam değişkeni ya da --env).")
        return 2
    if g.gecici_klon and not g.gecici_klon.is_file():
        print(f"Klon örneği bulunamadı: {g.gecici_klon}")
        return 2

    kaynak = g.referans.read_bytes()
    toplam = mp3.sure(kaynak)
    if toplam <= 0:
        print("Referans MP3 okunamadı.")
        return 2
    if g.sn and g.sn > 0:
        kaynak = mp3.kes(kaynak, g.sn)
    giden = mp3.sure(kaynak)
    if giden > voice_sts.PARCA_SN:
        print(f"Tek çağrıda en fazla {voice_sts.PARCA_SN} sn gönderilir; --sn ile kısaltın.")
        return 2
    tahmini = voice_sts.kredi(giden)
    print(f"Referans: {g.referans.name} ({toplam:.1f} sn), gönderilecek: {giden:.1f} sn")
    print(f"Model: {voice_sts.MODEL} · ses: "
          + (g.voice_id or f"geçici klon ({g.gecici_klon.name}; deneme sonunda silinir)"))
    print(f"Tahmini harcama: ~{tahmini} kredi (dakikada {voice_sts.KREDI_DAKIKA})")
    if not g.evet and input("Devam edilsin mi? [e/H] ").strip().lower() not in ("e", "evet", "y"):
        print("Vazgeçildi.")
        return 1

    if not g.gecici_klon:
        return _dene(g, anahtar, g.voice_id, kaynak, tahmini)
    voice_id = gecici_klon_ac(anahtar, g.gecici_klon)
    if not voice_id:
        return 3
    try:
        return _dene(g, anahtar, voice_id, kaynak, tahmini)
    finally:
        gecici_klonu_sil(voice_id)


def _dene(g, anahtar: str, voice_id: str, kaynak: bytes, tahmini: int) -> int:
    once = kullanilan_kredi(anahtar)
    t0 = time.perf_counter()
    r = requests.post(
        voice_sts.STS_URL.format(voice_id=voice_id),
        params={"output_format": voice_sts.CIKIS_BICIMI},
        headers={"xi-api-key": anahtar, "Accept": "audio/mpeg"},
        data={"model_id": voice_sts.MODEL,
              "voice_settings": json.dumps({"stability": 0.5, "similarity_boost": 0.85}),
              "remove_background_noise": "false"},
        files={"audio": (g.referans.name, kaynak, "audio/mpeg")},
        timeout=voice_sts.STS_TIMEOUT_S)
    sure_ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code in (401, 403):
        print(f"İZİN/ANAHTAR HATASI (HTTP {r.status_code}): {_hata(r)}")
        print("ElevenLabs panelinde anahtara 'Speech to Speech' izni verildiğini kontrol edin.")
        return 3
    if r.status_code != 200 or not r.content:
        print(f"STS başarısız (HTTP {r.status_code}): {_hata(r)}")
        return 3

    cikti = g.cikti or Path(f"sts_deneme_{g.referans.stem}_{time.strftime('%Y%m%d-%H%M%S')}.mp3")
    cikti.write_bytes(r.content)
    print(f"Çıktı: {cikti.resolve()} ({mp3.sure(r.content):.1f} sn, {sure_ms} ms)")
    sonra = kullanilan_kredi(anahtar)
    if once is not None and sonra is not None:
        print(f"Harcanan kredi (hesaptan): {sonra - once} "
              f"(önce {once:,}, sonra {sonra:,})".replace(",", "."))
        if sonra == once:
            print("Not: sayaç gecikmeli güncellenebilir; birkaç dakika sonra hesaptan bakın.")
    else:
        print(f"Hesap sayacı okunamadı; tahmini harcama ~{tahmini} kredi.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
