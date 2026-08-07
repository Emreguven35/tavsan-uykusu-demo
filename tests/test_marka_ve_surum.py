"""
MARKA KURALI + SÜRÜM DAMGASI testleri.

A) MARKA: üretilen HİÇBİR cevapta kişi adı geçmez. Ürün "Tavşan Uykusu"
   adıyla konuşur. İki katmanlı savunma denetlenir:
     1. KAYNAK — korpusa giren metinden ad temizlenir (modele hiç gitmez)
     2. TALİMAT — SYSTEM_PROMPT ayrıca kişi adı yasağını söyler
   Canlı doğrulama 20 ÇEŞİTLİ soruyla yapılır (yaş, uyku, ağlama, motivasyon,
   tıbbi sınır, kapsam dışı, K3.5 ve kriz kapısı dahil).

B) SÜRÜM: /health public alanları (version/build_time/corpus_units) ve tam
   SHA'nın yalnız X-API-Key + detail=1 ile döndüğü.

Çalıştırma: python tests/test_marka_ve_surum.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()
load_dotenv(ROOT.parent / ".env")

os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ["DEMO_API_KEY"] = "test-demo-key"
os.environ.setdefault("APP_VERSION", "faz-e2")
os.environ.setdefault("GIT_SHA", "0123456789abcdef0123456789abcdef01234567")

from engine import chatbot  # noqa: E402

HAS_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


AD = chatbot.KISI_ADI_DESENI          # r"ilayda", IGNORECASE

# =============================================================================
# A1) Temizleyici — dilbilgisel biçimler
# =============================================================================
ORNEKLER = [
    ("İlayda Hanım, ben gündüz yirmi dakika bekleyemiyorum",
     "ben gündüz yirmi dakika bekleyemiyorum"),
    ("İlayda'nın çerçevesi şudur", "Tavşan Uykusu yönteminin çerçevesi şudur"),
    ("bir İlayda Akın cümlesi", "bir Tavşan Uykusu cümlesi"),
    ("İlayda'dan öğrendim", "Tavşan Uykusu yönteminden öğrendim"),
    ("İlayda diyor ki", "Tavşan Uykusu diyor ki"),
]
_hata = [(g, chatbot.marka_temizle(g), b)
         for g, b in ORNEKLER if chatbot.marka_temizle(g) != b]
check("A1) marka_temizle tüm çekim biçimlerini doğru çeviriyor",
      not _hata, str(_hata))

check("A1b) Temizlenen metinde ad KALMIYOR",
      all(not AD.search(chatbot.marka_temizle(g)) for g, _ in ORNEKLER), "")

check("A1c) Adı olmayan metin DEĞİŞMEDEN geçiyor",
      chatbot.marka_temizle("Bebeğiniz 8 aylıkken 3 uyku yapmalı")
      == "Bebeğiniz 8 aylıkken 3 uyku yapmalı", "")


# =============================================================================
# A2) Korpus — modele giden hiçbir metinde/etikette/chunk_id'de ad yok
# =============================================================================
_units = chatbot.build_corpus()
_kirli_metin = [u["chunk_id"] for u in _units if AD.search(u["text"])]
_kirli_etiket = [u["chunk_id"] for u in _units if AD.search(str(u.get("label")))]
# chunk_id ChatResp.sources ile İSTEMCİYE DÖNER — o da temiz olmalı.
_kirli_id = [u["chunk_id"] for u in _units if AD.search(str(u["chunk_id"]))]

check("A2) Korpus metinlerinde kişi adı YOK",
      not _kirli_metin, f"{len(_kirli_metin)} birim: {_kirli_metin[:5]}")
check("A2b) Korpus etiketlerinde kişi adı YOK",
      not _kirli_etiket, str(_kirli_etiket[:5]))
check("A2c) chunk_id'lerde kişi adı YOK (sources ile istemciye dönüyor)",
      not _kirli_id, str(_kirli_id[:5]))


# =============================================================================
# A3) Sabit metinler, şablonlar ve promptlar
# =============================================================================
_SABITLER = {
    "SYSTEM_PROMPT": chatbot.SYSTEM_PROMPT,
    "KAPSAM_DISI_MESAJ": chatbot.KAPSAM_DISI_MESAJ,
    "KRIZ_MESAJI": chatbot.KRIZ_MESAJI,
    "SIKINTI_KURALI": chatbot.SIKINTI_KURALI,
    "DUYGU_KURALI_AGLAMA": chatbot.DUYGU_KURALI_AGLAMA,
    "DUYGU_KURALI_ZORLANMA": chatbot.DUYGU_KURALI_ZORLANMA,
    **{f"KATMAN_KURALLARI[{k}]": v for k, v in chatbot.KATMAN_KURALLARI.items()},
}
_kirli_sabit = [ad for ad, m in _SABITLER.items() if AD.search(m or "")]
check("A3) Tüm sabit metin ve şablonlarda kişi adı YOK",
      not _kirli_sabit, str(_kirli_sabit))

# Kullanıcının bildirdiği somut hata: K3.5 şablonu marka adını kullanmalı.
check("A3b) K3.5 şablonu 'Tavşan Uykusu yönteminde' diyor",
      "Tavşan Uykusu yönteminde" in chatbot.KATMAN_KURALLARI["k3_5"],
      chatbot.KATMAN_KURALLARI["k3_5"][:200])

check("A3c) SYSTEM_PROMPT kişi adı yasağını AÇIKÇA söylüyor",
      "KİŞİ ADI GEÇMEZ" in chatbot.SYSTEM_PROMPT
      and "Tavşan Uykusu" in chatbot.SYSTEM_PROMPT, "")

# Plan üretici prompt'u (fallback plan dahil)
from engine import plan_generator                                  # noqa: E402
from engine.parameter_engine import parametre_uret                 # noqa: E402
from datetime import date, timedelta                               # noqa: E402

_param = parametre_uret({"bebek_ad": "Test",
                         "dogum_tarihi": (date.today() - timedelta(days=240)).isoformat(),
                         "dogum_haftasi": 40})
_plan_prompt = plan_generator._build_user_prompt(_param)
check("A3d) Plan üretici prompt'unda kişi adı YOK",
      not AD.search(_plan_prompt) and not AD.search(plan_generator.SYSTEM_PROMPT),
      str(AD.findall(_plan_prompt)[:3]))

_fallback = plan_generator._fallback_plan(_param)
check("A3e) Fallback plan şablonunda kişi adı YOK",
      not AD.search(_fallback), str(AD.findall(_fallback)[:3]))


# =============================================================================
# B) /health sürüm damgası
# =============================================================================
from fastapi.testclient import TestClient                          # noqa: E402
from api.main import app                                           # noqa: E402

_c = TestClient(app)
_h = _c.get("/health").json()

check("B1) /health corpus_units veriyor ve korpusla eşleşiyor",
      _h.get("corpus_units") == len(_units),
      f"health={_h.get('corpus_units')} korpus={len(_units)}")
check("B2) /health build_time ISO-8601",
      isinstance(_h.get("build_time"), str) and "T" in _h["build_time"],
      str(_h.get("build_time")))
check("B3) /health kısa version etiketi veriyor",
      _h.get("version") == "faz-e2", str(_h.get("version")))
check("B4) Public /health TAM SHA SIZDIRMIYOR",
      "detail" not in _h
      and os.environ["GIT_SHA"] not in str(_h), str(_h))

_hd_anahtarsiz = _c.get("/health?detail=1").json()
check("B5) detail=1 anahtarsız → detay YOK (ama 200, healthcheck kırılmaz)",
      "detail" not in _hd_anahtarsiz, str(_hd_anahtarsiz.keys()))

_hd_yanlis = _c.get("/health?detail=1", headers={"X-API-Key": "yanlis"}).json()
check("B6) detail=1 YANLIŞ anahtar → detay YOK",
      "detail" not in _hd_yanlis, str(_hd_yanlis.keys()))

_hd = _c.get("/health?detail=1", headers={"X-API-Key": "test-demo-key"}).json()
check("B7) detail=1 DOĞRU anahtar → tam SHA döner",
      _hd.get("detail", {}).get("git_sha") == os.environ["GIT_SHA"],
      str(_hd.get("detail")))
check("B8) detail kısa SHA ve korpus dağılımı da veriyor",
      _hd["detail"].get("git_sha_short") == os.environ["GIT_SHA"][:7]
      and isinstance(_hd["detail"].get("corpus_breakdown"), dict), "")


# =============================================================================
# C) CANLI — 20 ÇEŞİTLİ SORU, hiçbir cevapta kişi adı geçmemeli
# =============================================================================
YIRMI_SORU = [
    # yaş / program
    "8 aylık bebeğim günde kaç uyku yapmalı",
    "9 aylık bebeğim ne kadar uyanık kalabilir",
    "15 aylık çocuğum tek uykuya ne zaman geçer",
    "2 yaşındaki çocuğum öğlen uykusunu reddediyor",
    "3 aylık bebeğim günde toplam kaç saat uyumalı",
    # uyku ortamı / rutin
    "bebeğin odası kaç derece olmalı",
    "beyaz gürültü kullanmalı mıyım",
    "akşam rutini nasıl olmalı",
    "karartma perdesi şart mı",
    # gece
    "bebeğim gece 5 kez uyanıyor ne yapmalıyım",
    "gece beslenmesini nasıl keserim",
    # eğitim / yöntem
    "uyku eğitimi kaç gün sürer",
    "yatır çık ne demek",
    "kademeli uzaklaşma nasıl uygulanır",
    # ağlama / motivasyon (marka sızıntısı riski EN YÜKSEK olanlar)
    "ağlamanın bebeğime zararı olur mu",
    "uyku eğitimi güven bağını zedeler mi",
    "Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum",
    "ben beceremiyorum",
    # tıbbi sınır + kapsam dışı
    "bebeğimde reflü var uyku eğitimi verebilir miyim",
    "bebeğime nasıl mama tarifi yapabilirim",
]
check("C0) Golden-set 20 çeşitli soru içeriyor", len(YIRMI_SORU) == 20,
      f"adet={len(YIRMI_SORU)}")

if not HAS_KEY:
    print("[ATLA ] Canlı 20 soru — ANTHROPIC_API_KEY yok")
else:
    chatbot.init_index()
    _kirli_cevaplar = []
    for _s in YIRMI_SORU:
        chatbot._cache_state["entries"] = []
        chatbot._rebuild_emb_matrix()
        r = chatbot._cevap_uret(_s)
        if AD.search(r["cevap"]):
            _kirli_cevaplar.append(f"{_s!r} → {AD.findall(r['cevap'])} :: "
                                   f"{r['cevap'][:150]}")
        # kaynak listesi de istemciye dönüyor
        for k in (r.get("kaynaklar") or []):
            if AD.search(str(k.get("chunk_id"))) or AD.search(str(k.get("label"))):
                _kirli_cevaplar.append(f"{_s!r} → kaynak: {k}")
    check("C) 20 canlı cevabın HİÇBİRİNDE kişi adı geçmiyor",
          not _kirli_cevaplar, "\n       ".join(_kirli_cevaplar[:5]))
    print(f"       ({len(YIRMI_SORU)} soru üretildi, "
          f"{len(_kirli_cevaplar)} ihlal)")

    # Kriz kapısı da marka kuralına tabi (sabit metin ama yine denetlenir).
    _kriz = chatbot._cevap_uret("Bebeğime zarar vereceğimden korkuyorum")
    check("C2) Kriz kapısı cevabında kişi adı YOK",
          not AD.search(_kriz["cevap"]), _kriz["cevap"][:150])


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("MARKA KURALI + SÜRÜM DAMGASI SONUÇLARI")
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
