"""
TTS öncesi metin temizleme — Markdown'lı cevabı akıcı KONUŞMA metnine çevirir.

Yalnızca SESE giden string dönüştürülür; /ask response'undaki "cevap" alanı HAM
(Markdown'lı) kalır. Ses cache hash'i cevap-cache anahtarından türer, bu metinden
DEĞİL — dolayısıyla bu dönüşüm hash'i etkilemez (aynı soru → aynı ses dosyası).

Dönüşüm kuralları (konusma_metnine_cevir):
  - **kalın**, *italik*, `kod`, ### başlık işaretleri kaldırılır (içerik kalır).
  - "- madde" / "1. madde" listeleri: her madde kendi cümlesi olur; "Başlık: açıklama"
    biçimindeki madde tek cümleye bağlanır (bağlaç EKLENMEZ — İlayda üslubuna
    müdahale yok, yalnız cümle sonu noktalaması garanti edilir).
  - Emoji ve özel semboller (💙 ⚡ → ✅ vb.) tamamen çıkarılır.
  - "—" (em dash) ve "--" virgüle çevrilir (TTS uzun duraksamasını önler).
    NOT: "–" (en dash, örn. "3–5 gün") ve normal tire ("yatır-çık") KORUNUR.
  - Parantezler virgüle çevrilir, içerik korunur:
    "(elini tutma, emme vb.)" → ", elini tutma, emme ve benzeri,".
  - Yaygın kısaltmalar açılır (vb.→ve benzeri, örn.→örneğin ...).
  - Çift boşluk/satır tekile iner; art arda noktalama temizlenir.

Ayrıca masal/ninni anlatımı için `masal_metni_hazirla` (bkz. aşağısı): paragraf
ve cümle aralarına ElevenLabs `<break>` etiketi koyar.
"""
import re

# Yaygın kısaltmalar (5-10 yeter). Nokta dahil eşleşir; kelime başında.
_ABBR = {
    "vb.": "ve benzeri",
    "vs.": "ve saire",
    "örn.": "örneğin",
    "bkz.": "bakınız",
    "dk.": "dakika",
    "sn.": "saniye",
    "yy.": "yüzyıl",
}

# Emoji + çeşitli sembol/ok/madde-işareti blokları → kaldırılır.
# DİKKAT: genel noktalama bloğu (2000-206F) BİLİNÇLİ olarak DAHİL EDİLMEDİ —
# içinde en dash "–" (2013) var ve onu KORUYORUZ ("3–5 gün" bozulmasın).
# Bullet karakterleri tek tek eklendi.
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # emojiler & piktogramlar
    "\U00002600-\U000027BF"   # misc symbols + dingbats (☀ ✅ ⛔ ✂ ...)
    "\U0001F000-\U0001F0FF"
    "\U0001F1E6-\U0001F1FF"   # bayraklar
    "\U00002190-\U000021FF"   # oklar (→ ← ↑ ↓)
    "\U00002B00-\U00002BFF"   # ek ok/şekiller
    "•‣⁃▪●∙·"  # bullet: • ‣ ⁃ ▪ ● ∙ ·
    "️"                  # variation selector
    "]",
    flags=re.UNICODE,
)


def _satir_isle(line: str) -> str:
    """Tek satırı işle: başlık/blok-alıntı/yatay-çizgi/liste maddesi."""
    s = line.strip()
    if not s:
        return ""
    s = re.sub(r"^>\s?", "", s)                       # blockquote işareti
    if re.fullmatch(r"[-–—_*=]{3,}", s):              # yatay çizgi (---) → at
        return ""
    is_heading = bool(re.match(r"^#{1,6}\s+", s))
    s = re.sub(r"^#{1,6}\s*", "", s)                  # başlık işaretleri

    m = re.match(r"^(?:[-*+•‣⁃●▪]|\d+[.)])\s+(.*)$", s)  # liste maddesi
    if m:
        content = m.group(1).strip()
        cm = re.match(r"^(.{1,40}?):\s*(.+)$", content)  # "Başlık: açıklama"
        if cm:
            content = f"{cm.group(1).strip()} — {cm.group(2).strip()}"
        if content and content[-1] not in ".!?":       # cümle sonu garanti
            content += "."
        return content

    if is_heading and s and s[-1] not in ".!?:":        # başlık da cümle olsun
        s += "."
    return s


def konusma_metnine_cevir(text: str) -> str:
    """Markdown'lı cevabı akıcı konuşma metnine çevir (yalnız TTS için)."""
    if not text:
        return ""

    # 1) Satır bazlı: başlık/liste/blok-alıntı → cümleler
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    s = " ".join(p for p in (_satir_isle(l) for l in lines) if p)

    # 2) Satır-içi Markdown işaretlerini kaldır (içerik kalır)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)            # **kalın**
    s = re.sub(r"__(.+?)__", r"\1", s)               # __kalın__
    s = re.sub(r"\*(.+?)\*", r"\1", s)               # *italik*
    s = re.sub(r"`(.+?)`", r"\1", s)                 # `kod`
    s = s.replace("*", "").replace("`", "").replace("#", "")

    # 3) Kısaltmaları aç (parantez/dash dönüşümünden ÖNCE)
    for k, v in _ABBR.items():
        s = re.sub(r"(?<!\w)" + re.escape(k), v, s)

    # 4) Parantezleri virgüle çevir (içerik korunur)
    s = re.sub(r"\s*\(\s*", ", ", s)
    s = re.sub(r"\s*\)\s*", ", ", s)

    # 5) em dash / "--" → virgül (en dash "–" ve normal tire "-" KORUNUR)
    s = s.replace("—", ", ").replace("--", ", ")

    # 6) Emoji ve özel semboller → boşluk
    s = _EMOJI.sub(" ", s)

    # 7) Boşluk + noktalama temizliği
    s = re.sub(r"\s+", " ", s)                        # çoklu boşluk
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)           # noktalama öncesi boşluk
    s = re.sub(r"([,;:])\s*(?=[,.;:!?])", "", s)     # üst üste noktalama (ilk at)
    s = re.sub(r"([.!?]){2,}", r"\1", s)             # "!!" / ".." → tek
    s = re.sub(r"(,\s*){2,}", ", ", s)               # çoklu virgül → tek
    s = re.sub(r",\s*([.!?])", r"\1", s)             # ", ." → "."
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,;:")


# ---------------------------------------------------------------------------
# MASAL RİTMİ — ElevenLabs <break> etiketleri
# ---------------------------------------------------------------------------
# ElevenLabs SSML `<break time="x.xs" />` destekler (max 3 sn). Flash v2.5'te
# çalıştığı ÖLÇÜLDÜ (2026-08-25): <break time="2.0s"/> sesi +2,25 sn uzattı.
#
# DİKKAT — neden her cümleye koymuyoruz: ElevenLabs "tek üretimde çok fazla
# break etiketi kararsızlık yapar (hızlı okuma, gürültü, artefakt)" diye
# uyarıyor. Korpustaki masallar 3.600-4.600 karakter ve 81-109 cümle; her cümle
# sonuna etiket koymak tek istekte ~100 etiket demek. Üstelik etiketler metnin
# parçası olarak gönderildiği için karakter başına ücretlendirmeyi de ~%50
# şişirir. Bu yüzden kural kendi kendini sınırlar:
#   - Paragraf araları HER ZAMAN duraklar (masalın nefes aldığı yer orası).
#   - Cümle sonu duraklamaları YALNIZCA toplam etiket sayısı MASAL_MAX_BREAK'i
#     aşmıyorsa eklenir. Pratikte: ninniler (6-7 cümle) cümle duraklaması ALIR,
#     uzun masallar yalnız paragraf duraklaması alır — genel tempoyu zaten
#     profildeki speed=0.85 sağlıyor.
MASAL_PARAGRAF_SN = 0.8
MASAL_CUMLE_SN = 0.3
MASAL_MAX_BREAK = 40

# Cümle sonu: nokta/ünlem/soru + (varsa) kapanış tırnağı + boşluk.
# Boşluk ZORUNLU olduğu için paragrafın SON cümlesi eşleşmez — oraya paragraf
# duraklaması gelecek, üst üste iki etiket olmaz.
_CUMLE_SONU = re.compile(r"([.!?][\"»”’']?)(\s+)")


def break_etiketi(saniye: float) -> str:
    """ElevenLabs duraklama etiketi. Süre 3 sn üstüne çıkamaz (API sınırı)."""
    return f'<break time="{min(float(saniye), 3.0):.1f}s" />'


def masal_metni_hazirla(text: str, paragraf_sn: float = MASAL_PARAGRAF_SN,
                        cumle_sn: float = MASAL_CUMLE_SN,
                        max_break: int = MASAL_MAX_BREAK) -> str:
    """Masal/ninni metnini anlatım ritmiyle TTS'e hazırla.

    `konusma_metnine_cevir` paragrafları tek satıra indirdiği için temizlik
    PARAGRAF PARAGRAF yapılır; yoksa duraklama koyacak yer kalmaz. Etiketler en
    SONDA eklenir, böylece temizlik kuralları etiketleri bozamaz.
    """
    if not text:
        return ""
    paragraflar = [p for p in
                   (konusma_metnine_cevir(ham) for ham in re.split(r"\n\s*\n", text))
                   if p]
    if not paragraflar:
        return ""

    paragraf_break = len(paragraflar) - 1
    cumle_break = sum(len(_CUMLE_SONU.findall(p)) for p in paragraflar)
    if cumle_sn > 0 and (paragraf_break + cumle_break) <= max_break:
        etiket = break_etiketi(cumle_sn)
        paragraflar = [_CUMLE_SONU.sub(rf"\1 {etiket}\2", p) for p in paragraflar]

    if paragraf_sn <= 0:
        return " ".join(paragraflar)
    return f" {break_etiketi(paragraf_sn)} ".join(paragraflar)
