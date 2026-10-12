"""
Yöntem tablosu — 13 günlük eğitim yönteminin SAYILARI için tek okuyucu.

data/yontem_tablosu.json, data/yas_bantlari.json desenini izler: sürüm, sürüm
notları, her değerde kaynak ve İlayda onayı. 2026-10-12 itibarıyla HİÇBİR uç ya
da iş bu modülü çağırmıyor; ilk tüketici salt okur denetim betiği
(scripts/yontem_denetim.py). Davranış bu tabloya henüz bağlı DEĞİL.

Alan biçimleri (bölüm → alan):
    dolu      {deger, birim?, kaynak: [...], onay, not?}
    bekleyen  {durum: "ilayda_bekliyor", soru, adaylar: [{deger, kaynak, not?}], onay}
    atıf      {atif: "yas_bantlari.json#yol", kaynak?, not?}

Bekleyen bir alan okunmak istenirse BekleyenAlanHatasi yükselir — sessizce
varsayılan ya da ilk aday DÖNMEZ. Yaşa bağlı değerler (uyanıklık penceresi,
kısa uyku eşiği) yas_bantlari'nda kalır; burada yalnız atıf vardır.

Ana API:
    tablo()                  ham tablo (salt okunur)
    surum()                  "0.1"
    dogrula(t)               şema hataları listesi (boşsa geçerli)
    bekleyen_alanlar()       ["kisa_gunduz.dis_ic_bekleme", …]
    deger("seans.mola_dk")   15 — bekleyende BekleyenAlanHatasi
    adaylar(yol)             bekleyen alanın aday değerleri (karar için)
    kaynaklar(yol)           alanın kaynak listesi
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TABLO_PATH = DATA_DIR / "yontem_tablosu.json"

BEKLIYOR = "ilayda_bekliyor"
BOLUMLER = ("merdiven", "gece_uyanma", "kisa_gunduz", "seans", "kucak_araligi_sn", "hazirlik",
            "yatir_cik", "regresyon", "asama_gecisi", "bildirim", "yas_atiflari")
_META = ("surum", "guncelleme", "surum_notlari", "aciklama", "okuma_kurallari",
         "plan_tipi")
ATIF_ONEKI = "yas_bantlari.json#"


class YontemTablosuHatasi(RuntimeError):
    """Tablo okunamadı, şeması bozuk ya da istenen alan yok."""


class BekleyenAlanHatasi(YontemTablosuHatasi):
    """Alan İlayda kararı bekliyor — değeri YOK (aday listesi `adaylar(yol)`)."""

    def __init__(self, yol: str, soru: str | None = None):
        self.yol = yol
        self.soru = soru
        super().__init__(f"{yol}: İlayda kararı bekliyor — değer yok"
                         + (f" ({soru})" if soru else ""))


# =============================================================================
# Şema
# =============================================================================
def _alan_turu(alan: Any) -> str | None:
    if not isinstance(alan, dict):
        return None
    if "atif" in alan:
        return "atif"
    if alan.get("durum") == BEKLIYOR:
        return "bekleyen"
    if "deger" in alan:
        return "dolu"
    return None


def _kaynak_gecerli(kaynak: Any) -> bool:
    return (isinstance(kaynak, list) and len(kaynak) > 0
            and all(isinstance(k, dict) and k for k in kaynak))


def dogrula(t: dict) -> list[str]:
    """Şema hatalarını döndür (boş liste = geçerli). Yüklemede çağrılır."""
    hatalar: list[str] = []
    if not isinstance(t, dict):
        return ["tablo bir sözlük değil"]
    for anahtar in ("surum", "surum_notlari"):
        if anahtar not in t:
            hatalar.append(f"eksik üst alan: {anahtar}")
    if "surum" in t and str(t["surum"]) not in (t.get("surum_notlari") or {}):
        hatalar.append(f"surum_notlari'nda {t['surum']} sürümünün notu yok")
    for bolum in BOLUMLER:
        if not isinstance(t.get(bolum), dict) or not t[bolum]:
            hatalar.append(f"eksik ya da boş bölüm: {bolum}")
    for bolum, alanlar in t.items():
        if bolum in _META:
            continue
        if bolum not in BOLUMLER:
            hatalar.append(f"tanınmayan bölüm: {bolum}")
            continue
        if not isinstance(alanlar, dict):
            continue
        for ad, alan in alanlar.items():
            yol = f"{bolum}.{ad}"
            tur = _alan_turu(alan)
            if tur is None:
                hatalar.append(f"{yol}: tanınmayan alan biçimi (deger / durum / atif yok)")
                if isinstance(alan, dict) and "durum" in alan:
                    hatalar.append(f"{yol}: bilinmeyen durum {alan['durum']!r}")
                continue
            if tur == "dolu":
                if not _kaynak_gecerli(alan.get("kaynak")):
                    hatalar.append(f"{yol}: dolu alanda kaynak yok")
                if "onay" not in alan:
                    hatalar.append(f"{yol}: onay alanı yok")
            elif tur == "bekleyen":
                if "deger" in alan:
                    hatalar.append(f"{yol}: bekleyen alanda deger olamaz")
                if not alan.get("soru"):
                    hatalar.append(f"{yol}: bekleyen alanda soru yok")
                adaylar_ = alan.get("adaylar")
                if not isinstance(adaylar_, list):
                    hatalar.append(f"{yol}: bekleyen alanda adaylar listesi yok")
                else:
                    if not adaylar_ and not alan.get("kaynak_notu"):
                        hatalar.append(f"{yol}: aday yoksa kaynak_notu zorunlu")
                    for i, a in enumerate(adaylar_):
                        if not isinstance(a, dict) or "deger" not in a:
                            hatalar.append(f"{yol}.adaylar[{i}]: deger yok")
                        elif not _kaynak_gecerli(a.get("kaynak")):
                            hatalar.append(f"{yol}.adaylar[{i}]: kaynak yok")
                if "onay" not in alan:
                    hatalar.append(f"{yol}: onay alanı yok")
            elif tur == "atif":
                if not str(alan["atif"]).startswith(ATIF_ONEKI):
                    hatalar.append(f"{yol}: atıf yalnız {ATIF_ONEKI}… olabilir")
                elif _atif_yolu_var_mi(str(alan["atif"])[len(ATIF_ONEKI):]) is False:
                    hatalar.append(f"{yol}: atıf yas_bantlari'nda bulunamadı")
    return hatalar


# =============================================================================
# Yükleme
# =============================================================================
@lru_cache(maxsize=1)
def _tablo() -> dict:
    try:
        with open(TABLO_PATH, "r", encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise YontemTablosuHatasi(f"yontem_tablosu.json okunamadı: {e}") from e
    hatalar = dogrula(t)
    if hatalar:
        raise YontemTablosuHatasi("yontem_tablosu.json şeması bozuk: " + "; ".join(hatalar))
    return t


def tablo() -> dict:
    """Ham tablo (salt okunur kullanın)."""
    return _tablo()


def surum() -> str:
    return str(_tablo()["surum"])


def _alan(yol: str) -> dict:
    bolum, _, ad = yol.partition(".")
    alan = (_tablo().get(bolum) or {}).get(ad) if bolum in BOLUMLER else None
    if not isinstance(alan, dict):
        raise YontemTablosuHatasi(f"tabloda böyle bir alan yok: {yol}")
    return alan


def bekleyen_alanlar() -> list[str]:
    """İlayda kararı bekleyen alanların yolları ("bolum.alan")."""
    out = []
    for bolum in BOLUMLER:
        for ad, alan in (_tablo().get(bolum) or {}).items():
            if _alan_turu(alan) == "bekleyen":
                out.append(f"{bolum}.{ad}")
    return out


def bekliyor_mu(yol: str) -> bool:
    return _alan_turu(_alan(yol)) == "bekleyen"


def deger(yol: str) -> Any:
    """Alanın değeri. Bekleyende BekleyenAlanHatasi; atıfta yas_bantlari'ndan
    çözülür (bant başına değişen atıf — '[]' içeren — burada çözülmez)."""
    alan = _alan(yol)
    tur = _alan_turu(alan)
    if tur == "bekleyen":
        raise BekleyenAlanHatasi(yol, alan.get("soru"))
    if tur == "atif":
        return atif_coz(alan["atif"])
    return alan["deger"]


def adaylar(yol: str) -> list[dict]:
    """Bekleyen alanın aday değerleri (kaynaklarıyla). Dolu alanda boş liste."""
    alan = _alan(yol)
    return list(alan.get("adaylar") or []) if _alan_turu(alan) == "bekleyen" else []


def kaynaklar(yol: str) -> list[dict]:
    return list(_alan(yol).get("kaynak") or [])


# =============================================================================
# yas_bantlari atıfları
# =============================================================================
def _atif_yolu_var_mi(yol: str) -> bool | None:
    """Atıf yolu yas_bantlari'nda var mı? '[]' (bant başına) adımında listenin
    İLK öğesine bakılır. Tablo okunamazsa None (doğrulama atlanır)."""
    try:
        from engine import yas_bantlari
        dugum: Any = yas_bantlari.tablo()
    except Exception:
        return None
    for parca in yol.split("."):
        liste = parca.endswith("[]")
        ad = parca[:-2] if liste else parca
        if not isinstance(dugum, dict) or ad not in dugum:
            if (isinstance(dugum, dict) and isinstance(dugum.get("varyantlar"), dict)
                    and all(ad in v for v in dugum["varyantlar"].values())):
                return True                       # 12-18 ay: alan varyantlarda
            return False
        dugum = dugum[ad]
        if liste:
            if not isinstance(dugum, list) or not dugum:
                return False
            dugum = dugum[0]
    return True


def atif_coz(atif: str) -> Any:
    """'yas_bantlari.json#a.b' → yas_bantlari tablosundaki değer. Bant başına
    değişen atıflar ('[]') tek değere indirgenemez: engine.yas_bantlari
    üzerinden yaşa göre okunmalı."""
    if not atif.startswith(ATIF_ONEKI):
        raise YontemTablosuHatasi(f"tanınmayan atıf: {atif}")
    yol = atif[len(ATIF_ONEKI):]
    if "[]" in yol:
        raise YontemTablosuHatasi(
            f"{atif} yaşa bağlı — engine.yas_bantlari.yas_bandi_getir() ile okuyun")
    from engine import yas_bantlari
    dugum: Any = yas_bantlari.tablo()
    for parca in yol.split("."):
        if not isinstance(dugum, dict) or parca not in dugum:
            raise YontemTablosuHatasi(f"atıf yas_bantlari'nda bulunamadı: {atif}")
        dugum = dugum[parca]
    return dugum
