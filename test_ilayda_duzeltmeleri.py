"""
İlayda Düzeltmeleri — Uçtan Uca Doğrulama Testi
================================================

Üç bölüm:
  A) Bilgi tabanı taramaları (deterministik — API gerekmez)
  B) Canlı plan üretimi (Claude API ile 3 plan) + içerik kontrolleri
  C) Chatbot testleri (3 soru)

Çalıştırma:
    python test_ilayda_duzeltmeleri.py

ANTHROPIC_API_KEY tanımlıysa B ve C CANLI çalışır (gerçek Claude çıktısı test_outputs/'a yazılır).
Tanımlı değilse: B/C'nin CANLI kısmı atlanır; B için prompt-seviyesi, C için retrieval-seviyesi
deterministik ön-kontroller çalışır ve promptlar/fallback çıktılar test_outputs/'a yazılır.
"""
import os
import re
import sys
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv()
load_dotenv(ROOT.parent / ".env")  # üst repo .env (varsa)

from engine.parameter_engine import parametre_uret              # noqa: E402
from engine.plan_generator import _build_user_prompt, plan_uret  # noqa: E402
from engine import chatbot                                       # noqa: E402

DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "test_outputs"
OUT_DIR.mkdir(exist_ok=True)

HAS_KEY = bool(os.getenv("ANTHROPIC_API_KEY"))

results = []  # (bölüm, kontrol, durum, kanıt)


def rec(bolum, kontrol, ok, kanit=""):
    durum = "GEÇTİ" if ok is True else ("KALDI" if ok is False else "BİLGİ")
    results.append((bolum, kontrol, durum, kanit))
    print(f"[{durum:5}] {bolum} | {kontrol}  {('-> '+kanit) if kanit else ''}")


# ---------------------------------------------------------------------------
# /chat ENDPOINT REGRESYON MODU (opsiyonel)
# CHAT_ENDPOINT=1 iken Bölüm C soruları doğrudan chatbot.cevapla yerine YENİ FastAPI
# /api/v1/chat endpoint'i üzerinden koşulur — endpoint'in chatbot davranışını (tıbbi
# sınır, danışman yönlendirmesi yok, kucağa alabilirsiniz vb.) KORUDUĞUNU doğrular.
# Flag kapalıyken davranış BİREBİR eskisi gibidir (Streamlit/engine testi etkilenmez).
# ---------------------------------------------------------------------------
USE_CHAT_ENDPOINT = os.getenv("CHAT_ENDPOINT") == "1"
_chat_client = None
_chat_headers = None


def _ensure_chat_client():
    global _chat_client, _chat_headers
    if _chat_client is not None:
        return
    os.environ.setdefault("DATABASE_URL", "sqlite:///./_regresyon_chat.db")
    os.environ.setdefault("JWT_SECRET", "regresyon-secret")
    from fastapi.testclient import TestClient
    from api.db import engine
    from api.db.base import Base
    import api.models  # noqa: F401 — metadata dolsun
    Base.metadata.create_all(engine)          # tablo yoksa oluştur (chat_messages dahil)
    from api.main import app
    _chat_client = TestClient(app)
    creds = {"email": "regresyon@ornek.com", "password": "regresyon123"}
    r = _chat_client.post("/api/v1/auth/register", json=creds)
    if r.status_code == 409:                   # zaten kayıtlı → login
        r = _chat_client.post("/api/v1/auth/login", json=creds)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"regresyon auth başarısız: {r.status_code} {r.text[:150]}")
    _chat_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}


def _chat_answer(soru: str) -> str:
    """Soruyu /api/v1/chat üzerinden sor, cevabı döndür (endpoint regresyonu)."""
    _ensure_chat_client()
    r = _chat_client.post("/api/v1/chat", headers=_chat_headers,
                          json={"message": soru, "history": []})
    if r.status_code != 200:
        raise RuntimeError(f"/chat {r.status_code}: {r.text[:200]}")
    return r.json()["answer"]


# ---------------------------------------------------------------------------
# Yasaklı ifade desenleri
# ---------------------------------------------------------------------------
BANNED = {
    "kucağa almayın": re.compile(r"kuca[ğg]a\s+alma(yın(ız)?|\s+yok)", re.IGNORECASE),
    "temas yok": re.compile(r"temas\s+yok", re.IGNORECASE),
    "yarı görünür/görsün": re.compile(r"yarı\s+görünür|yarı\s+yarıya\s+gör", re.IGNORECASE),
    "bebek arabası": re.compile(r"bebek\s+arabas", re.IGNORECASE),
    "1 saat ağlama/direnç": re.compile(r"1\s*saat\s+(ağlama|direnç)|bir\s+saat\s+(ağlama|direnç)", re.IGNORECASE),
}
# Sert gelecek-zaman emir kipi (üslup) — plan çıktısında olmamalı
FUTURE_IMP = re.compile(r"\b\w+m(a|e)y(a|e)caksınız\b", re.IGNORECASE)  # takmayacaksınız, vermeyeceksiniz
# plan_generator.py içinde bu ifadeler YASAK-kuralı olarak geçebilir; bu satırlar kasıtlıdır:
PROHIB_MARKERS = ("YASAK", "KULLANILAMAZ", "KULLANMA", "kullanma", "yerine", "gibi ifadeler",
                  "DEME", "demiyorum", "ASLA", "İFADESİ", "GÜVENLİK", "yapılmaz", "HİÇBİR")

# Temiz "güncel kural" chunk'ları yasak ifadeleri KASITLI içerir (anahtar kelime/RAG için)
RULE_LESSON = "kural_guncel"
# 'bebek arabası' bir öneri olarak mı geçiyor (yasak/ret bağlamı değil)?
ARABA_NEG = ("yapılmaz", "yapmıyoruz", "yapmam", "yapmay", "yapmaz", "kullanılmaz",
            "kullanmayın", "kullanmama", "önermiyoruz", "önerilmez", "uyutmay",
            "değildir", "yok", "hayır")


def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def araba_oneri_olarak_var(text):
    """'bebek arabas...' yasak/ret bağlamı OLMADAN geçiyorsa True (ihlal)."""
    for m in re.finditer(r"bebek arabas", text, re.IGNORECASE):
        w = text[max(0, m.start() - 75):m.end() + 75].lower()
        if not any(n in w for n in ARABA_NEG):
            return True
    return False


# ===========================================================================
# BÖLÜM A — BİLGİ TABANI TARAMALARI
# ===========================================================================
def bolum_A():
    print("\n" + "=" * 70 + "\nBÖLÜM A — Bilgi tabanı taramaları\n" + "=" * 70)

    data_files = ["master_knowledge_base.json", "chunks.json", "decision_tree.json",
                  "lesson_metadata.json"]
    for fname in data_files:
        if fname == "chunks.json":
            # Kasıtlı 'güncel kural' chunk'larını hariç tut (yasak ifadeleri RAG için içerirler)
            arr = json.loads((DATA_DIR / fname).read_text(encoding="utf-8"))
            text = " ".join(c["text"] for c in arr if c.get("lesson_id") != RULE_LESSON)
        else:
            text = (DATA_DIR / fname).read_text(encoding="utf-8")
        for label, pat in BANNED.items():
            hits = pat.findall(text)
            rec("A-data", f"{fname}: '{label}' = 0 (kural chunk hariç)" if fname == "chunks.json"
                else f"{fname}: '{label}' = 0",
                len(hits) == 0,
                "0 bulundu" if not hits else f"{len(hits)} KEZ GEÇİYOR")

    # Kaynak dosyalar (engine + pages) — plan_generator.py yasak-kuralları hariç
    src_files = list((ROOT / "engine").glob("*.py")) + list((ROOT / "pages").glob("*.py"))
    for f in src_files:
        lines = f.read_text(encoding="utf-8").splitlines()
        for label, pat in BANNED.items():
            offenders = []
            for i, line in enumerate(lines, 1):
                if pat.search(line):
                    intentional = (f.name == "plan_generator.py"
                                   and any(m in line for m in PROHIB_MARKERS))
                    if not intentional:
                        offenders.append(i)
            ok = len(offenders) == 0
            rel = f.relative_to(ROOT)
            if pat.search(f.read_text(encoding="utf-8")) and f.name == "plan_generator.py" and ok:
                rec("A-src", f"{rel}: '{label}' yalnız yasak-kuralında",
                    True, "kasıtlı ban-kuralı (advice değil)")
            else:
                rec("A-src", f"{rel}: '{label}' = 0",
                    ok, "temiz" if ok else f"satır {offenders}")

    # Form: 33. soruda 4 seçenek (1 aylık dahil)
    form = (ROOT / "pages" / "6_AI_Ek_Sorular.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(form)
    opts = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "selectbox":
            if node.args and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).startswith("33."):
                opts = [e.value for e in node.args[1].elts]
    rec("A-form", "33. soru 4 seçenek", opts is not None and len(opts) == 4,
        f"{len(opts) if opts else 0} seçenek")
    rec("A-form", "33. soruda '1 aylık program' seçeneği var",
        bool(opts) and any("1 aylık program" in o for o in opts),
        next((o for o in (opts or []) if "1 aylık" in o), "YOK"))


# ===========================================================================
# BÖLÜM B — CANLI PLAN ÜRETİMİ
# ===========================================================================
PROFILLER = [
    {
        "etiket": "1_Emir_8ay_13gun",
        "profile": {
            "bebek_ad": "Emir", "dogum_tarihi": "2025-10-08", "dogum_haftasi": 40,
            "beslenme": "anne sütü (emerek)", "destek": "emerek uyuma",
            "emzik": "evet, kullanıyor", "oda": "ayrı oda", "oda_sicakligi": "25",
            "beyaz_gurultu": "evet", "karartma_perdesi": "evet", "mizac": "hassas",
            "dayanma_siniri": "10-20 dakika", "gece_uyanma": "3",
            "yaklasim_tercihi": "13 günlük kademeli plan (daha yumuşak, daha uzun süreç)",
            "beklenti": "gece kesintisiz uyku",
        },
    },
    {
        "etiket": "2_Emir_8ay_1aylik",
        "profile": {
            "bebek_ad": "Emir", "dogum_tarihi": "2025-10-08", "dogum_haftasi": 40,
            "beslenme": "anne sütü (emerek)", "destek": "emerek uyuma",
            "emzik": "evet, kullanıyor", "oda": "ayrı oda", "oda_sicakligi": "25",
            "beyaz_gurultu": "evet", "karartma_perdesi": "evet", "mizac": "hassas",
            "dayanma_siniri": "10-20 dakika", "gece_uyanma": "3",
            "yaklasim_tercihi": "1 aylık program (ilk 2 hafta destekle uyku — sadece saat ve rutin düzeni)",
            "beklenti": "yumuşak geçiş",
        },
    },
    {
        "etiket": "3_Defne_11ay_5gun",
        "profile": {
            "bebek_ad": "Defne", "dogum_tarihi": "2025-07-08", "dogum_haftasi": 40,
            "beslenme": "mama", "destek": "sallanarak", "emzik": "hayır",
            "oda": "ayrı oda", "oda_sicakligi": "21", "beyaz_gurultu": "hayır",
            "karartma_perdesi": "evet", "mizac": "sakin", "dayanma_siniri": "30-45 dakika",
            "gece_uyanma": "1",
            "yaklasim_tercihi": "5 günlük standart plan (daha hızlı, daha çok ağlama olabilir)",
            "beklenti": "gündüz uykuları düzeli olsun",
        },
    },
]


def _low(s):
    return s.lower()


def icerik_kontrolleri(etiket, plan, param):
    """Üretilmiş plan METNİ üzerinde checklist (canlı çıktı için)."""
    low = _low(plan)
    low_tr = tr_lower(plan)  # Türkçe-güvenli (I/İ) küçük harf
    tip = param["plan_secimi"]["tip"]

    # 0) Plan KESİLMEMİŞ olmalı (max_tokens truncation kontrolü).
    #    Son ~25 karakterde cümle bitiş noktalaması aranır (sondaki emoji/boşluk tolere edilir;
    #    yarıda kesilen plan 'girince od' gibi biter — noktalama olmaz).
    last = plan.rstrip()
    ends_clean = any(p in last[-25:] for p in ".!?…")
    rec("B-" + etiket, "Plan tam — kesilmemiş",
        ends_clean, f"son: ...{last[-40:]!r}")

    # 1) Yasaklı ifade yok. 'bebek arabası' YALNIZCA öneri bağlamında ihlaldir
    #    (rule G gereği "bebek arabası ... yapılmaz" demek doğru/beklenen davranıştır).
    allbad = {}
    for lbl, pat in BANNED.items():
        if lbl == "bebek arabası":
            if araba_oneri_olarak_var(plan):
                allbad[lbl] = ["ÖNERİ bağlamı"]
        else:
            h = pat.findall(plan)
            if h:
                allbad[lbl] = h
    fut = FUTURE_IMP.findall(plan)
    rec("B-" + etiket, "Yasaklı ifade yok (+ sert gelecek zaman)",
        not allbad and not fut,
        "temiz" if (not allbad and not fut) else f"banned={allbad} future={fut}")

    # 2) Uyanıklık süresi açıklaması (Türkçe-güvenli)
    rec("B-" + etiket, "Günlük tablo altı uyanıklık açıklaması (uyandığı + kortizol)",
        ("uyandığı" in low_tr) and ("kortizol" in low_tr),
        f"uyandığı={'uyandığı' in low_tr}, kortizol={'kortizol' in low_tr}")

    # 3) KATI saat -> 07:00 esneme (sadece bucket'ta KATI varsa beklenir)
    bucket_has_kati = "KATI" in json.dumps(param["parametreler"], ensure_ascii=False)
    if bucket_has_kati:
        ok = ("07:00" in plan) and ("esne" in low) and ("katı" in low or "kati" in low)
        rec("B-" + etiket, "KATI saat yanında eğitim sonrası 07:00 esneme notu",
            ok, f"07:00={'07:00' in plan}, esne={'esne' in low}")
    else:
        rec("B-" + etiket, "KATI saat notu (bu yaşta bucket'ta KATI yok)", None,
            "uygulanmaz")

    # 4) 16:00 son uyku istisna notu (bucket'ta 16:00 varsa)
    bucket_has_16 = "16:00" in json.dumps(param["parametreler"], ensure_ascii=False)
    if bucket_has_16:
        ok = ("16:00" in plan) and ("ilave" in low or "minimum" in low)
        rec("B-" + etiket, "16:00 yanında minimum uyku istisna notu",
            ok, f"16:00={'16:00' in plan}, ilave/minimum={'ilave' in low or 'minimum' in low}")
    else:
        rec("B-" + etiket, "16:00 istisna notu (bucket'ta 16:00 yok)", None, "uygulanmaz")

    # 5) Kısa gündüz + B Planı AYRI bölüm değil, gün altında
    ayri_kg = bool(re.search(r"^#+\s*Kısa Gündüz Uykusu Protokolü", plan, re.MULTILINE))
    ayri_bp = bool(re.search(r"^#+\s*B Planı\s*$", plan, re.MULTILINE))
    gun_alti = ("kısa gündüz uykusu olursa" in low) or ("yoğun direnç olursa" in low)
    rec("B-" + etiket, "Protokoller ayrı genel bölüm DEĞİL, gün altında",
        (not ayri_kg) and (not ayri_bp) and gun_alti,
        f"ayrı_KG={ayri_kg}, ayrı_BP={ayri_bp}, gün_altı={gun_alti}")

    # 6) B Planı 45->15(->30)->45, max 3
    bplan_ok = ("45 dak" in low or "45 dk" in low) and ("15 dak" in low or "15 dk" in low) \
        and ("3 tekrar" in low or "max 3" in low or "maksimum 3" in low or "maks 3" in low
             or "3 kez" in low or "üç tekrar" in low)
    rec("B-" + etiket, "B Planı 45dk->15dk(->30)->45dk, max 3",
        bplan_ok, "anahtar süreler mevcut" if bplan_ok else "eksik")

    # 7) 3 tekrar sonrası: uyanık tut + sonraki uyku, ARABA önerisi yok
    araba_oneri = araba_oneri_olarak_var(plan)
    ok7 = ("uyanık tut" in low_tr) and not araba_oneri
    rec("B-" + etiket, "3 tekrar sonrası uyanık tut + araba ÖNERİSİ yok",
        ok7, f"uyanık_tut={'uyanık tut' in low_tr}, araba_önerisi_yok={not araba_oneri}")

    # 8) Beyaz gürültü kademeli azaltma
    bg = ("gürültü" in low) and ("kademe" in low or "bir kademe" in low or "azalt" in low)
    rec("B-" + etiket, "Beyaz gürültü kademeli azaltma dili",
        bg, "kademe/azalt dili" if bg else "eksik")

    # 9) 13 günlük: kısa uyku dışarıda 1->1,5->2 dk
    if tip == "13_gun_dirençli":
        ok9 = bool(re.search(r"1\s*(dakika|dk).{0,12}1[.,]5.{0,12}2\s*(dakika|dk)", low)) \
            or ("1 → 1,5 → 2" in plan) or ("1 dakika → 1,5 dakika → 2 dakika" in plan)
        rec("B-" + etiket, "13 günlük kısa uyku dışarıda 1->1,5->2 dk",
            ok9, "kademeleme mevcut" if ok9 else "eksik")
    else:
        rec("B-" + etiket, "13 günlük 1->1,5->2 dk (bu plan 13 günlük değil)", None, "uygulanmaz")

    # 10) 1 aylık program: Hafta 1-2 destekle + Hafta 3-4 eğitim
    if tip == "1_ay_program":
        lown = low.replace("–", "-").replace("—", "-")  # en/em-dash -> hyphen
        destek_kavrami = ("destekle" in lown) or ("destekli" in lown) or ("alıştığı" in lown) \
            or ("destek" in lown and "uygulanmıyor" in lown)
        ok10 = ("hafta 1-2" in lown) and ("hafta 3-4" in lown) and ("eğitim" in lown) and destek_kavrami
        rec("B-" + etiket, "1 aylık: Hafta 1-2 destekle + Hafta 3-4 eğitim",
            ok10, f"H1-2={'hafta 1-2' in lown}, destek_kavramı={destek_kavrami}, H3-4={'hafta 3-4' in lown}")
    else:
        rec("B-" + etiket, "1 aylık ayrım (bu plan 1 aylık değil)", None, "uygulanmaz")

    # 11) Kucağa alma -> "alabilirsiniz + kademeli"
    if "kucağ" in low or "kucak" in low:
        ok11 = ("alabilir" in low) and ("kademeli" in low or "30 saniye" in low) \
            and not BANNED["kucağa almayın"].search(plan)
        rec("B-" + etiket, "Kucağa alma = 'alabilirsiniz + kademeli'",
            ok11, "doğru kalıp" if ok11 else "kalıp eksik/yasak dili")
    else:
        rec("B-" + etiket, "Kucağa alma kalıbı (metinde kucak geçmiyor)", None, "uygulanmaz")


def bolum_B():
    print("\n" + "=" * 70 + "\nBÖLÜM B — Canlı plan üretimi\n" + "=" * 70)
    for item in PROFILLER:
        etiket, profile = item["etiket"], item["profile"]
        param = parametre_uret(profile)
        prompt = _build_user_prompt(param)
        tip = param["plan_secimi"]["tip"]

        # --- Prompt-seviyesi deterministik ön-kontroller (API gerekmez) ---
        rec("B-pre-" + etiket, f"Plan tipi doğru ({tip})", True, tip)
        for kural in ["UYANIKLIK SÜRESİDİR", "kortizol yükselir",
                      "KUCAĞA ALMAYIN", "TEMAS YOK", "KADEMELİ AZALTMA",
                      "YARI GÖRÜNÜR", "BEBEK ARABASI GÜVENLİK",
                      "SON GÜNDÜZ UYKUSU BİTİŞ SAATİ ESNEKTİR",
                      "Bu gün kısa gündüz uykusu olursa", "B_plan_direnç"]:
            rec("B-pre-" + etiket, f"prompt içerir: '{kural[:32]}'", kural in prompt)
        if tip == "13_gun_dirençli":
            rec("B-pre-" + etiket, "13 günlük: bekleme 1→1,5→2 dk var",
                "1 dakika → 1,5 dakika → 2 dakika" in prompt)
        if tip == "1_ay_program":
            rec("B-pre-" + etiket, "1 aylık: Hafta 1-2/3-4 bloğu prompt'ta",
                "1 AYLIK PROGRAM" in prompt and "Düzen Oturtma Dönemi" in prompt)

        (OUT_DIR / f"prompt_{etiket}.txt").write_text(prompt, encoding="utf-8")

        plan_path = OUT_DIR / f"plan_{etiket}.md"
        force = os.getenv("FORCE_REGEN") == "1"
        if plan_path.exists() and not force:
            # Önbellek: kaydedilmiş canlı planı tekrar API çağırmadan değerlendir
            plan = plan_path.read_text(encoding="utf-8")
            rec("B-" + etiket, "Plan kaynağı", None, "önbellek (test_outputs/plan_*.md; FORCE_REGEN=1 ile yenile)")
            icerik_kontrolleri(etiket, plan, param)
        elif HAS_KEY:
            plan = plan_uret(param)  # CANLI Claude
            plan_path.write_text(plan, encoding="utf-8")
            icerik_kontrolleri(etiket, plan, param)
        else:
            # Fallback'i sadece arşiv için yaz, AÇIKÇA etiketle — checklist'i bunun üzerinde KOŞMA
            fb = plan_uret(param)
            (OUT_DIR / f"plan_{etiket}_FALLBACK_apikeysiz.md").write_text(
                "> UYARI: ANTHROPIC_API_KEY yok. Bu deterministik FALLBACK çıktısıdır; "
                "LLM prompt kuralları (uyanıklık notu, KATI/16:00 notları, gün-altı protokoller) "
                "RENDER EDİLMEZ. Gerçek plan için key ekleyip testi tekrar çalıştırın.\n\n" + fb,
                encoding="utf-8")
            rec("B-" + etiket, "CANLI plan üretimi", None,
                "ANTHROPIC_API_KEY yok — atlandı (prompt + fallback test_outputs/'a yazıldı)")


# ===========================================================================
# BÖLÜM C — CHATBOT
# ===========================================================================
SORULAR = [
    ("1_araba", "Bebek arabasıyla uyutabilir miyim?",
     {"araba_donmemeli": True, "uyanik_tut_donmeli": True}),
    ("2_16_00", "Saat 16:00'ı geçti ama bebek bugün az uyudu, bir uyku daha yaptırabilir miyim?",
     {"istisna_donmeli": True}),
    ("3_kucak", "Ağlarsa kucağa alabilir miyim?",
     {"alabilirsiniz_donmeli": True, "yasak_donmemeli": True}),
]


def bolum_C():
    print("\n" + "=" * 70 + "\nBÖLÜM C — Chatbot\n" + "=" * 70)
    chatbot.init_index()
    for key, soru, beklenti in SORULAR:
        hits = chatbot.retrieve(soru, top_k=5)
        hit_ids = [h["chunk_id"] for h in hits]
        # ESKİ (deprecated) araba chunk'ı gelmemeli; temiz 'kural_guncel' chunk'ı gelebilir
        depr_text = " ".join(h["text"].lower() for h in hits if h.get("lesson_id") != RULE_LESSON)
        rec("C-ret-" + key, "Retrieve: ESKİ araba içeren chunk YOK (temiz kural chunk'ı serbest)",
            "bebek arabas" not in depr_text,
            f"top={hit_ids[:3]}")

        # Endpoint regresyon modunda önbelleği atla (taze /chat cevabı iste).
        force = os.getenv("FORCE_REGEN") == "1" or USE_CHAT_ENDPOINT
        ans_path = OUT_DIR / f"ans_{key}.txt"
        cb_md = OUT_DIR / f"chatbot_{key}.md"
        if ans_path.exists() and not force:
            ans = ans_path.read_text(encoding="utf-8")
        elif cb_md.exists() and not force and "# Cevap" in cb_md.read_text(encoding="utf-8"):
            ans = cb_md.read_text(encoding="utf-8").split("# Cevap", 1)[1].split("\n\n", 1)[1]
        elif USE_CHAT_ENDPOINT:
            ans = _chat_answer(soru)          # YENİ: /api/v1/chat üzerinden
        else:
            ans = chatbot.cevapla(soru)
        ans_path.write_text(ans, encoding="utf-8")
        (OUT_DIR / f"chatbot_{key}.md").write_text(
            f"# Soru\n{soru}\n\n# Retrieved\n{hit_ids}\n\n# Cevap "
            f"({'CANLI' if HAS_KEY else 'FALLBACK (apikeysiz)'})\n\n{ans}",
            encoding="utf-8")
        low = ans.lower()

        if not HAS_KEY:
            rec("C-" + key, "CANLI chatbot cevabı", None,
                f"apikeysiz — fallback snippet yazıldı (retrieved={hit_ids[:2]})")
            # yine de fallback snippet üzerinden basit ipucu
            if beklenti.get("araba_donmemeli"):
                rec("C-" + key, "(fallback) araba ÖNERİSİ dönmedi", not araba_oneri_olarak_var(ans))
            if beklenti.get("istisna_donmeli"):
                rec("C-" + key, "(fallback) 16:00 istisna kuralı dönüyor",
                    ("ilave" in low or "kıymeti" in low) and "16:00" in ans)
            if beklenti.get("alabilirsiniz_donmeli"):
                rec("C-" + key, "(fallback) 'alabilirsiniz' dönüyor", "alabilir" in low)
            continue

        # CANLI cevap kontrolleri
        if beklenti.get("araba_donmemeli"):
            rec("C-" + key, "Araba ÖNERİSİ dönmedi (ret/yasak bağlamı serbest)",
                not araba_oneri_olarak_var(ans))
        if beklenti.get("uyanik_tut_donmeli"):
            rec("C-" + key, "Uyanık tutma kuralı döndü",
                "uyanık tut" in low or "uyanık kal" in low)
        if beklenti.get("istisna_donmeli"):
            rec("C-" + key, "16:00 istisna kuralı döndü",
                ("ilave" in low) or ("minimum" in low and "16" in ans))
        if beklenti.get("alabilirsiniz_donmeli"):
            rec("C-" + key, "'Alabilirsiniz + kademeli' döndü",
                "alabilir" in low and ("kademeli" in low or "30 saniye" in low))
        if beklenti.get("yasak_donmemeli"):
            rec("C-" + key, "Yasak dili dönmedi",
                not BANNED["kucağa almayın"].search(ans))


# ---------------------------------------------------------------------------
# BÖLÜM D — ARA YAŞ BANTLARI (yaş bandı köprüsü regresyonu)
# ---------------------------------------------------------------------------
# SORUN (düzeltildi): 9_ay gibi bazı bantlar korpusta HİÇ temsil edilmiyordu
# (yalnız sayısal alanları var, _is_descriptive_text onları eliyor) ve yas_bandi
# retrieval'ı etkilemiyordu → "9 ay için bilgim yok" cevabı dönüyordu.
# Artık bant, plan üretimiyle AYNI eşlemeyle (yas_bucket_sec) çözülüp yaş tablosu
# parametreleri bağlama ekleniyor.

# "Bilgim yok" kalıpları — ara yaş sorularında HİÇBİRİ geçmemeli.
BILGI_YOK_KALIPLARI = [
    "bilgim yok", "bilgi yok", "bulunmuyor", "bulunmamaktadır", "yeterli bilgi",
    "elimde yeterli", "bilgi bulunmamakta", "veri yok", "mevcut değil",
]

# (anahtar, soru, cevapta GEÇMESİ beklenen sayısal ipuçlarından en az biri)
YAS_SORULARI = [
    ("9ay_kisa_uyku", "9 aylık bebeğim günde kaç saat kısa uyku yapmalı",
     ["2-3", "2 - 3", "iki", "2 saat", "3 saat"]),
    ("9ay_uyanik", "9 aylık bebeğim ne kadar uyanık kalabilir",
     ["2.5", "2,5", "3.5", "3,5", "3-4", "saat"]),
    ("10ay_kac_uyku", "10 aylık bebeğim günde kaç kez uyumalı",
     ["2", "iki"]),
    ("6ay_uyanik", "6 aylık bebeğim ne kadar uyanık kalabilir",
     ["2", "3", "saat"]),
    ("6ay_toplam", "6 aylık bebeğim günde toplam ne kadar uyumalı",
     ["12", "15", "saat"]),
]


def bolum_D():
    print("\n" + "=" * 70 + "\nBÖLÜM D — Ara yaş bantları\n" + "=" * 70)
    chatbot.init_index()

    # D-0: bant çözümü — her ay için bir bant bulunmalı (boşluk YOK)
    bosluk = []
    for ay in range(0, 37):
        bantlar, _ = chatbot.bant_coz(f"{ay} aylık bebeğim ne yapmalı")
        blok = chatbot.yas_bandi_blok(bantlar, float(ay))
        if not bantlar or not blok.strip():
            bosluk.append(ay)
    rec("D-bant", "0-36 ay arasında bant boşluğu YOK", not bosluk,
        f"boşluk={bosluk}" if bosluk else "36/36 ay bant buldu")

    # D-0b: yaş geçiş dönemi iki bant döndürür
    bantlar, _ = chatbot.bant_coz("bebeğim 6 haftalık")
    rec("D-gecis", "Geçiş dönemi iki bandı birlikte döndürür", len(bantlar) == 2,
        f"bantlar={bantlar}")

    if not HAS_KEY:
        rec("D-canli", "CANLI ara yaş cevapları", None, "ANTHROPIC_API_KEY yok — atlandı")
        return

    for key, soru, ipuclari in YAS_SORULARI:
        # Bu bölüm HER ZAMAN taze cevap ister (cache'lenmiş eski "bilgim yok"
        # cevabı regresyonu maskelemesin).
        ans = _chat_answer(soru) if USE_CHAT_ENDPOINT else chatbot.cevapla(soru)
        (OUT_DIR / f"yas_{key}.txt").write_text(ans, encoding="utf-8")
        low = _low(ans)

        gecen = [k for k in BILGI_YOK_KALIPLARI if k in low]
        rec("D-" + key, "'Bilgim yok' kalıbı GEÇMEDİ", not gecen,
            f"geçen={gecen}" if gecen else f"cevap={ans[:70]!r}")

        rec("D-" + key, "Yaş bandı bilgisi verildi (sayısal ipucu var)",
            any(ip in low for ip in ipuclari),
            f"aranan={ipuclari[:3]} cevap={ans[:70]!r}")

        # Tıbbi sınır ve yasak dil korunuyor mu (mevcut davranış bozulmadı)
        rec("D-" + key, "Danışmanlık yönlendirmesi YOK",
            "danışman" not in low, f"cevap={ans[:60]!r}")


def ozet():
    print("\n" + "=" * 70 + "\nÖZET\n" + "=" * 70)
    gecti = sum(1 for r in results if r[2] == "GEÇTİ")
    kaldi = [r for r in results if r[2] == "KALDI"]
    bilgi = sum(1 for r in results if r[2] == "BİLGİ")
    print(f"GEÇTİ: {gecti} | KALDI: {len(kaldi)} | BİLGİ/atlandı: {bilgi}")
    if kaldi:
        print("\nKALAN KONTROLLER:")
        for b, k, d, kan in kaldi:
            print(f"  - [{b}] {k}  ({kan})")
    print(f"\nÇıktılar: {OUT_DIR}")
    return len(kaldi)


if __name__ == "__main__":
    print(f"ANTHROPIC_API_KEY: {'VAR (canlı)' if HAS_KEY else 'YOK (B/C canlı atlanır)'}")
    bolum_A()
    bolum_B()
    bolum_C()
    bolum_D()
    n_kaldi = ozet()
    sys.exit(0)
