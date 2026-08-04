"""
Topluluk moderasyon hattı (Faz T) — 4 katman.

K0  Yazarken, senkron, ÜCRETSİZ: küfür/hakaret sözlüğü (normalizasyon: translit,
    leetspeak, tekrar daraltma, ayraç kaçışı) + iletişim regex (URL/tel/IBAN/e-posta)
    + spam sezgileri. Yakalanırsa içerik KAYDEDİLMEZ (router 400 döner). Ayrıca
    hız limiti (60 sn'de 2. gönderi → 429).
K1  Risk skorlama, ücretsiz: tıbbi risk / ticari sözcük / yeni hesap → flagged.
    Flagged olmayan doğrudan yayınlanır.
K2  Haiku, ASENKRON: flagged içerik ANINDA yayınlanır; arka planda Haiku sınıflar.
    izin=false & güven>=0.7 → hidden. Hata/timeout → published KALIR (fail-open).
K3  Şikayet: senkron Haiku değerlendirmesi + "2 farklı kullanıcı şikayeti → oto-hide".

Eskalasyon: 3 içerik hidden → muted (24s), 5 → banned + tüm içerik hidden.

Fail-open ilkesi: moderasyon HATASI (LLM/ağ) kullanıcıyı susturmaz — içerik yayında
kalır, olay loglanır. Kesin karar insan moderatöre bırakılır.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("tavsan.moderation")

# moderation.py api/services/ altında → proje kökü .parent.parent.parent (voice.py ile aynı)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
WORDLIST_PATH = DATA_DIR / "mod_wordlist.txt"

MODEL = "claude-haiku-4-5"
HAIKU_TIMEOUT_S = 5.0
HAIKU_CONFIDENCE = 0.7        # izin=false & güven>=bu → hidden
# Hız limitleri AYRI sayaçlar (B4): "konu aç → hemen ilk cevabı yaz" akışı duvara
# çarpmasın. Konu açma spam frenlidir (uzun); cevap daha sık yazılabilir.
THREAD_RATE_S = 60           # konu açma: 60 sn'de 1
REPLY_RATE_S = 15            # cevap yazma: 15 sn'de 1
RATE_WINDOW_S = THREAD_RATE_S  # geriye uyum (eski ad)
HIDDEN_MUTE_THRESHOLD = 3    # bu kadar hidden → muted (24s)
HIDDEN_BAN_THRESHOLD = 5     # bu kadar hidden → banned
MUTE_HOURS = 24

# --- Normalizasyon -----------------------------------------------------------
_TR = str.maketrans({"ş": "s", "ı": "i", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
                     "İ": "i", "Ş": "s", "Ğ": "g", "Ü": "u", "Ö": "o", "Ç": "c"})
_LEET = str.maketrans({"@": "a", "0": "o", "1": "i", "3": "e", "4": "a",
                       "5": "s", "7": "t", "$": "s", "€": "e"})


def _basic(text: str) -> str:
    """küçült + Türkçe translit + leetspeak + ascii."""
    t = (text or "").lower().translate(_TR).translate(_LEET)
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


def _collapse(tok: str) -> str:
    """Tekrarlanan harfleri tek harfe indir (siiik → sik)."""
    return re.sub(r"(.)\1{1,}", r"\1", tok)


def _prep(text: str) -> tuple[list[str], str]:
    """Ham normalize tokenlar (çekim daraltılMAMIŞ) + ayraçsız 'squeezed' biçim.
    Çekim daraltma (collapse) çağıran tarafında token bazında yapılır ki whitelist
    daraltmadan ÖNCE değerlendirilebilsin (örn. 'sikke' → daraltınca 'sike' olur,
    whitelist ham 'sikke' üzerinden korur)."""
    base = _basic(text)
    raw_tokens = re.findall(r"[a-z0-9]+", base)
    squeezed = _collapse(re.sub(r"[^a-z0-9]", "", base))
    return raw_tokens, squeezed


# --- Sözlükler ---------------------------------------------------------------
def _load_wordlist() -> set[str]:
    try:
        return {w.strip() for w in WORDLIST_PATH.read_text(encoding="utf-8").splitlines()
                if w.strip()}
    except FileNotFoundError:
        logger.warning("mod_wordlist.txt yok — K0 küfür filtresi boş.")
        return set()


WORDSET = _load_wordlist()
# Ayraç-kaçışı (squeezed) taraması yalnız GÜÇLÜ (uzun) köklerde — kısa köklerin
# masum kelimelere (sikke vb.) alt-dize olarak denk gelmesini önler.
_STRONG = {w for w in WORDSET if len(w) >= 5}

# Bağlamsal masumlar (EBEVEYN/EMZİRME/BESLENME): asla küfür sayılmaz.
WHITELIST = {
    "sikke", "orospuotu", "amca", "amac", "amele", "gotur", "goturur", "goturdu",
    "meme", "memeli", "hiyar", "mal", "adi", "salatalik", "top", "topuk",
    "picture", "toparlanma", "malzeme", "malatya",
}
_WL_SQUEEZED = {_collapse(_basic(w)) for w in WHITELIST}

# İletişim bilgisi (reklam/dolandırıcılık yüzeyi) — engellenir.
_RE_URL = re.compile(r"(https?://|www\.)\S+|\b[a-z0-9-]+\.(com|net|org|co|io|me|xyz|"
                     r"shop|store|info|biz|online|site)\b", re.IGNORECASE)
_RE_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
_RE_IBAN = re.compile(r"\btr\s?\d{2}(\s?\d{2,4}){5,6}\b", re.IGNORECASE)
# Türk cep no: +90/0 5xx ... — ayraçlara toleranslı; ayrıca 10+ ardışık rakam.
_RE_PHONE = re.compile(r"(\+?90[\s.\-]?)?0?5\d{2}[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}"
                       r"|\b\d{10,}\b")

# --- Risk sözcükleri (K1) ----------------------------------------------------
MEDICAL_WORDS = {
    "ilac", "doz", "mg", "ml", "ates", "kusma", "havale", "nobet", "kan", "ishal",
    "alerji", "antibiyotik", "asi", "parol", "nurofen", "augmentin", "serum",
    "recete", "damla", "surup", "kanama", "morarma", "bayilma", "zehirlen",
    "ospen", "calpol", "buscopan", "majezik", "apiretal",
}
COMMERCIAL_WORDS = {
    "satiyorum", "satilik", "ucret", "indirim", "kampanya", "dm", "siparis",
    "whatsapp", "wp", "fiyat", "kargo", "stok", "link", "tikla", "kazan",
    "para", "iban", "havale", "odeme", "ucretli", "reklam", "sponsor",
}
NEW_ACCOUNT_POSTS = 3        # post_count < bu → yeni hesap (flagged)


# --- K0: içerik filtresi -----------------------------------------------------
def check_content(text: str) -> str | None:
    """K0 içerik kapısı. Engel sebebi (str) döner, temizse None. İçerik KAYDEDİLMEZ.
    Dönen sebepler: 'hakaret' | 'iletisim_bilgisi' | 'spam'."""
    if not text or not text.strip():
        return "bos"

    # 1) Küfür/hakaret. Whitelist DARALTMADAN önce (ham token) değerlendirilir.
    raw_tokens, squeezed = _prep(text)
    for raw in raw_tokens:
        col = _collapse(raw)
        if raw in WHITELIST or col in WHITELIST:
            continue
        if raw in WORDSET or col in WORDSET:
            return "hakaret"
    for w in _STRONG:                     # ayraç-kaçışı (s i k t i r)
        if w in squeezed and not any(w in wl for wl in _WL_SQUEEZED):
            return "hakaret"

    # 2) İletişim bilgisi (URL/e-posta/IBAN/telefon)
    if _RE_URL.search(text) or _RE_EMAIL.search(text) or _RE_IBAN.search(text) \
            or _RE_PHONE.search(text):
        return "iletisim_bilgisi"

    # 3) Spam sezgileri
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 10:
        caps = sum(1 for c in letters if c.isupper())
        if caps / len(letters) >= 0.60:
            return "spam"
    if re.search(r"(.)\1{4,}", text):     # 5+ ardışık tekrar karakter
        return "spam"
    return None


# --- Hız limiti — konu/cevap AYRI sayaç (B4) --------------------------------
_LOCK = threading.Lock()
_last_action: dict[str, float] = {}     # anahtar: "{scope}:{user_id}"


def check_rate(user_id, scope: str = "thread") -> tuple[bool, int]:
    """Dönen: (limitli_mi, retry_after_sn). scope='thread' (60 sn) | 'reply' (15 sn).
    Konu ve cevap AYRI pencerelerdir → biri diğerini bloklamaz. İzinliyse zaman
    damgasını KAYDEDER."""
    win = REPLY_RATE_S if scope == "reply" else THREAD_RATE_S
    key = f"{scope}:{user_id}"
    now = time.time()
    with _LOCK:
        last = _last_action.get(key)
        if last is not None and (now - last) < win:
            return True, int(win - (now - last)) + 1
        _last_action[key] = now
        return False, 0


def rate_reset() -> None:
    with _LOCK:
        _last_action.clear()


# --- K1: risk skorlama -------------------------------------------------------
def risk_flags(text: str, post_count: int) -> tuple[bool, list[str]]:
    """flagged (Haiku'ya gitmeli mi) + sebep listesi. Flagged değilse doğrudan published."""
    reasons: list[str] = []
    raw_tokens, _ = _prep(text)
    tokens = set(raw_tokens) | {_collapse(t) for t in raw_tokens}
    if tokens & MEDICAL_WORDS:
        reasons.append("tibbi_risk")
    if tokens & COMMERCIAL_WORDS:
        reasons.append("ticari")
    if post_count < NEW_ACCOUNT_POSTS:
        reasons.append("yeni_hesap")
    return (len(reasons) > 0, reasons)


# --- K2/K3: Haiku sınıflandırma ---------------------------------------------
_SYSTEM_PROMPT = (
    "Bir anne topluluğu moderatörüsün. Metni değerlendir. "
    "Sadece JSON dön: {\"izin\": true|false, \"sebep\": \"spam|tibbi_risk|"
    "hakaret|reklam|uygunsuz|temiz\", \"guven\": 0-1}"
)


def classify(text: str) -> dict | None:
    """Haiku ile sınıflandır. {izin,sebep,guven} döner; hata/timeout/anahtarsız → None
    (fail-open: çağıran içeriği yayında bırakır)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    try:
        client = Anthropic(api_key=api_key, timeout=HAIKU_TIMEOUT_S, max_retries=0)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=100,
            system=[{"type": "text", "text": _SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": text[:2000]}],
        )
        raw = resp.content[0].text
        return _parse_verdict(raw)
    except Exception as e:                # timeout/ağ/kota/parse → fail-open
        logger.warning("Haiku moderasyon hatası (fail-open): %s", e)
        return None


def _parse_verdict(raw: str) -> dict | None:
    import json
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    if "izin" not in d:
        return None
    try:
        guven = float(d.get("guven", 0))
    except (ValueError, TypeError):
        guven = 0.0
    return {"izin": bool(d["izin"]), "sebep": str(d.get("sebep", "uygunsuz")),
            "guven": max(0.0, min(1.0, guven))}


def should_hide(verdict: dict | None) -> bool:
    """izin=false ve güven yeterli mi (→ hidden)."""
    return bool(verdict and verdict["izin"] is False
                and verdict["guven"] >= HAIKU_CONFIDENCE)


def review_async(target_type: str, target_id, text: str) -> None:
    """K2 arka plan işi (BackgroundTask): flagged içeriği Haiku ile sınıfla; gerekiyorsa
    gizle. KENDİ DB oturumunu açar. Hata/timeout → içerik yayında KALIR (fail-open)."""
    from api.db import SessionLocal
    db = SessionLocal()
    try:
        verdict = classify(text)
        if should_hide(verdict):
            hidden = hide_content(db, target_type, target_id, "haiku", verdict["sebep"])
            logger.info("K2 Haiku değerlendirmesi: %s %s gizlendi=%s sebep=%s güven=%.2f",
                        target_type, target_id, hidden, verdict["sebep"], verdict["guven"])
        else:
            logger.info("K2 Haiku: %s %s yayında kaldı (verdict=%s)",
                        target_type, target_id, verdict)
    except Exception:
        logger.exception("K2 review beklenmedik hata (fail-open, yayında kalır)")
    finally:
        db.close()


# --- Moderasyon eylemleri + eskalasyon --------------------------------------
def log_action(db, target_type: str, target_id, action: str, source: str,
               reason: str | None = None, actor_id=None) -> None:
    from api.models import ModerationLog
    db.add(ModerationLog(target_type=target_type, target_id=target_id,
                         action=action, source=source, reason=reason, actor_id=actor_id))


def _content_owner_id(db, target_type: str, target_id):
    from api.models import Reply, Thread
    obj = db.get(Thread if target_type == "thread" else Reply, target_id)
    return (obj.user_id if obj is not None else None), obj


def hide_content(db, target_type: str, target_id, source: str,
                 reason: str | None = None, actor_id=None, commit: bool = True) -> bool:
    """İçeriği hidden yap + logla + yazarına eskalasyon uygula. Zaten hidden/removed
    ise dokunmaz. Dönen: gerçekten gizlendiyse True."""
    owner_id, obj = _content_owner_id(db, target_type, target_id)
    if obj is None or obj.status in ("hidden", "removed"):
        return False
    obj.status = "hidden"
    log_action(db, target_type, target_id, "hide", source, reason)
    if owner_id is not None:
        _apply_escalation(db, owner_id)
    if commit:
        db.commit()
    return True


def _count_hidden(db, user_id) -> int:
    from sqlalchemy import func
    from api.models import Reply, Thread
    t = db.query(func.count(Thread.id)).filter(
        Thread.user_id == user_id, Thread.status == "hidden").scalar() or 0
    r = db.query(func.count(Reply.id)).filter(
        Reply.user_id == user_id, Reply.status == "hidden").scalar() or 0
    return int(t) + int(r)


def _apply_escalation(db, user_id) -> None:
    """3 hidden → muted (24s); 5 → banned + tüm içerik hidden."""
    from api.models import CommunityProfile, Reply, Thread
    prof = db.query(CommunityProfile).filter(CommunityProfile.user_id == user_id).one_or_none()
    if prof is None:
        return
    db.flush()                      # autoflush=False → bekleyen hidden değişimi sayıma girsin
    hidden = _count_hidden(db, user_id)
    if hidden >= HIDDEN_BAN_THRESHOLD and prof.status != "banned":
        prof.status = "banned"
        prof.muted_until = None
        # tüm içerikleri hidden
        for t in db.query(Thread).filter(Thread.user_id == user_id,
                                         Thread.status == "published").all():
            t.status = "hidden"
            log_action(db, "thread", t.id, "ban", "filter", "ban_cascade")
        for r in db.query(Reply).filter(Reply.user_id == user_id,
                                        Reply.status == "published").all():
            r.status = "hidden"
            log_action(db, "reply", r.id, "ban", "filter", "ban_cascade")
        log_action(db, "thread", user_id, "ban", "filter", f"hidden>={HIDDEN_BAN_THRESHOLD}",
                   actor_id=None)
        logger.info("Kullanıcı banlandı (hidden=%d): user=%s", hidden, user_id)
    elif hidden >= HIDDEN_MUTE_THRESHOLD and prof.status == "active":
        prof.status = "muted"
        prof.muted_until = datetime.now(timezone.utc) + timedelta(hours=MUTE_HOURS)
        log_action(db, "thread", user_id, "mute", "filter", f"hidden>={HIDDEN_MUTE_THRESHOLD}")
        logger.info("Kullanıcı susturuldu 24s (hidden=%d): user=%s", hidden, user_id)


def posting_block_reason(prof) -> str | None:
    """Profil gönderi yapabilir mi? banned → kalıcı; muted & süre dolmadı → geçici.
    Dönen: engel sebebi (str) ya da None (izinli). Süre dolmuş mute otomatik kalkar."""
    if prof is None:
        return None
    if prof.status == "banned":
        return "banned"
    if prof.status == "muted":
        mu = prof.muted_until
        if mu is not None:
            if mu.tzinfo is None:
                mu = mu.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < mu:
                return "muted"
        # süre doldu → otomatik aktife çek (çağıran commit eder)
        prof.status = "active"
        prof.muted_until = None
    return None
