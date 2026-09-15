"""
LLM MÜHRÜ — deterministik test suite'lerinin canlı Sonnet çağırmasını engeller.

NEDEN VAR (2026-09-15'te ölçüldü): test dosyaları başında
`os.environ.pop("ANTHROPIC_API_KEY", None)` yazıyordu ama bu YETMİYORDU —
`api/main.py` import edilirken `load_dotenv()` çağırıyor ve anahtarı `.env`'den
GERİ YÜKLÜYOR:

    import ONCESI anahtar: False
    import SONRASI anahtar: True        ← pop boşa gitti

Sonuç: "LLM yok, deterministik" diye yazılmış suite'ler kurulumdaki tek
`POST /plans/generate` için gerçek Sonnet faturası üretiyordu (suite başına
~130 sn + ücret). Bu modül mührü import SONRASINA koyar ve ayrıca sayaçla
KANITLAR — sessizce canlıya kayma yolu kapanır.

KULLANIM (api.main / plan_service import'larından SONRA çağır):

    from tests.llm_muhuru import muhurle, canli_cagri_sayisi
    muhurle()
    ...
    assert canli_cagri_sayisi() == 0

`muhurle()` üç şeyi birden yapar, çünkü tek başına hiçbiri yeterli değil:
  1. env anahtarını siler (load_dotenv'den SONRA, bu yüzden kalıcı),
  2. `plan_generator.HAS_ANTHROPIC = False` yapar (anahtar bir yerden geri
     gelse bile kod fallback'e düşer),
  3. `Anthropic` istemcisini, çağrılırsa TEST'İ PATLATAN bir sahteyle değiştirir
     — yani sızıntı sessiz kalamaz.
"""
from __future__ import annotations

import os
from typing import Any

from engine import plan_generator

_SAYAC = {"canli": 0, "fallback": 0}


class CanliCagriSizintisi(AssertionError):
    """Deterministik suite canlı LLM çağırmaya kalktı — sessiz kalmamalı."""


class _PatlayanIstemci:
    """Anthropic yerine geçer; kurulması bile hatadır."""

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        _SAYAC["canli"] += 1
        raise CanliCagriSizintisi(
            "Deterministik test canlı Anthropic istemcisi kurmaya çalıştı. "
            "muhurle() çağrıldıysa buraya HİÇ gelinmemeliydi — "
            "plan_generator.plan_uret'in fallback dalı atlanmış demektir."
        )


def muhurle() -> None:
    """Canlı LLM yolunu kapat. api.main import edildikten SONRA çağrılmalı."""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    plan_generator.HAS_ANTHROPIC = False
    plan_generator.Anthropic = _PatlayanIstemci          # type: ignore[assignment]

    ham = plan_generator._fallback_plan

    def _sayan_fallback(param: dict) -> str:
        _SAYAC["fallback"] += 1
        return ham(param)

    if getattr(plan_generator._fallback_plan, "_muhurlu", False):
        return                                           # iki kez sarma
    _sayan_fallback._muhurlu = True                      # type: ignore[attr-defined]
    plan_generator._fallback_plan = _sayan_fallback      # type: ignore[assignment]


def canli_cagri_sayisi() -> int:
    """Mühürden sonra kaç kez canlı istemci kurulmaya çalışıldı (0 olmalı)."""
    return _SAYAC["canli"]


def fallback_cagri_sayisi() -> int:
    """Yedek motorun kaç kez koştuğu — 'plan gerçekten üretildi mi' kanıtı."""
    return _SAYAC["fallback"]


def muhur_saglam_mi() -> tuple[bool, str]:
    """Mühür gerçekten duruyor mu? (anahtar yok + bayrak kapalı + hiç sızıntı yok)"""
    anahtar = bool(os.getenv("ANTHROPIC_API_KEY"))
    bayrak = plan_generator.HAS_ANTHROPIC
    sizinti = _SAYAC["canli"]
    ok = (not anahtar) and (not bayrak) and sizinti == 0
    return ok, (f"anahtar={anahtar} HAS_ANTHROPIC={bayrak} "
                f"canli_deneme={sizinti} fallback={_SAYAC['fallback']}")
