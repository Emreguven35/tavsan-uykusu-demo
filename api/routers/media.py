"""
media router — eğitim videolarının ve kapak görsellerinin PUBLIC sunumu.

    GET /media/videos/{slug}.mp4     → video (Range destekli)
    GET /media/posters/{slug}.jpg    → kapak görseli

NEDEN AUTH YOK: bunlar kişisel veri içermez ve herkese aynı dosya sunulur.
Daha önemlisi iOS/AVPlayer Range isteklerini **Authorization başlığı taşımadan**
yapar; korumalı bir uca bağlanan oynatıcı ilk byte'ta 401 alır. Ses paketleri
(PRIVATE, kişiye özel) imzalı `/media/{yol:path}` ucundan sunulmaya devam eder.

NEDEN ELDE RANGE: AVPlayer önce `Range: bytes=0-1` ile yoklar ve **206** +
`Content-Range` görmezse dosyayı akış olarak kabul etmez — uzun videoda sarma
çalışmaz, bazı sürümlerde hiç başlamaz. Starlette'in FileResponse'una bel
bağlamak yerine 206/416 burada üretilir: davranış sürümden bağımsız olur.

ROTA SIRASI ÖNEMLİ: `api/main.py` içindeki imzalı `/media/{yol:path}` yakalayıcı
bu yolları da eşlerdi. Bu router `include_router` ile ONDAN ÖNCE kaydedilir
(main.py'de router'lar 250'li satırlarda, yakalayıcı ise ~400'de tanımlı).
"""
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from api.services import storage

logger = logging.getLogger("tavsan.media")
router = APIRouter(prefix="/media", tags=["media"])

# Bir yıl. Dosya içeriği slug başına DEĞİŞMEZ (yeni içerik → yeni slug), bu
# yüzden immutable işaretlenebilir: mobil bir kez indirir, bir daha sormaz.
CACHE = "public, max-age=31536000, immutable"

# Slug sözleşmesi: küçük harf, rakam, tire. CSV üretimi bunu garanti eder;
# burada ayrıca doğrulanır (traversal ve sürpriz dosya adı engeli).
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,118}$")

# Range başlığı: yalnız TEK aralık desteklenir (oynatıcıların kullandığı biçim).
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

PARCA = 1024 * 1024          # 1 MB'lik bloklarla akıt (bellekte dosya tutma)


def _dosya(yol: str) -> Path:
    p = storage.yerel_dosya(yol)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Dosya bulunamadı")
    return p


def _slug_dogrula(slug: str) -> str:
    if not _SLUG.match(slug or ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Geçersiz video kimliği")
    return slug


def _akit(p: Path, bas: int, bit: int):
    """[bas, bit] aralığını bloklar hâlinde oku. Dosya belleğe ALINMAZ."""
    with p.open("rb") as f:
        f.seek(bas)
        kalan = bit - bas + 1
        while kalan > 0:
            blok = f.read(min(PARCA, kalan))
            if not blok:
                break
            kalan -= len(blok)
            yield blok


def _range_yaniti(request: Request, p: Path, tur: str) -> Response:
    """Range varsa 206, yoksa 200. Geçersiz/kapsam dışı aralıkta 416."""
    boyut = p.stat().st_size
    ham = request.headers.get("range")
    ortak = {"Cache-Control": CACHE, "Accept-Ranges": "bytes"}

    if not ham:
        # Range yok → tam dosya. FileResponse sendfile kullanır (daha hızlı).
        return FileResponse(p, media_type=tur, headers=ortak)

    m = _RANGE.match(ham.strip())
    if not m:
        # Anlaşılmayan Range → RFC 7233 "yok say" der. FileResponse'a DÜŞÜLMEZ:
        # Starlette başlığı kendisi yeniden ayrıştırıp 400 üretiyor. Dosyayı
        # kendimiz akıtarak davranışı sürümden bağımsız kılıyoruz.
        return StreamingResponse(
            _akit(p, 0, boyut - 1) if boyut else iter(()), status_code=200,
            media_type=tur,
            headers={**ortak, "Content-Length": str(boyut)})

    bas_ham, bit_ham = m.group(1), m.group(2)
    if bas_ham == "":
        if bit_ham == "":
            raise HTTPException(status_code=416, detail="Geçersiz aralık")
        # "bytes=-500" → son 500 bayt
        uzunluk = min(int(bit_ham), boyut)
        bas, bit = boyut - uzunluk, boyut - 1
    else:
        bas = int(bas_ham)
        bit = int(bit_ham) if bit_ham else boyut - 1
        bit = min(bit, boyut - 1)

    if boyut == 0 or bas >= boyut or bas > bit:
        return Response(status_code=416, headers={**ortak,
                                                  "Content-Range": f"bytes */{boyut}"})

    return StreamingResponse(
        _akit(p, bas, bit), status_code=206, media_type=tur,
        headers={**ortak,
                 "Content-Range": f"bytes {bas}-{bit}/{boyut}",
                 "Content-Length": str(bit - bas + 1)})


@router.get("/videos/{slug}.mp4")
def video(slug: str, request: Request):
    """Eğitim videosu. Range destekli (iOS oynatıcı için ŞART), 1 yıl cache."""
    return _range_yaniti(request, _dosya(storage.video_yolu(_slug_dogrula(slug))),
                         "video/mp4")


@router.get("/posters/{slug}.jpg")
def poster(slug: str, request: Request):
    """Video kapak görseli. Liste ekranı bunu yükler, video indirilmez."""
    return _range_yaniti(request, _dosya(storage.poster_yolu(_slug_dogrula(slug))),
                         "image/jpeg")
