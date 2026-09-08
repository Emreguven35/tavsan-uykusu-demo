"""
Türkçe küfür/hakaret sözlüğü üreteci (Faz T, K0 filtresi).

data/mod_wordlist.txt üretir: NET küfür/hakaret kökleri + ölçülü çekim ekleri.
Girdiler NORMALİZE biçimde (küçük, ascii-translit) yazılır — moderation._normalize
ile aynı biçim; runtime'da liste yeniden normalize edilmeden eşleşir.

DEFANSİF amaç: anne topluluğunu taciz/hakaretten korumak. AŞIRI BLOKLAMAYI önlemek
kritik — bu bir EBEVEYN/EMZİRME topluluğu: "meme", "hıyar" (salatalık), "götür",
"adı", "mal" gibi masum kelimeler ASLA bloklanmamalı. Bu yüzden:
  - Kök listesi yalnız NET hakaret içerir (ambigü kelimeler dışarıda),
  - Ek genişletmesi dar tutulur (masum türev üretmemek için),
  - moderation.WHITELIST bağlamsal masumları ayıklar (ikinci güvenlik ağı).

TÜRKÇE ı/i UYARISI: burada üretilen kökler ascii'dir ("sik", "gerizekali").
Runtime normalizasyonu ARTIK "ı"yı "i"ye katlamaz — yoksa masum "sık" ("sık sık
uyanıyor") bu listedeki "sik" köküne çarpardı. moderation tarafı sözlüğe bakarken
ı→i katlanmış biçimi de dener (kullanıcı "gerizekalı" yazar, liste "gerizekali"
tutar), ama "sik-" ailesinde net olmayan çekimleri BLOKLAMAZ; onları K1'de
"belirsiz_hakaret" ile işaretleyip kararı K2'deki Haiku'ya bırakır. Bu listeye
"sik" kökü altında yeni çekim eklerken bunu hatırla: eklediğin biçimin noktasız
yazılmış bir "sık-" kelimesiyle çakışıp çakışmadığına bak.

Çalıştırma: python scripts/build_mod_wordlist.py
"""
import sys
import unicodedata
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "mod_wordlist.txt"

_TR = str.maketrans({"ş": "s", "ı": "i", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
                     "İ": "i", "Ş": "s", "Ğ": "g", "Ü": "u", "Ö": "o", "Ç": "c"})


def norm(s: str) -> str:
    s = s.strip().lower().translate(_TR)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s


# NET hakaret kökleri (ekle genişletilir). Ambigü/masum-çakışan kelimeler YOK.
EXPANDABLE = [
    "sik", "siktir", "sikik", "sikis", "siktir", "yarrak", "yarak",
    "amcik", "orospu", "orospucocugu", "kahpe", "kaltak", "surtuk", "kancik",
    "pic", "pezevenk", "gavat", "kevase", "fahise", "yavsak", "godos",
    "gerizekali", "dangalak", "embesil", "serefsiz", "haysiyetsiz", "namussuz",
    "pust", "ibne", "ipne", "pust", "gotveren", "godoslu",
]
# İSİM/FİİL ekleri — masum türev üretmeyecek kadar dar tutuldu.
SUFFIXES = ["", "i", "e", "in", "im", "sin", "siniz", "ler", "leri", "tir", "ci", "cik"]

# Tek biçim (ek EKLENMEZ) — çok kelimeli kalıplar + kısaltmalar + riskli tek gövdeler.
LITERAL = [
    "siktir git", "siktir lan", "sikeyim", "sikerim", "sikeyim seni", "sikimi",
    "sikimde", "amina koyayim", "amina koyim", "aminakoyayim", "amk", "amq",
    "amcik", "ananin ami", "anani sikeyim", "avradini", "orospu cocugu",
    "oc", "oç", "ocoglu", "gotoglani", "got oglani", "gotlek", "gotu",
    "yarragi", "yarragimi", "taşşak", "tassak", "gebertirim", "gebertecegim",
    "defol", "defol git", "gerizekali", "geri zekali", "salaksin", "aptalsin",
    "malsin", "orospusun", "kahpesin", "picsin", "surtuksun", "yavsaksin",
    "serefsizsin", "gerizekalisin", "haysiyetsiz", "sulaleni sikeyim",
    "ananı", "avradını", "sülaleni", "pezevengin",
]
# Bağlamsal olarak masum — üretilse bile filtreye YAZILMAZ (referans + whitelist).
# EBEVEYN/EMZİRME/BESLENME bağlamı kritik: meme, hıyar, mal, adı, götür, ...
INNOCENT = {
    "sikke", "orospuotu", "amca", "amac", "amele", "gotur", "goturur",
    "picture", "salatalik", "meme", "memeli", "hiyar", "mal", "adi", "top",
}


def main():
    words: set[str] = set()
    for root in EXPANDABLE:
        base = norm(root)
        if not base:
            continue
        for suf in SUFFIXES:
            w = norm(base + suf)
            if len(w) >= 3:
                words.add(w)
    for lit in LITERAL:
        w = norm(lit)
        if len(w) >= 2:
            words.add(w)
    words -= {norm(w) for w in INNOCENT}
    lines = sorted(words)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    print(f"{len(lines)} kelime yazildi -> {OUT}")


if __name__ == "__main__":
    main()
