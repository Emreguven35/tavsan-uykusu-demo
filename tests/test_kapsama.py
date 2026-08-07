"""
FAZ E-2 — Kapsama golden-set'i: GERÇEK ANNE CÜMLELERİ.

NEDEN BU DOSYA VAR: Faz E'nin testi "…bırakmak istiyorum artık VAZGEÇECEĞİM"
cümlesini kullanıyordu; 'vazgeç' sözlükte olduğu için geçti. Gerçek kullanıcı
ise "…bırakmak istiyorum" yazdı ve benzer cümlelerin çoğu K4'e (kapsam dışı)
düşüyordu. Test cümlesi ile gerçek kullanım arasındaki fark buydu.

Bu dosyadaki cümleler TEST İÇİN UYDURULMAZ — gerçek anne dilidir ve hiçbirinde
metodoloji terimi (uyku, eğitim, bebek...) geçmek ZORUNDA değildir.

GENEL İLKE (kalıcı kural): K4 SON ÇAREDİR. Cevap üretilemeyen her durumda önce
K3.5 denenir. K4 yalnızca soru gerçekten başka bir konudaysa verilir.

Çalıştırma: python tests/test_kapsama.py
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()
load_dotenv(ROOT.parent / ".env")

# Şema kontrolü (madde 5) api.models'i import eder; o da ayarları doğrular.
# Testin gerçek bir DB'ye ihtiyacı YOK — yalnız kolon tanımına bakılıyor.
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.setdefault("ENVIRONMENT", "development")

from engine import chatbot  # noqa: E402

HAS_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def katman_of(soru: str) -> tuple[str, float]:
    """Sorunun düşeceği katmanı ve ham skoru hesapla (LLM çağırmadan)."""
    olcum = chatbot.retrieve(soru, top_k=1, min_score=0.0)
    top = float(olcum[0].get("_score", 0.0)) if olcum else 0.0
    bantlar, yas_ay = chatbot.bant_coz(soru, None)
    return chatbot._katman_belirle(
        top, chatbot._alan_sinyali(soru, yas_ay),
        bool(chatbot.yas_bandi_blok(bantlar, yas_ay)),
        ebeveynlik=chatbot._ebeveynlik_sinyali(soru),
        kapsam_disi=chatbot._kapsam_disi_sinyali(soru)), top


chatbot.init_index()

# =============================================================================
# 1) GERÇEK ANNE CÜMLELERİ — hiçbiri K4 olmayacak
# =============================================================================
GERCEK_CUMLELER = [
    # Kullanıcının canlı testte bildirdiği cümle:
    "Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum",
    # Spesifikasyonda istenen gerçek cümleler:
    "bırakmak istiyorum",
    "pes ettim",
    "bu yöntem bizde işe yaramıyor",
    "3 gündür ağlıyor hiç düzelmedi",
    "ben beceremiyorum",
    # Aynı ailedeki diğer gerçek ifadeler:
    "yapamıyorum",
    "boşuna",
    "vazgeçiyorum",
    "olmuyor bizde",
    "hiçbir şey değişmedi",
    "artık dayanamıyorum",
    "çok yorgunum",
    "gece boyu ağlıyor",
    "her şeyi denedim hiçbiri olmadı",
    "bu iş bende yürümüyor",
]

_k4_dusenler = []
for _c in GERCEK_CUMLELER:
    _k, _s = katman_of(_c)
    if _k == "k4":
        _k4_dusenler.append(f"{_c!r} → k4 (skor={_s:.3f})")
check("1) GERÇEK anne cümlelerinin HİÇBİRİ kapsam dışına (K4) düşmüyor",
      not _k4_dusenler, "\n       ".join(_k4_dusenler))

# Kullanıcının bildirdiği cümle ayrıca tek tek raporlanır.
_k, _s = katman_of("Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum")
check("1b) Bildirilen cümle cevaplanabilir katmanda",
      _k != "k4", f"katman={_k} skor={_s:.3f}")

# Metodoloji terimi İÇERMEYEN cümleler de alan içi sayılmalı (duygusal sinyal
# TEK BAŞINA yeterli — madde 2).
_METODOLOJISIZ = ["pes ettim", "yapamıyorum", "boşuna", "ben beceremiyorum",
                  "bu iş bende yürümüyor"]
_alan_disi = [c for c in _METODOLOJISIZ if not (
    chatbot._alan_sinyali(c, None) or chatbot._ebeveynlik_sinyali(c))]
check("1c) Metodoloji terimi içermeyen duygusal cümleler alan içi sayılıyor",
      not _alan_disi, str(_alan_disi))


# =============================================================================
# 2) GERÇEKTEN ALAN DIŞI — K4 korunuyor
# =============================================================================
ALAN_DISI = [
    "bebeğime nasıl mama tarifi yapabilirim",
    "vergi beyannamesi nasıl doldurulur",
    "yarın hava nasıl olacak",
    "dolar kaç lira",
    "kek tarifi verir misin",
    "maç kaç kaç bitti",
]
_yanlis_ici = []
for _c in ALAN_DISI:
    _k, _s = katman_of(_c)
    if _k != "k4":
        _yanlis_ici.append(f"{_c!r} → {_k} (skor={_s:.3f})")
check("2) Gerçekten alan dışı sorular hâlâ K4 (kapı gevşemedi)",
      not _yanlis_ici, "\n       ".join(_yanlis_ici))


# =============================================================================
# 3) TÜRKÇE ÜNLÜ DEĞİŞİMİ — kök eşleşmesi tuzağı
# =============================================================================
# "ağla" kökü "ağlıyor"u YAKALAMAZ (ağla + ıyor → ağlıyor). Gerçek cümlelerin
# çoğu geniş/şimdiki zamandadır; bu yüzden kök "ağl" olmalı.
_AGLAMA_BICIMLERI = ["ağlıyor", "ağlıyordu", "ağladı", "ağlamış", "ağlamaya",
                     "ağlarken", "ağlıyoruz"]
_kacan = [b for b in _AGLAMA_BICIMLERI
          if not chatbot._alan_sinyali(f"bebeğim {b}", None)]
check("3) 'ağla' çekimlerinin hepsi alan içi (ünlü değişimi tuzağı kapandı)",
      not _kacan, str(_kacan))


# =============================================================================
# 4) K3.5 KATMANI
# =============================================================================
check("4) K4 SON ÇARE: alan sinyali yok + ebeveynlik sinyali var → k3_5",
      chatbot._katman_belirle(0.30, False, False, ebeveynlik=True) == "k3_5",
      chatbot._katman_belirle(0.30, False, False, ebeveynlik=True))

check("4b) Hiçbir sinyal yok + düşük skor → k4 (gerçekten alan dışı)",
      chatbot._katman_belirle(0.10, False, False, ebeveynlik=False) == "k4", "")

check("4c) Açık kapsam dışı işareti K4'ü zorlar (yüksek skorda bile)",
      chatbot._katman_belirle(0.52, False, False, ebeveynlik=True,
                              kapsam_disi=True) == "k4", "")

check("4d) K3.5 kuralı 'kapsam dışı' DEMİYOR, eksikliği dürüstçe söylüyor "
      "+ netleştirme sorusu istiyor",
      "net bir kayıt yok" in chatbot.KATMAN_KURALLARI["k3_5"]
      and "netleştirme sorusu" in chatbot.KATMAN_KURALLARI["k3_5"]
      and "ASLA kullanma" in chatbot.KATMAN_KURALLARI["k3_5"], "")

check("4e) K3.5 kuralı SERBEST YORUMU yasaklıyor (KB'ye dayalı kalsın)",
      "DIŞINA ÇIKMA" in chatbot.KATMAN_KURALLARI["k3_5"]
      and "EKLEME" in chatbot.KATMAN_KURALLARI["k3_5"], "")

check("4f) K3.5 genel ilkeleri bağlama alıyor (boş bağlamla cevap üretilmez)",
      len(chatbot._genel_ilke_birimleri()) > 0, "")


# =============================================================================
# 5) TELEMETRİ — Postgres uzunluk tuzağı
# =============================================================================
# chat_messages.retrieval_layer Faz 6.4'te String(2) idi. Faz E 'ruhsal_kriz'
# (11 karakter) yazınca Postgres StringDataRightTruncation fırlatıyor ve KRİZ
# ANINDAKİ ANNE 500 alıyordu. SQLite uzunluk zorlamadığı için yerel testler
# bunu GÖRMEDİ — bu yüzden kontrol şema seviyesinde yapılır.
from api.models import ChatMessage                              # noqa: E402

_kolon = ChatMessage.__table__.c.retrieval_layer.type.length
_URETILEN_KATMANLAR = ["k1", "k2", "k3", "k3_5", "k4", "ruhsal_kriz"]
_uzun = [k for k in _URETILEN_KATMANLAR if len(k) > (_kolon or 0)]
check("5) retrieval_layer kolonu ÜRETİLEN tüm katman adlarını alabiliyor",
      not _uzun, f"kolon={_kolon} sığmayan={_uzun}")

check("5b) Kolon genişliği migration ile hizalı (0007)",
      _kolon == 32, f"kolon={_kolon}")

# Motorun ürettiği katman adları listeyle birebir mi? (yeni katman eklenirse
# bu test kolonu genişletmeyi hatırlatır)
check("5c) Motor bilinmeyen bir katman adı üretmiyor",
      set(chatbot.KATMAN_KURALLARI.keys()) <= set(_URETILEN_KATMANLAR),
      str(set(chatbot.KATMAN_KURALLARI.keys()) - set(_URETILEN_KATMANLAR)))

# Kapsama raporu betiği çalışır durumda mı (import + konu sınıflama)?
sys.path.insert(0, str(ROOT / "scripts"))
import kapsama_raporu                                            # noqa: E402

check("5d) Kapsama raporu konu sınıflaması çalışıyor",
      kapsama_raporu.konu_bul("bırakmak istiyorum") == "ağlama/motivasyon"
      and kapsama_raporu.konu_bul("odası kaç derece") == "uyku ortamı"
      and kapsama_raporu.konu_bul("gece 5 kez uyanıyor") == "gece uyanma", "")

check("5e) Rapor eksiklik katmanları k3 ve k3_5",
      set(kapsama_raporu.EKSIKLIK_KATMANLARI) == {"k3", "k3_5"},
      str(kapsama_raporu.EKSIKLIK_KATMANLARI))


# =============================================================================
# 6) CANLI — cevap gerçekten geliyor mu, "kapsam dışı" demiyor mu?
# =============================================================================
_KAPSAM_DISI_IZI = re.compile(r"kapsamı? dışında|kapsam dışı", re.IGNORECASE)
_NETLESTIRME = re.compile(r"\?", re.IGNORECASE)

if not HAS_KEY:
    print("[ATLA ] Canlı bölüm — ANTHROPIC_API_KEY yok")
else:
    CANLI = [
        "Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum",
        "pes ettim",
        "bu yöntem bizde işe yaramıyor",
        "3 gündür ağlıyor hiç düzelmedi",
        "ben beceremiyorum",
    ]
    for _soru in CANLI:
        chatbot._cache_state["entries"] = []
        chatbot._rebuild_emb_matrix()
        r = chatbot._cevap_uret(_soru)
        cevap = r["cevap"]
        print(f"\n--- [{r['retrieval_layer']}] {_soru}")
        print("    " + cevap[:220].replace("\n", " ") + "...")

        check(f"6) {_soru!r}: cevap ÜRETİLDİ (kapsam dışı mesajı DEĞİL)",
              r["retrieval_layer"] != "k4"
              and not _KAPSAM_DISI_IZI.search(cevap),
              f"katman={r['retrieval_layer']} cevap={cevap[:160]}")
        check(f"6) {_soru!r}: cevap boş değil ve anlamlı uzunlukta",
              len(cevap.strip()) > 150, f"uzunluk={len(cevap)}")

    # K3.5 formu: eksikliği dürüstçe söyler + netleştirme sorusu sorar.
    chatbot._cache_state["entries"] = []
    r35 = chatbot._cevap_uret("hiçbir şey değişmedi")
    print(f"\n--- [{r35['retrieval_layer']}] hiçbir şey değişmedi")
    print("    " + r35["cevap"][:260].replace("\n", " ") + "...")
    check("6b) K3.5 cevabı 'kapsam dışı' DEMİYOR",
          not _KAPSAM_DISI_IZI.search(r35["cevap"]), r35["cevap"][:200])
    check("6c) K3.5 cevabı netleştirme sorusu içeriyor",
          bool(_NETLESTIRME.search(r35["cevap"])), r35["cevap"][-200:])


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("KAPSAMA GOLDEN-SET SONUÇLARI (Faz E-2)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
        print(f"[{mark}] {name}")
    else:
        print(f"[{mark}] {name}\n       {detail}")

print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} geçti")
sys.exit(0 if passed == len(results) else 1)
