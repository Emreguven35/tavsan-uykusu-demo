"""
MP3'ü çerçeve sınırında kesme, parçalama ve birleştirme — Anne Sesi
önizlemesi (30 sn) ve speech-to-speech parçaları için.

Çözücü/ffmpeg bağımlılığı YOK: MPEG ses çerçeveleri başlıklarından boyut ve
süre okunarak sayılır, süre dolunca kalan çerçeveler atılır. Çerçeve sınırında
kesildiği için çıkan dosya her oynatıcıda geçerlidir.

Xing/Info/VBRI çerçevesi (kodlayıcının "toplam süre" notu) ATILIR: içinde
ESKİ toplam süre yazar ve oynatıcı 30 sn'lik dosyayı 5 dk sanırdı.
"""
from __future__ import annotations

_BITRATE = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_ORNEKLEME = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000),
              25: (11025, 12000, 8000)}
# Bilinmeyen biçimde kaba kesim için varsayılan (ElevenLabs 128 kbps CBR).
VARSAYILAN_KBPS = 128


def _id3_atla(veri: bytes) -> int:
    if len(veri) >= 10 and veri[:3] == b"ID3":
        boyut = ((veri[6] & 0x7F) << 21 | (veri[7] & 0x7F) << 14
                 | (veri[8] & 0x7F) << 7 | (veri[9] & 0x7F))
        return 10 + boyut + (10 if veri[5] & 0x10 else 0)
    return 0


def _cerceve(veri: bytes, i: int) -> tuple[int, float] | None:
    """(bayt uzunluğu, saniye) — i konumunda geçerli bir çerçeve yoksa None."""
    if i + 4 > len(veri) or veri[i] != 0xFF or (veri[i + 1] & 0xE0) != 0xE0:
        return None
    b1, b2 = veri[i + 1], veri[i + 2]
    surum = {0: 25, 2: 2, 3: 1}.get((b1 >> 3) & 0x03)
    katman = {1: 3, 2: 2, 3: 1}.get((b1 >> 1) & 0x03)
    bi, si, dolgu = b2 >> 4, (b2 >> 2) & 0x03, (b2 >> 1) & 0x01
    if surum is None or katman is None or bi in (0, 15) or si == 3:
        return None
    kbps = _BITRATE[(1 if surum == 1 else 2, katman)][bi]
    sr = _ORNEKLEME[surum][si]
    if katman == 1:
        uzunluk, ornek = (12 * kbps * 1000 // sr + dolgu) * 4, 384
    elif katman == 2 or surum == 1:
        uzunluk, ornek = 144 * kbps * 1000 // sr + dolgu, 1152
    else:                                           # MPEG-2/2.5 Layer III
        uzunluk, ornek = 72 * kbps * 1000 // sr + dolgu, 576
    return (uzunluk, ornek / sr) if uzunluk > 4 else None


def kes(veri: bytes, saniye: float) -> bytes:
    """İlk `saniye` kadar ses (çerçeve sınırında). Zaten kısaysa aynen döner."""
    if not veri:
        return veri
    i = _id3_atla(veri)
    parcalar: list[bytes] = []
    toplam = 0.0
    ilk = True
    while i < len(veri):
        c = _cerceve(veri, i)
        if c is None:
            # Senkron kaybı: bir sonraki çerçeve başını ara (çöp bayt atlanır).
            j = veri.find(b"\xff", i + 1)
            if j < 0:
                break
            i = j
            continue
        uzunluk, sure = c
        govde = veri[i:i + uzunluk]
        if ilk and any(e in govde[:200] for e in (b"Xing", b"Info", b"VBRI")):
            ilk = False
            i += uzunluk                            # süre notu çerçevesi atılır
            continue
        ilk = False
        if toplam + sure > saniye + 1e-9:
            break
        parcalar.append(govde)
        toplam += sure
        i += uzunluk
    if not parcalar:
        # Çerçeve okunamadı: bit hızından kaba kesim (yine en fazla `saniye`).
        return veri[:int(saniye * VARSAYILAN_KBPS * 1000 / 8)]
    return b"".join(parcalar)


def sure(veri: bytes) -> float:
    """Çerçevelerden toplam süre (sn) — test ve log için."""
    i, toplam = _id3_atla(veri), 0.0
    while i < len(veri):
        c = _cerceve(veri, i)
        if c is None:
            j = veri.find(b"\xff", i + 1)
            if j < 0:
                break
            i = j
            continue
        toplam += c[1]
        i += c[0]
    return toplam


def _cerceveler(veri: bytes):
    """(çerçeve baytları, saniye, süre_notu_mu) — ID3 ve çöp baytlar atlanır."""
    i, ilk = _id3_atla(veri), True
    while i < len(veri):
        c = _cerceve(veri, i)
        if c is None:
            j = veri.find(b"\xff", i + 1)
            if j < 0:
                break
            i = j
            continue
        govde = veri[i:i + c[0]]
        not_mu = ilk and any(e in govde[:200] for e in (b"Xing", b"Info", b"VBRI"))
        ilk = False
        yield govde, c[1], not_mu
        i += c[0]


def parcala(veri: bytes, saniye: float) -> list[bytes]:
    """En fazla `saniye` uzunluğunda parçalar (çerçeve sınırında). ID3 ve süre
    notu atılır; her parça kendi başına geçerli bir MP3'tür. Çerçeve
    okunamazsa tek parça (veri olduğu gibi) döner."""
    parcalar: list[bytes] = []
    simdiki: list[bytes] = []
    toplam = 0.0
    for govde, sure_, not_mu in _cerceveler(veri):
        if not_mu:
            continue
        if simdiki and toplam + sure_ > saniye + 1e-9:
            parcalar.append(b"".join(simdiki))
            simdiki, toplam = [], 0.0
        simdiki.append(govde)
        toplam += sure_
    if simdiki:
        parcalar.append(b"".join(simdiki))
    return parcalar or ([veri] if veri else [])


def birlestir(parcalar: list[bytes]) -> bytes:
    """Parçaları tek MP3'te birleştir: her parçanın ID3 etiketi ve süre notu
    atılır (aksi hâlde oynatıcı ilk parçanın süresini toplam sanır)."""
    out: list[bytes] = []
    for veri in parcalar:
        cerceveler = [g for g, _, n in _cerceveler(veri) if not n]
        out.append(b"".join(cerceveler) if cerceveler else veri)
    return b"".join(out)
