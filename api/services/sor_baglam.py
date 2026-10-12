"""
Sor bağlamına ZAMAN satırı — SOR_BAGLAM_V2 bayrağıyla (2026-10-12).

NEDEN: prod incelemesi (2026-10-12) — bağlamda yalnız "eğitim başlangıcı
2026-10-09" vardı; bugünün tarihi, saat ve eğitimin kaçıncı günü yoktu. Model
gece 02:30'da "gündüz mü gece mi, eğitimin kaçıncı günü?" diye soruyor, bekleme
sürelerini her turda farklı uyduruyordu.

Bu modül yalnız /chat'e EK satırlar üretir; build_baby_context'e dokunmaz (o
fonksiyonun çıktısı bayrak kapalıyken birebir aynı kalmalı).

GÜN NUMARASI YENİDEN HESAPLANMAZ: uygulamanın Eğitim sekmesi günü
GET /plans/today → adaptation.egitim_gunu'dan okur; o değer
plan_adapter.egitim_gunu(training_started_at, bugun_tr()) — TÜRKİYE TAKVİM
GÜNÜDÜR. Yani gece 02:30'da uygulama YENİ günü gösterir (ör. "Gün 3").

GECE KURALI: gece yarısından sabah uyanışına kadar olan saatler ÖNCEKİ GÜNÜN
GECESİDİR — "Eğitimin 2. gününün gecesi; sabah 3. gün başlıyor". Satır
uygulamanın gösterdiği sayıyı da söyler ki anne "ekranda 3 yazıyor" dediğinde
model çelişmesin. Sabah uyanışı: bugünün kaydı (motorun K5/K10 kuralı,
plan_adapter.sabah_uyanisi), kayıt yoksa planın uyanış saati.

Gündüz/gece etiketi uygulamanın K19 sınırlarıyla: 06:00–19:00 gündüz.

Plan YALNIZ OKUNUR (ensure_today_plan çağrılmaz — o yazar).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from api.config import _bayrak
from api.models import Baby, SleepLog, SleepPlan
from api.services import plan_adapter

logger = logging.getLogger("tavsan.sor_baglam")

BAYRAK = "SOR_BAGLAM_V2"

AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
GUNLER = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")

# Plan satırı uzunluk sınırları (bağlam bir ektir, planın kopyası değil).
SATIR_MAX = 220
BEKLEME_SATIR_MAX = 3
GECE_SATIR_MAX = 2

# Sistem talimatı eki — bayrak açıkken SYSTEM_PROMPT'un ARDINDAN ayrı blok
# olarak gider (önbelleğe alınan ilk blok değişmez).
SISTEM_EKI = """\
EK KURALLAR (sohbet bağlamı ve güvenlik):
E1. Gün ve saat için BEBEK VERİSİ'ndeki "Şu an" ve "Eğitim günü" satırlarını esas al. \
Annenin bu sohbette zaten verdiği bilgiyi (eğitimin günü, saat, gece/gündüz, beklediği süre) \
TEKRAR SORMA; önceki mesajlarda yazdıysa onu kullan. Bekleme sürelerini ve eğitim adımını \
"Plandaki adım" satırıyla tutarlı ver; plan satırı varken farklı süre uydurma.
E2. Önceki cevaplarında verdiğin motivasyon ve teselli cümlelerini (ör. "ilk gün 45 dakika \
ağlayan çocuk…", "ağlama her gün azalır") tekrarlama; aynı sohbette bir kez yeter.
E3. GÜVENLİK — Naylon poşet YALNIZ pencereyi karartmak için, cama yapıştırılmış olarak \
önerilebilir. Bebeğin yatağının yakınında, asılı ya da bölme/paravan olarak poşet, naylon \
ya da bant ÖNERME; paravanı, perdeyi ya da bölmeyi bantla, yapışkanla sabitlemeyi de \
önerme. Odayı bölmek için perde, kumaş paravan ya da sabit duran bir mobilya öner; \
bölmenin yatağa uzanamayacak mesafede ve devrilmeyecek biçimde durmasını söyle.
E4. GÜVENLİK — Ses kısılması, kusma, nefes almada zorlanma, ateş gibi BEDENSEL belirtileri \
eğitimin olumlu işareti ya da ilerleme belirtisi olarak YORUMLAMA. Soruda böyle bir belirti \
geçiyorsa cevaba MUTLAKA şu anlamda bir cümle ekle: belirti sürerse ya da sizi \
endişelendiriyorsa çocuk doktoruna danışın.
E5. Uyku kaydı eksikse bunu cevabın konusu yapma; planın saatlerine göre tam cevap ver. \
Kayıt girmenin programı kişiselleştireceğini bir sohbette en fazla bir kez, tek cümleyle \
hatırlatabilirsin. "Kayıt girilmediği için göremiyorum / bilemiyorum" gibi cümle kurma."""


def acik_mi() -> bool:
    """Her çağrıda env'den okunur (önbelleksiz) — bkz. hazirlik.acik_mi."""
    return _bayrak(BAYRAK)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def _ss(dk: int) -> str:
    dk = int(dk) % (24 * 60)
    return f"{dk // 60:02d}:{dk % 60:02d}"


def _tarih_metni(gun: date) -> str:
    return f"{gun.day} {AYLAR[gun.month - 1]} {gun.year} {GUNLER[gun.weekday()]}"


def gece_mi(dk: int) -> bool:
    """Uygulamanın K19 sınırları: 06:00 öncesi ve 19:00 sonrası gece."""
    return dk < plan_adapter.GUNDUZ_PENCERE_BAS or dk >= plan_adapter.GUNDUZ_PENCERE_BIT


def _plan_oku(db: Session, baby: Baby, gun: date) -> SleepPlan | None:
    """Bugünün planı, yoksa en güncel plan — SALT OKUMA."""
    plan = (db.query(SleepPlan)
            .filter(SleepPlan.baby_id == baby.id, SleepPlan.plan_date == gun)
            .order_by(SleepPlan.created_at.desc()).first())
    if plan is not None:
        return plan
    return (db.query(SleepPlan).filter(SleepPlan.baby_id == baby.id)
            .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc())
            .first())


def _plan_uyanis_dk(content: dict) -> int | None:
    for b in plan_adapter.normalize_schedule(content.get("schedule")):
        if b.get("type") == "wake":
            if b.get("start_minute") is not None:
                return int(b["start_minute"])
            m = re.match(r"^(\d{1,2}):(\d{2})$", str(b.get("time") or ""))
            if m:
                return int(m.group(1)) * 60 + int(m.group(2))
    return None


def _sabah_uyanisi(db: Session, baby: Baby, gun: date, hedef: int,
                   simdi_dk: int) -> tuple[int, str]:
    """(uyanış dakikası, kaynak) — bugünün kaydı varsa motorun kuralıyla, yoksa
    planın saati. Motor hata verirse plan saatine düşülür (bağlam bir ektir)."""
    try:
        bas = datetime.combine(gun - timedelta(days=1), datetime.min.time(),
                               tzinfo=timezone.utc)
        logs = (db.query(SleepLog)
                .filter(SleepLog.baby_id == baby.id, SleepLog.started_at >= bas)
                .order_by(SleepLog.started_at).all())
        kayitlar = plan_adapter.gun_kayitlari(logs, gun, hedef,
                                              now_minute=simdi_dk)
        s = plan_adapter.sabah_uyanisi(kayitlar, hedef)
        if s.get("kaynak") and s["kaynak"] != "varsayilan":
            return int(s["minute"]), "kayıt"
    except Exception:
        logger.warning("Sabah uyanışı kayıttan çözülemedi (baby=%s)", baby.id,
                       exc_info=True)
    return hedef, "plan"


def _temiz(satir: str) -> str:
    s = re.sub(r"^[#>\-\*\s]+", "", satir)
    s = re.sub(r"[*_`]+", "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    if s.endswith(":"):                      # içeriksiz alt başlık ("Gece beslenme:")
        return ""
    return s if len(s) <= SATIR_MAX else s[:SATIR_MAX].rsplit(" ", 1)[0] + "…"


def _gun_bolumu(content: dict, gun_no: int) -> dict | None:
    days = content.get("days")
    if not isinstance(days, list) or not days:
        from engine import plan_gunleri
        days = plan_gunleri.days_from_content(content)
    for d in days or []:
        try:
            if int(d.get("start")) <= gun_no <= int(d.get("end") or d.get("start")):
                return d
        except (TypeError, ValueError):
            continue
    return None


def _bekleme_satirlari(markdown: str) -> list[str]:
    out: list[str] = []
    for ham in (markdown or "").splitlines()[1:]:          # başlık satırı düşer
        if re.search(r"bekle|dakika|\bdk\b", ham, re.IGNORECASE):
            s = _temiz(ham)
            if s and s not in out:
                out.append(s)
        if len(out) >= BEKLEME_SATIR_MAX:
            break
    return out


def _gece_uyanma_satirlari(markdown: str) -> list[str]:
    """Plan metnindeki "Gece Uyanma…" bölümünün İLK PARAGRAFI (ardından gelen
    gece beslenme listesi vb. alınmaz)."""
    satirlar = (markdown or "").splitlines()
    for i, s in enumerate(satirlar):
        if re.match(r"^\s*#{1,6}\s*.*gece\s+uyanma", s, re.IGNORECASE):
            out: list[str] = []
            for t in satirlar[i + 1:]:
                if re.match(r"^\s*#{1,6}\s", t) or (out and not t.strip()):
                    break
                t = _temiz(t)
                if t:
                    out.append(t)
                if len(out) >= GECE_SATIR_MAX:
                    break
            return out
    return []


def _plan_adim_satiri(content: dict, gun_no: int, gece: bool) -> str | None:
    """Uygulamanın Eğitim sekmesinde o gün gösterdiği bölümden kısa satır."""
    d = _gun_bolumu(content, gun_no)
    parcalar: list[str] = []
    if d is not None:
        bas, bit = d.get("start"), d.get("end") or d.get("start")
        aralik = f"Gün {bas}" if bas == bit else f"Gün {bas}–{bit}"
        etiket = (d.get("label") or "").strip()
        parcalar.append(f"Plandaki adım ({aralik}{': ' + etiket if etiket else ''})")
        if d.get("position"):
            parcalar.append(f"pozisyon: {d['position']}")
        bekleme = _bekleme_satirlari(d.get("markdown") or "")
        if bekleme:
            parcalar.append("bekleme: " + " / ".join(bekleme))
    if gece:
        gu = _gece_uyanma_satirlari(content.get("markdown") or "")
        if gu:
            parcalar.append("gece uyanmaları (plan): " + " / ".join(gu))
    if not parcalar:
        return None
    return "; ".join(parcalar) + "."


# ---------------------------------------------------------------------------
# Ana giriş
# ---------------------------------------------------------------------------
def gun_durumu(db: Session, baby: Baby, simdi: datetime | None = None) -> dict:
    """Sorulan anın eğitim durumu — zaman satırı ve Sor kural bloğu (sor_kural)
    AYNI hesabı kullanır. Plan yalnız okunur.

    Dönen: {simdi, gun, dk, gece, plan, content, tip, durum, app_gun, etkin_gun,
            sabah_once, toplam, uyanis, kaynak}
      durum: "yenidogan" | "baslamadi" | "tamamlandi" | "egitim"
      etkin_gun: gecesi sayılan gün (gece yarısından sabah uyanışına kadar önceki gün)."""
    from api.services import plan_service
    from api.zaman import simdi_tr

    simdi = simdi or simdi_tr()
    gun = simdi.date()
    dk = simdi.hour * 60 + simdi.minute
    plan = _plan_oku(db, baby, gun)
    content = (plan.content or {}) if plan is not None else {}
    tip = plan_service.tip_turet(content) if plan is not None else None
    d = {"simdi": simdi, "gun": gun, "dk": dk, "gece": gece_mi(dk), "plan": plan,
         "content": content, "tip": tip, "app_gun": None, "etkin_gun": None,
         "sabah_once": False, "toplam": None, "uyanis": None, "kaynak": None}
    if tip == plan_service.TYPE_YENIDOGAN:
        return dict(d, durum="yenidogan")
    if tip == plan_service.TYPE_BEKLEME or baby.training_started_at is None:
        return dict(d, durum="baslamadi")

    # Gün numarası: UYGULAMANIN gösterdiği (TR takvim günü)
    app_gun = plan_adapter.egitim_gunu(baby.training_started_at, gun)
    toplam = None
    try:
        toplam = int((content.get("plan_secimi") or {}).get("gunler") or 0) or None
    except (TypeError, ValueError):
        toplam = None
    toplam = toplam or 13

    hedef = _plan_uyanis_dk(content) or plan_adapter.GUNDUZ_PENCERE_BAS
    uyanis, kaynak = (hedef, "plan")
    if dk < 12 * 60:
        uyanis, kaynak = _sabah_uyanisi(db, baby, gun, hedef, dk)
    sabah_once = dk < 12 * 60 and dk < uyanis
    etkin_gun = app_gun - 1 if sabah_once else app_gun     # gecesi sayılan gün
    d.update(app_gun=app_gun, etkin_gun=etkin_gun, sabah_once=sabah_once,
             toplam=toplam, uyanis=uyanis, kaynak=kaynak)
    if baby.training_completed_at is not None or etkin_gun > toplam:
        return dict(d, durum="tamamlandi")
    return dict(d, durum="egitim")


def zaman_satirlari(db: Session, baby: Baby, simdi: datetime | None = None) -> str | None:
    """Sor bağlamına eklenecek satırlar (düz metin). Kurulamazsa None.

    `simdi`: Türkiye saatli an (testler); verilmezse api.zaman.simdi_tr()."""
    from api.services import plan_service

    d = gun_durumu(db, baby, simdi)
    gun, dk, gece = d["gun"], d["dk"], d["gece"]
    plan, content, tip = d["plan"], d["content"], d["tip"]
    satirlar = [f"Şu an: {_tarih_metni(gun)}, saat {_ss(dk)} "
                f"({'gece' if gece else 'gündüz'})."]

    # --- Özel durumlar ------------------------------------------------------
    if d["durum"] == "yenidogan":
        satirlar.append("Eğitim günü: Yenidoğan dönemi (eğitim programı yok).")
        return " ".join(satirlar)
    if d["durum"] == "baslamadi":
        tahmini = (content.get("egitim_baslangic") or {}).get("tahmini_tarih")
        if tip == plan_service.TYPE_BEKLEME and baby.birth_date is not None:
            try:
                tahmini = plan_service.egitim_baslangic_hesapla(baby, gun)["tahmini_tarih"]
            except Exception:
                pass
        bas = baby.training_started_at.isoformat() if (
            tip != plan_service.TYPE_BEKLEME and baby.training_started_at) else tahmini
        satirlar.append(f"Eğitim günü: Eğitim henüz başlamadı (başlangıç: "
                        f"{bas or 'belirlenmedi'}).")
        return " ".join(satirlar)

    app_gun, etkin_gun, toplam = d["app_gun"], d["etkin_gun"], d["toplam"]
    uyanis, kaynak, sabah_once = d["uyanis"], d["kaynak"], d["sabah_once"]
    if d["durum"] == "tamamlandi":
        satirlar.append(f"Eğitim günü: {toplam} günlük program tamamlandı.")
        return " ".join(satirlar)

    uyanis_notu = (f"kayıttaki sabah uyanışı {_ss(uyanis)}" if kaynak == "kayıt"
                   else f"planlanan sabah uyanışı {_ss(uyanis)}")
    if sabah_once:
        if etkin_gun < 1:
            satirlar.append(f"Eğitim günü: eğitim bu sabah başlıyor (1. gün; "
                            f"{uyanis_notu}).")
        else:
            satirlar.append(
                f"Eğitim günü: Eğitimin {etkin_gun}. gününün gecesi; sabah "
                f"{app_gun}. gün başlıyor ({uyanis_notu}). Uygulama ekranında "
                f"bugün 'Gün {app_gun}' görünür — gece yarısından sabah "
                f"uyanışına kadar olan saatler önceki günün gecesidir.")
    elif gece:
        satirlar.append(f"Eğitim günü: Eğitimin {app_gun}. günü, akşam/gece "
                        f"(bu gece {app_gun}. günün gecesi).")
    else:
        satirlar.append(f"Eğitim günü: Eğitimin {app_gun}. günü (gündüz).")

    adim = _plan_adim_satiri(content, max(1, etkin_gun), gece or sabah_once)
    if adim:
        satirlar.append(adim)
    # build_baby_context "Bugünün planı"nı yalnız bugün tarihli plan satırından
    # yazar; o satır henüz yoksa (uygulama bugün açılmadı) saatler en güncel
    # plandan verilir — kaydı olmayan annenin cevabı saatsiz kalmasın.
    if plan is not None and plan.plan_date != gun:
        from api.services.baby_context import _plan_ozeti
        ozet = _plan_ozeti(plan)
        if ozet:
            satirlar.append(f"Planın saatleri (en güncel plan): {ozet}.")
    # Bağlamdaki "uyku kaydı girilmemiş" cümlesi modeli "göremiyorum" demeye
    # itiyordu (canlı E senaryosu) — kayıt yoksa nasıl davranacağı burada söylenir.
    if not _son_kayit_var_mi(db, baby, gun):
        satirlar.append("Kayıt notu: son günlerde uyku kaydı yok; cevabı planın "
                        "saatlerine göre TAM ver, kayıt eksikliğini cevabın konusu yapma.")
    return " ".join(satirlar)


def _son_kayit_var_mi(db: Session, baby: Baby, gun: date) -> bool:
    """build_baby_context'in 3 günlük penceresiyle aynı: o pencerede kayıt var mı."""
    from api.services.baby_context import LOOKBACK_DAYS
    bas = datetime.combine(gun - timedelta(days=LOOKBACK_DAYS), datetime.min.time(),
                           tzinfo=timezone.utc)
    return db.query(SleepLog.id).filter(SleepLog.baby_id == baby.id,
                                        SleepLog.started_at >= bas).first() is not None
