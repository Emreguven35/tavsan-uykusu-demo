"""
Topluluk dürüstlüğü, 2. adım (B5 — onaylı plan): "Tavşan Uykusu Ekibi"ne
taşınmış 5 başlık + 25 cevap.

  • Her başlık ekip diline yeniden yazılır: "Annelerden sık gelen bir soru:
    … Önerimiz: …" — genel bilgi/tavsiye içeren 8 cevap bu metne BİRLEŞTİRİLİR.
  • Birinci ağızdan kişisel deneyim/hikâye cevapları (17) ve birleştirilen 8
    cevap SİLİNİR (yalnız resmi hesaba taşınmış cevaplar). Metodolojiye aykırı cümle (092b2f2b: "dalmadan memeden
    ayır" ↔ "beslenme uykudan 1 saat önce biter") ve tıbbi iddia bırakılmaz.
  • Değiştirilen/silinen HER ŞEY önce ARŞİVLENİR: DENETIM kökünün yanında
    /data/arsiv/topluluk-ekip-duzenleme-{zaman}.json (tam satırlar).

    python scripts/topluluk_ekip_duzenleme.py            # kuru koşu
    python scripts/topluluk_ekip_duzenleme.py --uygula
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Başlık kimlik öneki → (yeni başlık, yeni gövde, birleştirilen cevap önekleri)
PLAN: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "116de09d": (
        "Bebek gece uyanıp oyun oynuyorsa",
        "Annelerden sık gelen bir soru: 8-9 aylık bebek gece uyanıyor, ağlamıyor "
        "ama yatağında oturup oynayarak uzun süre uyanık kalıyor; aç değil, bezi "
        "kuru. Ne yapmalı?\n\n"
        "Önerimiz: Bu yaşta gece uyanıklığı çoğu zaman yeni kazanılan bir hareket "
        "becerisiyle (oturma, emekleme, tutunup kalkma) birlikte görülür. Gündüz bu "
        "beceriye bolca alan açın; gece uyanıklığında ortamı sıkıcı tutun: ışığı "
        "açmayın, göz temasını ve konuşmayı en aza indirin. Uyanıklık haftalarca "
        "aynı şiddette sürüyorsa ya da gündüz belirgin huzursuzluk veya beslenmede "
        "değişiklik eşlik ediyorsa doktorunuza danışın.",
        ("8767bf34",)),
    "60969c1c": (
        "Öğlen uykusunu reddediyor: tek uykuya geçiş zamanı mı?",
        "Annelerden sık gelen bir soru: 14 aylık bebek bir süredir öğlen uykusunu "
        "reddediyor ama akşamüstü çok huysuzlanıyor. Tek uykuya geçmek için erken mi?\n\n"
        "Önerimiz: Tek uykuya geçiş çoğunlukla 15-18 ay arasında olur; 14 ayda da "
        "görülebilir ama bu yaşta en sık görülen şey kalıcı bir geçişten çok geçici "
        "bir dönemdir. İki işarete bakın: uykuyu atladığı günlerde akşam çöküşü "
        "oluyorsa henüz hazır değildir; birkaç hafta üst üste atlıyor ve akşamı da "
        "iyi geçiyorsa geçiş başlamış olabilir. Aceleye getirmeden gözlemlemek en "
        "rahatıdır.",
        ("3f0399af",)),
    "8bf31b05": (
        "Eğitimin 3.-4. günü zorlaşınca",
        "Annelerden sık gelen bir soru: Uyku eğitiminin ilk günleri iyi gitti, 3.-4. "
        "günde geri gidiş var ve çok yıprandım. Bırakmalı mıyım?\n\n"
        "Önerimiz: Bir iki günlük geri gidiş sürecin sık görülen bir parçasıdır. "
        "Devam edecekseniz en çok işe yarayan şey tutarlılıktır; bunu sürdürebilmek "
        "için önce sizin dinlenmeniz gerekir — destek isteyebileceğiniz biri varsa "
        "tam bu günler için isteyin. Kendinizi kötü hissettiren bir yönteme devam "
        "etmek zorunda değilsiniz: ara verip daha sonra yeniden başlamak da geçerli "
        "bir seçenektir (Eğitim videolarındaki \"Eğitimi ne zaman durdurmalı ya da "
        "ara vermeliyiz?\" bölümüne bakabilirsiniz). Uzun süre kendinizi çok kötü "
        "hissediyorsanız bunu bir uzmanla konuşmaktan çekinmeyin.",
        ("37cfe11e",)),
    "9f8ab693": (
        "Uyku ortamı: karartma, sıcaklık ve beyaz gürültü",
        "Annelerden sık gelen bir soru: Bebeğin odasını düzenlerken karartma, beyaz "
        "gürültü ve yaz sıcağı için nelere dikkat etmeli?\n\n"
        "Önerimiz:\n"
        "• Karartmada asıl fark kumaşta değil, kenarlardan sızan ışıktadır; perdeyi "
        "tavana kadar ve pencereden taşacak şekilde takmak ışığı en iyi keser.\n"
        "• Sıcak günlerde gündüz panjurları kapalı tutup akşam havalandırmak odayı "
        "serin tutar; klima kullanıyorsanız hava akımını doğrudan bebeğe yöneltmeyin.\n"
        "• Yazın battaniye yerine ince pamuklu bir uyku tulumu kullanılabilir.\n"
        "• Beyaz gürültüyü telefondan çalacaksanız telefonu uçak moduna alın; sesi "
        "yatağa yakın koymayın ve düşük seviyede tutun.",
        ("23d43b1f", "6593063a", "b77345f2", "955d4cc0")),
    "dac4d856": (
        "Emzirerek uyuyan bebekte emzirme ile uykuyu ayırmak",
        "Annelerden sık gelen bir soru: 10 aylık bebek yalnızca emerek uykuya dalıyor "
        "ve gece her uyanışta meme istiyor. Emzirmeyi bırakmadan uykuyla bağlantısını "
        "nasıl gevşetebiliriz?\n\n"
        "Önerimiz: Emzirmeyi uyku rutininin başına alın: önce emzirme, sonra kitap "
        "ya da ninni, en son yatak. Beslenme ile uykuya dalma arasına mesafe koymak, "
        "uykunun memeye bağlanmasını yavaş yavaş çözer; bu birkaç haftada olur. "
        "Ayrıntısı Eğitim videolarındaki \"Uykuya geçmeden önce beslenme mesafesi\" "
        "ve \"Emzirilerek uyutulan bebeklerde…\" bölümlerinde.",
        ("cccadaee",)),
}
# Onaylı tablodaki kararların toplamı: 8 cevap birleşir (9f8ab693'te 4), 17 silinir.
BEKLENEN_BIRLESEN = 8
BEKLENEN_SILINEN = 17


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from api.db import SessionLocal
    from api.models import Like, Reply, Thread, User
    from api.services.denetim import denetim_koku
    from api.services.kvkk import _satir
    from scripts.topluluk_resmi_hesap import RESMI_EPOSTA

    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        resmi = db.query(User).filter(User.email == RESMI_EPOSTA).one()
        konular = db.query(Thread).filter(Thread.user_id == resmi.id).all()
        onek = {t.id.hex[:8] if hasattr(t.id, "hex") else str(t.id)[:8]: t for t in konular}
        eksik = [k for k in PLAN if k not in onek]
        if eksik:
            print("HATA — plandaki başlık bulunamadı:", eksik)
            return 1
        arsiv = {"zaman": datetime.now(timezone.utc).isoformat(), "konular": [],
                 "silinen_cevaplar": [], "silinen_begeniler": []}
        silinecek, birlesen = [], []
        for k, (baslik, govde, birlestir) in PLAN.items():
            t = onek[k]
            arsiv["konular"].append(_satir(t))
            # YALNIZ resmi hesaba taşınmış (tohum) cevaplar; silinmiş hesaplardan
            # kalan cevaplar onaylı planın parçası DEĞİL — dokunulmaz.
            cevaplar = db.query(Reply).filter(Reply.thread_id == t.id,
                                              Reply.user_id == resmi.id).all()
            for r in cevaplar:
                arsiv["silinen_cevaplar"].append(
                    {**_satir(r), "karar": "birlestirildi"
                     if str(r.id)[:8] in birlestir else "silindi"})
                (birlesen if str(r.id)[:8] in birlestir else silinecek).append(r)
        print(f"başlık yeniden yazılacak: {len(PLAN)} · birleşen cevap: {len(birlesen)} "
              f"· silinecek cevap: {len(silinecek)}")
        if len(birlesen) != BEKLENEN_BIRLESEN or len(silinecek) != BEKLENEN_SILINEN:
            print("HATA — sayılar onaylanan planla uyuşmuyor, dokunulmadı")
            return 1
        if not a.uygula:
            print("KURU KOŞU — --uygula verilmedi")
            return 0

        # 1) ARŞİV (değişiklikten ÖNCE)
        klasor = denetim_koku().parent / "arsiv"
        klasor.mkdir(parents=True, exist_ok=True)
        cevap_idleri = [r.id for r in birlesen + silinecek]
        for lk in db.query(Like).filter(Like.target_type == "reply",
                                        Like.target_id.in_(cevap_idleri)).all():
            arsiv["silinen_begeniler"].append(_satir(lk))
        yol = klasor / f"topluluk-ekip-duzenleme-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
        yol.write_text(json.dumps(arsiv, ensure_ascii=False, indent=1), "utf-8")
        print("arşiv:", yol)

        # 2) UYGULA
        db.query(Like).filter(Like.target_type == "reply",
                              Like.target_id.in_(cevap_idleri)).delete(synchronize_session=False)
        for r in birlesen + silinecek:
            db.delete(r)
        for k, (baslik, govde, _b) in PLAN.items():
            t = onek[k]
            t.title, t.body = baslik, govde
            t.reply_count = db.query(Reply).filter(Reply.thread_id == t.id,
                                                   Reply.status == "published").count()
            t.expert_replied = False
        from api.models import CommunityProfile
        prof = db.query(CommunityProfile).filter(CommunityProfile.user_id == resmi.id).one()
        prof.post_count = len(konular)
        db.commit()
        print(f"uygulandı: {len(PLAN)} başlık yeniden yazıldı, {len(birlesen)} cevap "
              f"birleştirildi, {len(silinecek)} cevap silindi, "
              f"{len(arsiv['silinen_begeniler'])} beğeni silindi")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
