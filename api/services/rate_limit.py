"""
Basit in-memory rate limit (Faz G2) — brute-force / kredi tüketimi freni.

Denetimde login'e 30 ardışık hatalı deneme hiç engellenmedi (credential stuffing +
Resend e-posta kotası tüketimi riski). Bu modül harici bağımlılık (slowapi/Redis)
olmadan iki koruma sağlar:

  1) IP bazlı kayan pencere: IP başına dakikada en fazla IP_MAX istek (login +
     reset-request uçlarında). DoS/spray freni.
  2) Hesap bazlı kilit: aynı e-postada ACCT_MAX_FAILS ardışık başarısız login →
     ACCT_LOCK_S saniye kilit (429 + Retry-After). Başarılı login sayacı sıfırlar.

TASARIM: tek-instance için yeterli. Çok-instance'a geçilirse limitler instance
başına uygulanır (daha gevşek ama yine faydalı) → merkezi sayaç için Redis'e
taşınmalı. Bellek: pencereler süresi dolunca temizlenir; hesap kaydı başarıda silinir.
Zaman kaynağı time.time() (test edilebilirlik için _now ile sarıldı).
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_ip_hits: dict[str, list[float]] = {}       # "bucket:ip" -> [timestamp]
_acct: dict[str, dict] = {}                 # email -> {"fails": int, "locked_until": float}

IP_MAX = 10                                 # IP başına pencere içinde izinli istek
IP_WINDOW_S = 60                            # kayan pencere (sn)
ACCT_MAX_FAILS = 5                          # ardışık hata → kilit
ACCT_LOCK_S = 15 * 60                       # kilit süresi (sn)
_MAX_TRACKED = 50_000                       # bellek güvenlik tavanı (spray koruması)


def _now() -> float:
    return time.time()


def client_ip(request) -> str:
    """Gerçek istemci IP'si — Railway/proxy arkasında X-Forwarded-For'un ilk değeri."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_ip(ip: str, bucket: str) -> int | None:
    """İzinliyse hit'i KAYDEDER ve None döner; limit aşıldıysa Retry-After (sn) döner."""
    key = f"{bucket}:{ip}"
    now = _now()
    with _LOCK:
        if len(_ip_hits) > _MAX_TRACKED:                 # taşma koruması
            _ip_hits.clear()
        hits = [t for t in _ip_hits.get(key, []) if now - t < IP_WINDOW_S]
        if len(hits) >= IP_MAX:
            _ip_hits[key] = hits
            return max(1, int(IP_WINDOW_S - (now - hits[0])) + 1)
        hits.append(now)
        _ip_hits[key] = hits
        return None


def check_account_locked(email: str) -> int | None:
    """Hesap kilitliyse kalan süreyi (sn) döner; değilse None."""
    now = _now()
    with _LOCK:
        rec = _acct.get(email)
        if rec and rec["locked_until"] > now:
            return max(1, int(rec["locked_until"] - now) + 1)
        return None


def record_failure(email: str) -> None:
    """Başarısız login say; eşiğe ulaşınca hesabı kilitle."""
    now = _now()
    with _LOCK:
        if len(_acct) > _MAX_TRACKED:
            _acct.clear()
        rec = _acct.setdefault(email, {"fails": 0, "locked_until": 0.0})
        if rec["locked_until"] and rec["locked_until"] <= now:    # kilit bitti → sıfırla
            rec["fails"] = 0
            rec["locked_until"] = 0.0
        rec["fails"] += 1
        if rec["fails"] >= ACCT_MAX_FAILS:
            rec["locked_until"] = now + ACCT_LOCK_S
            rec["fails"] = 0                              # kilit sonrası sayaç sıfır


def record_success(email: str) -> None:
    """Başarılı login → hesabın hata sayacını/kilidini temizle."""
    with _LOCK:
        _acct.pop(email, None)


def reset() -> None:
    """Test yardımcısı."""
    with _LOCK:
        _ip_hits.clear()
        _acct.clear()
