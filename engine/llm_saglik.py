"""
LLM sağlık izleyicisi — /health'in "llm" alanını besler.

NEDEN VAR (2026-09-14'te yaşandı): Anthropic kredi bakiyesi bitti, TÜM LLM
çağrıları `400 invalid_request_error: Your credit balance is too low` almaya
başladı. /health LLM'e hiç dokunmadığı için **200 dönmeye devam etti** ve
UptimeRobot arızayı GÖRMEDİ. Yani sohbet ve plan üretimi tamamen çalışmazken
altyapı "sağlıklı" görünüyordu.

TASARIM — iki katman, /health'i ASLA BLOKLAMAZ:

  1. PASİF (bedava, birincil): her gerçek LLM çağrısının sonucu burada
     kaydedilir. Kredi bitince ilk gerçek çağrı hatayı üretir ve /health
     anında "error" döner. Ek maliyet YOK, ek gecikme YOK.

  2. AKTİF YOKLAMA (çok ucuz, boşluk kapatıcı): hiç trafik yoksa pasif katman
     sessiz kalır — kredi gece biterse sabaha kadar kimse görmez. Bu yüzden
     PROBE_ARALIK_SN'de bir kez minik bir çağrı yapılır (max_tokens=1).
     Yoklama ARKA PLAN İŞ PARÇACIĞINDA koşar; /health o an elindeki
     ÖNBELLEKLENMİŞ kararı döndürür ve beklemez.

/health'i bloklamamak KRİTİK: Railway healthcheck bu ucu kullanıyor. Senkron
bir LLM çağrısı yavaşlarsa konteyner sağlıksız sayılıp yeniden başlatılabilirdi
— yani izleme aracı arızanın kendisine dönüşürdü.

Dönen "llm" alanı:
    "ok"       — son çağrılar başarılı
    "error"    — LLM çağrıları başarısız (detayda sebep: credit/auth/rate_limit/…)
    "unknown"  — henüz ne çağrı ne yoklama var (taze başlatma)
    "disabled" — ANTHROPIC_API_KEY tanımlı değil (yerel/test ortamı)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger("tavsan.llm_saglik")

# Bir hatadan sonra kaç saniye boyunca "error" raporlanır. Görev tanımındaki
# "son 5 dakikadaki hata oranı" bu pencereyle karşılanıyor: pencere içinde
# BAŞARILI bir çağrı olursa durum hemen "ok"a döner (aşağıdaki mantık son
# olayı esas alır, oran değil — tek bir kredi hatası bile kritik).
HATA_PENCERESI_SN = 300
# Aktif yoklama sıklığı. 15 dakika: UptimeRobot 5 dk'da bir sorsa bile en geç
# 15 dakikada arıza görünür; günde ~96 çağrı × ~15 token = ihmal edilebilir.
PROBE_ARALIK_SN = 900
# Yoklama isteğinin kendi zaman aşımı (arka planda bile sonsuza kadar asılmasın).
PROBE_TIMEOUT_SN = 10

_kilit = threading.Lock()
_durum: dict[str, Any] = {
    "son_basari_ts": 0.0,
    "son_hata_ts": 0.0,
    "son_hata_tipi": None,      # "credit" | "auth" | "rate_limit" | "overloaded" | "error"
    "son_hata_mesaj": None,
    "ardisik_hata": 0,
    "son_probe_ts": 0.0,
    "probe_calisiyor": False,
}


def _hata_tipi(exc: BaseException) -> str:
    """İstisnayı izlenebilir bir sınıfa indir.

    Sınıf ayrımı önemli: 'credit' insan müdahalesi (kredi yükle) isterken
    'overloaded' kendiliğinden geçer. Alarm kuran kişi bunu ayırt edebilmeli."""
    ad = type(exc).__name__
    m = str(getattr(exc, "message", "") or exc).lower()
    if "credit balance" in m or "billing" in m or "quota" in m:
        return "credit"
    if "authentication" in m or "invalid x-api-key" in m or ad == "AuthenticationError":
        return "auth"
    if "rate limit" in m or ad == "RateLimitError":
        return "rate_limit"
    if "overloaded" in m or ad == "InternalServerError":
        return "overloaded"
    return "error"


def kaydet_basari() -> None:
    """Gerçek bir LLM çağrısı başarılı oldu."""
    with _kilit:
        _durum["son_basari_ts"] = time.time()
        _durum["ardisik_hata"] = 0
        _durum["son_hata_tipi"] = None
        _durum["son_hata_mesaj"] = None


def kaydet_hata(exc: BaseException) -> None:
    """Gerçek bir LLM çağrısı hata verdi. Çağıranın akışını DEĞİŞTİRMEZ."""
    tip = _hata_tipi(exc)
    with _kilit:
        _durum["son_hata_ts"] = time.time()
        _durum["son_hata_tipi"] = tip
        # Mesaj KIRPILIR: /health herkese açık, sağlayıcı iç detayı sızmasın.
        _durum["son_hata_mesaj"] = str(exc)[:160]
        _durum["ardisik_hata"] += 1
    if tip in ("credit", "auth"):
        # Bu ikisi kendiliğinden geçmez; logda da bağırsın.
        logger.error("LLM çağrısı başarısız (%s) — insan müdahalesi gerekiyor: %s",
                     tip, str(exc)[:200])
    else:
        logger.warning("LLM çağrısı başarısız (%s): %s", tip, str(exc)[:200])


def anahtar_var_mi() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# Aktif yoklama — arka planda, /health'i bloklamadan
# ---------------------------------------------------------------------------
def _probe() -> None:
    """Mümkün olan en küçük çağrı: 1 token girdi, 1 token çıktı.

    Model olarak chatbot'un ucuz modeli kullanılır. Bilerek system prompt ve
    cache_control YOK — amaç üretim yolunu taklit etmek değil, kimlik
    doğrulama/kota/erişilebilirlik kapısının açık olduğunu görmek."""
    try:
        from anthropic import Anthropic
        from engine.config import CHATBOT_MODEL
        istemci = Anthropic(timeout=PROBE_TIMEOUT_SN)
        istemci.messages.create(
            model=CHATBOT_MODEL, max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        kaydet_basari()
        logger.info("LLM yoklaması başarılı")
    except Exception as e:                       # noqa: BLE001 — her hata sinyaldir
        kaydet_hata(e)
    finally:
        with _kilit:
            _durum["probe_calisiyor"] = False
            _durum["son_probe_ts"] = time.time()


def _probe_gerekli_mi() -> bool:
    """Yoklama zamanı geldi mi? (trafik varsa gereksiz — pasif katman yeter)"""
    simdi = time.time()
    with _kilit:
        if _durum["probe_calisiyor"]:
            return False
        # Yakın zamanda GERÇEK bir çağrı olduysa yoklamaya gerek yok.
        son_olay = max(_durum["son_basari_ts"], _durum["son_hata_ts"])
        if simdi - son_olay < PROBE_ARALIK_SN:
            return False
        if simdi - _durum["son_probe_ts"] < PROBE_ARALIK_SN:
            return False
        _durum["probe_calisiyor"] = True
        return True


def yoklamayi_tetikle() -> None:
    """Gerekiyorsa yoklamayı ARKA PLANDA başlat. Çağıran BEKLEMEZ."""
    if not anahtar_var_mi() or not _probe_gerekli_mi():
        return
    threading.Thread(target=_probe, name="llm-saglik-probe", daemon=True).start()


# ---------------------------------------------------------------------------
# /health görünümü
# ---------------------------------------------------------------------------
def durum(detay: bool = False) -> tuple[str, dict[str, Any] | None]:
    """(llm_durumu, detay_sozlugu | None).

    Karar SON OLAYA göre verilir, orana göre değil: tek bir 'credit' hatası bile
    servisin tamamen durduğu anlamına gelir; oran hesabı bunu seyreltirdi.
    Hata penceresi içinde başarılı bir çağrı olursa durum 'ok'a döner."""
    if not anahtar_var_mi():
        return "disabled", None

    with _kilit:
        d = dict(_durum)

    simdi = time.time()
    son_hata_yasi = simdi - d["son_hata_ts"] if d["son_hata_ts"] else None
    taze_hata = son_hata_yasi is not None and son_hata_yasi <= HATA_PENCERESI_SN
    # Hatadan SONRA başarı geldiyse arıza kapanmıştır.
    hata_sonrasi_basari = d["son_basari_ts"] > d["son_hata_ts"]

    if taze_hata and not hata_sonrasi_basari:
        llm = "error"
    elif d["son_basari_ts"] or d["son_hata_ts"]:
        llm = "ok"
    else:
        llm = "unknown"

    if not detay:
        return llm, None
    return llm, {
        "son_hata_tipi": d["son_hata_tipi"],
        "son_hata_mesaj": d["son_hata_mesaj"],
        "son_hata_saniye_once": round(son_hata_yasi) if son_hata_yasi else None,
        "son_basari_saniye_once": (round(simdi - d["son_basari_ts"])
                                   if d["son_basari_ts"] else None),
        "ardisik_hata": d["ardisik_hata"],
        "hata_penceresi_sn": HATA_PENCERESI_SN,
        "probe_aralik_sn": PROBE_ARALIK_SN,
    }


def sifirla() -> None:
    """Test yardımcısı — durumu temizle."""
    with _kilit:
        _durum.update({"son_basari_ts": 0.0, "son_hata_ts": 0.0,
                       "son_hata_tipi": None, "son_hata_mesaj": None,
                       "ardisik_hata": 0, "son_probe_ts": 0.0,
                       "probe_calisiyor": False})
