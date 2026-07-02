"""
Birim test — TTS metin temizleme (api/konusma_metni.konusma_metnine_cevir).
Her dönüşüm kuralı için girdi/çıktı + kullanıcının yaşadığı GERÇEK cevap formatı
(kalın + tireli liste + emoji + "—") uçtan uca test edilir.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from api.konusma_metni import konusma_metnine_cevir as K  # noqa: E402

_r = []


def chk(ad, kosul, detay=""):
    _r.append((ad, bool(kosul), detay))


def main():
    # 1) Markdown işaretleri kalkar, içerik kalır
    o = K("**Kalın** ve *italik* ile `kod` metni.")
    chk("1) markdown kalkar",
        "Kalın ve italik ile kod metni." == o and "*" not in o and "`" not in o, o)

    # 2) Başlık işareti kalkar
    o = K("### Önemli Başlık\nAçıklama cümlesi.")
    chk("2) başlık işareti kalkar", "#" not in o and "Önemli Başlık" in o, o)

    # 3) Tireli liste → cümleler ("Başlık: açıklama" tek cümleye bağlanır)
    o = K("- Bekleme süresi: kısa aralıklarla bekleyin\n- Beyaz gürültü kısılır")
    chk("3) liste → cümle + Başlık bağlama",
        "Bekleme süresi, kısa aralıklarla bekleyin." in o
        and "Beyaz gürültü kısılır." in o, o)

    # 4) Numaralı liste → cümle
    o = K("1. İlk adım yapılır\n2. İkinci adım yapılır")
    chk("4) numaralı liste → cümle",
        "İlk adım yapılır." in o and "İkinci adım yapılır." in o
        and "1." not in o, o)

    # 5) Emoji ve semboller çıkar
    o = K("Beyaz gürültü 💙 açık kalır ⚡ ve A → B geçişi ✅ tamam.")
    chk("5) emoji/sembol çıkar",
        all(sym not in o for sym in ("💙", "⚡", "→", "✅")), o)

    # 6) em dash / "--" → virgül; en dash & tire KORUNUR
    o = K("Birinci nokta — ikinci nokta -- üçüncü. 3–5 gün, yatır-çık yöntemi.")
    chk("6a) em dash & -- → virgül", "—" not in o and "--" not in o, o)
    chk("6b) en dash '–' korunur", "3–5 gün" in o, o)
    chk("6c) normal tire korunur", "yatır-çık" in o, o)

    # 7) Parantez → virgül, içerik korunur; vb. → ve benzeri
    o = K("Yatıştırma yöntemleri (elini tutma, emme vb.) uygulanır.")
    chk("7) parantez→virgül + kısaltma açılır",
        "(" not in o and ")" not in o and "elini tutma, emme ve benzeri" in o
        and "vb." not in o, o)

    # 8) Kısaltmalar açılır
    o = K("örn. gece uykusu; bkz. protokol; 45 dk. bekle.")
    chk("8) kısaltmalar açılır",
        "örneğin" in o and "bakınız" in o and "dakika" in o
        and "örn." not in o and "bkz." not in o, o)

    # 9) Çift boşluk/satır ve art arda noktalama sadeleşir
    o = K("İlk cümle.   İkinci   cümle..\n\n\nÜçüncü,, cümle.")
    chk("9) boşluk/noktalama sadeleşir",
        "   " not in o and ".." not in o and ",," not in o, o)

    # 10) GERÇEK cevap formatı — uçtan uca (kalın + tireli liste + emoji + —)
    gercek = (
        "**Gece uyanmalarında** şunları deneyebilirsiniz:\n\n"
        "- Bekleme süresi: bebeğinizi hemen almayın, kısa aralıklarla bekleyin\n"
        "- Kademeli müdahale (elini tutma, emme vb.) — sakinleşince yatağa bırakın\n"
        "- Beyaz gürültü 💙 açık kalabilir; her uykuda bir kademe kısın\n\n"
        "3–5 gün içinde yatır-çık yöntemiyle iyileşir. ⚡ Sabırlı olun!"
    )
    o = K(gercek)
    chk("10a) gerçek: markdown/emoji/em-dash yok",
        all(x not in o for x in ("**", "*", "💙", "⚡", "—", "(", ")")), o)
    chk("10b) gerçek: en-dash & tire korunur",
        "3–5 gün" in o and "yatır-çık" in o, o)
    chk("10c) gerçek: liste içeriği cümleleşti",
        "Bekleme süresi, bebeğinizi hemen almayın" in o
        and "elini tutma, emme ve benzeri" in o, o)

    # --- özet ---
    print("\n" + "=" * 74)
    print("KONUŞMA METNİ TEMİZLEME — BİRİM TEST")
    print("=" * 74)
    ok = 0
    for ad, cond, detay in _r:
        print(f"[{'PASS' if cond else 'FAIL'}] {ad}")
        if not cond:
            print(f"       ÇIKTI: {detay}")
        ok += cond
    print("-" * 74)
    print(f"TOPLAM: {ok}/{len(_r)} gecti")
    print("=" * 74)
    # örnek çıktı (rapora)
    print("\nÖRNEK (gerçek cevap) TEMİZLENMİŞ:\n" + K(gercek))
    return 0 if ok == len(_r) else 1


if __name__ == "__main__":
    sys.exit(main())
