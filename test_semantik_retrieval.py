"""
Semantik (hibrit) retrieval doğrulama — eşanlamlı/dolaylı soru seti.

TF-IDF'in kaçırdığı ama semantik'in yakalaması gereken soruları chatbot'a sorar
ve hiçbirinin güvenli düşüşe (yetersiz bilgi) gitmediğini doğrular.

Çalıştırma:
    python test_semantik_retrieval.py          # retrieval + (key varsa) canlı cevap
ANTHROPIC_API_KEY tanımlıysa cevaplar CANLI üretilir.
"""
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

logging.disable(logging.WARNING)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv()
load_dotenv(ROOT.parent / ".env")

from engine import chatbot  # noqa: E402

HAS_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))

# "Cevap gelmedi" (güvenli düşüş) işaretleri. Yeni düşüş metni: "Bu konuda elimde
# yeterli bilgi yok. Sorunuzu biraz farklı ifade ederek tekrar sorabilirsiniz."
# (Danışmanlık yönlendirmesi kaldırıldı.) Gerçek güvenli düşüş KISA olur.
NO_INFO_MARKERS = ["bilgim yok", "yeterli bilgi yok", "net bir bilgi bulamadım",
                   "farklı ifade", "farklı şekilde ifade"]

# (kategori, soru)
SORULAR = [
    # --- Zorunlu beslenme seti ---
    ("beslenme", "18 aylık bebeğimi gece uykusundan kaç saat önce emzirmeliyim"),
    ("beslenme", "emzirdikten kaç dakika sonra uyutmalıyım"),
    ("beslenme", "beslenmeyi uykudan ne kadar önce kesmeliyim"),
    ("beslenme", "mama verdikten sonra ne kadar bekleyeyim"),
    ("beslenme", "gece son sütü ne zaman vermeliyim"),
    # --- Feeding ladder yaşa özel (türetilmiş) ---
    ("feeding-ladder", "8 aylık bebek gece kaç kez beslenir"),
    ("feeding-ladder", "6 aylık gece beslemesi saat kaçta verilir"),
    ("feeding-ladder", "9 aylık bebek gece sütünü ne zaman bırakmalı"),
    ("feeding-ladder", "12 aylık bebeğe gece sütü vermeli miyim"),
    # --- Diğer konular: eşanlamlı/dolaylı (TF-IDF'in kaçırdıkları) ---
    ("ortam-ışık", "odanın ışığı nasıl olmalı uyurken"),
    ("ortam-ışık", "karanlık oda şart mı gündüz uykusunda"),
    ("ortam-ses", "beyaz gürültü makinesini kısmalı mıyım"),
    ("ortam-sıcaklık", "oda kaç derece olmalı bebek uyurken"),
    ("gündüz-uyku", "gündüz uykusu kısa olursa ne yaparım"),
    ("gündüz-uyku", "tek uykuya ne zaman geçilir"),
    ("uyutma-yöntem", "bebeğimi sallayarak uyutmak doğru mu"),
    ("uyutma-yöntem", "kucağa alıp sallamadan nasıl uyuturum"),
    ("uyutma-yöntem", "bebek arabasında uyutmak sakıncalı mı"),
    ("gece-uyanma", "gece bebeğim sık uyanıyor ne yapmalıyım"),
    ("rutin", "yatmadan önce nasıl bir rutin uygulamalıyım"),
    ("hazırlık", "uyku eğitimine başlamadan önce nelere dikkat etmeliyim"),
    ("prematüre", "prematüre bebekte yaşı nasıl hesaplarım"),
]


def is_no_info(ans: str) -> bool:
    """Gerçek güvenli düşüş: kısa (<260 karakter) ve bir no-info işareti içeren cevap.
    Uzun, bilgi-dolu cevaplar (kısa düşüş işareti içermeyen) no-info DEĞİLDİR."""
    low = ans.lower()
    has_marker = any(k in low for k in NO_INFO_MARKERS)
    return has_marker and len(ans.strip()) < 260


def main() -> int:
    chatbot.init_index()
    aktif = chatbot.active_retrieval()
    print(f"Aktif retrieval: {aktif} | model={chatbot.EMB_MODEL_NAME} | "
          f"key={'VAR' if HAS_KEY else 'YOK'}\n")

    rows = []
    fails = 0
    for kat, soru in SORULAR:
        hits = chatbot.retrieve(soru, top_k=chatbot.SEM_TOP_K)
        top = hits[0] if hits else None
        kaynak = f"{top['source']}/{top.get('label','')[:28]}" if top else "—"
        skor = f"{top['_score']:.2f}" if top else "—"
        if HAS_KEY:
            ans = chatbot.cevapla(soru)
            geldi = not is_no_info(ans) and len(ans.strip()) > 40
            note = ans.strip().replace("\n", " ")[:70]
        else:
            # key yok: retrieval var mı + fallback snippet
            geldi = bool(hits)
            note = (top["text"][:70] if top else "(retrieval boş)")
        if not geldi:
            fails += 1
        rows.append((kat, soru, "E" if geldi else "H", skor, kaynak, note))

    # Tablo
    print(f"{'KATEGORİ':16} {'CEVAP':5} {'SKOR':5} {'KAYNAK':40} SORU")
    print("-" * 120)
    for kat, soru, geldi, skor, kaynak, note in rows:
        mark = geldi
        print(f"{kat:16} {mark:5} {skor:5} {kaynak:40} {soru[:46]}")
    print("-" * 120)
    print(f"TOPLAM: {len(rows)} soru | CEVAP GELEN: {len(rows)-fails} | "
          f"GELMEYEN (güvenli düşüş): {fails}")

    # Detaylı notlar (cevap içeriği)
    print("\n=== CEVAP NOTLARI (kısa) ===")
    for kat, soru, geldi, skor, kaynak, note in rows:
        print(f"[{geldi}] {soru[:44]:44} | {kaynak[:30]:30} | {note}")

    return fails


if __name__ == "__main__":
    rc = main()
    print(f"\n{'✅ TÜM SORULAR CEVAP ALDI' if rc == 0 else f'⚠️ {rc} soru cevapsız'}")
    sys.exit(0 if rc == 0 else 1)
