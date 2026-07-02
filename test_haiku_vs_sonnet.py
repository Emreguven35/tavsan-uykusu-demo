"""
Haiku vs Sonnet — chatbot cevap karşılaştırması (Task A doğrulama).

10 soruyu HER İKİ modele (claude-haiku-4-5 ve claude-sonnet-4-6) AYNI kod
yolundan (aynı retrieval + aynı prompt + aynı system) sordurur ve karşılaştırır:
  - uzunluk (kelime),
  - protokol sadakati (soruya özel + genel yasak-ifade taraması),
  - ton (yasak dil / ders-kayıt sızıntısı yok mu).

Cache, karşılaştırmayı bozmasın diye her çağrıdan önce sıfırlanır (temp dosya).
Çıktı: test_outputs/haiku_vs_sonnet.md (tam cevaplar) + konsol tablosu.
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

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-4-6"

# Protokol-kritik 3 soru (C bölümü) + 7 genel soru
SORULAR = [
    ("araba", "Bebek arabasıyla uyutabilir miyim?",
     {"araba_oneri_yok": True, "uyanik_tut": True}),
    ("16_00", "Saat 16:00'ı geçti ama bebek bugün az uyudu, bir uyku daha yaptırabilir miyim?",
     {"istisna": True}),
    ("kucak", "Ağlarsa kucağa alabilir miyim?",
     {"alabilir_kademeli": True, "yasak_yok": True}),
    ("beyaz_gurultu", "Beyaz gürültü bebeğe zararlı mı?", {}),
    ("gece_uyanma", "Gece uyanmalarında ne yapmalıyım?", {}),
    ("kisa_uyku", "Kısa gündüz uykusunu nasıl uzatabilirim?", {}),
    ("emzik", "Uykuda emzik kullanmalı mıyım?", {}),
    ("sadece_kucak", "Bebeğim sadece kucakta uyuyor, ne yapmalıyım?", {"yasak_yok": True}),
    ("ne_zaman", "Uyku eğitimine ne zaman başlayabilirim?", {}),
    ("oda_isik", "Bebeğin odası karanlık mı olmalı, gece lambası mı kullanmalıyım?", {}),
]

BANNED = {
    "kucağa almayın": re.compile(r"kuca[ğg]a\s+alma(yın(ız)?|\s+yok)", re.IGNORECASE),
    "temas yok": re.compile(r"temas\s+yok", re.IGNORECASE),
    "yarı görünür": re.compile(r"yarı\s+görünür|yarı\s+yarıya\s+gör", re.IGNORECASE),
}
LEAK = re.compile(r"\bkayıt\s*\d|\bders\b|kayıt36|kayıt37", re.IGNORECASE)
ARABA_ONERI = re.compile(r"araba(yla|yı)?\s+(uyut|kullan|gezdir)", re.IGNORECASE)


def _fresh_cache():
    """Cache'i sıfırla (karşılaştırma bozulmasın — her çağrı taze LLM)."""
    tmp = Path(tempfile.gettempdir()) / "hs_cache.json"
    if tmp.exists():
        tmp.unlink()
    chatbot.CACHE_PATH = tmp
    chatbot._cache_state.update(
        {"loaded": True, "entries": [], "emb_matrix": None, "emb_idx": []})


def _ask(model, soru):
    _fresh_cache()
    old = chatbot.CHATBOT_MODEL
    chatbot.CHATBOT_MODEL = model
    try:
        return chatbot.cevapla(soru)  # band=None → prompt aynı, model farklı
    finally:
        chatbot.CHATBOT_MODEL = old


def _protokol(ans, beklenti):
    low = ans.lower()
    fails = []
    # genel
    for name, rx in BANNED.items():
        if rx.search(ans):
            fails.append(f"yasak:'{name}'")
    if LEAK.search(ans):
        fails.append("ders/kayıt sızıntısı")
    # soruya özel
    if beklenti.get("araba_oneri_yok") and ARABA_ONERI.search(ans):
        fails.append("araba önerisi verildi")
    if beklenti.get("uyanik_tut") and not ("uyanık tut" in low or "uyanık kal" in low):
        fails.append("uyanık-tutma kuralı yok")
    if beklenti.get("istisna") and not ("ilave" in low or "kıymeti" in low
                                        or ("minimum" in low and "16" in ans)):
        fails.append("16:00 istisna kuralı yok")
    if beklenti.get("alabilir_kademeli") and not (
            "alabilir" in low and ("kademeli" in low or "30 saniye" in low or "1 dakika" in low)):
        fails.append("'alabilirsiniz+kademeli' yok")
    if beklenti.get("yasak_yok") and BANNED["kucağa almayın"].search(ans):
        fails.append("kucağa almayın yasağı")
    return fails


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY yok — karşılaştırma yapılamaz.")
        return 1
    chatbot.init_index()

    rows, detay = [], []
    for key, soru, beklenti in SORULAR:
        ah = _ask(HAIKU, soru)
        as_ = _ask(SONNET, soru)
        wh, ws = len(ah.split()), len(as_.split())
        fh, fs = _protokol(ah, beklenti), _protokol(as_, beklenti)
        rows.append((key, wh, ws, fh, fs))
        detay.append((key, soru, ah, as_, fh, fs))

    # --- Markdown rapor (tam cevaplar) ---
    out = ["# Haiku vs Sonnet — Chatbot Karşılaştırma\n"]
    out.append(f"Haiku=`{HAIKU}`  Sonnet=`{SONNET}`  | 10 soru, aynı retrieval+prompt\n")
    out.append("| # | Soru | Haiku kel. | Sonnet kel. | Haiku protokol | Sonnet protokol |")
    out.append("|---|------|-----------:|------------:|----------------|-----------------|")
    for i, (key, wh, ws, fh, fs) in enumerate(rows, 1):
        hp = "PASS" if not fh else "FAIL: " + ", ".join(fh)
        sp = "PASS" if not fs else "FAIL: " + ", ".join(fs)
        out.append(f"| {i} | {key} | {wh} | {ws} | {hp} | {sp} |")
    out.append("\n---\n\n## Tam cevaplar (yan yana)\n")
    for key, soru, ah, as_, fh, fs in detay:
        out.append(f"### {key} — {soru}\n")
        out.append(f"**HAIKU** ({len(ah.split())} kel, protokol: {'PASS' if not fh else fh}):\n\n{ah}\n")
        out.append(f"**SONNET** ({len(as_.split())} kel, protokol: {'PASS' if not fs else fs}):\n\n{as_}\n")
        out.append("---\n")
    (ROOT / "test_outputs" / "haiku_vs_sonnet.md").write_text(
        "\n".join(out), encoding="utf-8")

    # --- Konsol özeti (ASCII) ---
    print("\n" + "=" * 78)
    print("HAIKU vs SONNET  (10 soru, ayni retrieval+prompt)")
    print("=" * 78)
    print(f"{'#':>2} {'soru':<16} {'H_kel':>6} {'S_kel':>6}  {'H_prot':<8} {'S_prot':<8}")
    print("-" * 78)
    hpass = spass = 0
    th = ts = 0
    for i, (key, wh, ws, fh, fs) in enumerate(rows, 1):
        th += wh; ts += ws
        hp = "PASS" if not fh else "FAIL"
        sp = "PASS" if not fs else "FAIL"
        hpass += (not fh); spass += (not fs)
        print(f"{i:>2} {key:<16} {wh:>6} {ws:>6}  {hp:<8} {sp:<8}")
    n = len(rows)
    print("-" * 78)
    print(f"Protokol PASS:  Haiku {hpass}/{n} | Sonnet {spass}/{n}")
    print(f"Ort. uzunluk:   Haiku {th//n} kel | Sonnet {ts//n} kel "
          f"(Haiku/Sonnet = {th/ts:.2f}x)")
    print("Tam cevaplar: test_outputs/haiku_vs_sonnet.md")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
