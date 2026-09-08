"""
Plan gün bölümleri — markdown'daki eğitim günlerini YAPISAL alana çevirir.

NEDEN: gün başlıklarını LLM yazıyor ve biçimi sabit değil. Aynı backend sürümünden
ölçülen beş ayrı kalıp:
    ### 📍 Gün 1–3: Beşik Yanı
    ### 🗓️ Günler 1-3: Beşik Yanı
    ### Gün 1–3 | Beşik Yanı
    ### 🔵 1. – 3. Gün: Beşik Yanı
    ### 📅 1–3. Günler: Beşik / Yatak Yanı
İstemci bunu regex'le ayrıştırmaya çalışıyordu ve eğitim ekranı iki kez sessizce
boş kaldı. Artık ayrıştırma SUNUCUDA, üretim anında yapılır; sonuç content["days"]
alanında saklanır ve istemci hiç regex yapmaz.

SÖZLEŞME — content["days"]:
    [{"start": 1, "end": 3, "label": "Beşik yanı",
      "position": "Beşik yanı (sandalye veya ayakta)",   # KB'deki merdiven metni
      "markdown": "### Gün 1-3: ...\\n\\n<o bölümün tam metni>"}]
content["markdown"] AYNEN KALIR (geriye uyumluluk + detay gösterimi).

DOĞRULAMA KATIDIR: aşama sayısı, sınırlar ve 1..N gün kapsaması KB'deki merdivenle
(parameter_engine.bekleme_sureleri_planla) birebir tutmalıdır. Tutmazsa DayParseError
yükselir — çağıran plan_service planı REDDEDİP YENİDEN ÜRETİR. Sessizce boş days
DÖNMEZ; eğitim ekranının boş kalma yolu budur ve kapatılmıştır.

Merdiven (13_gun_dirençli): 1-3 beşik yanı, 4-6 oda ortası, 7-9 kapı,
10-12 kapı eşiği, 13 yatır-çık. Aşamalar burada SABİT DEĞİL — plan tipine göre
KB'den okunur (6_gun_buyuk_cocuk 24+ ay istisnasında farklıdır).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("tavsan.plan_gunleri")


class DayParseError(ValueError):
    """Gün bölümleri beklenen yapıya çevrilemedi (biçim beklenmedik)."""


# --- Karakter dağarcığı ------------------------------------------------------
# LLM tire yerine uzun tire/en-dash kullanabiliyor; hepsi eşdeğer sayılır.
# (Kısa tire EN SONDA — karakter sınıfı içinde aralık sanılmasın.)
_TIRE_CHARS = "–—‒−-"
_TIRE = f"[{_TIRE_CHARS}]"
# "gün"/"günler" — ama "gündüz"/"günlük" DEĞİL (sonrasında harf gelmemeli).
_GUN = r"g[uü]n(?:ler)?(?![a-zA-ZçğıöşüÇĞİıÖŞÜ])"
_SAYI = r"(\d{1,3})"

# "Gün 1-3" / "Günler 1-3" / "Gün 13"
_RE_GUN_ONCE = re.compile(rf"^{_GUN}\s*:?\s*{_SAYI}\s*(?:{_TIRE}\s*{_SAYI})?\s*\.?",
                          re.IGNORECASE)
# "1-3. Günler" / "1. – 3. Gün" / "13. Gün"
_RE_SAYI_ONCE = re.compile(rf"^{_SAYI}\s*\.?\s*(?:{_TIRE}\s*{_SAYI}\s*\.?)?\s*{_GUN}",
                           re.IGNORECASE)
# Başlık önündeki emoji/işaret ("### 📍 Gün 1-3" → "Gün 1-3")
_RE_ONEK = re.compile(r"^[^\w]+", re.UNICODE)
# Aralık ile etiket arasındaki ayraç (":", "|", "—", "–", "-", "•")
_RE_AYRAC = re.compile(rf"^[\s:：|·•{_TIRE_CHARS}]+")
# Markdown başlık satırı (## .. ######)
_RE_BASLIK = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
# Yedek aday: tek başına kalın satır ("**Gün 1-3: Beşik yanı**")
_RE_KALIN = re.compile(r"^[ \t]{0,3}\*\*(.+?)\*\*[ \t]*:?[ \t]*$", re.M)

_TR_KATLAMA = str.maketrans({"ş": "s", "ı": "i", "ğ": "g", "ü": "u", "ö": "o",
                             "ç": "c", "İ": "i", "Ş": "s", "Ğ": "g", "Ü": "u",
                             "Ö": "o", "Ç": "c"})


def _katla(s: str) -> str:
    """Başlık karşılaştırması için sadeleştir (küçült + Türkçe harfleri ascii'ye)."""
    return (s or "").strip().lower().translate(_TR_KATLAMA)


# =============================================================================
# KB merdiveni → beklenen aşamalar
# =============================================================================
_RE_ASAMA_KEY = re.compile(r"^gun_(\d{1,3})(?:_(\d{1,3}))?$")


def asamalar(plan_tipi: str) -> list[dict]:
    """Plan tipinin kademeli uzaklaşma merdivenini yapısal olarak döndür.

    Dönen: [{"start": 1, "end": 3, "position": "Beşik yanı (sandalye veya ayakta)"}]
    Kaynak KB'dir (parameter_engine.bekleme_sureleri_planla) — burada kopya
    tutulMAZ, merdiven tek yerde değişsin."""
    from engine.parameter_engine import bekleme_sureleri_planla   # döngüsel importu önle

    ham = (bekleme_sureleri_planla(plan_tipi) or {}).get("kademeli_uzaklasma") or {}
    out: list[dict] = []
    for key, pozisyon in ham.items():
        m = _RE_ASAMA_KEY.match(key)
        if m is None:                       # beklenmedik anahtar → merdiven bozuk
            raise DayParseError(f"Tanınmayan merdiven anahtarı: {key!r}")
        bas = int(m.group(1))
        son = int(m.group(2)) if m.group(2) else bas
        out.append({"start": bas, "end": son, "position": str(pozisyon).strip()})
    out.sort(key=lambda a: (a["start"], a["end"]))
    return out


def kisa_etiket(pozisyon: str) -> str:
    """Merdiven metninden kısa başlık etiketi çıkar.

    "Beşik yanı (sandalye veya ayakta)" → "Beşik yanı"
    "Kapı eşiği (anneye tam görünür — oda müsaitse)" → "Kapı eşiği" """
    etiket = re.split(r"[(;]", pozisyon or "", maxsplit=1)[0]
    return etiket.strip(" -–—.") or (pozisyon or "").strip()


def baslik_satiri(asama: dict) -> str:
    """Bu aşamanın ZORUNLU başlık biçimi. Prompt'a dayatılan ve fallback planın
    yazdığı tek kalıp — ayrıştırıcı da bunu sorunsuz okur."""
    aralik = (f"{asama['start']}-{asama['end']}"
              if asama["end"] != asama["start"] else f"{asama['start']}")
    return f"### Gün {aralik}: {kisa_etiket(asama['position'])}"


# =============================================================================
# Ayrıştırma
# =============================================================================
def parse_gun_basligi(baslik: str) -> tuple[int, int, str] | None:
    """Tek bir başlık metnini (start, end, etiket) olarak çöz; gün başlığı
    değilse None. Ölçülen beş kalıbın hepsini ve tek gün biçimlerini kabul eder."""
    metin = _RE_ONEK.sub("", (baslik or "").strip())
    if not metin:
        return None
    for rx in (_RE_GUN_ONCE, _RE_SAYI_ONCE):
        m = rx.match(metin)
        if m is None:
            continue
        bas = int(m.group(1))
        son = int(m.group(2)) if m.group(2) else bas
        if bas < 1 or son < bas:
            return None
        etiket = _RE_AYRAC.sub("", metin[m.end():]).strip(" *#:•|")
        return bas, son, etiket
    return None


def _bolum_sinirlari(markdown: str) -> tuple[int, int] | None:
    """'## Eğitim Planı' bölümünün (içerik başlangıcı, bitişi) ofsetleri.

    Bölüm, gün başlığı OLMAYAN bir sonraki üst düzey (#/##) başlıkta biter —
    böylece LLM gün başlıklarını ## düzeyinde yazsa bile bölüm erken kapanmaz.
    Ön Hazırlık'taki gün başlıkları bölümün DIŞINDA kalır (o yüzden tüm metni
    değil, yalnız bu bölümü tararız)."""
    basliklar = list(_RE_BASLIK.finditer(markdown or ""))
    bas_i = None
    for i, m in enumerate(basliklar):
        if len(m.group(1)) <= 2 and _katla(m.group(2)).startswith("egitim plani"):
            bas_i = i
            break
    if bas_i is None:
        return None
    baslangic = basliklar[bas_i].end()
    bitis = len(markdown)
    for m in basliklar[bas_i + 1:]:
        if len(m.group(1)) <= 2 and parse_gun_basligi(m.group(2)) is None:
            bitis = m.start()
            break
    return baslangic, bitis


def parse_days(markdown: str) -> list[dict]:
    """'## Eğitim Planı' bölümündeki gün bloklarını ayrıştır (doğrulama YOK).

    Dönen: [{"start","end","label","markdown"}]. Bölüm ya da gün başlığı yoksa []."""
    sinir = _bolum_sinirlari(markdown or "")
    if sinir is None:
        return []
    bas, son = sinir

    adaylar: list[tuple[int, tuple[int, int, str]]] = []
    for m in _RE_BASLIK.finditer(markdown):
        if bas <= m.start() < son:
            cozum = parse_gun_basligi(m.group(2))
            if cozum is not None:
                adaylar.append((m.start(), cozum))
    if not adaylar:                       # başlık yoksa kalın satırları dene
        for m in _RE_KALIN.finditer(markdown):
            if bas <= m.start() < son:
                cozum = parse_gun_basligi(m.group(1))
                if cozum is not None:
                    adaylar.append((m.start(), cozum))
    adaylar.sort(key=lambda a: a[0])

    out: list[dict] = []
    for i, (ofset, (g_bas, g_son, etiket)) in enumerate(adaylar):
        blok_sonu = adaylar[i + 1][0] if i + 1 < len(adaylar) else son
        out.append({"start": g_bas, "end": g_son, "label": etiket,
                    "markdown": markdown[ofset:blok_sonu].strip()})
    return out


def build_days(markdown: str, plan_tipi: str, gun_sayisi: int) -> list[dict]:
    """Doğrulanmış gün bölümleri. Beklenmedik biçimde DayParseError yükseltir.

    Doğrulanan: aşama sayısı, aşama sınırlarının KB merdiveniyle birebir eşleşmesi,
    1..gun_sayisi arası her günün TAM BİR aralığa düşmesi (boşluk/çakışma yok) ve
    her bloğun metin taşıması. Etiket boşsa ("### Gün 1-3" gibi) KB pozisyonundan
    türetilir — yapı sağlamsa 130 saniyelik yeniden üretim kozmetik bir eksik için
    harcanmaz."""
    beklenen = asamalar(plan_tipi)
    if not beklenen:
        raise DayParseError(f"Plan tipi için merdiven bulunamadı: {plan_tipi!r}")

    bulunan = parse_days(markdown)
    if not bulunan:
        raise DayParseError("'## Eğitim Planı' bölümünde hiç gün başlığı bulunamadı")
    if len(bulunan) != len(beklenen):
        raise DayParseError(
            f"Aşama sayısı uyuşmuyor: {len(bulunan)} bulundu, {len(beklenen)} bekleniyor "
            f"(bulunan: {[(d['start'], d['end']) for d in bulunan]})")

    days: list[dict] = []
    for bul, bek in zip(bulunan, beklenen):
        if (bul["start"], bul["end"]) != (bek["start"], bek["end"]):
            raise DayParseError(
                f"Aşama sınırı uyuşmuyor: {bul['start']}-{bul['end']} bulundu, "
                f"{bek['start']}-{bek['end']} bekleniyor")
        if not bul["markdown"].strip():
            raise DayParseError(f"Gün {bul['start']}-{bul['end']} bloğu boş")
        days.append({
            "start": bek["start"],
            "end": bek["end"],
            "label": bul["label"] or kisa_etiket(bek["position"]),
            "position": bek["position"],
            "markdown": bul["markdown"],
        })

    kapsanan: set[int] = set()
    for d in days:
        for gun in range(d["start"], d["end"] + 1):
            if gun in kapsanan:
                raise DayParseError(f"{gun}. gün birden fazla aralıkta")
            kapsanan.add(gun)
    beklenen_gunler = set(range(1, int(gun_sayisi) + 1))
    if kapsanan != beklenen_gunler:
        eksik = sorted(beklenen_gunler - kapsanan)
        fazla = sorted(kapsanan - beklenen_gunler)
        raise DayParseError(
            f"Gün kapsaması eksik/taşkın: eksik={eksik} fazla={fazla} "
            f"(1..{gun_sayisi} arası boşluk kalmamalı)")
    return days


def days_from_content(content: dict | None) -> list[dict]:
    """Saklanmış plan içeriğinden gün bölümlerini türet (OKUMA yolu, hata YÜKSELTMEZ).

    Eski planlarda content["days"] yok; GET yolunda bir kez türetilip DB'ye yazılır.
    Ayrıştırılamazsa [] döner ve UYARI loglanır — eski bir planın okunması 502
    olmamalı, ama sessiz de kalmamalı (Sentry'de görünür)."""
    icerik = content or {}
    markdown = icerik.get("markdown")
    secim = icerik.get("plan_secimi") or {}
    tip, gunler = secim.get("tip"), secim.get("gunler")
    if not markdown or not tip or not gunler:
        return []
    try:
        return build_days(markdown, str(tip), int(gunler))
    except (DayParseError, ValueError, TypeError) as e:
        logger.warning("Eski plandan gün bölümleri türetilemedi (tip=%s): %s", tip, e)
        return []
