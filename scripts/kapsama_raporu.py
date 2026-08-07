"""
Kapsama raporu — korpusta karşılıksız kalan konuları çıkarır (Faz E-2, madde 5).

AMAÇ: K3.5 ve K4'e düşen sorular, bilgi tabanının EKSİK olduğu yerlerdir.
Haftalık olarak bu liste İlayda'ya gider ve korpus güncelleme turunun girdisi
olur — kalıcı çözüm budur, sözlük yamamak değil.

Katmanların anlamı:
  k1    — doğrudan cevaplandı (korpus yeterli)
  k2    — en yakın bilgiye dayandırıldı
  k3    — alan içi, spesifik kayıt yok → genel ilkelerden cevaplandı
  k3_5  — alan içi ama korpusta karşılığı ZAYIF → "net kayıt yok" + ilkeler
  k4    — gerçekten alan dışı (mama tarifi, vergi...) — normal, aksiyon gerekmez
  ruhsal_kriz — destek kapısı (kapsama analizine GİRMEZ)

Kullanım:
    python scripts/kapsama_raporu.py                 # son 7 gün
    python scripts/kapsama_raporu.py --gun 30        # son 30 gün
    python scripts/kapsama_raporu.py --json rapor.json

NOT (KVKK): rapor SORU METNİNİ içerir — bu, hangi konunun eksik olduğunu
görmenin tek yolu. Rapor dışarı paylaşılırken kişisel ayrıntı içermediği
kontrol edilmelidir; kullanıcı kimliği (user_id) rapora HİÇ girmez.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.db import SessionLocal                     # noqa: E402
from api.models import ChatMessage                  # noqa: E402

# Korpus eksikliğine işaret eden katmanlar. k4 raporlanır ama AYRI: çoğu
# gerçekten alan dışıdır; içinde alan içi bir soru varsa sözlük eksiği demektir.
EKSIKLIK_KATMANLARI = ("k3", "k3_5")

# Konu çıkarımı için kaba anahtar kelimeler (rapor okunabilir olsun diye).
KONULAR = {
    # Araya kelime girebilir: "gece 5 kez uyanıyor" da eşleşmeli.
    "gece uyanma": r"gece[^.!?]{0,25}(uyan|kalk)",
    "gündüz uykusu": r"gündüz\s*uyku|şekerleme|kestirme|nap",
    "uykuya dalma": r"dalam|uykuya geç|kendi kendine",
    "ağlama/motivasyon": r"ağl|vazgeç|pes|bırak|beceremi|yapamı|işe yaramı|boşuna",
    "beslenme": r"emzir|meme|mama|biberon|beslen",
    "emzik": r"emzik",
    "uyku ortamı": r"oda|sıcaklık|ışık|karanlık|gürültü|beşik|yatak",
    "rutin": r"rutin|banyo|masaj|düzen",
    "regresyon": r"regres|atak|diş",
    "sağlık": r"hasta|reflü|kolik|ateş|ilaç|alerji",
    "yaş/program": r"aylık|haftalık|yaşında|program|çizelge|saat",
}


def konu_bul(metin: str) -> str:
    low = (metin or "").replace("I", "ı").replace("İ", "i").lower()
    for ad, desen in KONULAR.items():
        if re.search(desen, low):
            return ad
    return "sınıflanamadı"


def rapor_uret(gun: int = 7) -> dict:
    """Son `gun` günün chat telemetrisinden kapsama raporu üret."""
    db = SessionLocal()
    try:
        baslangic = datetime.now(timezone.utc) - timedelta(days=gun)
        satirlar = (db.query(ChatMessage)
                    .filter(ChatMessage.role == "user",
                            ChatMessage.created_at >= baslangic,
                            ChatMessage.retrieval_layer.isnot(None))
                    .order_by(ChatMessage.created_at)
                    .all())
    finally:
        db.close()

    dagilim = Counter(s.retrieval_layer for s in satirlar)
    eksik = [s for s in satirlar if s.retrieval_layer in EKSIKLIK_KATMANLARI]
    kapsam_disi = [s for s in satirlar if s.retrieval_layer == "k4"]

    konu_sayaci = Counter(konu_bul(s.content) for s in eksik)

    def ornekle(kayitlar, limit=40):
        return [{"soru": s.content[:300],
                 "katman": s.retrieval_layer,
                 "skor": round(s.top_score, 3) if s.top_score is not None else None,
                 "konu": konu_bul(s.content),
                 "tarih": s.created_at.isoformat() if s.created_at else None}
                for s in kayitlar[:limit]]

    toplam = len(satirlar)
    return {
        "gun": gun,
        "toplam_soru": toplam,
        "katman_dagilimi": dict(dagilim),
        "eksiklik_orani": round(len(eksik) / toplam, 3) if toplam else 0.0,
        "konu_dagilimi": konu_sayaci.most_common(),
        "eksik_sorular": ornekle(eksik),
        "kapsam_disi_sorular": ornekle(kapsam_disi, limit=20),
    }


def yazdir(r: dict) -> None:
    print("=" * 70)
    print(f"KAPSAMA RAPORU — son {r['gun']} gün")
    print("=" * 70)
    print(f"Toplam soru (cache'siz): {r['toplam_soru']}")
    if not r["toplam_soru"]:
        print("\nHenüz telemetri kaydı yok.")
        return
    print(f"Katman dağılımı        : {r['katman_dagilimi']}")
    print(f"Eksiklik oranı (k3+k3_5): %{r['eksiklik_orani'] * 100:.1f}")

    print("\n--- KORPUSTA EKSİK KONULAR (İlayda'ya gidecek liste) ---")
    if not r["konu_dagilimi"]:
        print("  (eksiklik yok)")
    for konu, adet in r["konu_dagilimi"]:
        print(f"  {adet:4}×  {konu}")

    print("\n--- ÖRNEK SORULAR (karşılıksız kalanlar) ---")
    for s in r["eksik_sorular"][:15]:
        print(f"  [{s['katman']:5} skor={s['skor']}] {s['konu']:18} | {s['soru'][:90]}")

    if r["kapsam_disi_sorular"]:
        print("\n--- K4'E DÜŞENLER (gözden geçir: alan içi bir soru varsa "
              "sözlük eksiği demektir) ---")
        for s in r["kapsam_disi_sorular"][:10]:
            print(f"  [skor={s['skor']}] {s['soru'][:90]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Kapsama raporu (K3.5/K4 analizi)")
    ap.add_argument("--gun", type=int, default=7, help="kaç günlük pencere")
    ap.add_argument("--json", type=str, default=None, help="JSON çıktı dosyası")
    a = ap.parse_args()

    r = rapor_uret(a.gun)
    yazdir(r)
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"\nJSON yazıldı: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
