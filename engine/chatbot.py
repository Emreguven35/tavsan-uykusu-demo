"""
RAG soru-cevap motoru — SEMANTİK (embedding) retrieval + Claude generation.

Birincil retrieval: sentence-transformers ile çok dilli (Türkçe destekli) local
embedding'ler üzerinden cosine similarity. Eşanlamlı/farklı ifadeyle sorulan
sorular (örn. "emzirme" ↔ "beslenme/mama/süt") artık eşleşir.

Korpus = chunks.json (506) + master_knowledge_base.json'daki global_rules ve
yaş-bazlı AÇIKLAYICI metinler (besleme merdiveni dahil). Sayısal parametre
tabloları HARİÇ.

Embedding'ler diske bir kez yazılır (data/embeddings.npy + data/corpus_meta.json);
uygulama her açılışta yeniden embed etmez. Yeniden üretmek için: build_embeddings.py.

Güvenlik: sentence-transformers/torch yüklenemez veya RAM yetmezse, otomatik
olarak eski TF-IDF retrieval'a düşülür (aynı birleşik korpus üzerinde). Demo
asla tamamen çökmez. Hangi retrieval'ın aktif olduğu loglanır.
"""
import os
import json
import re
import hashlib
import logging
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from engine.config import CHATBOT_MODEL  # chatbot/RAG modeli (haiku — merkezi)
from engine.config import MODEL_NAME  # noqa: F401 — build_embeddings doc2query re-export

MAX_TOKENS = 1024
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Embedding modeli: çok dilli, Türkçe destekli, hafif (~470MB, ~118M param).
# Streamlit Cloud ücretsiz katman (~1GB RAM) için MiniLM-L12 tercih edildi;
# mpnet-base (~420M param) kalite olarak biraz daha iyi ama RAM'i zorlar.
EMB_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMB_PATH = DATA_DIR / "embeddings.npy"
META_PATH = DATA_DIR / "corpus_meta.json"
# Doc2query genişletmeleri (build-time, LLM ile bir kez üretilip cache'lenir).
# Curated kurallar terse olduğundan "18 aylık emzirme kaç saat önce" gibi compound
# sorularda yaş-chunk'larının altında kalıyor; bu dosya her kuralın yanıtladığı
# doğal ebeveyn sorularını embed metnine ekleyerek (yalnızca ARAMA için, gösterimde
# değil) sorgu seyrelmesini çözer. RUNTIME synonym yaması DEĞİL — bir kez üretilir.
EXPANSIONS_PATH = DATA_DIR / "kb_expansions.json"

# HİBRİT retrieval: semantik (eşanlamlı yakalar) + lexical/TF-IDF (tam terim eşleşmesi).
# Saf semantik, "kucağa alabilirsiniz → 30 saniye" gibi spesifik kural metinlerini
# genel sohbet chunk'larının altında bırakabiliyor; lexical sinyal bunları yukarı çeker.
# Saf lexical ise eşanlamlıları kaçırır. İkisinin füzyonu kalıcı çözümdür.
HYBRID_ALPHA = 0.65        # semantik ağırlığı (1-ALPHA = lexical ağırlığı)
SEM_MIN_SCORE = 0.22       # birleşik skor eşiği (testle ayarlandı; agresif değil)
SEM_TOP_K = 8              # 8: spesifik kural chunk'ı bağlama girsin (k=5 bazılarını kaçırıyor)
TFIDF_MIN_SCORE = 0.05     # saf TF-IDF fallback eşiği

logger = logging.getLogger("tavsan.chatbot")

# Lazy-loaded global cache (process boyunca tek sefer; Streamlit rerun'larında korunur)
_state: dict[str, Any] = {
    "ready": False,
    "active": None,        # "semantic" | "tfidf"
    # semantic
    "model": None,
    "embeddings": None,    # (N, dim) float32, normalize edilmiş
    "units": None,         # korpus birimleri (chunk_id, text, source, lesson_id, label)
    # tfidf fallback
    "vectorizer": None,
    "vectors": None,
}


# Türkçe için basit stopword listesi (TF-IDF fallback'inde kullanılır)
TURKCE_STOPWORDS = {
    "ve", "ile", "için", "bu", "şu", "o", "bir", "iki", "üç", "da", "de",
    "ki", "mi", "mı", "mu", "mü", "ne", "var", "yok", "olan", "ama", "ancak",
    "fakat", "ya", "veya", "hem", "hiç", "çok", "az", "daha", "en", "gibi",
    "kadar", "sonra", "önce", "sırasında", "üzerine", "altına", "yani", "şey",
}


def _normalize(text: str) -> str:
    """Türkçe karakterleri normalize et, küçük harfe çevir (TF-IDF için)."""
    text = text.lower()
    text = text.replace("i̇", "i")
    text = re.sub(r"[^\wçğıöşü\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_chunks() -> list[dict]:
    with open(DATA_DIR / "chunks.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# KORPUS KURMA — chunks + global_rules + yaş-bazlı açıklayıcı metinler
# ---------------------------------------------------------------------------
def _humanize(key: str) -> str:
    """JSON anahtarını okunabilir Türkçe etikete çevir.
    'beslenme_uyku_iliskisi (kayıt36, kayıt37)' -> 'Beslenme uyku iliskisi'
    Parantez içi kayıt/ders referansları etiketten temizlenir (modele sızmasın)."""
    key = re.sub(r"\([^)]*\)", "", key)          # parantez içini at
    key = re.sub(r"\[[^\]]*\]", "", key)          # köşeli parantez içini at
    key = key.replace("_", " ").replace(".", " ")
    key = re.sub(r"\s+", " ", key).strip()
    return key[:1].upper() + key[1:] if key else key


def _is_descriptive_text(value: Any) -> bool:
    """Açıklayıcı metin mi (embed edilmeli), yoksa sayısal/kısa parametre mi?"""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) < 20:
        return False
    # Saf saat/sayı aralığı ("19:00-20:00", "14-16 saat") gibi şeyleri ele
    if re.fullmatch(r"[\d\.\,\:\-\s]+(saat|dakika|dk|ay|hafta|öğün|kez)?", v, re.IGNORECASE):
        return False
    return True


# ---------------------------------------------------------------------------
# YAŞ BANDI KÖPRÜSÜ — retrieval'daki bant boşluğunu kapatır
# ---------------------------------------------------------------------------
# SORUN: yaş bucket'larından korpusa YALNIZCA açıklayıcı metinler giriyor
# (_is_descriptive_text sayısal alanları eler). 9_ay gibi bazı bantlarda yalnız
# sayısal alan olduğundan o bant korpusta HİÇ TEMSİL EDİLMİYOR → "9 ay için
# bilgim yok" cevabı. Ayrıca yas_bandi parametresi retrieval'ı hiç etkilemiyordu
# (yalnız cache anahtarıydı).
#
# ÇÖZÜM: soru metninden (veya yas_bandi parametresinden) yaşı çöz, plan üretimiyle
# AYNI eşlemeyi (parameter_engine.yas_bucket_sec) kullanarak bandı seç ve o bandın
# SAYISAL parametrelerini bağlama deterministik bir blok olarak ekle. Bandın bir
# alanı boşsa EN YAKIN banttan doldurulur ve hangi banda dayandığı belirtilir.
# Böylece bantlar arasında boşluk kalmaz.

# yas_bucket_sec'in ürettiği bantlar, KÜÇÜKTEN BÜYÜĞE (komşuluk hesabı için).
YAS_BANT_SIRASI = [
    "0-6_hafta", "7-12_hafta", "3_ay", "4_ay", "5_ay", "6_ay", "7_ay", "8_ay",
    "9_ay", "10_ay", "11_ay", "12_ay", "12-13_ay", "14_ay", "15-17_ay",
    "18_ay", "18-24_ay", "2-3_yas", "40_ay_buyuk_cocuk",
]

# FAZ Y: sayısal yaş parametreleri artık data/yas_bantlari.json'dan gelir (plan
# motoruyla AYNI kaynak). KB bucket'ları yalnız yardımcı içerik (yatma vakti,
# gündüz uykusunu bitirme saati, örnek program) için okunur.
# Soru metninde yaş geçmeyip yalnız `yas_bandi` parametresi geldiğinde tabloyu
# sorgulayabilmek için her KB bucket'ının TEMSİLİ AYI:
BANT_TEMSILI_AY = {
    "0-6_hafta": 1.0, "7-12_hafta": 2.0, "3_ay": 3.0, "4_ay": 4.0, "5_ay": 5.0,
    "6_ay": 6.0, "7_ay": 7.0, "8_ay": 8.0, "9_ay": 9.0, "10_ay": 10.0,
    "11_ay": 11.0, "12_ay": 12.0, "12-13_ay": 12.5, "14_ay": 14.0,
    "15-17_ay": 16.0, "18_ay": 18.0, "18-24_ay": 21.0, "2-3_yas": 30.0,
    "40_ay_buyuk_cocuk": 40.0,
}

# Bağlama taşınacak sayısal parametreler (insan-okur etiketleriyle).
YAS_PARAM_ETIKET = {
    "toplam_uyku_24h": "Toplam uyku (24 saat)",
    "gece_uyku": "Gece uykusu",
    "gunduz_uyku_total": "Gündüz uyku toplamı",
    "uyku_sayisi": "Gündüz uyku sayısı",
    "uyaniklik_penceresi": "Uyanıklık penceresi",
    "yatma_vakti": "Yatma vakti",
    "gunduz_uyku_bitirme": "Gündüz uykusunu bitirme saati",
}

# "9 aylık", "9 ay", "9,5 aylik", "6 haftalık", "2 yaşında" → ay cinsinden yaş.
_RE_AY = re.compile(r"(\d+(?:[.,]\d+)?)\s*ay", re.IGNORECASE)
_RE_HAFTA = re.compile(r"(\d+(?:[.,]\d+)?)\s*haft", re.IGNORECASE)
_RE_YAS = re.compile(r"(\d+(?:[.,]\d+)?)\s*yaş", re.IGNORECASE)


def yas_ay_tespit(soru: str) -> float | None:
    """Soru metninden bebeğin ay cinsinden yaşını çıkar. Bulamazsa None."""
    if not soru:
        return None
    for rx, carpan in ((_RE_AY, 1.0), (_RE_HAFTA, 1 / 4.345), (_RE_YAS, 12.0)):
        m = rx.search(soru)
        if m:
            try:
                return float(m.group(1).replace(",", ".")) * carpan
            except ValueError:
                continue
    return None


def _bant_index(band: str) -> int | None:
    try:
        return YAS_BANT_SIRASI.index(band)
    except ValueError:
        return None


def _bant_sinirinda_mi(yas_ay: float, band: str) -> str | None:
    """Yaş, bandın üst sınırına ≤0.5 ay kaldıysa BİR SONRAKİ bandı döndür.

    Geçiş dönemindeki bebekte iki bandın aralığı birlikte özetlenmelidir."""
    from engine.parameter_engine import yas_bucket_sec
    ust_bant = yas_bucket_sec(yas_ay + 0.5)
    return ust_bant if ust_bant != band else None


def bant_coz(soru: str, yas_bandi: str | None = None) -> tuple[list[str], float | None]:
    """Etkin yaş bandını çöz. Dönen: (bantlar, tespit_edilen_ay).

    Öncelik: soru metnindeki açık yaş > yas_bandi parametresi. Metindeki yaş daha
    özeldir (kullanıcı "9 aylık bebeğim" diyorsa profildeki bant eski olabilir).
    Geçiş dönemindeyse iki bant birden döner."""
    from engine.parameter_engine import yas_bucket_sec

    yas_ay = yas_ay_tespit(soru)
    if yas_ay is not None and 0 <= yas_ay <= 120:
        band = yas_bucket_sec(yas_ay)
        bantlar = [band]
        komsu = _bant_sinirinda_mi(yas_ay, band)
        if komsu:
            bantlar.append(komsu)
        return bantlar, yas_ay

    if yas_bandi and yas_bandi in YAS_BANT_SIRASI:
        return [yas_bandi], None
    return [], None


def _param_deger(buckets: dict, band: str, alan: str) -> tuple[Any, str] | None:
    """Bandın `alan` değerini getir; boşsa EN YAKIN banttan doldur.

    Dönen: (değer, değerin_alındığı_bant) veya None."""
    idx = _bant_index(band)
    if idx is None:
        return None
    # Mesafe 0'dan başlayıp dışa doğru tara (önce yakın bantlar).
    for mesafe in range(0, len(YAS_BANT_SIRASI)):
        for yon in ((0,) if mesafe == 0 else (-mesafe, mesafe)):
            j = idx + yon
            if not (0 <= j < len(YAS_BANT_SIRASI)):
                continue
            b = YAS_BANT_SIRASI[j]
            val = (buckets.get(b) or {}).get(alan)
            if isinstance(val, dict):                    # {'RESMI_DEGER': '...'}
                val = next((v for k, v in val.items() if "RESMI" in k.upper()),
                           next(iter(val.values()), None))
            if val not in (None, "", []):
                return val, b
    return None


# Tablodan gelen (birincil) değerlerin tekrar KB'den yazılmasını önle.
_TABLO_KAPSAMINDAKI_ALANLAR = {
    "uyaniklik_penceresi", "uyku_sayisi", "gunduz_uyku_total", "gece_uyku",
    # toplam_uyku_24h: KB "12-15 Saat" gibi geniş aralıklar veriyor, İlayda'nın
    # resmi tablosu bant başına net değer (ör. 6-8 ay = 14 saat). Tablo kazanır.
    "toplam_uyku_24h",
}


def _tablo_bant_ay(band: str, yas_ay: float | None) -> float | None:
    """Bir KB bandı için yaş bandı tablosuna sorulacak ay değeri.

    Sorudaki yaş O BANDA düşüyorsa ayın kendisi kullanılır (en isabetli);
    aksi halde (geçiş dönemindeki komşu bant) bandın temsili ayı kullanılır."""
    from engine.parameter_engine import yas_bucket_sec
    if yas_ay is not None and yas_bucket_sec(yas_ay) == band:
        return yas_ay
    return BANT_TEMSILI_AY.get(band)


def yas_bandi_blok(bantlar: list[str], yas_ay: float | None = None) -> str:
    """Yaş bandı parametrelerini LLM bağlamına girecek metin bloğu olarak kur.

    FAZ Y — İKİ KATMAN:
      1. BİRİNCİL: İlayda yaş bandı tablosu (data/yas_bantlari.json). Plan
         motorunun kullandığı sayıların BİREBİR aynısı; 0-36 ay arasında ara yaş
         yoktur, "9 aylık" sorusu artık boşluğa düşemez.
      2. TAMAMLAYICI: KB bucket'ından tabloda BULUNMAYAN alanlar (yatma vakti,
         gündüz uykusunu bitirme saati, 24 saatlik toplam). Boşsa en yakın
         banttan doldurulur ve bu AÇIKÇA belirtilir.
    Ayrıca evrensel kestirme kuralı her cevaba taşınır."""
    if not bantlar:
        return ""
    kb = _load_kb_safe()
    buckets = kb.get("yas_buckets", {}) if kb else {}

    satirlar: list[str] = []
    tablo_bulundu = False
    for band in bantlar:
        baslik = _humanize(band)
        bant_satirlari: list[str] = []
        # Bu bant tablodan çözülebildi mi? BANT BAŞINA tutulur: bir bant tablodan
        # gelirken diğerinin KB alanları yanlışlıkla elenmesin.
        bu_bant_tablodan = False

        # 1) Tablo (birincil sayısal kaynak)
        ay = _tablo_bant_ay(band, yas_ay)
        if ay is not None:
            try:
                from engine import yas_bantlari
                cozulmus = yas_bantlari.yas_bandi_getir(ay)
                bant_satirlari.append(f"[{cozulmus['ad']} bandı — Tavşan Uykusu yaş tablosu]")
                bant_satirlari += yas_bantlari.bant_ozet_satirlari(cozulmus)
                if cozulmus.get("tek_uykuya_gecis_sartlari"):
                    sartlar = cozulmus["tek_uykuya_gecis_sartlari"]
                    bant_satirlari.append(
                        "- Tek uykuya geçiş şartları (ÜÇÜ BİRDEN gerekli): "
                        + "; ".join(s["metin"] for s in sartlar["sartlar"])
                        + f". {sartlar['saglanmazsa']}")
                if cozulmus.get("ogle_uykusu_reddi_protokolu"):
                    red = cozulmus["ogle_uykusu_reddi_protokolu"]
                    bant_satirlari.append(
                        "- Öğlen uykusu reddi protokolü: "
                        + " ".join(f"{a['sira']}) {a['metin']}" for a in red["adimlar"]))
                bu_bant_tablodan = True
                tablo_bulundu = True
            except Exception as e:                   # tablo bozuk → KB'ye düş
                logger.warning("Yaş bandı tablosu okunamadı: %s", e)

        if not bant_satirlari:
            bant_satirlari.append(f"[{baslik} bandı]")

        # 2) KB'den tamamlayıcı alanlar (tabloda olmayanlar)
        for alan, etiket in YAS_PARAM_ETIKET.items():
            if bu_bant_tablodan and alan in _TABLO_KAPSAMINDAKI_ALANLAR:
                continue
            bulunan = _param_deger(buckets, band, alan) if buckets else None
            if bulunan is None:
                continue
            val, kaynak_band = bulunan
            not_ = "" if kaynak_band == band else f"  (en yakın bant: {_humanize(kaynak_band)})"
            bant_satirlari.append(f"- {etiket}: {val}{not_}")

        satirlar += bant_satirlari

    if not satirlar:
        return ""

    # Evrensel kural — tüm bantlarda geçerli, her cevaba taşınır.
    if tablo_bulundu:
        try:
            from engine import yas_bantlari
            proto = yas_bantlari.kestirme_protokolu()
            satirlar.append(
                f"[Tüm yaşlarda geçerli kural] Gündüz toplam uyku minimumu "
                f"tamamlanamazsa ilave {proto['sure_dk']} dakikalık kestirme uykusu "
                f"yaptırılır ({proto['sure_dk']} dakika dolunca uyandırılır); bu "
                f"kestirmeden uyandıktan {proto['gece_uykusuna_gecis_dk']} dakika "
                "(1 saat) sonra bile gece uykusuna geçilebilir.")
        except Exception:
            pass

    yas_ifade = ""
    if yas_ay is not None:
        yas_ifade = f" (sorudaki yaş: yaklaşık {yas_ay:.1f} ay)"
    gecis = ""
    if len(bantlar) > 1:
        gecis = (" Bebek YAŞ GEÇİŞ DÖNEMİNDE; iki bandın aralığını birlikte "
                 "özetle.")
    return ("YAŞ BANDI PARAMETRELERİ (Tavşan Uykusu yaş tabloları)"
            f"{yas_ifade}:{gecis}\n" + "\n".join(satirlar))


def _tablo_bant_onekleri(bantlar: list[str]) -> list[str]:
    """KB bant adlarını yaş bandı tablosunun chunk_id öneklerine çevir.

    'yas_bucket:9_ay' → 'yas_bandi:9-12_ay' (tekrarlar teke indirilir, sıra korunur)."""
    onekler: list[str] = []
    for band in bantlar:
        ay = BANT_TEMSILI_AY.get(band)
        if ay is None:
            continue
        try:
            from engine import yas_bantlari
            onek = f"yas_bandi:{yas_bantlari.yas_bandi_getir(ay)['id']}"
        except Exception:
            continue
        if onek not in onekler:
            onekler.append(onek)
    return onekler


def _bant_birimleriyle_birlestir(retrieved: list[dict], bantlar: list[str],
                                 max_ek: int = 4) -> list[dict]:
    """Çözülen bantların korpustaki birimlerini sonuca EKLE (skorları değiştirmeden).

    Retrieval sıralamasına dokunulmaz; yalnızca eksik bant birimleri sona eklenir.
    Böylece semantik sıralama bandı ıskalasa bile o bandın içeriği bağlama girer.
    Zaten getirilmiş birimler tekrar eklenmez.

    Faz Y: KB bucket birimlerinin YANINDA, yaş bandı tablosunun (yas_bantlari.json)
    o yaşa karşılık gelen metin birimi de eklenir — KB'de karşılığı olmayan
    aralıklarda (12-13/14/15-17 ay) bağlam boş kalmaz."""
    units = (_state.get("units") or []) if isinstance(_state, dict) else []
    if not units:
        return retrieved
    mevcut = {u.get("chunk_id") for u in retrieved}
    ekler: list[dict] = []
    for band in _tablo_bant_onekleri(bantlar) + [f"yas_bucket:{b}." for b in bantlar]:
        onek = band
        for u in units:
            cid = u.get("chunk_id", "")
            if cid.startswith(onek) and cid not in mevcut:
                ek = dict(u)
                ek.setdefault("_score", 0.0)     # skor yok: sıralamaya karışmaz
                ekler.append(ek)
                mevcut.add(cid)
            if len(ekler) >= max_ek:
                break
        if len(ekler) >= max_ek:
            break
    return retrieved + ekler


def _load_kb_safe() -> dict | None:
    """master_knowledge_base'i yükle; hata olursa None (chat çökmesin)."""
    try:
        from engine.parameter_engine import load_kb
        return load_kb()
    except Exception as e:                          # dosya yok/bozuk → bant bloğu yok
        logger.warning("Yaş bandı tablosu yüklenemedi: %s", e)
        return None


def _flatten_texts(prefix: str, val: Any, out: list[tuple]) -> None:
    """global_rules içindeki metinsel içerikleri (str/dict/list) düz birime indir."""
    if isinstance(val, str):
        if _is_descriptive_text(val):
            out.append((prefix, val))
    elif isinstance(val, dict):
        for k, v in val.items():
            _flatten_texts(f"{prefix}.{k}", v, out)
    elif isinstance(val, list):
        for i, item in enumerate(val):
            _flatten_texts(f"{prefix}[{i}]", item, out)


def _load_expansions() -> dict:
    """Doc2query genişletmelerini yükle (chunk_id -> 'soru1 | soru2 ...'). Yoksa {}."""
    if EXPANSIONS_PATH.exists():
        try:
            return json.loads(EXPANSIONS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("kb_expansions.json okunamadı: %s", e)
    return {}


# Doc2query genişletmesi uygulanacak birimler: curated kurallar (terse + yüksek değer).
def _is_expandable(unit: dict) -> bool:
    return (unit["source"] in ("global_rule", "yas_bucket", "yas_bandi")
            or unit["chunk_id"].startswith("kural"))


def build_corpus() -> list[dict]:
    """Birleşik aranabilir korpusu kur. Sıra deterministiktir (embeddings.npy ile hizalı):
    1) chunks.json, 2) global_rules (metinsel), 3) yaş bucket'larının açıklayıcı
    metinleri, 4) İlayda yaş bandı tablosu (Faz Y — metin formu).

    (4) metinleri data/yas_bantlari.json'daki SAYILARDAN deterministik üretilir
    (engine.yas_bantlari.bant_metinleri): motor ile chat cevabı asla ayrışamaz.

    Her birimde 'text' (modele gösterilecek temiz metin) bulunur. Doc2query
    genişletmesi ayrı RETRIEVAL SATIRLARI olarak eklenir (bkz. _build_rows) — tek
    embedding'e eklenmez (concat ortalaması seyreltir). Çok-vektörlü yaklaşım.
    """
    units: list[dict] = []

    # 1) chunks.json — metni AYNEN korunur (mevcut davranış değişmesin)
    for c in load_chunks():
        units.append({
            "chunk_id": c["chunk_id"],
            "text": c["text"],
            "source": "chunk",
            "lesson_id": c.get("lesson_id"),
            "label": c.get("lesson_title") or "İçerik",
        })

    with open(DATA_DIR / "master_knowledge_base.json", "r", encoding="utf-8") as f:
        kb = json.load(f)

    # 2) global_rules — her metinsel kuralı ayrı aranabilir birim yap
    for key, val in kb.get("global_rules", {}).items():
        label_root = _humanize(key)
        flat: list[tuple] = []
        _flatten_texts(key, val, flat)
        for path, text in flat:
            sub = path[len(key):].lstrip(".")
            label = f"{label_root}{(' — ' + _humanize(sub)) if sub else ''}"
            units.append({
                "chunk_id": f"global_rule:{path}",
                "text": f"{label}: {text}",
                "source": "global_rule",
                "lesson_id": None,
                "label": label,
            })

    # 3) yaş bucket'ları — yalnızca AÇIKLAYICI metin alanları (sayısal tablolar HARİÇ)
    for band, bucket in kb.get("yas_buckets", {}).items():
        if not isinstance(bucket, dict):
            continue
        band_h = _humanize(band)
        for fk, fv in bucket.items():
            if _is_descriptive_text(fv):
                label = f"{band_h} — {_humanize(fk)}"
                units.append({
                    "chunk_id": f"yas_bucket:{band}.{fk}",
                    "text": f"{label}: {fv}",
                    "source": "yas_bucket",
                    "lesson_id": None,
                    "label": label,
                })

    # 4) İlayda yaş bandı tablosu — metin formu (0-36 ay tam kapsam + evrensel
    #    kestirme kuralı + 12-18 ay geçiş şartları + 24-36 ay reddi protokolü).
    try:
        from engine import yas_bantlari
        for b in yas_bantlari.bant_metinleri():
            units.append({
                "chunk_id": b["chunk_id"],
                "text": b["text"],
                "source": "yas_bandi",
                "lesson_id": None,
                "label": b["label"],
            })
    except Exception as e:                       # tablo yoksa korpus yine çalışır
        logger.warning("Yaş bandı tablosu korpusa eklenemedi: %s", e)

    return units


def _build_rows(units: list[dict]) -> tuple[list[str], list[int]]:
    """Çok-vektörlü retrieval satırları kur. Her birim için:
      - 1 temel satır (birimin kendi metni),
      - curated birimlerde doc2query sorularının HER BİRİ ayrı satır (aynı birime işaret).
    Döner: (row_texts, row_unit_idx) — embeddings.npy ile aynı sırada.
    Sorgu, genişletme sorusuyla yüksek benzerlik kurup ana birime ulaşır (seyrelme yok).
    """
    expansions = _load_expansions()
    row_texts: list[str] = []
    row_unit: list[int] = []
    for i, u in enumerate(units):
        row_texts.append(u["text"])
        row_unit.append(i)
        if _is_expandable(u):
            exp = expansions.get(u["chunk_id"])
            if exp:
                for q in exp.split(" | "):
                    q = q.strip()
                    if q:
                        row_texts.append(q)
                        row_unit.append(i)
    return row_texts, row_unit


def corpus_stats(units: list[dict] | None = None) -> dict:
    units = units if units is not None else build_corpus()
    from collections import Counter
    c = Counter(u["source"] for u in units)
    return {"toplam": len(units), **dict(c)}


# ---------------------------------------------------------------------------
# SEMANTİK retrieval (birincil)
# ---------------------------------------------------------------------------
def _load_model():
    """Embedding modelini bir kez yükle (ağır işlem)."""
    if _state["model"] is None:
        from sentence_transformers import SentenceTransformer
        _state["model"] = SentenceTransformer(EMB_MODEL_NAME)
    return _state["model"]


def build_index_to_disk(verbose: bool = True) -> dict:
    """Korpusu embed edip diske yaz (embeddings.npy + corpus_meta.json).
    build_embeddings.py bunu çağırır. Çıktıyı tekrar üretilebilir kılar."""
    units = build_corpus()
    row_texts, row_unit = _build_rows(units)        # çok-vektörlü satırlar
    model = _load_model()
    emb = model.encode(
        row_texts, batch_size=64, normalize_embeddings=True,
        show_progress_bar=verbose,
    )
    emb = np.asarray(emb, dtype=np.float32)
    np.save(EMB_PATH, emb)
    META_PATH.write_text(
        json.dumps(
            {"model": EMB_MODEL_NAME, "dim": int(emb.shape[1]),
             "count": len(units), "n_rows": len(row_texts),
             "units": units, "row_unit": row_unit},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stats = corpus_stats(units)
    if verbose:
        logger.info("Embedding index yazıldı: %s satır (%d birim) | %s",
                    emb.shape, len(units), stats)
    return {"shape": list(emb.shape), "n_rows": len(row_texts), **stats}


def _init_semantic() -> None:
    """Model + embedding index'i yükle. Eksik/bayatsa diske yeniden yaz.
    Hata olursa istisnayı yukarı fırlatır (init_index TF-IDF'e düşer)."""
    needs_build = True
    units = build_corpus()                          # ucuz (sadece JSON)
    row_texts, row_unit = _build_rows(units)        # cache hizası için yeniden kur
    if EMB_PATH.exists() and META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            emb = np.load(EMB_PATH)
            if (meta.get("model") == EMB_MODEL_NAME
                    and meta.get("count") == len(units)
                    and emb.shape[0] == len(row_texts)):
                _state["units"] = meta["units"]
                _state["row_unit"] = np.asarray(meta["row_unit"], dtype=np.int64)
                _state["row_texts"] = row_texts
                _state["embeddings"] = emb.astype(np.float32)
                needs_build = False
        except Exception as e:  # bozuk cache → yeniden kur
            logger.warning("Embedding cache okunamadı, yeniden kurulacak: %s", e)

    if needs_build:
        logger.info("Embedding cache yok/bayat — yeniden üretiliyor (%d birim, %d satır)...",
                    len(units), len(row_texts))
        build_index_to_disk(verbose=False)
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        _state["units"] = meta["units"]
        _state["row_unit"] = np.asarray(meta["row_unit"], dtype=np.int64)
        _state["row_texts"], _ = _build_rows(meta["units"])
        _state["embeddings"] = np.load(EMB_PATH).astype(np.float32)

    _load_model()              # sorgu embedding'i için modeli hazırla
    _build_lexical_index()     # hibrit için lexical (TF-IDF) sinyali


def _max_per_unit(row_scores: np.ndarray) -> np.ndarray:
    """Satır skorlarını birim başına MAX ile topla (çok-vektörlü → birim skoru)."""
    n_units = len(_state["units"])
    agg = np.zeros(n_units, dtype=np.float32)
    np.maximum.at(agg, _state["row_unit"], row_scores)
    return agg


def _build_lexical_index() -> None:
    """Retrieval SATIRLARI üzerinde TF-IDF index'i kur — hibrit füzyon için."""
    texts = [_normalize(t) for t in _state["row_texts"]]
    vectorizer = TfidfVectorizer(
        analyzer="word", ngram_range=(1, 2), max_features=20000,
        min_df=1, max_df=0.95, stop_words=list(TURKCE_STOPWORDS),
        token_pattern=r"(?u)\b\w\w+\b",
    )
    _state["vectors"] = vectorizer.fit_transform(texts)   # (M_rows, V)
    _state["vectorizer"] = vectorizer


def _retrieve_semantic(query: str, top_k: int, min_score: float) -> list[dict]:
    """HİBRİT + ÇOK-VEKTÖRLÜ: satır bazında semantik & lexical cosine hesapla,
    birim başına MAX'a indir, sonra füzyonla. combined = ALPHA*sem + (1-ALPHA)*lex."""
    model = _state["model"]
    qv = model.encode([query], normalize_embeddings=True)[0].astype(np.float32)
    sem_rows = np.clip(_state["embeddings"] @ qv, 0.0, 1.0)        # (M,)
    sem = _max_per_unit(sem_rows)                                  # (N,)

    lex = np.zeros_like(sem)
    if _state.get("vectorizer") is not None:
        q_lex = _state["vectorizer"].transform([_normalize(query)])
        lex_rows = cosine_similarity(q_lex, _state["vectors"]).flatten()  # (M,)
        lex = _max_per_unit(lex_rows)                                     # (N,)

    combined = HYBRID_ALPHA * sem + (1.0 - HYBRID_ALPHA) * lex
    top_idx = combined.argsort()[-top_k:][::-1]
    out = []
    for i in top_idx:
        if combined[i] >= min_score:
            u = dict(_state["units"][i])
            u["_score"] = float(combined[i])
            u["_sem"] = float(sem[i])
            u["_lex"] = float(lex[i])
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# TF-IDF retrieval (FALLBACK) — aynı birleşik korpus üzerinde
# ---------------------------------------------------------------------------
def _init_tfidf() -> None:
    units = build_corpus()
    texts = [_normalize(u["text"]) for u in units]
    vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        max_features=20000,
        min_df=1,
        max_df=0.95,
        stop_words=list(TURKCE_STOPWORDS),
        token_pattern=r"(?u)\b\w\w+\b",
    )
    vectors = vectorizer.fit_transform(texts)
    _state["units"] = units
    _state["vectorizer"] = vectorizer
    _state["vectors"] = vectors


def _retrieve_tfidf(query: str, top_k: int, min_score: float) -> list[dict]:
    q_vec = _state["vectorizer"].transform([_normalize(query)])
    sims = cosine_similarity(q_vec, _state["vectors"]).flatten()
    top_idx = sims.argsort()[-top_k:][::-1]
    out = []
    for i in top_idx:
        if sims[i] >= min_score:
            u = dict(_state["units"][i])
            u["_score"] = float(sims[i])
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# PUBLIC API — init_index / retrieve / cevapla (imzalar korundu)
# ---------------------------------------------------------------------------
def init_index() -> None:
    """Retrieval index'ini bir kez kur. Önce semantik denenir; başarısız olursa
    (model/torch yok ya da RAM hatası) TF-IDF'e otomatik düşülür."""
    if _state["ready"]:
        return
    try:
        _init_semantic()
        _state["active"] = "semantic"
        logger.info("Retrieval: SEMANTİK aktif (model=%s, %d birim)",
                    EMB_MODEL_NAME, len(_state["units"]))
    except Exception as e:
        logger.warning("Semantik retrieval kurulamadı (%s) — TF-IDF fallback'e düşülüyor.", e)
        _init_tfidf()
        _state["active"] = "tfidf"
        logger.info("Retrieval: TF-IDF FALLBACK aktif (%d birim)", len(_state["units"]))
    _state["ready"] = True


def active_retrieval() -> str | None:
    """Aktif retrieval türü ('semantic' | 'tfidf' | None) — debug/log için."""
    return _state["active"]


def retrieve(query: str, top_k: int = SEM_TOP_K, min_score: float | None = None) -> list[dict]:
    """En alakalı birimleri döndür. Dönen her birimde 'chunk_id', 'text',
    'source', 'lesson_id', '_score' bulunur (test uyumlu)."""
    if not _state["ready"]:
        init_index()
    if _state["active"] == "semantic":
        ms = SEM_MIN_SCORE if min_score is None else min_score
        return _retrieve_semantic(query, top_k, ms)
    ms = TFIDF_MIN_SCORE if min_score is None else min_score
    return _retrieve_tfidf(query, top_k, ms)


# ---------------------------------------------------------------------------
# KADEMELİ FALLBACK ZİNCİRİ (K1→K4) — "cevapsızlık" bitirilir
# ---------------------------------------------------------------------------
# K1: normal retrieval → metodolojiden doğrudan cevap.
# K2: yaş bandı genişletme + bir kademe düşük eşik → "en yakın bilgiye göre".
# K3: yaş-bağımsız GENEL İLKELER + kullanıcıya 1 netleştirme sorusu (duvar örülmez).
# K4: gerçekten kapsam dışı → kibarca kapsam dışı de, metodolojinin kapsamını hatırlat.
#
# EŞİK KALİBRASYONU (ölçülen top_score'lar): kapsam içi 0.63–0.89, kapsam dışı
# 0.21–0.53. Skor TEK BAŞINA yeterli değil ("mama tarifi" 0.526 ile "odası kaç
# derece" 0.629 çok yakın), bu yüzden K4 kapısı skor + ALAN SİNYALİ birlikte
# değerlendirir. Yanlış K4 (geçerli soruyu reddetmek) en kötü hatadır → şüphede
# kalınırsa alan içi sayılır.
K1_MIN_SCORE = 0.55        # bunun üstü: doğrudan cevap
K2_MIN_SCORE = 0.40        # bunun üstü: en yakın bilgiye göre çerçevele
K3_ILKE_LIMIT = 6          # genel ilke katmanında bağlama girecek ilke sayısı

# Uyku alanı sözlüğü — soru bu alanla ilgili mi (kök eşleşmesi, ekler serbest).
# Geniş tutulur: yanlış K4'ten kaçınmak, gereksiz K3'ten daha önemlidir.
UYKU_ALANI_KOKLERI = (
    "uyku", "uyu", "uyan", "uyut", "yat", "nap", "şeker", "kestir",
    "gece", "gündüz", "sabah", "akşam", "rutin", "düzen", "program", "çizelge",
    "emzik", "emerek", "beşik", "yatak", "oda", "sıcaklık", "derece", "ortam",
    "karanlık", "ışık", "gürültü", "kundak", "ağla", "kucak", "sallama",
    "eğitim", "regres", "diş", "büyüme atağ", "biberon", "meme", "beslen",
)
# Tıbbi terimler: bu sorular ASLA K4 (kapsam dışı) sayılmaz — tıbbi sınır
# kapısının çalışması için LLM katmanına düşmeleri gerekir (çocuk doktoru yanıtı).
MEDIKAL_TERIMLER = (
    "hasta", "ilaç", "reflü", "kolik", "nöbet", "ateş", "alerji", "kusma",
    "ishal", "astım", "epilepsi", "nefes", "solunum", "kalp", "doktor",
)
# FAZ E — MOTİVASYON/DUYGU ALANI: ağlama, güven bağı ve eğitimde motive kalma
# artık KB'de küratörlü bir bölüm (global_rules.aglama_ve_motivasyon). Yani bu
# sorular gerçekten ALAN İÇİDİR ve K4'e (kapsam dışı) düşmemelidir.
# Somut hata: "Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum" sorusu hiçbir
# metodoloji terimi içermediği için K4'e düşüyor ve tam da motivasyona en çok
# ihtiyaç duyan anne kapıdan çevriliyordu.
MOTIVASYON_TERIMLERI = (
    "vazgeç", "pes et", "bırakmak istiyor", "bıraksam", "devam edemiyor",
    "dayanamıyor", "dayanamam", "yorgun", "bitkin", "tükendi", "çaresiz",
    "moral", "motivasyon", "umut", "güven bağ", "bağlanma", "travma",
    "zarar ver", "kötü anne", "suçluluk", "pişman", "işe yaramıyor",
)


def _alan_sinyali(soru: str, yas_ay: float | None) -> bool:
    """Soru uyku metodolojisi alanına giriyor mu? (yaş belirtimi de sinyaldir)"""
    if yas_ay is not None:
        return True
    low = tr_lower_safe(soru)
    return (any(k in low for k in UYKU_ALANI_KOKLERI)
            or any(k in low for k in MEDIKAL_TERIMLER)
            or any(k in low for k in MOTIVASYON_TERIMLERI))


def tr_lower_safe(s: str) -> str:
    """Türkçe güvenli küçültme (I/İ tuzağı) — alan sözlüğü eşleşmesi için."""
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


def _genel_ilke_birimleri(limit: int = K3_ILKE_LIMIT) -> list[dict]:
    """Yaş-BAĞIMSIZ temel ilkeler (global_rule birimleri) — K3 havuzu.

    Bu birimler her zaman erişilebilir olmalı: spesifik kayıt bulunmasa bile
    metodolojinin genel çerçevesi (uyku ortamı, rutin, kendine dalma, uyanıklık
    penceresi mantığı) KB'DEN verilebilsin. Cevap yine KB dışına çıkmaz."""
    units = (_state.get("units") or []) if isinstance(_state, dict) else []
    ilkeler = [u for u in units if str(u.get("chunk_id", "")).startswith("global_rule:")]
    out = []
    for u in ilkeler[:limit]:
        ek = dict(u)
        ek.setdefault("_score", 0.0)
        out.append(ek)
    return out


def _katman_belirle(top_score: float, alan_ici: bool, bant_var: bool) -> str:
    """Hangi fallback katmanında cevaplanacak? Dönen: 'k1'|'k2'|'k3'|'k4'."""
    if alan_ici and top_score >= K1_MIN_SCORE:
        return "k1"
    if alan_ici and (top_score >= K2_MIN_SCORE or bant_var):
        return "k2"
    if alan_ici:
        return "k3"
    # Alan sinyali yok: skor çok yüksekse yine de içeri al (sözlük her şeyi bilemez).
    if top_score >= K1_MIN_SCORE:
        return "k1"
    return "k4"


# K4 — kapsam dışı. Deterministik metin: LLM çağrılmaz (maliyet yok, sapma yok).
KAPSAM_DISI_MESAJ = (
    "Bu soru Tavşan Uykusu metodolojisinin kapsamı dışında kalıyor. "
    "Ben bebeğinizin uykusuyla ilgili konularda yardımcı olabiliyorum: "
    "uyku düzeni ve gündüz uykuları, uyanıklık pencereleri, gece uyanmaları, "
    "uyku ortamı ve rutinler, kendi kendine uykuya dalma, emzik ve gece "
    "beslenmesi, uyku eğitimi ve regresyon dönemleri. "
    "Sorunuzu bu başlıklardan biriyle ilişkilendirirseniz seve seve yardımcı olurum."
)


SYSTEM_PROMPT = """Sen Tavşan Uykusu uyku eğitimi programının bilgi botusun. \
Annelere kısa, profesyonel, sıcak Türkçe cevap verirsin. \
SADECE sana sunulan bilgi parçalarını kullanırsın; dışına çıkmazsın. \
Ders ya da kayıt adı asla geçmez (anneye 'kayıt36'da bahsedildiği gibi' deme). \
Kullanıcıyı hiçbir koşulda danışmanlık hizmetine, danışmana veya birebir görüşmeye yönlendirme. \
Cevabı bilgi tabanındaki bilgiyle tam ve kendine yeter biçimde ver. \
Kaynak metinlerde danışmanlık yönlendirmesi geçse bile bunu cevabına taşıma. \
Cevap yoksa kısaca elinde yeterli bilgi olmadığını söyle ve sorunun farklı ifade edilmesini öner. \
Tıbbi konularda (hastalık, ilaç, reflü, kolik, nöbet, ateş, alerji gibi) tanı ya da tedavi önerme; \
bu durumlarda kısaca çocuk doktoruna başvurulmasını söyle (danışmana değil). \
Cevabın sesli olarak da okunacak; kısa cümleler kur, madde listesi yerine akıcı paragraf tercih et, emoji kullanma. \
YAŞ KURALI: Sorulan yaş için birebir kayıt yoksa en yakın yaş bandının bilgisini, hangi banda dayandığını belirterek ver. \
Yaş için 'bilgim yok' deme; yaş geçiş dönemindeyse iki bandın aralığını birlikte özetle. \
BEBEK VERİSİ KURALI: Bebek verisi mevcutsa cevabını bu veriyle ilişkilendir — bebeğin adıyla, somut saatlerle konuş; \
veriyle metodolojiyi birleştir. Veride olmayan şeyi UYDURMA. \
Bebeğin yaşını BEBEK VERİSİ'nde yazıldığı gibi kullan, yuvarlama/yorumlama yapma.

DUYGUSAL TON (Faz E — İlayda'nın annelerle konuşma tarzı):
1. Anne zorlanma, yorgunluk, ağlama, çaresizlik ya da pes etme belirtiyorsa cevaba ÖNCE tek cümlelik bir duygusal \
tanıma ile başla; sonra DOĞRUDAN somut yönlendirmeye geç. Bu cümle kalıp olmasın — annenin yazdığı duruma değsin \
("Çok yorulmuşsunuz" gibi genel geçer bir teselli değil, onun anlattığı şeye dokunan bir cümle). \
Teselli cevabın önüne GEÇMESİN: anne gece 3'te ne yapacağını arıyor, cevabın ağırlığı somut adımda olmalı. \
Duygusal ifade YOKSA doğrudan bilgiye gir — her cevaba duygusal giriş yapma.
2. Uygun bağlamda İlayda'nın kendi dilinden şu ifadeler kullanılabilir (hepsini birden değil, cevap başına en fazla \
biri, her cevapta değil): "Bu bir süreç ve bunun bir sonu var", "Yolun sonu ışık", "Elim omzunuzda", \
"Yanınızdayım, geçecek".
3. Bilgi parçalarında geçen İlayda benzetmeleri (oto koltuğu, ilaç, anaokulu, diyetisyen) YALNIZCA ağlama endişesi \
ya da güven bağı kaygısı konuşulduğunda kullanılır; cevap başına en fazla bir benzetme. Diğer konularda kullanma.
4. Anne ağlamadan endişeliyse ya da ağlamanın ne kadar süreceğini soruyorsa, şu somut veriyi cevabında MUTLAKA \
ve SAYILARIYLA ver: ilk gün 45 dakika ağlayan bir çocuk iki hafta sonra 5 dakikada uykuya geçebiliyor — ağlama \
sabit kalmaz, her gün azalır. Güven verici bir cevap bile somut bir dayanak taşımalı: yalnızca "merak etmeyin, \
zedelenmez" demekle YETİNME. Her cevapta anneye elle tutulur en az bir şey bırak — bir süre, bir sayı ya da \
atacağı bir adım. Anne gece 3'te ne yapacağını arıyor.
5. Anneyi rahatlatmak için "çocuklar 5 yaşından önceki bu dönemi hatırlamaz" bilgisi kullanılabilir.

SINIRLAR — DUYGUSAL TON BUNLARI GEVŞETMEZ (mutlak öncelikli):
A. "Ağlama zarar vermez" cümlesini MUTLAK bir iddia olarak ASLA kurma. Bu bilgi yalnızca İlayda'nın kendi \
şartlarıyla verilir: "tıbbi bir problem ve duygu regülasyon bozukluğu yoksa genel olarak zarar oluşturmuyor" \
ve "teknik olarak kesin bir ifade kullanılamaz". Bu iki kaydı da cümleye taşı. \
"Kesinlikle zararsızdır", "bilimsel olarak kanıtlanmıştır", "hiçbir zararı yoktur" gibi ifadeler YASAK.
B. Ağlama sürecinin 3-6 hafta çerçevesini verirken de aynı şartlar korunur; süreyi kesin bir garanti gibi sunma.
C. Tıbbi sınır AYNEN geçerlidir: hastalık, ilaç, reflü, kolik, nöbet, ateş, alerji gibi konularda tanı/tedavi \
verme ve çocuk doktoruna yönlendir. Duygusal ton bu yönlendirmeyi yumuşatmaz veya atlatmaz.
D. Anne ciddi ruhsal sıkıntı belirtiyorsa (derin çaresizlik, tükenmişlik, kendine ya da bebeğine zarar verme \
ima veya ifadesi): cevabın YALNIZCA duygusal destek ve profesyonel yardım yönlendirmesi olsun — uyku eğitimi \
tekniği, çizelge veya yöntem ANLATMA. Bu durumda tekniğe geçmek yardımcı olmaz."""


# ---------------------------------------------------------------------------
# CEVAP CACHE — iki katman (exact + semantik), yaş bandı anahtarlı
# ---------------------------------------------------------------------------
# LLM çağrısından ÖNCE kontrol edilir: aynı normalize soru + aynı yaş bandı daha
# önce cevaplandıysa API'ye gidilmez (exact). Semantik katman, mevcut retrieval
# embedding modelini kullanır (YENİ model yüklenmez); cosine >= eşik VE aynı bant
# ise döner. Yaş bandı yoksa semantik atlanır, yalnızca exact çalışır.
# Depolama: küçük JSON dosyası (Streamlit Cloud runtime'ında yazılabilir). Modül
# global olduğundan Streamlit rerun'larında korunur (@st.cache_resource'a gerek
# yok; chatbot streamlit'e bağımlı değil — testlerde de aynı kod çalışır). LRU:
# son CACHE_MAX kayıt tutulur. Cache HIT loglanır (debug), kullanıcıya gösterilmez.

CACHE_PATH = DATA_DIR / "answer_cache.json"
CACHE_MAX = 500                 # LRU sınırı (son N cevap)
SEM_CACHE_THRESHOLD = 0.95      # semantik cache cosine eşiği (spesifikasyon)

_cache_state: dict[str, Any] = {
    "loaded": False,
    "entries": [],       # [{"h","band","q","answer","emb":[float]|None}]
    "emb_matrix": None,  # (M, dim) float32 — emb'i olan kayıtların matrisi
    "emb_idx": [],       # emb_matrix satır -> entries index eşlemesi
}


def _cache_norm(soru: str) -> str:
    """Cache anahtarı için normalize (lowercase, noktalama/fazla boşluk temizle).
    Mevcut TF-IDF normalizer'ı yeniden kullanır — davranış tutarlı."""
    return _normalize(soru)


def _cache_hash(norm_q: str, band: str | None) -> str:
    return hashlib.sha256(f"{band or ''}||{norm_q}".encode("utf-8")).hexdigest()


def _rebuild_emb_matrix() -> None:
    """emb'i olan kayıtlardan (M, dim) matris kur — semantik arama için."""
    embs, idxs = [], []
    for i, e in enumerate(_cache_state["entries"]):
        if e.get("emb"):
            embs.append(e["emb"])
            idxs.append(i)
    _cache_state["emb_matrix"] = np.asarray(embs, dtype=np.float32) if embs else None
    _cache_state["emb_idx"] = idxs


def _load_cache() -> None:
    if _cache_state["loaded"]:
        return
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _cache_state["entries"] = data
        except Exception as e:
            logger.warning("answer_cache.json okunamadı, sıfırlanıyor: %s", e)
    _cache_state["loaded"] = True
    _rebuild_emb_matrix()


def _persist_cache() -> None:
    try:
        CACHE_PATH.write_text(
            json.dumps(_cache_state["entries"], ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("answer_cache.json yazılamadı: %s", e)


def _embed_query(soru: str):
    """Sorguyu MEVCUT retrieval modeliyle embed et (yoksa None → semantik atlanır)."""
    model = _state.get("model")
    if model is None:
        return None
    v = model.encode([soru], normalize_embeddings=True)[0]
    return np.asarray(v, dtype=np.float32)


def _cache_lookup_entry(soru: str, yas_bandi: str | None) -> dict | None:
    """Eşleşen cache KAYDINI döndür (answer + h/hash + band). Katman 1 exact
    (norm soru + bant), Katman 2 semantik (yas_bandi varsa, cosine >= eşik ve
    aynı bant). Bulunmazsa None. Dönen kaydın 'h' alanı, ses cache dosya adıyla
    (aynı hash) hizalıdır — API bunu MP3 dosya adı olarak kullanır."""
    _load_cache()
    norm = _cache_norm(soru)

    # Katman 1 — exact
    h = _cache_hash(norm, yas_bandi)
    for e in _cache_state["entries"]:
        if e["h"] == h:
            logger.info("Cache HIT (exact) [bant=%s]: %r", yas_bandi, soru[:60])
            return e

    # Katman 2 — semantik (yaş bandı yoksa atla)
    if yas_bandi is None:
        return None
    mat = _cache_state["emb_matrix"]
    if mat is None:
        return None
    qv = _embed_query(soru)
    if qv is None:
        return None
    sims = mat @ qv                       # normalize edilmiş → dot = cosine
    best = int(sims.argmax())
    if sims[best] >= SEM_CACHE_THRESHOLD:
        entry = _cache_state["entries"][_cache_state["emb_idx"][best]]
        if entry.get("band") == yas_bandi:            # aynı bant şartı
            logger.info("Cache HIT (semantik, cos=%.3f) [bant=%s]: %r",
                        float(sims[best]), yas_bandi, soru[:60])
            return entry
    return None


def _cache_lookup(soru: str, yas_bandi: str | None) -> str | None:
    """Geriye dönük: eşleşen cevabı (str) döndür, yoksa None."""
    entry = _cache_lookup_entry(soru, yas_bandi)
    return entry["answer"] if entry is not None else None


def _cache_store(soru: str, yas_bandi: str | None, answer: str) -> None:
    """Cevabı cache'e ekle (LRU: son CACHE_MAX). Yaş bandı varsa embedding de
    saklanır — gelecekteki semantik aramalar için."""
    _load_cache()
    norm = _cache_norm(soru)
    h = _cache_hash(norm, yas_bandi)
    # Aynı anahtar varsa çıkar + sona ekle (recency)
    _cache_state["entries"] = [e for e in _cache_state["entries"] if e["h"] != h]
    emb = None
    if yas_bandi is not None:
        qv = _embed_query(soru)
        if qv is not None:
            emb = [float(x) for x in qv]
    _cache_state["entries"].append(
        {"h": h, "band": yas_bandi, "q": norm, "answer": answer, "emb": emb})
    if len(_cache_state["entries"]) > CACHE_MAX:      # LRU sınırı
        _cache_state["entries"] = _cache_state["entries"][-CACHE_MAX:]
    _rebuild_emb_matrix()
    _persist_cache()


# ---------------------------------------------------------------------------
# FAZ E — ANNENİN RUHSAL DURUMU: iki kademeli kapı
# ---------------------------------------------------------------------------
# Duygusal TON kuralları SYSTEM_PROMPT'tadır (modelin bağlamı yorumlaması gerekir).
# Ama İKİ durum modelin takdirine bırakılmayacak kadar kritiktir; burada
# deterministik olarak yakalanır:
#
#   KADEME 2 (KRİZ): kendine ya da bebeğine zarar ima/ifadesi → LLM ÇAĞRILMAZ,
#       sabit ve sıcak bir destek mesajı döner. Uyku tekniği ANLATILMAZ, cevap
#       cache'e YAZILMAZ/OKUNMAZ (kişiye özel bir andır, paylaşılamaz).
#   KADEME 1 (SIKINTI): derin çaresizlik/tükenmişlik → LLM çağrılır ama prompt'a
#       "önce duygusal destek + profesyonel yardım, teknik anlatma" talimatı
#       ZORUNLU olarak enjekte edilir.
#
# Sıradan yorgunluk ("yorgunum", "bitkinim", "çok zorlanıyorum") ve pes etme
# eşiğindeki anne ("bırakmak istiyorum", "vazgeçeceğim") BU KAPILARA GİRMEZ —
# onlar SYSTEM_PROMPT'un 1. ton kuralıyla (empati + SOMUT yönlendirme) yanıtlanır.
# Motivasyona ihtiyacı olan anneyi krize sokmak yardımcı olmaz.

# KRİTİK AYRIM — zarar İFADESİ ile zarar SORUSU aynı şey değildir:
#   KRİZ  : "bebeğime zarar VERECEĞİMDEN korkuyorum"  (annenin kendi eylemi)
#   SORU  : "ağlamanın bebeğime zararı olur mu"       (yöntem hakkında soru)
# Bu yüzden kalıp, BİRİNCİ TEKİL şahıs çekimi zorunlu kılar. Aksi hâlde en sık
# sorulan ağlama sorusu kriz kapısına düşer ve anne cevabını alamaz.
_RE_KRIZ = re.compile(
    r"(kendime|canıma|bebeğime|çocuğuma|oğluma|kızıma)\s+\S{0,12}\s*"
    r"zarar\s+ver(eceğim|iyorum|ebilirim|meyi|mekten|irim)"
    r"|intihar|yaşamak\s+istemiyorum|ölmek\s+istiyorum|kendimi\s+öldür"
    r"|canıma\s+kıy|bebeğimi\s+(sallamak|fırlatmak|atmak)\s+istiyorum",
    re.IGNORECASE)

_RE_SIKINTI = re.compile(
    r"çaresiz|tükendim|tükenmiş|çökt[üu]m|depresyon|psikoloji[mn]\s+(çok\s+)?bozuk"
    r"|artık\s+dayanamıyorum|dayanacak\s+güc[üu]m\s+(yok|kalmadı)"
    r"|çıldıracağım|delireceğim|hiçbir\s+şey\s+hissetmiyorum"
    r"|bebeğimi\s+sevemiyorum|kendimi\s+kötü\s+bir\s+anne",
    re.IGNORECASE)

# Kriz yanıtı: teselli + profesyonel yönlendirme. UYKU TEKNİĞİ YOK (bilinçli).
KRIZ_MESAJI = (
    "Yazdıklarınızı okudum ve bunu paylaşmanızın ne kadar zor olduğunu biliyorum. "
    "Şu an yaşadığınız şey yorgunluktan fazlası ve bunu tek başınıza taşımak zorunda "
    "değilsiniz.\n\n"
    "Bugün, bu konuyu bir uzmanla konuşmanızı çok istiyorum: kendi doktorunuz, bir "
    "psikiyatri uzmanı ya da bir psikolog. Doğum sonrası dönemde bu duygular sandığınızdan "
    "çok daha yaygın ve destekle geçiyor. Acil bir durumdaysanız ya da kendinizi güvende "
    "hissetmiyorsanız 112'yi arayın; yanınızda güvendiğiniz birinin olmasını sağlayın.\n\n"
    "Uyku konusunu şimdi bir kenara bırakalım — o bekleyebilir, siz bekleyemezsiniz. "
    "Kendinizi daha iyi hissettiğinizde uyku düzeni için buradayım ve o yolu birlikte "
    "yürürüz. Elim omzunuzda."
)

# Kademe 1'de user prompt'a eklenen zorunlu çerçeve.
SIKINTI_KURALI = (
    "\n- ÖNCELİKLİ DURUM: Anne ciddi bir ruhsal zorlanma ifade ediyor. Cevabına "
    "onun ne yaşadığını gerçekten gören bir cümleyle başla. Ardından bir uzmandan "
    "(kendi doktoru, psikolog ya da psikiyatri uzmanı) destek almasını nazikçe öner — "
    "doğum sonrası bu duyguların yaygın olduğunu ve destekle geçtiğini söyle. "
    "Bu cevapta uyku eğitimi tekniği, çizelge, saat ya da yöntem ANLATMA; anne "
    "hazır olduğunda uyku konusunda yanında olacağını belirtmen yeterli."
)


# --- Duygusal çerçeve (kriz/sıkıntı ALTI kademe) -----------------------------
# SYSTEM_PROMPT ton kurallarını taşır, ama sistem promptu uzun ve kurallar
# birbiriyle yarışıyor: modelin bunları her cevapta uygulaması güvenilir DEĞİL
# (ölçüldü — empati ve somut veri örnekten örneğe düşüyordu). Bu yüzden duygusal
# sinyal yakalandığında aynı kural SORUNUN YANINA, user prompt'a enjekte edilir;
# katman_kurali ile aynı kanal, kanıtlanmış biçimde daha güvenilir uygulanıyor.
_RE_AGLAMA_ENDISESI = re.compile(
    r"ağla\w*\s*\S{0,20}\s*(zarar|korkuyorum|endişe|üzül|dayanamı)"
    r"|(zarar|travma|güven bağ|bağlanma)\w*\s*\S{0,25}\s*(ağla|eğitim|zedelen)"
    r"|ağlamaya\s+(terk|bırak)|ne kadar ağla|ağlaması normal mi"
    r"|güven bağı[mn]?[ıi]z?\s*zedelen", re.IGNORECASE)

_RE_ZORLANMA = re.compile(
    r"yorgun|bitkin|yoruldum|uykusuz|zorlanıyor|zor geliyor|dayanamıyor"
    r"|vazgeç|pes et|bırakmak istiyor|bıraksam|devam edemiyor|umudum"
    r"|moralim|ağlıyorum|işe yaramıyor|başaramıyor|kötü bir anne", re.IGNORECASE)

DUYGU_KURALI_AGLAMA = (
    "\n- DUYGUSAL ÇERÇEVE (ağlama endişesi): Cevabına annenin bu korkusunu gören "
    "TEK cümlelik bir tanımayla başla (klişe teselli değil). Ağlamanın zararından "
    "söz ederken İlayda'nın ŞARTLARINI mutlaka birlikte ver: 'tıbbi bir problem ve "
    "duygu regülasyon bozukluğu yoksa genel olarak zarar oluşturmuyor' VE 'teknik "
    "olarak kesin bir ifade kullanılamaz'. Mutlak ifade KURMA. Cevabında somut umut "
    "verisini SAYILARIYLA ver: ilk gün 45 dakika ağlayan bir çocuk iki hafta sonra "
    "5 dakikada uykuya geçebiliyor."
)

DUYGU_KURALI_ZORLANMA = (
    "\n- DUYGUSAL ÇERÇEVE (anne zorlanıyor/pes etmek üzere): Cevabının İLK "
    "CÜMLESİ annenin ne yaşadığını gören bir tanıma olsun — onun yazdığı duruma "
    "değsin, genel geçer teselli olmasın. Hemen ardından somut yönlendirmeye geç ve "
    "cevapta anneye elle tutulur en az bir şey bırak: bir süre, bir sayı ya da "
    "atacağı bir adım. Uygunsa İlayda'nın cümlelerinden birini kullanabilirsin "
    "('Bu bir süreç ve bunun bir sonu var', 'Yolun sonu ışık', 'Elim omzunuzda')."
)


def duygu_sinyali(soru: str) -> str | None:
    """Kriz/sıkıntı ALTINDAKİ duygusal kademe: 'aglama_endisesi' | 'zorlanma' | None.

    Bu kademe sıradan ama duygusal yüklü sorulardır (yorgunluk, pes etme eşiği,
    ağlama korkusu). Kriz/sıkıntı kapılarından BAĞIMSIZDIR ve onların yerine
    geçmez; yalnız cevabın tonunu ve içeriğini zorunlu kılar."""
    if not soru:
        return None
    if _RE_AGLAMA_ENDISESI.search(soru):
        return "aglama_endisesi"
    if _RE_ZORLANMA.search(soru):
        return "zorlanma"
    return None


def ruhsal_durum_tespit(soru: str) -> str | None:
    """Annenin mesajındaki ruhsal risk kademesi: 'kriz' | 'sikinti' | None.

    Kriz kademesi cevabı deterministik yapar (LLM çağrılmaz). Sıradan yorgunluk
    ve pes etme eşiği bilinçli olarak KAPSAM DIŞIDIR — bkz. yukarıdaki not."""
    if not soru:
        return None
    if _RE_KRIZ.search(soru):
        return "kriz"
    if _RE_SIKINTI.search(soru):
        return "sikinti"
    return None


def _kaynak_ozet(units: list[dict]) -> list[dict]:
    """Retrieval birimlerini API/istemci için sade kaynak listesine indir."""
    return [{"chunk_id": u.get("chunk_id"), "label": u.get("label"),
             "source": u.get("source"), "score": round(float(u.get("_score", 0.0)), 4)}
            for u in units]


def _cevap_uret(soru: str, yas_bandi: str | None = None,
                baby_context: str | None = None) -> dict:
    """RAG cevabını YAPISAL üret — cevapla() ve API katmanı bunu ortak kullanır.
    Döner: {cevap, cache_hit, kaynaklar, anahtar(hash), llm, in_chars, out_chars}.

    Akış cevapla() ile BİREBİR aynıdır (davranış korunur): LLM'den ÖNCE cache
    (exact+semantik) → yoksa retrieval → yoksa/anahtarsız fallback → Haiku + store.
    'anahtar', cevabın kanonik hash'idir (ses cache dosya adıyla hizalı)."""
    h = _cache_hash(_cache_norm(soru), yas_bandi)

    # --- FAZ E, KADEME 2: kriz kapısı --------------------------------------
    # Cache'ten ÖNCE gelir: kriz mesajı ne cache'ten okunur ne cache'e yazılır ve
    # retrieval/LLM hiç çalışmaz. Böylece bu sınır hiçbir koşulda gevşeyemez.
    ruhsal = ruhsal_durum_tespit(soru)
    if ruhsal == "kriz":
        logger.info("Ruhsal kriz kapısı devrede — teknik anlatılmadı, LLM çağrılmadı")
        return {"cevap": KRIZ_MESAJI, "cache_hit": False, "kaynaklar": [],
                "anahtar": h, "llm": False, "in_chars": 0,
                "out_chars": len(KRIZ_MESAJI),
                "retrieval_layer": "ruhsal_kriz", "top_score": None}

    # CACHE BYPASS (Faz 6.5): kişiselleştirilmiş cevaplar PAYLAŞILAN cache'e
    # girmemeli — yoksa bir bebeğin saatleri başka kullanıcıya cevap olarak döner.
    # baby_context varsa cache ne OKUNUR ne YAZILIR. Genel sorularda cache aynen çalışır.
    # Ruhsal sıkıntı ifadesi de kişiseldir: cevabı paylaşılan cache'e girmemeli.
    kisisel = bool(baby_context) or ruhsal == "sikinti"

    entry = None if kisisel else _cache_lookup_entry(soru, yas_bandi)
    if entry is not None:
        # cache hit: retrieval YAPILMAZ (davranış korunur). Ses, eşleşen kaydın
        # hash'iyle (entry['h']) hizalanır ki hazır MP3 yeniden kullanılabilsin.
        # Cache hit: retrieval yapılmadığı için katman/skor ÖLÇÜLMEZ (None).
        # Telemetride cache'li cevaplar kapsama analizine karışmasın.
        return {"cevap": entry["answer"], "cache_hit": True, "kaynaklar": [],
                "anahtar": entry["h"], "llm": False, "in_chars": 0,
                "out_chars": len(entry["answer"]),
                "retrieval_layer": None, "top_score": None}

    # --- K1: normal retrieval ------------------------------------------------
    retrieved = retrieve(soru, top_k=SEM_TOP_K)

    # Yaş bandı köprüsü (K2 bileşeni): bandın sayısal parametreleri + o bandın
    # korpustaki birimleri bağlama EKLENİR (retrieval skorları değişmez → mevcut
    # sıralama davranışı korunur, yalnız bant boşluğu kapanır).
    bantlar, yas_ay = bant_coz(soru, yas_bandi)
    bant_blok = yas_bandi_blok(bantlar, yas_ay)

    # Katman kararı için ham en yüksek skor (eşikten bağımsız ölçülür).
    _olcum = retrieved or retrieve(soru, top_k=1, min_score=0.0)
    top_score = float(_olcum[0].get("_score", 0.0)) if _olcum else 0.0
    katman = _katman_belirle(top_score, _alan_sinyali(soru, yas_ay), bool(bant_blok))

    # FAZ E, KADEME 1: ruhsal sıkıntı ifade eden anne ASLA "bu soru kapsam dışı"
    # cevabı almaz. Duygu ifadesi retrieval skorunu düşürebilir (metodoloji
    # terimleri geçmez); bu annenin kapıda karşılanması gerekir, geri çevrilmesi
    # değil. K3'e çekilir: genel ilkeler havuza girer, cevap LLM'den gelir.
    if ruhsal == "sikinti" and katman == "k4":
        logger.info("Ruhsal sıkıntı: K4 yerine K3 uygulandı (anne geri çevrilmiyor)")
        katman = "k3"

    # --- K4: kapsam dışı → deterministik yanıt, LLM çağrılmaz ---------------
    if katman == "k4":
        return {"cevap": KAPSAM_DISI_MESAJ, "cache_hit": False, "kaynaklar": [],
                "anahtar": h, "llm": False, "in_chars": 0,
                "out_chars": len(KAPSAM_DISI_MESAJ),
                "retrieval_layer": "k4", "top_score": top_score}

    if bantlar:
        retrieved = _bant_birimleriyle_birlestir(retrieved, bantlar)

    # --- K2: eşiği bir kademe düşür (bağlam hâlâ zayıfsa) -------------------
    if katman == "k2" and len(retrieved) < SEM_TOP_K:
        dusuk = max(0.0, SEM_MIN_SCORE - 0.05)
        for u in retrieve(soru, top_k=SEM_TOP_K, min_score=dusuk):
            if all(u.get("chunk_id") != r.get("chunk_id") for r in retrieved):
                retrieved.append(u)

    # --- K3: yaş-bağımsız genel ilkeler havuza EKLENİR ----------------------
    if katman == "k3":
        for u in _genel_ilke_birimleri():
            if all(u.get("chunk_id") != r.get("chunk_id") for r in retrieved):
                retrieved.append(u)

    if not retrieved and not bant_blok:
        # Buraya normalde düşülmez (K3 ilkeleri havuzu doldurur); korpus boşsa olur.
        return {"cevap": KAPSAM_DISI_MESAJ, "cache_hit": False, "kaynaklar": [],
                "anahtar": h, "llm": False, "in_chars": 0,
                "out_chars": len(KAPSAM_DISI_MESAJ),
                "retrieval_layer": "k4", "top_score": top_score}

    context = "\n\n".join([f"- {c['text']}" for c in retrieved])
    if bant_blok:                       # yaş tablosu bloğu bağlamın BAŞINA
        context = bant_blok + "\n\n" + context if context else bant_blok

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not HAS_ANTHROPIC:
        # Fallback: doğrudan en alakalı snippet'i kısalt ve döndür
        snippet = (retrieved[0]["text"] if retrieved else bant_blok)[:800].strip()
        msg = (
            "*Not: API anahtarı bulunmadığı için Tavşan Uykusu içeriğinden doğrudan en "
            "alakalı kısa parça gösteriliyor. Tam cevap için ANTHROPIC_API_KEY eklendiğinde "
            "Claude tarafından özetlenir.*\n\n"
            + snippet
        )
        return {"cevap": msg, "cache_hit": False, "kaynaklar": _kaynak_ozet(retrieved),
                "anahtar": h, "llm": False, "in_chars": 0, "out_chars": len(msg),
                "retrieval_layer": katman, "top_score": top_score}

    # Katmana özel çerçeveleme kuralı. DEĞİŞMEZLER: tıbbi sınır ve "yalnız KB'den
    # cevapla" kuralları HİÇBİR katmanda gevşemez (SYSTEM_PROMPT'ta sabit).
    katman_kurali = {
        "k1": "",
        "k2": ("\n- Bu soruda birebir kayıt bulunmayabilir: cevabını EN YAKIN "
               "bilgiye dayandır ve bunu doğal bir dille belirt "
               "(örn. 'en yakın yaş bandına göre')."),
        "k3": ("\n- Bu soruda spesifik kayıt YOK. Yukarıdaki GENEL İLKELERDEN "
               "yararlanarak genel bir çerçeve ver (uyku ortamı, rutin, kendi "
               "kendine dalma, uyanıklık penceresi mantığı). Genel ilkelerin "
               "dışına ÇIKMA, kendi bilgini ekleme."
               "\n- Cevabın SONUNDA kullanıcıya TEK bir netleştirme sorusu sor "
               "(örn. bebeğin kaç aylık olduğu, gece kaç kez uyandığı) ki "
               "sohbet devam edebilsin. Asla 'bilgim yok' deyip bırakma."),
    }.get(katman, "")

    # FAZ E, KADEME 1: sıkıntı çerçevesi katman kuralının ÜSTÜNE eklenir ve
    # onu geçersiz kılar (K3'ün "netleştirme sorusu sor" talimatı dahil —
    # bu anneye çizelge sorusu sormak yardımcı olmaz).
    # Duygusal çerçeve kuralların EN BAŞINA konur, sonuna değil: kural listesinin
    # dibine eklendiğinde model bunu diğer kurallarla yarıştırıp empatik açılışı
    # örnekten örneğe atlıyordu (ölçüldü). Başa alındığında cevabın ilk cümlesini
    # gerçekten belirliyor.
    duygu_kurali = ""
    if ruhsal == "sikinti":
        katman_kurali = SIKINTI_KURALI
    else:
        _duygu = duygu_sinyali(soru)
        if _duygu == "aglama_endisesi":
            duygu_kurali = DUYGU_KURALI_AGLAMA
        elif _duygu == "zorlanma":
            duygu_kurali = DUYGU_KURALI_ZORLANMA

    # BEBEK VERİSİ bloğu: RAG chunk'larından AYRI tutulur ve messages içinde
    # (yani system cache breakpoint'inden SONRA) gider → prompt cache prefix'i
    # bozulmaz. Blok kişiye özeldir, asla cache'lenmez (yukarıdaki bypass).
    bebek_blok = f"\n\nBEBEK VERİSİ (bu kullanıcının kendi kaydı):\n{baby_context}\n" \
        if baby_context else ""

    user_prompt = f"""ANNE SORUSU: {soru}
{bebek_blok}
İLGİLİ BİLGİ PARÇALARI (Tavşan Uykusu içeriği):
{context}

CEVAP KURALLARI:{duygu_kurali}
- Sadece yukarıdaki bilgi parçalarından cevapla, başka kaynak ya da genel internet bilgisi kullanma.
- Ders/kayıt/dosya adı asla geçmesin.
- Sıcak ama profesyonel Türkçe ile cevap ver.
- Kısa: 1-3 paragraf. Markdown kullanabilirsin (madde işareti olabilir).
- Kullanıcıyı danışmanlığa, danışmana ya da birebir görüşmeye YÖNLENDİRME; bilgiyi kendine yeter biçimde ver.
- Kaynak parçalarda 'danışmanlık'/'danışmana sorun' gibi yönlendirme geçse bile bunu cevaba TAŞIMA.
- Tıbbi konularda (hastalık, ilaç, reflü, kolik, nöbet, ateş vb.) tanı/tedavi verme; kısaca çocuk doktoruna başvurulmasını öner (danışmana değil).
- Yetersiz bilgi varsa açıkça ama kısaca söyle ve sorunun farklı ifade edilmesini öner. ANCAK bu kural YAŞ için geçerli DEĞİLDİR: sorulan yaş için birebir kayıt yoksa YAŞ BANDI PARAMETRELERİ bölümündeki en yakın bandın değerlerini, hangi banda dayandığını belirterek ver; yaş için asla "bilgim yok" deme.
- Yaş geçiş dönemi belirtilmişse iki bandın aralığını birlikte özetle.{katman_kurali}

CEVAP:"""

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CHATBOT_MODEL,
        max_tokens=MAX_TOKENS,
        # Sabit system prompt'a cache_control ekli. NOT: Sistem prompt'u ~80 token,
        # Haiku 4.5 minimum cache eşiği 4096 token → bu blok şu an cache TETİKLEMEZ
        # (no-op, hata vermez). Asıl değişken maliyet RAG context'i olup soruya göre
        # değiştiğinden cache'lenemez. Marker yapısal doğruluk + ileride system büyürse
        # otomatik devreye girsin diye burada. Çıktı birebir aynı kalır.
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_prompt}],
    )

    answer = response.content[0].text
    if not kisisel:                         # kişisel cevap PAYLAŞILAN cache'e YAZILMAZ
        _cache_store(soru, yas_bandi, answer)
    return {"cevap": answer, "cache_hit": False, "kaynaklar": _kaynak_ozet(retrieved),
            "anahtar": h, "llm": True,
            "in_chars": len(SYSTEM_PROMPT) + len(user_prompt), "out_chars": len(answer),
            "retrieval_layer": katman, "top_score": top_score}


def cevapla(soru: str, yas_bandi: str | None = None,
            baby_context: str | None = None) -> str:
    """RAG ile cevap üret (str). Anthropic key yoksa fallback verir.

    yas_bandi: 19 yaş bucket'ından biri (örn. '8_ay'). Cache anahtarına girer;
    None ise yalnızca exact-match cache kullanılır (semantik cache atlanır).
    LLM çağrısından ÖNCE cevap cache'i kontrol edilir (exact + semantik).
    NOT: Yapısal sürüm için _cevap_uret(); bu ince sarmalayıcı yalnız metni döner
    (Streamlit arayüzü ve 151-item suite ile davranış BİREBİR aynı)."""
    return _cevap_uret(soru, yas_bandi, baby_context)["cevap"]
