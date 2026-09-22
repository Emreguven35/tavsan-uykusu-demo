"""
Medya deposu — ses paketleri (ve ileride eğitim videoları) için tek arayüz.

NEDEN SOYUTLAMA: bugün Railway kalıcı diski (/data) kullanılıyor; yarın Supabase
Storage ya da Cloudflare R2'ye geçilebilir. Çağıran taraf (voice üretimi, router)
YALNIZ bu arayüzü bilir; geçiş TEK dosyada yeni bir `Depo` sınıfı + `depo()`
içinde tek satırlık dal demektir.

YOL SÖZLEŞMESİ (sağlayıcıdan bağımsız):
    voice-audio/{user_id}/{voice_profile_id}/{content_id}.mp3
    egitim-videolari/{...}
İlk parça "bucket" karşılığıdır. Yerel diskte klasör, Supabase'te bucket olur.

GİZLİLİK: `voice-audio` PRIVATE'tır. Dosyalar doğrudan servis edilmez; istemciye
kısa ömürlü İMZALI bağlantı verilir (bkz. `imzali_url`). İmza JWT_SECRET ile
HMAC'tir — anahtar dışarı çıkmaz, bağlantı süresi dolunca ölür.
"""
import hashlib
import hmac
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("tavsan.storage")

# Bucket karşılıkları (yol önekleri).
KOVA_SES = "voice-audio"              # PRIVATE — imzalı bağlantı ile sunulur
# Eğitim videoları PUBLIC'tir: kişisel veri içermez, herkese aynı dosya sunulur
# ve iOS oynatıcısı Range isteklerini Authorization başlığı TAŞIMADAN yapar.
# İmzalı bağlantı burada yanlış olurdu — imza süresi video ortasında dolar ve
# oynatma "sebepsiz" durur. Yetki kontrolü KATALOGDA değil, dosyada anlamsız.
KOVA_VIDEOLAR = "videos"              # PUBLIC — /media/videos/{slug}.mp4
KOVA_POSTERLER = "posters"            # PUBLIC — /media/posters/{slug}.jpg

# İmzalı bağlantı varsayılan ömrü (spec: 1 saat).
IMZA_OMRU_SN = 3600

# Yol güvenliği: yalnız bu karakterler. `..` ve mutlak yol İMKÂNSIZ.
_GUVENLI_YOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,300}$")


def yol_guvenli_mi(yol: str) -> bool:
    """Depo yolu kabul edilebilir mi? (traversal engeli)"""
    if not yol or ".." in yol or yol.startswith("/") or "\\" in yol:
        return False
    return bool(_GUVENLI_YOL.match(yol))


def ses_yolu(user_id, voice_profile_id, content_id: str) -> str:
    """Spec'teki yol: voice-audio/{user_id}/{voice_profile_id}/{content_id}.mp3"""
    return f"{KOVA_SES}/{user_id}/{voice_profile_id}/{content_id}.mp3"


def video_yolu(slug: str) -> str:
    """Eğitim videosunun depo yolu: videos/{slug}.mp4"""
    return f"{KOVA_VIDEOLAR}/{slug}.mp4"


def poster_yolu(slug: str) -> str:
    """Eğitim videosunun kapak görselinin depo yolu: posters/{slug}.jpg"""
    return f"{KOVA_POSTERLER}/{slug}.jpg"


def video_url(slug: str) -> str:
    """İstemciye verilen GÖRELİ yol. Mutlak URL SAKLANMAZ: alan adı değişince
    kayıtlı mutlak adresler bayatlar, mobil tarafta düzeltilemez."""
    return f"/media/{KOVA_VIDEOLAR}/{slug}.mp4"


def poster_url(slug: str) -> str:
    return f"/media/{KOVA_POSTERLER}/{slug}.jpg"


def ses_klasoru(user_id, voice_profile_id=None) -> str:
    """Bir kullanıcının (ya da tek bir ses profilinin) klasör öneki."""
    if voice_profile_id is None:
        return f"{KOVA_SES}/{user_id}"
    return f"{KOVA_SES}/{user_id}/{voice_profile_id}"


class Depo(Protocol):
    """Medya deposu arayüzü. Uygulamalar: YerelDepo (Railway volume), ileride
    SupabaseDepo / R2Depo."""

    def yaz(self, yol: str, veri: bytes, icerik_turu: str = "audio/mpeg") -> int:
        """Dosyayı yaz, yazılan bayt sayısını döndür."""

    def oku(self, yol: str) -> bytes | None: ...

    def var_mi(self, yol: str) -> bool: ...

    def sil(self, yol: str) -> bool: ...

    def klasor_sil(self, onek: str) -> int:
        """Önekle başlayan TÜM dosyaları sil, silinen sayısını döndür."""

    def boyut(self, yol: str) -> int | None: ...


class YerelDepo:
    """Dosya sistemi deposu — Railway kalıcı diski (MEDIA_ROOT, /data/media).

    Railway volume'u konteyner yeniden başlasa da kalır; ses paketleri bir kez
    üretilip ORADA durur. (Eski `data/audio_cache` efemer diskteydi ve her
    deploy'da siliniyordu — ses paketleri için kabul edilemez.)"""

    def __init__(self, kok: str | Path):
        self.kok = Path(kok)

    def _tam(self, yol: str) -> Path:
        if not yol_guvenli_mi(yol):
            raise ValueError(f"güvensiz depo yolu: {yol!r}")
        return self.kok / yol

    def yaz(self, yol: str, veri: bytes, icerik_turu: str = "audio/mpeg") -> int:
        p = self._tam(yol)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Önce geçici dosyaya, sonra taşı: yarım yazılmış bir MP3 asla
        # "hazır" görünmesin (üretim ortasında restart olabilir).
        gecici = p.with_suffix(p.suffix + ".tmp")
        gecici.write_bytes(veri)
        gecici.replace(p)
        return len(veri)

    def oku(self, yol: str) -> bytes | None:
        p = self._tam(yol)
        return p.read_bytes() if p.exists() else None

    def var_mi(self, yol: str) -> bool:
        return self._tam(yol).exists()

    def boyut(self, yol: str) -> int | None:
        p = self._tam(yol)
        return p.stat().st_size if p.exists() else None

    def sil(self, yol: str) -> bool:
        p = self._tam(yol)
        if not p.exists():
            return False
        p.unlink()
        return True

    def klasor_sil(self, onek: str) -> int:
        if not yol_guvenli_mi(onek):
            raise ValueError(f"güvensiz depo yolu: {onek!r}")
        p = self.kok / onek
        if not p.is_dir():
            return 0
        n = sum(1 for _ in p.rglob("*") if _.is_file())
        shutil.rmtree(p, ignore_errors=True)
        return n


_depo: Depo | None = None


def medya_koku() -> Path:
    """MEDIA_ROOT env'i; tanımsızsa yerel geliştirme için proje içi klasör."""
    ham = (os.getenv("MEDIA_ROOT") or "").strip()
    if ham:
        return Path(ham)
    return Path(__file__).resolve().parent.parent.parent / "data" / "media"


def depo() -> Depo:
    """Süreç boyunca tek depo örneği.

    Sağlayıcı değişikliği BURADA yapılır: MEDIA_PROVIDER=supabase gelince
    SupabaseDepo döndürmek yeter, çağıran kodun hiçbiri değişmez."""
    global _depo
    if _depo is None:
        kok = medya_koku()
        kok.mkdir(parents=True, exist_ok=True)
        _depo = YerelDepo(kok)
        logger.info("Medya deposu: yerel disk (%s)", kok)
    return _depo


def yerel_dosya(yol: str) -> Path | None:
    """Dosyanın diskteki yolu — YALNIZ yerel depo için.

    Uzak sağlayıcıya (Supabase/R2) geçildiğinde None döner ve `/media` ucu
    sağlayıcının kendi imzalı bağlantısına yönlendirir; çağıran kod değişmez."""
    d = depo()
    if isinstance(d, YerelDepo) and d.var_mi(yol):
        return d._tam(yol)
    return None


# ---------------------------------------------------------------------------
# İmzalı bağlantı
# ---------------------------------------------------------------------------
def _imza_anahtari() -> bytes:
    """İmza anahtarı JWT_SECRET'tan türetilir — ayrı bir sır yönetmeyelim.

    Türetme (sabit etiketle HMAC) bilinçli: imza anahtarı sızsa bile JWT
    anahtarı geri hesaplanamaz."""
    from api.config import get_settings
    return hmac.new(get_settings().jwt_secret.encode("utf-8"),
                    b"tavsan-media-signature-v1", hashlib.sha256).digest()


def imzala(yol: str, omur_sn: int = IMZA_OMRU_SN) -> tuple[int, str]:
    """(bitis_zamani, imza) — bağlantı bu ikisiyle doğrulanır."""
    bitis = int(time.time()) + int(omur_sn)
    mesaj = f"{yol}|{bitis}".encode("utf-8")
    return bitis, hmac.new(_imza_anahtari(), mesaj, hashlib.sha256).hexdigest()


def imza_gecerli_mi(yol: str, bitis: int, imza: str) -> bool:
    """Sabit-zaman doğrulama. Süre dolmuşsa imza doğru olsa bile GEÇERSİZ."""
    if not imza or int(bitis) < int(time.time()):
        return False
    mesaj = f"{yol}|{int(bitis)}".encode("utf-8")
    beklenen = hmac.new(_imza_anahtari(), mesaj, hashlib.sha256).hexdigest()
    return hmac.compare_digest(beklenen, imza)


def imzali_url(yol: str, omur_sn: int = IMZA_OMRU_SN) -> str:
    """İstemciye verilecek kısa ömürlü bağlantı.

    PUBLIC_BASE_URL tanımlıysa MUTLAK URL döner (mobilin göreli path birleştirme
    riski yok); tanımsızsa göreli path."""
    from api.config import get_settings
    bitis, imza = imzala(yol, omur_sn)
    yol_q = f"/media/{yol}?exp={bitis}&sig={imza}"
    taban = get_settings().public_base_url
    return f"{taban}{yol_q}" if taban else yol_q
