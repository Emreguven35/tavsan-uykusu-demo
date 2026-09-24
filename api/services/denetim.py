"""
İlayda günlük denetim raporu — dün kaydı olan her beta bebeği için TEK HTML sayfa.

Her bebek için: yaş, bant, eğitim günü/aşama, gerçek kayıtların 24 saatlik
zaman çizelgesi ile uygulamanın verdiği plan YAN YANA (sabah verilen şablon +
gün sonundaki uyarlanmış çizelge), uyarılar ve varsa annenin geri bildirimleri.

KİŞİSEL VERİ YOK: bebek adı, e-posta, doğum tarihi yazılmaz. Bebekler "Bebek 7"
diye numaralanır — numara TÜM gerçek bebeklerin oluşturulma sırasıdır, yani
gün değişse de aynı bebek aynı numarayı taşır. Annenin serbest metninde
bebeğin adı geçerse "[bebek]" ile değiştirilir.

TEST HESAPLARI DAHİL DEĞİL (@example.com ve iki duman-testi alanı).

Çıktı: DENETIM_ROOT/{tarih}.html (prod: /data/denetim, volume). Bağlantı 7 gün
geçerli, JWT_SECRET'tan türetilen HMAC ile imzalı (storage.imzala) — giriş
gerektirmez, İlayda telefondan açabilir.
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import not_, or_
from sqlalchemy.orm import Session

from api.models import Baby, PlanFeedback, SleepLog, SleepPlan, User
from api.services import plan_adapter, plan_service, storage

logger = logging.getLogger("tavsan.denetim")

TEST_ALANLARI = ("@example.com", "@tavsanduman.com", "@tavsansmoke.com")
BAGLANTI_GUN = 7
TZ = plan_adapter.TZ_OFFSET_MIN
KILIT_ID = 776699002                  # notifier 776699001 kullanıyor


def denetim_koku() -> Path:
    ham = (os.getenv("DENETIM_ROOT") or "").strip()
    if ham:
        return Path(ham)
    if Path("/data").is_dir():                           # Railway volume
        return Path("/data/denetim")
    return Path(__file__).resolve().parent.parent.parent / "data" / "denetim"


def dosya_yolu(gun: date) -> Path:
    return denetim_koku() / f"{gun.isoformat()}.html"


def imza_yolu(gun: date) -> str:
    """İmzalanan mantıksal yol — medya yollarıyla karışmasın diye önekli."""
    return f"denetim/{gun.isoformat()}.html"


def imzali_baglanti(gun: date, gun_sayisi: int = BAGLANTI_GUN) -> str:
    from api.config import get_settings
    bitis, imza = storage.imzala(imza_yolu(gun), gun_sayisi * 86400)
    yol = f"/denetim/{gun.isoformat()}.html?exp={bitis}&sig={imza}"
    taban = (get_settings().public_base_url
             or (f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
                 if os.getenv("RAILWAY_PUBLIC_DOMAIN") else ""))
    return f"{taban}{yol}"


# ---------------------------------------------------------------------------
# Veri
# ---------------------------------------------------------------------------
def _utc(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _gercek_kullanici():
    return not_(or_(*[User.email.like("%" + a) for a in TEST_ALANLARI]))


def bebek_numaralari(db: Session) -> dict:
    """baby_id → kalıcı numara (gerçek bebeklerin oluşturulma sırası)."""
    satirlar = (db.query(Baby.id).join(User, User.id == Baby.user_id)
                .filter(_gercek_kullanici())
                .order_by(Baby.created_at, Baby.id).all())
    return {satir[0]: i for i, satir in enumerate(satirlar, 1)}


def _dk(t: datetime, gun: date) -> float:
    """UTC zaman → o yerel günün dakikası (gün dışına taşabilir)."""
    yerel = _utc(t) + timedelta(minutes=TZ)
    return (yerel - datetime(gun.year, gun.month, gun.day,
                             tzinfo=timezone.utc)).total_seconds() / 60


def _saat(dk: float) -> str:
    dk = int(round(dk)) % 1440
    return f"{dk // 60:02d}:{dk % 60:02d}"


def _yas_metni(baby: Baby) -> tuple[str, dict | None, dict | None]:
    from api.services.geri_bildirim import yas_bant
    yas, bant = yas_bant(baby)
    if yas is None:
        return "yaşı bilinmiyor", None, None
    ay = yas["duzeltilmis_ay"]
    return (f"{int(ay)} aylık" if ay >= 1 else f"{int(ay * 30.44)} günlük"), yas, bant


def _metni_temizle(metin: str | None, baby: Baby) -> str:
    if not metin:
        return ""
    ad = (baby.name or "").strip()
    if len(ad) >= 2:
        metin = re.sub(re.escape(ad), "[bebek]", metin, flags=re.IGNORECASE)
    metin = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[e-posta]", metin)
    metin = re.sub(r"\+?\d[\d\s-]{8,}\d", "[telefon]", metin)
    return metin


UYKU_TIPLERI = ("sleep", "nap", "sekerleme")
ESKI_SURUM_BUILD = 20                 # bu build'in altı "eski sürüm" sayılır


def surum_build(surum: str | None) -> int | None:
    """"1.0.0+21" → 21. Build numarası yoksa None."""
    if not surum or "+" not in surum:
        return None
    try:
        return int(surum.rsplit("+", 1)[1])
    except ValueError:
        return None


def rapor(db: Session, gun: date) -> dict:
    """{"bolumler", "kayitsiz": [numara], "ozet": {...}}."""
    bolumler = rapor_verisi(db, gun)
    numara = bebek_numaralari(db)
    detayli = {b["no"] for b in bolumler}
    kayitsiz = sorted(n for n in numara.values() if n not in detayli)
    # Anne sürümleri — gerçek bebeği olan her kullanıcı bir kez sayılır.
    anneler = {u.id: u for u in (db.query(User).join(Baby, Baby.user_id == User.id)
                                 .filter(Baby.id.in_(list(numara))).all()
                                 if numara else [])}
    buildler = [surum_build(u.app_version) for u in anneler.values()]
    ozet = {
        "kayitli_bebek": len(bolumler),
        "sabah_uyanisi_girilen": sum(1 for b in bolumler if b["sabah_girildi"]),
        "cakisan_sifir_sureli": sum(b["cakisan_sifir"] for b in bolumler),
        "geri_bildirim": sum(len(b["geri_bildirim"]) for b in bolumler),
        "anne": len(anneler),
        "eski_surum": sum(1 for x in buildler if x is not None and x < ESKI_SURUM_BUILD),
        "surum_bilinmiyor": sum(1 for x in buildler if x is None),
    }
    return {"bolumler": bolumler, "kayitsiz": kayitsiz, "ozet": ozet}


def rapor_verisi(db: Session, gun: date) -> list[dict]:
    """Dün (yerel gün) en az bir UYKU kaydı olan gerçek bebeklerin bölümleri."""
    from api.routers.logs import gun_filtresi
    from api.services import education
    from api.models.education_video import ASAMA_ETIKETLERI

    numara = bebek_numaralari(db)
    # Ayrıntılı bölüm yalnız UYKU kaydı olan bebeklere (beslenme/uyanma
    # tek başına bir gün çizelgesi kurmaz). Diğerleri tek satırda listelenir.
    kayitli = {bid for (bid,) in (db.query(SleepLog.baby_id)
                                  .filter(gun_filtresi(gun),
                                          SleepLog.type.in_(UYKU_TIPLERI))
                                  .distinct().all())}
    bolumler = []
    for baby in (db.query(Baby).filter(Baby.id.in_(kayitli)).all()
                 if kayitli else []):
        if baby.id not in numara:                      # test hesabı
            continue
        kayitlar = (db.query(SleepLog)
                    .filter(SleepLog.baby_id == baby.id, gun_filtresi(gun))
                    .order_by(SleepLog.started_at).all())
        plan = (db.query(SleepPlan)
                .filter(SleepPlan.baby_id == baby.id, SleepPlan.plan_date <= gun)
                .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc())
                .first())
        icerik = (plan.content or {}) if plan is not None else {}
        adapt = icerik.get("adaptation") or {}
        bas = datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc) \
            - timedelta(minutes=TZ)
        geri = (db.query(PlanFeedback)
                .filter(PlanFeedback.baby_id == baby.id,
                        PlanFeedback.created_at >= bas,
                        PlanFeedback.created_at < bas + timedelta(days=1))
                .order_by(PlanFeedback.created_at).all())
        yas_metni, yas, bant = _yas_metni(baby)
        asama = education.asama_belirle(baby, gun)
        # Denetim B3: bekleme/yenidoğan planında eğitim günü YOK.
        egitim_aktif = (plan_service.tip_turet(icerik) == plan_service.TYPE_EGITIM
                        if plan is not None
                        else plan_service.egitim_aktif_mi(db, baby))
        egitim_gunu = (plan_adapter.egitim_gunu(baby.training_started_at, gun)
                       if egitim_aktif else None)

        uyarilar = []
        for u in list(icerik.get("uyarilar") or []) + list(adapt.get("uyarilar") or []):
            metin = u if isinstance(u, str) else (u.get("metin") or u.get("baslik")
                                                   or u.get("text") or str(u))
            if metin and metin not in uyarilar:
                uyarilar.append(_metni_temizle(metin, baby))

        o_gunun_plani = plan is not None and plan.plan_date == gun
        anne = db.get(User, baby.user_id)
        bolumler.append({
            "no": numara[baby.id],
            "surum": getattr(anne, "app_version", None),
            "sabah_girildi": bool(o_gunun_plani and adapt.get("sabah_uyanis_kaynak")
                                  not in (None, "varsayilan")),
            "cakisan_sifir": (sum(1 for y in (adapt.get("yok_sayilan_kayitlar") or [])
                                  if isinstance(y, dict)
                                  and y.get("kod") in ("cakisma", "sifir_sure"))
                              if o_gunun_plani else 0),
            "yas_metni": yas_metni,
            "bant": (bant or {}).get("ad"),
            "egitim_gunu": egitim_gunu,
            "asama": ASAMA_ETIKETLERI.get(asama["kod"], asama["kod"]),
            "asama_kaynak": asama["kaynak"],
            "plan_tipi": icerik.get("type"),
            "plan_tarihi": plan.plan_date.isoformat() if plan else None,
            "sablon": icerik.get("schedule_template") or [],
            "cizelge": icerik.get("schedule") or [],
            "kayitlar": [{
                "type": r.type,
                "bas": _dk(r.started_at, gun),
                "bit": _dk(r.ended_at, gun) if r.ended_at else None,
            } for r in kayitlar],
            "uyarilar": uyarilar,
            "yok_sayilan": [_metni_temizle(
                (y.get("sebep") if isinstance(y, dict) else str(y)), baby)
                for y in (adapt.get("yok_sayilan_kayitlar") or [])][:10],
            "geri_bildirim": [{
                "saat": _saat(_dk(g.created_at, gun)),
                "blok": g.block_key, "blok_saati": g.block_time,
                "secenek": g.secenek,
                "metin": _metni_temizle(g.metin, baby),
            } for g in geri],
        })
    bolumler.sort(key=lambda b: b["no"])
    return bolumler


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
TIP_ADI = {"sleep": "Uyku", "nap": "Uyku", "sekerleme": "Uyku",
           "night_wake": "Gece uyanması", "feed": "Beslenme", "wake": "Uyanış",
           "nap_skipped": "Uyku atlandı"}
BLOK_ADI = {"wake": "Uyanış", "bedtime": "Gece yatışı", "night": "Gece uykusu"}


def _yuzde(dk: float) -> float:
    return max(0.0, min(100.0, dk / 1440 * 100))


def _serit(ogeler: list[tuple[float, float | None, str, str]]) -> str:
    """(baş_dk, bit_dk|None, css sınıfı, başlık) → 24 saatlik şerit."""
    parcalar = []
    for bas, bit, sinif, baslik in ogeler:
        if bit is None or bit - bas < 6:               # anlık olay → işaret
            if not (-1 <= bas <= 1441):
                continue
            parcalar.append(f'<span class="nokta {sinif}" style="left:{_yuzde(bas):.2f}%"'
                            f' title="{html.escape(baslik)}"></span>')
            continue
        if bit < 0 or bas > 1440:
            continue
        sol, sag = _yuzde(bas), _yuzde(bit)
        parcalar.append(f'<span class="bar {sinif}" style="left:{sol:.2f}%;'
                        f'width:{max(sag - sol, 0.4):.2f}%" title="{html.escape(baslik)}"></span>')
    cizgiler = "".join(f'<span class="saat" style="left:{h / 24 * 100:.3f}%"></span>'
                       for h in range(3, 24, 3))
    return f'<div class="serit">{cizgiler}{"".join(parcalar)}</div>'


def _blok_ogeleri(bloklar: list[dict], sinif: str):
    out = []
    for b in bloklar:
        bas, bit = b.get("start_minute"), b.get("end_minute")
        if bas is None:
            continue
        if bit is not None and bit < bas:              # gece yarısını aşan
            bit += 1440
        ad = b.get("title") or BLOK_ADI.get(b.get("key"), b.get("key"))
        out.append((float(bas), None if bit in (None, bas) else float(bit),
                    f'{sinif} t-{b.get("type", "")}',
                    f'{ad} {b.get("time", "")}–{b.get("end", "")}'))
    return out


def _kayit_ogeleri(kayitlar: list[dict]):
    out = []
    for k in kayitlar:
        sinif = {"night_wake": "k-uyanma", "feed": "k-besleme",
                 "nap_skipped": "k-atlandi"}.get(k["type"], "k-uyku")
        bit = k["bit"]
        if k["type"] in ("sleep", "nap", "sekerleme") and bit is None:
            sinif += " acik"
            bit = min(k["bas"] + 60, 1440)             # açık kayıt: 1 sa göster
        baslik = (f'{TIP_ADI.get(k["type"], k["type"])} {_saat(k["bas"])}'
                  + (f'–{_saat(k["bit"])}' if k["bit"] is not None else " (açık)"))
        out.append((k["bas"], bit, sinif, baslik))
    return out


def _karsilastirma(b: dict) -> str:
    """Plan blokları ile gerçek uykuların tablo hâli."""
    uykular = [k for k in b["kayitlar"] if k["type"] in ("sleep", "nap", "sekerleme")]
    satirlar = []
    for blok in b["cizelge"]:
        bas = blok.get("start_minute")
        if bas is None:
            continue
        yakin = min(uykular, key=lambda k: abs(k["bas"] - bas), default=None)
        if blok.get("type") in ("nap", "bedtime") and yakin and abs(yakin["bas"] - bas) <= 120:
            fark = int(round(yakin["bas"] - bas))
            gercek = (f'{_saat(yakin["bas"])}'
                      + (f'–{_saat(yakin["bit"])}' if yakin["bit"] is not None else " (açık)"))
            fark_m = f'{fark:+d} dk'
        else:
            gercek, fark_m = "—", ""
        sablon = next((s for s in b["sablon"] if s.get("key") == blok.get("key")), {})
        kaynak = "kayıttan" if blok.get("kaynak") == "kayit" else ""
        satirlar.append(
            f'<tr><td>{html.escape(str(blok.get("title") or blok.get("key")))}</td>'
            f'<td>{html.escape(str(sablon.get("time", "—")))}</td>'
            f'<td>{html.escape(str(blok.get("time", "")))}'
            f'{"–" + html.escape(str(blok.get("end"))) if blok.get("end") and blok.get("end") != blok.get("time") else ""}'
            f' <small>{kaynak}</small></td>'
            f'<td>{gercek}</td><td>{fark_m}</td></tr>')
    if not satirlar:
        return '<p class="bos">Bu gün için çizelge yok (yenidoğan rehberi ya da plan üretilmemiş).</p>'
    return ('<table><thead><tr><th>Blok</th><th>Sabah planı</th><th>Gün sonu plan</th>'
            '<th>Gerçek</th><th>Fark</th></tr></thead><tbody>'
            + "".join(satirlar) + "</tbody></table>")


CSS = """
:root{--bg:#faf8f5;--kart:#fff;--yazi:#2b2b2b;--soluk:#777;--cizgi:#e6e1da;
--uyku:#5b7fbf;--sablon:#c9b99a;--plan:#8a6fb0;--uyanma:#d9534f;--besleme:#4caf7d}
@media (prefers-color-scheme:dark){:root{--bg:#1c1b1a;--kart:#262523;--yazi:#eee;
--soluk:#aaa;--cizgi:#3a3835}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--yazi);
font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;padding:16px}
main{max-width:900px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}
.alt{color:var(--soluk);margin:0 0 18px}
section{background:var(--kart);border:1px solid var(--cizgi);border-radius:12px;
padding:14px 16px;margin:0 0 16px}
h2{font-size:18px;margin:0 0 2px}.etiket{color:var(--soluk);font-size:13px;margin-bottom:10px}
.satir{display:grid;grid-template-columns:92px 1fr;gap:8px;align-items:center;margin:4px 0}
.satir b{font-size:12px;color:var(--soluk);font-weight:600}
.serit{position:relative;height:18px;background:var(--bg);border-radius:4px;overflow:hidden}
.saat{position:absolute;top:0;bottom:0;border-left:1px dashed var(--cizgi)}
.bar{position:absolute;top:3px;bottom:3px;border-radius:3px}
.nokta{position:absolute;top:3px;width:3px;height:12px;margin-left:-1px;border-radius:1px}
.sablon{background:var(--sablon)}.plan{background:var(--plan)}
.t-wake{background:transparent;border-left:3px solid currentColor}
.k-uyku{background:var(--uyku)}.k-uyku.acik{opacity:.45;background:repeating-linear-gradient(45deg,var(--uyku),var(--uyku) 4px,transparent 4px,transparent 8px)}
.k-uyanma{background:var(--uyanma)}.k-besleme{background:var(--besleme)}.k-atlandi{background:var(--soluk)}
.olcek{display:grid;grid-template-columns:92px 1fr;gap:8px;font-size:11px;color:var(--soluk)}
.olcek div{display:flex;justify-content:space-between}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--cizgi)}
th{color:var(--soluk);font-weight:600}td small{color:var(--soluk)}
.kutu{margin-top:10px;font-size:13px}.kutu ul{margin:4px 0 0;padding-left:18px}
.geri{border-left:3px solid var(--plan);padding-left:10px}
.ozet{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:0 0 12px}
.ozet div{background:var(--kart);border:1px solid var(--cizgi);border-radius:10px;padding:8px 10px}
.ozet b{display:block;font-size:22px}.ozet span{font-size:12px;color:var(--soluk)}
.kayitsiz{font-size:13px;color:var(--soluk);margin:0 0 16px}
.surum{font-size:12px;font-weight:400;color:var(--soluk);margin-left:8px}
.surum.eski{color:var(--uyanma);font-weight:600}
.bos{color:var(--soluk)}.lejant{font-size:12px;color:var(--soluk);margin-bottom:14px}
.lejant span{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:-1px}
@media (max-width:560px){.satir,.olcek{grid-template-columns:70px 1fr}table{font-size:12px}}
"""


def html_uret(gun: date, veri: dict) -> str:
    bolumler, kayitsiz, ozet = veri["bolumler"], veri["kayitsiz"], veri["ozet"]
    olcek = ('<div class="olcek"><span></span><div>'
             + "".join(f"<span>{h:02d}</span>" for h in range(0, 25, 3))
             + "</div></div>")
    parcalar = []
    for b in bolumler:
        egitim = (f'eğitimin {b["egitim_gunu"]}. günü' if b["egitim_gunu"]
                  else "eğitim başlamamış")
        ust = " · ".join(x for x in [
            b["bant"] and f'bant {b["bant"]}', egitim,
            f'aşama: {b["asama"]}' + (" (anne beyanı)" if b["asama_kaynak"] == "anne" else ""),
            b["plan_tipi"] and f'plan: {b["plan_tipi"]}'] if x)
        kutular = []
        if b["uyarilar"]:
            kutular.append('<div class="kutu"><b>Uygulamanın uyarıları</b><ul>'
                           + "".join(f"<li>{html.escape(u)}</li>" for u in b["uyarilar"])
                           + "</ul></div>")
        if b["yok_sayilan"]:
            kutular.append('<div class="kutu"><b>Hesapta yok sayılan kayıtlar</b><ul>'
                           + "".join(f"<li>{html.escape(u)}</li>" for u in b["yok_sayilan"])
                           + "</ul></div>")
        if b["geri_bildirim"]:
            kutular.append('<div class="kutu geri"><b>Annenin geri bildirimleri</b><ul>'
                           + "".join(
                               f'<li>{g["saat"]} — <b>{html.escape(str(g["blok"]))}</b>'
                               f'{" (" + html.escape(g["blok_saati"]) + ")" if g["blok_saati"] else ""}'
                               f'{": " + html.escape(g["secenek"]) if g["secenek"] else ""}'
                               f'{" — “" + html.escape(g["metin"]) + "”" if g["metin"] else ""}</li>'
                               for g in b["geri_bildirim"]) + "</ul></div>")
        surum = (f'uygulama {b["surum"]}' if b["surum"] else "uygulama sürümü bilinmiyor")
        eski = (surum_build(b["surum"]) or ESKI_SURUM_BUILD) < ESKI_SURUM_BUILD
        parcalar.append(
            f'<section><h2>Bebek {b["no"]}, {html.escape(b["yas_metni"])}'
            f'<span class="surum{" eski" if eski else ""}">{html.escape(surum)}</span></h2>'
            f'<div class="etiket">{html.escape(ust)}</div>'
            f'{olcek}'
            f'<div class="satir"><b>Sabah planı</b>{_serit(_blok_ogeleri(b["sablon"], "sablon"))}</div>'
            f'<div class="satir"><b>Gün sonu plan</b>{_serit(_blok_ogeleri(b["cizelge"], "plan"))}</div>'
            f'<div class="satir"><b>Gerçek</b>{_serit(_kayit_ogeleri(b["kayitlar"]))}</div>'
            f'{_karsilastirma(b)}{"".join(kutular)}</section>')
    govde = "".join(parcalar) or '<p class="bos">Dün uyku kaydı olan beta bebeği yok.</p>'
    kayitsiz_satir = ('<p class="kayitsiz"><b>Kayıt girilmemiş:</b> '
                      + ", ".join(f"Bebek {n}" for n in kayitsiz) + "</p>"
                      if kayitsiz else "")
    bilinmiyor = (f' · {ozet["surum_bilinmiyor"]}/{ozet["anne"]} sürümü bilinmiyor'
                  if ozet["surum_bilinmiyor"] else "")
    ozet_html = (
        '<div class="ozet">'
        f'<div><b>{ozet["kayitli_bebek"]}</b><span>kayıtlı bebek</span></div>'
        f'<div><b>{ozet["sabah_uyanisi_girilen"]}</b><span>sabah uyanışı girilmiş</span></div>'
        f'<div><b>{ozet["cakisan_sifir_sureli"]}</b><span>çakışan / sıfır süreli kayıt</span></div>'
        f'<div><b>{ozet["geri_bildirim"]}</b><span>anne geri bildirimi</span></div>'
        f'<div><b>{ozet["eski_surum"]}</b><span>eski sürümde anne '
        f'(build &lt; {ESKI_SURUM_BUILD}){bilinmiyor}</span></div>'
        '</div>')
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Günlük denetim {gun.isoformat()}</title><style>{CSS}</style></head>
<body><main><h1>Günlük denetim — {gun.strftime("%d.%m.%Y")}</h1>
<p class="alt">saatler Türkiye saati · kişisel veri içermez</p>
{ozet_html}{kayitsiz_satir}
<div class="lejant"><span style="background:var(--sablon)"></span>sabah planı
<span style="background:var(--plan)"></span>gün sonu plan
<span style="background:var(--uyku)"></span>uyku
<span style="background:var(--uyanma)"></span>gece uyanması
<span style="background:var(--besleme)"></span>beslenme</div>
{govde}</main></body></html>"""


# ---------------------------------------------------------------------------
# Üretim + zamanlanmış iş
# ---------------------------------------------------------------------------
def rapor_yaz(db: Session, gun: date) -> tuple[Path, str, int]:
    """(dosya, imzalı bağlantı, bebek sayısı)."""
    veri = rapor(db, gun)
    yol = dosya_yolu(gun)
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(".tmp")
    gecici.write_text(html_uret(gun, veri), encoding="utf-8")
    gecici.replace(yol)
    return yol, imzali_baglanti(gun), len(veri["bolumler"])


def dun() -> date:
    from api.zaman import bugun_tr
    return bugun_tr() - timedelta(days=1)


def _tek_worker(is_fn) -> None:
    """Çok worker'da işi YALNIZ BİRİ koşsun (Postgres advisory lock); kilidi
    alamayan sessizce çıkar. SQLite'ta (yerel/test) doğrudan koşar."""
    from sqlalchemy import text
    from api.db.session import engine

    conn = None
    try:
        if engine.dialect.name == "postgresql":
            conn = engine.connect()
            if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                {"k": KILIT_ID}).scalar():
                conn.close()
                return
        is_fn()
    except Exception:
        logger.exception("Denetim işi başarısız (%s)", getattr(is_fn, "__name__", is_fn))
    finally:
        if conn is not None:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": KILIT_ID})
            finally:
                conn.close()


def _uret() -> tuple[date, str, int]:
    from api.db.session import SessionLocal
    db = SessionLocal()
    try:
        gun = dun()
        _yol, baglanti, n = rapor_yaz(db, gun)
    finally:
        db.close()
    logger.info("Günlük denetim hazır: %s · %d bebek · %s", gun, n, baglanti)
    return gun, baglanti, n


def gunluk_is() -> None:
    """APScheduler işi — her sabah 08:00 (TR): dünün raporu."""
    _tek_worker(_uret)


def alicilar() -> list[str]:
    return [a.strip() for a in (os.getenv("DENETIM_ALICILARI") or "").split(",")
            if "@" in a]


def eposta_konusu(gun: date) -> str:
    return f"Tavşan Uykusu — günlük denetim {gun.strftime('%d.%m.%Y')}"


def eposta_gonder(gun: date, baglanti: str, bebek_sayisi: int) -> list[dict]:
    """Bağlantıyı DENETIM_ALICILARI'na gönder (mevcut mailer). Dönen: sonuçlar."""
    from api.services import mailer
    tarih = gun.strftime("%d.%m.%Y")
    konu = eposta_konusu(gun)
    metin = (f"Günaydın,\n\n{tarih} günlük denetim raporu hazır "
             f"({bebek_sayisi} bebek).\n\n{baglanti}\n\n"
             f"Bağlantı {BAGLANTI_GUN} gün geçerlidir; giriş gerektirmez, "
             f"kişisel veri içermez.\n")
    html_govde = (f"<p>Günaydın,</p><p>{tarih} günlük denetim raporu hazır "
                  f"({bebek_sayisi} bebek).</p>"
                  f'<p><a href="{html.escape(baglanti)}">Raporu aç</a></p>'
                  f'<p style="color:#777;font-size:12px">Bağlantı {BAGLANTI_GUN} '
                  f"gün geçerlidir; giriş gerektirmez, kişisel veri içermez.</p>")
    return [dict(mailer.send_email(alici, konu, metin, html_govde), alici=alici)
            for alici in alicilar()]


def _eposta_isi() -> None:
    gun = dun()
    if not alicilar():
        logger.info("DENETIM_ALICILARI boş — denetim e-postası gönderilmedi")
        return
    if not dosya_yolu(gun).exists():         # 08:00 işi koşmadıysa şimdi üret
        _uret()
    from api.db.session import SessionLocal
    db = SessionLocal()
    try:
        n = len(rapor_verisi(db, gun))
    finally:
        db.close()
    sonuclar = eposta_gonder(gun, imzali_baglanti(gun), n)
    logger.info("Denetim e-postası: %s", [(r["alici"].split("@")[1], r.get("ok"),
                                          r.get("provider")) for r in sonuclar])


def eposta_isi() -> None:
    """APScheduler işi — her sabah 08:05 (TR): bağlantıyı e-postayla gönder."""
    _tek_worker(_eposta_isi)
