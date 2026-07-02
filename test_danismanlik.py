"""
Danışmanlık yönlendirmesi kaldırma testi (canlı).

10 soru sorar (uzun cevap üretenler + 2 tıbbi sınır). Doğrular:
  - "danışman"/"danışmanlık" kelimesi cevaplarda GEÇMEZ (tıbbi sınır soruları
    strict assert'ten HARİÇ; onlarda da gözlenir/raporlanır).
    NOT: yasak olan "danışman" ADI/hizmetidir; "doktora danışın" gibi FİİL
    kullanımı serbesttir (substring "danışman" içermez).
  - Tıbbi sınır sorularında "doktor" GEÇER (hedef danışman değil çocuk doktoru).
  - Cevaplar eksik/yarım hissettirmez (asgari uzunluk).

Taze üretim için cevap cache temp'e alınır (yeni system prompt test edilsin).
Çıktı: test_outputs/danismanlik_kontrol.md (10 cevabın tamamı).
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from engine import chatbot  # noqa: E402

# YASAK olan İSİM'dir: danışman (kişi) / danışmanlık (hizmet) / danışmana / danışmanınız.
# SERBEST olan FİİL'dir: danışmak → "danışmanız / danışmanıza" (örn. "doktora danışmanız").
# Ayırt edici: fiil ekinden sonra "ız"/"ıza" gelir; isim hep "danışman(lık|ınız|a|ı|...)".
DANISMAN = re.compile(r"danışman(?!ız|ıza)", re.IGNORECASE)
DOKTOR = re.compile(r"doktor|hekim", re.IGNORECASE)

# (id, soru, tibbi_mi)
SORULAR = [
    ("gece_uyanma", "Bebeğim gece sık sık uyanıyor, ne yapmalıyım?", False),
    ("kisa_uyku", "Gündüz uykuları çok kısa sürüyor, nasıl uzatabilirim?", False),
    ("destek", "Bebeğim sadece kucakta ve emerek uyuyor, bu alışkanlığı nasıl bırakırım?", False),
    ("baslangic", "Uyku eğitimine nasıl başlarım?", False),
    ("rutin", "Yatmadan önce nasıl bir uyku rutini uygulamalıyım?", False),
    ("beyaz_gurultu", "Beyaz gürültü kullanmalı mıyım?", False),
    ("oda", "Bebeğin uyku odası nasıl olmalı?", False),
    ("gece_besleme", "Gece beslemesini ne zaman ve nasıl keserim?", False),
    # --- tıbbi sınır (hedef: çocuk doktoru) ---
    ("tibbi_reflu", "Bebeğimde reflü var, uykusu için ilaç kullanmalı mıyım?", True),
    ("tibbi_ates", "Bebeğim ateşleniyor ve geceleri nöbet geçirdi, ne yapmalıyım?", True),
]


def _fresh():
    tmp = Path(tempfile.gettempdir()) / "danisman_cache.json"
    if tmp.exists():
        tmp.unlink()
    chatbot.CACHE_PATH = tmp
    chatbot._cache_state.update(
        {"loaded": True, "entries": [], "emb_matrix": None, "emb_idx": []})


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY yok — canlı test yapılamaz."); return 1
    chatbot.init_index()

    results, kayit = [], ["# Danışmanlık Kaldırma — 10 Canlı Cevap\n"]
    for sid, soru, tibbi in SORULAR:
        _fresh()
        ans = chatbot.cevapla(soru, yas_bandi="8_ay")
        has_dan = bool(DANISMAN.search(ans))
        has_dok = bool(DOKTOR.search(ans))
        kelime = len(ans.split())
        yeterli = kelime >= 30                       # eksik/yarım değil

        if tibbi:
            # tıbbi: strict danışman-yasağından hariç; doktor hedefi + yeterlilik
            ok = has_dok and yeterli
            note = f"tıbbi | doktor={has_dok} danışman={has_dan}(gözlem) {kelime}kel"
        else:
            # non-tıbbi: danışman YASAK + yeterli uzunluk
            ok = (not has_dan) and yeterli
            note = f"danışman_yok={not has_dan} {kelime}kel"
        results.append((sid, ok, note))
        kayit.append(f"## {sid} — {soru}\n(tıbbi={tibbi}, danışman={has_dan}, "
                     f"doktor={has_dok}, {kelime} kelime)\n\n{ans}\n")

    (ROOT / "test_outputs").mkdir(exist_ok=True)
    (ROOT / "test_outputs" / "danismanlik_kontrol.md").write_text(
        "\n".join(kayit), encoding="utf-8")

    print("\n" + "=" * 74)
    print("DANIŞMANLIK KALDIRMA — CANLI KONTROL")
    print("=" * 74)
    ok = 0
    for sid, cond, note in results:
        print(f"[{'PASS' if cond else 'FAIL'}] {sid:16} | {note}")
        ok += cond
    print("-" * 74)
    print(f"TOPLAM: {ok}/{len(results)} gecti | tam cevaplar: test_outputs/danismanlik_kontrol.md")
    print("=" * 74)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
