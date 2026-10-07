"""
Bebek adı için TAKMA AD — gerçek ad yapay zekâ sağlayıcısına (Anthropic) gitmez.

KARAR (2026-10-07): cevaplarda bebeğin adı kalır, ama istemde gerçek ad yerine
aynı SES UYUMUNA sahip bir takma ad gider; cevap dönünce takma ad gerçek adla
değiştirilir. Ses uyumu korunduğu için modelin yazdığı ekler ("…'nın", "…'ya",
"…'da") gerçek ad için de doğru kalır:

  • son ünlünün grubu aynı: a/ı · e/i · o/u · ö/ü (dört yönlü uyum, ikisi de
    iki yönlü uyumu belirler),
  • adın sonu aynı tür: ünlü · yumuşak ünsüz · sert ünsüz (f s t k ç ş h p —
    "-da/-ta", "-dan/-tan" seçimini bu belirler).

Takma adlar uydurmadır ("Z?lv?" kalıbı): Türkçede anlamı yok, gerçek bir kelimeyle
ya da korpustaki bir adla karışmaz; bu sayede geri çevirme düz metin değişimidir.

Annenin sorusunda ad geçiyorsa (ekli ve kesmesiz yazımlar dahil: "Ada'nın",
"Adanın") göndermeden önce takma adla değiştirilir. YANLIŞ EŞLEŞME KORUMASI:
  • kesmesiz yazımda yalnız ad BÜYÜK harfle başlıyorsa ve ardından bir hâl eki
    geliyorsa eşlenir — "canım" (iyelik eki) ve küçük harfli "can" eşlenmez;
  • küçük harfle yazılmış ad yalnız kesmeyle eşlenir ("ada'nın") — "ada" (kara
    parçası) kelimesi dokunulmadan kalır.
"""
from __future__ import annotations

import hashlib
import re

UNLU_GRUBU = {"a": "a", "ı": "a", "â": "a",
              "e": "e", "i": "e", "î": "e",
              "o": "o", "u": "o", "û": "o",
              "ö": "ö", "ü": "ö"}
SERT_UNSUZLER = set("fstkçşhp")

# (ünlü grubu, son tür) → (birincil, yedek). Yedek: gerçek ad birincile eşitse.
TAKMA_ADLAR = {
    ("a", "unlu"): ("Zalva", "Zarva"),
    ("a", "yumusak"): ("Zalvan", "Zarvan"),
    ("a", "sert"): ("Zalvat", "Zarvat"),
    ("e", "unlu"): ("Zelvi", "Zervi"),
    ("e", "yumusak"): ("Zelvin", "Zervin"),
    ("e", "sert"): ("Zelvit", "Zervit"),
    ("o", "unlu"): ("Zolvu", "Zorvu"),
    ("o", "yumusak"): ("Zolvun", "Zorvun"),
    ("o", "sert"): ("Zolvut", "Zorvut"),
    ("ö", "unlu"): ("Zölvü", "Zörvü"),
    ("ö", "yumusak"): ("Zölvün", "Zörvün"),
    ("ö", "sert"): ("Zölvüt", "Zörvüt"),
}

# Kesmesiz yazılan özel addan sonra gelebilecek HÂL EKLERİ (iyelik HARİÇ:
# "canım", "canın" gibi kelimeler ad sanılmasın diye -ım/-im… listede yok;
# "-ın/-nın" tamlayan eki ise ad için gerekli, büyük harf şartıyla korunur).
_HAL_EKLERI = (
    r"n?[ıiuü]n(?:ki)?",          # tamlayan: -ın/-nın (+ki)
    r"y?[ıiuü]",                  # belirtme: -ı/-yı
    r"y?[ae]",                    # yönelme: -a/-ya
    r"[dt][ae](?:ki|n)?",         # bulunma/ayrılma: -da/-ta, -dan/-tan, -daki
    r"y?l[ae]",                   # vasıta: -la/-yla
    r"c[ıiuü]k",                  # sevgi eki: -cık
)
_EK = "(?:" + "|".join(_HAL_EKLERI) + ")"
_HARF = r"[^\W\d_]"
_KESME = "['’`´]"


def tr_kucuk(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def tr_buyuk(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def tr_buyuk_bas(s: str) -> str:
    if not s:
        return s
    ilk = s[0]
    ilk = {"i": "İ", "ı": "I"}.get(ilk, ilk.upper())
    return ilk + s[1:]


def _ses_ozelligi(ad: str) -> tuple[str, str]:
    """(ünlü grubu, son tür) — çok kelimeli adda SON kelimeye bakılır (ek oraya
    gelir). Ünlüsüz/harfsiz adda varsayılan a-grubu, ünlü son."""
    kelimeler = [k for k in re.split(r"\s+", tr_kucuk(ad).strip()) if k]
    son = re.sub(r"[^\w]", "", kelimeler[-1]) if kelimeler else ""
    son = re.sub(r"[\d_]", "", son)
    grup = next((UNLU_GRUBU[h] for h in reversed(son) if h in UNLU_GRUBU), "a")
    if not son or son[-1] in UNLU_GRUBU:
        tur = "unlu"
    elif son[-1] in SERT_UNSUZLER:
        tur = "sert"
    else:
        tur = "yumusak"
    return grup, tur


def takma_ad_sec(ad: str | None) -> str | None:
    """Gerçek ada ses uyumlu takma ad. Ad boşsa None (değiştirilecek bir şey yok)."""
    if not ad or not ad.strip():
        return None
    birincil, yedek = TAKMA_ADLAR[_ses_ozelligi(ad)]
    return yedek if tr_kucuk(ad.strip()) in (tr_kucuk(birincil),) else birincil


def _bicimler(ad: str) -> list[str]:
    """Metinde aranacak ad biçimleri: tam ad (+ çok kelimeliyse ilk kelime)."""
    ad = ad.strip()
    out = [ad]
    ilk = ad.split()[0] if " " in ad else None
    if ilk and len(ilk) >= 2:
        out.append(ilk)
    return out


def adi_gizle(metin: str | None, ad: str | None, takma: str | None) -> str | None:
    """Metindeki gerçek adı takma adla değiştir (eki korunarak).

    Eşlenen yazımlar: "Ada", "Ada'nın", "Adanın", "ADA", "ada'nın".
    Eşlenmeyen: "ada" (kesmesiz küçük harf), "canım" (iyelik eki)."""
    if not metin or not ad or not takma:
        return metin
    for bicim in sorted(_bicimler(ad), key=len, reverse=True):
        govdeler = {tr_buyuk_bas(bicim), tr_buyuk(bicim), bicim}
        govdeler.discard(tr_kucuk(bicim))            # küçük harf yalnız kesmeyle
        for g in sorted(govdeler, key=len, reverse=True):
            desen = re.compile(
                rf"(?<!{_HARF}){re.escape(g)}"
                rf"(?=(?:{_KESME}{_HARF}*)?(?!{_HARF})|{_EK}(?!{_HARF}))")
            metin = desen.sub(takma, metin)
        kucuk = re.compile(rf"(?<!{_HARF}){re.escape(tr_kucuk(bicim))}(?={_KESME})")
        metin = kucuk.sub(takma, metin)
    return metin


def adi_geri_koy(metin: str | None, takma: str | None, ad: str | None) -> str | None:
    """Cevaptaki takma adı gerçek adla değiştir. Takma ad uydurma olduğu için
    düz değişim yeterli; BÜYÜK harfli yazım da çevrilir."""
    if not metin or not takma or not ad:
        return metin
    gercek = ad.strip()
    metin = metin.replace(tr_buyuk(takma), tr_buyuk(gercek))
    metin = metin.replace(takma, gercek)
    return metin.replace(tr_kucuk(takma), gercek)


def ozet(ad: str | None) -> str:
    """Log için: adın kendisi değil, kısa bir özeti."""
    return hashlib.sha256((ad or "").encode("utf-8")).hexdigest()[:8]
