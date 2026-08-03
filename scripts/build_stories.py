"""
Masal kütüphanesi üretici — Claude API ile BİR KEZ üretir, data/stories.json'a yazar.

Faz 6.7. Metinler statik dosyada saklanır; /voice/stories ve /voice/generate her
istekte LLM ÇAĞIRMAZ (maliyet + gecikme + tutarlılık). Katalog değişecekse bu
script yeniden koşulur ve çıktı commit'lenir.

Çalıştırma:
    python scripts/build_stories.py            # eksik masalları üret
    python scripts/build_stories.py --force    # hepsini yeniden üret

Ninniler DEĞİŞMEZ — mevcut 3 ninni olduğu gibi korunur.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()

import anthropic  # noqa: E402

MODEL = "claude-opus-5"
STORIES_PATH = ROOT / "data" / "stories.json"

# Uyku öncesi masal yazarlığı kuralları. TTS'e düz metin gider (mevcut
# konusma_metnine_cevir katmanı markdown/emoji temizler, ama biz baştan üretmeyiz).
SYSTEM = """Sen Türkçe çocuk masalı yazarısın. Uyku öncesi dinlenmek üzere, sesli \
okunacak masallar yazıyorsun.

KURALLAR:
- Türkçe, akıcı, sıcak bir anlatı. Kısa cümleler kur (ortalama 8-14 kelime).
- Uyku öncesi ton: sakin, yumuşak, giderek yavaşlayan ritim.
- ŞİDDET, KORKU, ölüm, yeme-yutma, tehdit YOK. Gerilim yerine merak kullan.
- Sonu MUTLU ve SAKİN bitsin; dinleyen çocuk huzurla uykuya geçebilsin.
- Sadece DÜZ METİN üret: başlık yazma, markdown yok, madde işareti yok, emoji yok, \
yıldız/tire/başlık işareti yok. Yalnız paragraflar ve normal noktalama.
- Tırnak içinde konuşmalar olabilir ama abartılı ünlem kullanma.
- UZUNLUK ZORUNLU: EN AZ 620, EN FAZLA 700 kelime. Sesli okunduğunda ~5 dakika
  sürmeli. Kısa bitirme; sahneleri ve betimlemeleri gerektiği kadar genişlet.
- Doğrudan masalın ilk cümlesiyle başla."""

BRIEFLER = [
    ("masal_kelogan_sihirli_degnek", "Keloğlan ile Sihirli Değnek",
     "Türk halk masalı Keloğlan'ı uyku öncesine uygun biçimde anlat. Keloğlan "
     "iyi kalpli, tembel değil ama rahatına düşkün, kurnazlığı zararsız bir "
     "çocuktur. Bulduğu sihirli değnek dilediğini yapar; ama Keloğlan sonunda "
     "en güzel şeyin değnekle değil, kendi elleriyle ve annesiyle birlikte "
     "yapılan şeyler olduğunu anlar. Kavga, ceza, korku olmasın."),

    ("masal_kirmizi_baslikli_kiz", "Kırmızı Başlıklı Kız",
     "Kırmızı Başlıklı Kız'ı YUMUŞATILMIŞ anlat: kurt KİMSEYİ YEMEZ, kimseyi "
     "yutmaz, kimseye zarar vermez. Kurt sadece yalnız, biraz utangaç ve aslında "
     "arkadaş arayan bir ormancıdır; büyükannenin evine erken varıp uyuyakalmıştır. "
     "Kırmızı Başlıklı Kız korkmaz, şaşırır ve gülümser. Sonunda üçü birlikte "
     "çorba içer, kurt ormana uğurlanır. Avcı, balta, tehlike, saklanma korkusu "
     "OLMASIN. Ton tamamen tatlı ve güven verici."),

    ("masal_uc_kucuk_domuzcuk", "Üç Küçük Domuzcuk",
     "Üç Küçük Domuzcuk'u YUMUŞATILMIŞ anlat: kurt evleri yıkmaz, kimseyi "
     "kovalamaz, kimseyi yemez. Kurt sadece üşümüş ve yorgundur; kapıları "
     "çalarken aslında sığınacak sıcak bir yer aramaktadır. Üfleme sahnesi yerine, "
     "kurt saman ve çöp evlerin rüzgârda üşüttüğünü fark eder ve kardeşlere tuğla "
     "evi birlikte yapmalarında YARDIM eder. Sonunda dördü birlikte ocağın başında "
     "ısınır. Yıkım, kaçış, baca, kaynar kazan OLMASIN."),

    ("masal_cirkin_ordek_yavrusu", "Çirkin Ördek Yavrusu",
     "Çirkin Ördek Yavrusu'nu anlat ama dışlanma ve alay sahnelerini çok "
     "yumuşat: kimse ona kötü söz söylemez, sadece kendini farklı hisseder ve "
     "yerini arar. Yolculuğunda nazik hayvanlarla tanışır. Kış sahnesi korkutucu "
     "değil, sessiz ve uyku gibi olsun. Sonunda kuğu olduğunu anlar; asıl mesaj "
     "'herkesin büyüme zamanı farklıdır' olsun. Üzüntü değil, şefkat hissi kalsın."),

    ("masal_aysecik_uyku_perisi", "Ayşecik ile Uyku Perisi",
     "ÖZGÜN bir uyku masalı yaz (halk masalı değil). Ayşecik adında bir kız, "
     "gece uyuyamayınca penceresine Uyku Perisi gelir ve onu sakin bir yolculuğa "
     "çıkarır. TEKRARLI bir yapı kur: her durakta aynı kalıp cümle biraz "
     "değişerek döner (örneğin her yerde bir şey usulca uykuya dalar). Ritim "
     "GİDEREK YAVAŞLASIN: cümleler sona doğru kısalsın. Esneme, göz kapaklarının "
     "ağırlaşması, yumuşak nefes, ılık yorgan gibi uyku imgeleri kullan. "
     "Son paragraf çok kısa olsun ve FISILTIYLA biten bir cümleyle kapansın."),
]


def _ilk_masal_metni(client: anthropic.Anthropic, baslik: str, brief: str) -> str:
    """Tek bir masal üret. Uzun çıktı → streaming (SDK zaman aşımı riski yok)."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   f"Masalın adı: {baslik}\n\n{brief}\n\n"
                   f"Şimdi masalı yaz. Başlık yazma, doğrudan anlatıya başla."}],
    ) as stream:
        mesaj = stream.get_final_message()

    if mesaj.stop_reason == "refusal":
        raise RuntimeError(f"Model reddetti ({baslik}): {mesaj.stop_details}")
    metin = "".join(b.text for b in mesaj.content if b.type == "text").strip()
    if not metin:
        raise RuntimeError(f"Boş metin döndü: {baslik}")
    return _temizle(metin)


# Emoji/markdown kalıntısı: TTS katmanı zaten temizliyor ama metin mobil ekranda
# da gösteriliyor — kaynakta temiz tutulur.
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]+")


def _temizle(metin: str) -> str:
    metin = _EMOJI.sub("", metin)
    metin = re.sub(r"\*\*|\*|#{1,6}\s*|^\s*[-•]\s+", "", metin, flags=re.MULTILINE)
    metin = re.sub(r"[ \t]{2,}", " ", metin)
    metin = re.sub(r"\n{3,}", "\n\n", metin)
    return metin.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="mevcut masalları da yeniden üret")
    args = ap.parse_args()

    mevcut = json.loads(STORIES_PATH.read_text(encoding="utf-8"))
    ninniler = mevcut.get("ninniler", [])           # NİNNİLER AYNEN KALIR
    eski_masallar = {m["id"]: m for m in mevcut.get("masallar", [])}

    client = anthropic.Anthropic()
    masallar = []
    for sid, baslik, brief in BRIEFLER:
        if not args.force and sid in eski_masallar and eski_masallar[sid].get("text"):
            print(f"[ATLA] {baslik} (zaten var)")
            masallar.append(eski_masallar[sid])
            continue
        print(f"[ÜRET] {baslik} ...", flush=True)
        metin = _ilk_masal_metni(client, baslik, brief)
        kelime = len(metin.split())
        print(f"        {kelime} kelime, {len(metin)} karakter")
        masallar.append({
            "id": sid, "type": "masal", "title": baslik,
            "duration_hint": "5 dk", "text": metin,
        })

    STORIES_PATH.write_text(
        json.dumps({"masallar": masallar, "ninniler": ninniler},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\nYazıldı: {STORIES_PATH}")
    print(f"masallar={len(masallar)} ninniler={len(ninniler)} (ninniler değişmedi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
