"""
Yöntem tablosu denetimi — eğitim planlarındaki sayıları tabloyla karşılaştırır.

SALT OKUR: DB'ye yazmaz (okuma tek bir transaction'da, sonunda rollback). Plan
METNİ YAZDIRILMAZ; çıktı yalnız sayı, rol ve satır sayısıdır (KVKK: plan metni
bebeğin adını taşıyabilir).

Her eğitim planının (content.type == egitim_plani) markdown satırlarındaki
süreler rolleriyle çıkarılır — dış, iç, mola, seans, gece_uyanma, kucak,
yatir_cik, tekrar — ve data/yontem_tablosu.json ile karşılaştırılır:

    uyumlu      değer tablonun dolu alanıyla uyuşuyor
    uyumsuz     değer tablonun dolu alanıyla çelişiyor
    bekliyor    rolün değeri İlayda kararı bekleyen bir alana bağlı (karşılaştırılmaz)
    kapsam_disi tablonun konusu olmayan süre (kestirme, başarı ölçütü)
    belirsiz    sayının rolü satırdan çıkarılamadı

Satır durumu: herhangi bir sayı uyumsuzsa uyumsuz, değilse bekliyor, değilse
uyumlu; yalnız belirsiz sayı içeren satır belirsiz.

Rol çıkarımı SEZGİSELDİR: aynı cümlede sayıya en yakın anahtar sözcük (önce ya
da sonra) rolü belirler. Sonuçlar "tahmini" okunmalı.

Çalıştırma:
    python scripts/yontem_denetim.py                 # DATABASE_URL'deki DB (salt okur)
    python scripts/yontem_denetim.py --json plan.json  # [{content, bant?}, …] dosyası
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import yontem  # noqa: E402

# Sayı + rol çıkarımı engine/sayi_rol.py'de (Sor doğrulamasıyla ortak).
from engine.sayi_rol import KAPSAM_DISI, ROL_SOZCUKLERI, satir_sayilari  # noqa: E402,F401


# =============================================================================
# Tabloyla karşılaştırma
# =============================================================================
def kurallar() -> dict:
    """Rol → karşılaştırma kuralı. Değerler YALNIZ dolu alanlardan okunur;
    bekleyen alana bağlı durumlar 'bekliyor' döner (karşılaştırılmaz)."""
    b = set(yontem.bekleyen_alanlar())
    g13 = yontem.deger("kisa_gunduz.gun_1_3_dis_ic_dk")
    yc_kd = yontem.deger("kisa_gunduz.yatir_cik_ve_sonrasi_dis_ic_dk")
    yc = (set(yontem.deger("yatir_cik.standart_dizi_dk"))
          | set(yontem.deger("yatir_cik.en_fazla_dk"))
          | {yontem.deger("yatir_cik.yirmi_bir_gun_kurali")["en_fazla_dk"]})
    rutin = yontem.deger("seans.rutin_suresi_dk")
    return {
        "kucak": {"izinli": {v / 60 for v in yontem.deger("kucak_araligi_sn.dizi")}},
        "mola": {"izinli": {yontem.deger("seans.mola_dk"), yontem.deger("seans.mola_en_fazla_dk")}},
        "seans": {"izinli": {yontem.deger("seans.deneme_dk"), yontem.deger("kisa_gunduz.seans_toplam_dk")}},
        "tekrar": {"izinli": {yontem.deger("seans.tekrar_en_fazla")}},
        "rutin": {"aralik": (rutin["en_az"], rutin["en_fazla"])},
        "yatir_cik": {"izinli": yc},
        "artis": {"en_az": yontem.deger("gece_uyanma.gunluk_artis_en_az_dk")},
        "gece_uyanma": {"taban": yontem.deger("gece_uyanma.taban_dk"),
                        "bekleyen": [y for y in ("gece_uyanma.ilk_gece_ilk_bekleme_dk",
                                                  "gece_uyanma.gece_ici_artis_dk") if y in b]},
        # Kısa gündüz dış/iç: gün 1-3 ve yatır-çık dolu (İlayda), gün 4-12 bekliyor.
        "dis": {"gun": {g: v["dis"] for g, v in ((1, g13["gun_1"]), (2, g13["gun_2"]), (3, g13["gun_3"]))},
                "yatir_cik": yc_kd["dis"],
                "bekleyen": "kisa_gunduz.gun_4_12_dis_ic_dk" in b},
        "ic": {"gun": {g: v["ic"] for g, v in ((1, g13["gun_1"]), (2, g13["gun_2"]), (3, g13["gun_3"]))},
               "yatir_cik": yc_kd["ic"],
               "bekleyen": "kisa_gunduz.gun_4_12_dis_ic_dk" in b},
    }


def sayi_durumu(rol: str, dk: float, k: dict, gun: tuple[int, int] | None = None) -> str:
    """`gun`: sayının geçtiği plan gün bölümü (başlangıç, bitiş); genel bölümde None."""
    if rol in KAPSAM_DISI:
        return "kapsam_disi"
    if rol == "belirsiz" or rol not in k:
        return "belirsiz"
    kural = k[rol]
    if rol == "artis":                             # artış miktarı: en az 30 sn
        return "uyumlu" if dk >= kural["en_az"] - 1e-9 else "uyumsuz"
    if rol == "rutin":
        a, b = kural["aralik"]
        return "uyumlu" if a - 1e-9 <= dk <= b + 1e-9 else "uyumsuz"
    if rol == "gece_uyanma":
        if dk < kural["taban"] - 1e-9:
            return "uyumsuz"                       # taban altı: dolu alanla çelişir
        return "bekliyor" if kural["bekleyen"] else "uyumlu"
    if rol in ("dis", "ic"):
        gunluk = set(kural["gun"].values()) | {kural["yatir_cik"]}
        if gun is not None and gun[1] <= 3:        # gün 1-3 bölümü: o günlerin değerleri
            izinli = {kural["gun"][g] for g in range(gun[0], gun[1] + 1) if g in kural["gun"]}
            return "uyumlu" if dk in izinli else "uyumsuz"
        if gun is not None and gun[0] >= 13:       # yatır-çık
            return "uyumlu" if dk == kural["yatir_cik"] else "uyumsuz"
        if dk in gunluk:
            return "uyumlu"
        return "bekliyor" if kural["bekleyen"] else "uyumsuz"
    return "uyumlu" if any(abs(dk - v) < 1e-6 for v in kural["izinli"]) else "uyumsuz"


def satir_durumu(durumlar: list[str]) -> str | None:
    if not durumlar:
        return None
    for d in ("uyumsuz", "bekliyor", "uyumlu", "kapsam_disi"):
        if d in durumlar:
            return d
    return "belirsiz"


def plan_denetle(content: dict, k: dict | None = None) -> dict:
    """Bir planın satır sayımları — METİN DÖNMEZ."""
    k = k or kurallar()
    content = content or {}
    gun_satirlari: dict[str, tuple[int, int]] = {}
    for d in content.get("days") or []:
        try:
            aralik = (int(d.get("start")), int(d.get("end") or d.get("start")))
        except (TypeError, ValueError):
            continue
        for s in (d.get("markdown") or "").splitlines():
            if s.strip():
                gun_satirlari.setdefault(s.strip(), aralik)
    sonuc = {"satir": Counter(), "bolum": defaultdict(Counter), "uyumsuz": Counter(),
             "bekliyor": Counter()}
    for satir in (content.get("markdown") or "").splitlines():
        s = satir.strip()
        if not s:
            continue
        bulunan = satir_sayilari(s)
        gun = gun_satirlari.get(s)
        durumlar = [(rol, dk, sayi_durumu(rol, dk, k, gun)) for rol, dk in bulunan]
        sd = satir_durumu([d for _, _, d in durumlar])
        if sd is None:
            continue
        bolum = "gun" if s in gun_satirlari else "genel"
        sonuc["satir"][sd] += 1
        sonuc["bolum"][bolum][sd] += 1
        for rol, dk, d in durumlar:
            if d == "uyumsuz":
                sonuc["uyumsuz"][f"{rol}:{dk:g}"] += 1
            elif d == "bekliyor":
                sonuc["bekliyor"][rol] += 1
    return sonuc


# =============================================================================
# Çalıştırma
# =============================================================================
def _planlari_db() -> list[dict]:
    from sqlalchemy import text
    from api.db.session import engine
    with engine.connect() as c:
        tx = c.begin()
        try:
            rows = c.execute(text("""
              select distinct on (p.baby_id) p.content, b.training_started_at
              from sleep_plans p join babies b on b.id = p.baby_id
              where b.training_started_at is not null
              order by p.baby_id, p.plan_date desc, p.created_at desc""")).all()
        finally:
            tx.rollback()                          # salt okur
    rows = sorted(rows, key=lambda r: str(r[1]))
    return [{"content": r[0] or {}} for r in rows]


def rapor(planlar: list[dict]) -> str:
    k = kurallar()
    satirlar = [f"yontem_tablosu surum {yontem.surum()} — bekleyen alanlar: "
                + ", ".join(yontem.bekleyen_alanlar()), "",
                "plan | tip | bant | uyumlu | uyumsuz | bekliyor | kapsam dışı | belirsiz | uyumsuz (rol:dk ×n)"]
    toplam = Counter(); rol_uyumsuz = Counter(); rol_bekliyor = Counter(); tip_disi = 0
    for i, p in enumerate(planlar, 1):
        ct = p.get("content") or {}
        if (ct.get("type") or "egitim_plani") != "egitim_plani":
            continue
        tip = (ct.get("plan_secimi") or {}).get("tip") or "?"
        bant = (ct.get("yas_bandi") or {}).get("id") or ct.get("bucket") or "?"
        s = plan_denetle(ct, k)
        if tip != yontem.tablo().get("plan_tipi"):
            tip_disi += 1
            satirlar.append(f"B{i:02d} | {tip} | {bant} | — tablo dışı plan tipi (karşılaştırılmadı) —")
            continue
        toplam.update(s["satir"]); rol_uyumsuz.update(s["uyumsuz"]); rol_bekliyor.update(s["bekliyor"])
        uy = ", ".join(f"{a}×{n}" for a, n in s["uyumsuz"].most_common())
        satirlar.append(f"B{i:02d} | {tip} | {bant} | {s['satir']['uyumlu']} | {s['satir']['uyumsuz']} | "
                        f"{s['satir']['bekliyor']} | {s['satir']['kapsam_disi']} | "
                        f"{s['satir']['belirsiz']} | {uy or '-'}")
    satirlar += ["", f"TOPLAM satır: uyumlu {toplam['uyumlu']} · uyumsuz {toplam['uyumsuz']} · "
                 f"İlayda bekliyor {toplam['bekliyor']} · kapsam dışı {toplam['kapsam_disi']} · "
                 f"belirsiz {toplam['belirsiz']} · "
                 f"tablo dışı plan {tip_disi}",
                 "Uyumsuz sayılar (rol:dk): " + (", ".join(f"{a}×{n}" for a, n in rol_uyumsuz.most_common()) or "-"),
                 "İlayda bekleyen sayılar (rol): " + (", ".join(f"{a}×{n}" for a, n in rol_bekliyor.most_common()) or "-")]
    return "\n".join(satirlar)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", help="[{content}, …] dosyası (DB yerine)")
    a = ap.parse_args(argv)
    if a.json:
        planlar = json.loads(Path(a.json).read_text(encoding="utf-8"))
    else:
        planlar = _planlari_db()
    print(rapor(planlar))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
