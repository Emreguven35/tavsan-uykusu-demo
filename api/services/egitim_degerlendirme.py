"""
13. gün eğitim değerlendirmesi (2026-11-01, danışman kuralları).

Program gün sayısına (13) ulaşan, tamamlanmamış eğitimde anneye üç soru
sorulur; cevaplar KARAR_TABLOSU'ndan (tek yer — danışman değiştirebilir) bir
sonuç üretir:

  mezun          training_completed_at = bugün
  yeniden_basla  eğitim YARIN 1. günden; eski deneme egitim_degerlendirmeleri
                 satırında kalır (başlangıç, gün, aşama)
  devam          program uzar: aşama yatır-çık (mevcut_asama boşaltılır →
                 programın son aşaması), sayılar 13. günün değerlerinde
                 (sor_kural.uzatma_asamasi); YENIDEN_DEGERLENDIRME_GUN sonra
                 yeniden sorulur. devam_turu (DEVAM_TURU_TABLOSU):
                   yatir_cik     gece kendi dönüyor, yatır-çık yok
                   gece_bekleme  yatır-çık var, gece kendi dönmüyor
                   ikisi         ikisi de yok
                 gece_bekleme/ikisi'de gece ilk bekleme uzatmanın her gecesi
                 GECE_ARTIS_DK artar, GECE_UST_SINIR_DK'da durur
                 (uzatma_gece_ilk_dk); diğer sayılar 13. gün değerinde.

egitim_durumu (mobile giden plan yanıtında, adaptation içinde):
  "devam"                  gün < program uzunluğu
  "degerlendirme_bekliyor" gün >= program uzunluğu ve bu denemede geçerli
                           değerlendirme yok (ya da "devam"ın tarihi geldi)
  "uzatma"                 "devam" kararı verildi, yeniden değerlendirme günü gelmedi
  "tamamlandi"             training_completed_at dolu
  None                     eğitim planı yok / eğitim başlamadı

ESKİ MOBİL SÜRÜMLER: tamamlanmamış eğitimde `egitim_gunu` program uzunluğunda
sabitlenir (ekran "Gün 18" göstermesin); gerçek gün `egitim_gunu_gercek`.
Sor bağlamı, bildirimler, denetim ve KVKK dökümü gerçek günü kullanmaya devam
eder (plan_adapter.egitim_gunu) — sabitleme YALNIZ mobil plan yanıtında.
"""
from __future__ import annotations

from datetime import date, timedelta
from sqlalchemy.orm import Session

from api.models import Baby, EgitimDegerlendirme, SleepPlan, User
from api.services import plan_adapter
from api.zaman import bugun_tr

PROGRAM_GUN = 13                       # plan_secimi.gunler yoksa
YENIDEN_DEGERLENDIRME_GUN = 7          # "devam" sonrası kaç gün sonra yeniden sorulur

# Uzatmada gece ilk bekleme (gece_bekleme / ikisi). DANIŞMANDAN BEKLENİYOR — şimdilik:
#   GECE_ARTIS_DK     programın kendi günlük adımı (gece_uyanma.gunluk_artis_standart_dk = 5)
#   GECE_UST_SINIR_DK 13. gecenin değeri (5 + 5×12 = 65) + 30
# Yöntem tablosu değişirse bunlar KENDİLİĞİNDEN değişmez (test uyarır).
GECE_ARTIS_DK = 5
GECE_UST_SINIR_DK = 95

MEZUN, DEVAM, YENIDEN_BASLA = "mezun", "devam", "yeniden_basla"
SONUCLAR = (MEZUN, DEVAM, YENIDEN_BASLA)

# egitim_durumu değerleri
D_DEVAM, D_BEKLIYOR, D_UZATMA, D_TAMAMLANDI = (
    "devam", "degerlendirme_bekliyor", "uzatma", "tamamlandi")

# --- KARAR TABLOSU (tek yer) -------------------------------------------------
# Sırayla denenir, ilk tutan kural kazanır; koşulda olmayan alan aranmaz.
# Hiçbiri tutmazsa VARSAYILAN_KARAR.
KARAR_TABLOSU: tuple[tuple[dict[str, bool], str], ...] = (
    ({"kendi_donuyor": True, "yatir_cik": True}, MEZUN),
    ({"yatir_cik": False, "uzun_aglama": True}, YENIDEN_BASLA),
)
VARSAYILAN_KARAR = DEVAM

DT_YATIR_CIK, DT_GECE, DT_IKISI = "yatir_cik", "gece_bekleme", "ikisi"
DEVAM_TURLERI = (DT_YATIR_CIK, DT_GECE, DT_IKISI)
GECE_ARTAN_TURLER = (DT_GECE, DT_IKISI)
# "devam" kararında tür: (kendi_donuyor, yatir_cik) → tür. (True, True) mezundur.
DEVAM_TURU_TABLOSU: dict[tuple[bool, bool], str] = {
    (True, False): DT_YATIR_CIK,
    (False, True): DT_GECE,
    (False, False): DT_IKISI,
}


def karar(kendi_donuyor: bool, yatir_cik: bool, uzun_aglama: bool | None) -> str:
    cevap = {"kendi_donuyor": kendi_donuyor, "yatir_cik": yatir_cik,
             "uzun_aglama": uzun_aglama}
    for kosul, sonuc in KARAR_TABLOSU:
        if all(cevap[k] is v for k, v in kosul.items()):
            return sonuc
    return VARSAYILAN_KARAR


def devam_turu(kendi_donuyor: bool, yatir_cik: bool, uzun_aglama: bool | None) -> str | None:
    """Karar "devam" ise türü, değilse None."""
    if karar(kendi_donuyor, yatir_cik, uzun_aglama) != DEVAM:
        return None
    return DEVAM_TURU_TABLOSU.get((bool(kendi_donuyor), bool(yatir_cik)), DT_IKISI)


def cevap_hatasi(yatir_cik: bool, uzun_aglama: bool | None) -> str | None:
    """uzun_aglama yalnız yatır-çık yapılamıyorsa sorulur: o zaman zorunlu,
    değilse gönderilmemeli (null)."""
    if not yatir_cik and uzun_aglama is None:
        return "Yatır-çık yapılamıyorsa uzun_aglama cevaplanmalı."
    if yatir_cik and uzun_aglama is not None:
        return "Yatır-çık yapılabiliyorsa uzun_aglama gönderilmez (null olmalı)."
    return None


# --- Okuma -------------------------------------------------------------------
def program_gun_sayisi(content: dict | None) -> int:
    try:
        return int(((content or {}).get("plan_secimi") or {}).get("gunler") or 0) or PROGRAM_GUN
    except (TypeError, ValueError):
        return PROGRAM_GUN


def _son_plan_icerigi(db: Session, baby: Baby) -> dict:
    plan = (db.query(SleepPlan).filter(SleepPlan.baby_id == baby.id)
            .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc()).first())
    return (plan.content or {}) if plan is not None else {}


def deneme_no(db: Session, baby: Baby) -> int:
    """Bebeğin şu anki eğitim denemesi (1'den): 1 + yeniden başlatma sayısı."""
    return 1 + (db.query(EgitimDegerlendirme)
                .filter(EgitimDegerlendirme.baby_id == baby.id,
                        EgitimDegerlendirme.sonuc == YENIDEN_BASLA).count())


def son_degerlendirme(db: Session, baby: Baby, gun: date | None = None,
                      yalniz_bu_deneme: bool = True) -> EgitimDegerlendirme | None:
    """`gun`e kadarki (dahil) en son değerlendirme. yalniz_bu_deneme: yalnız
    şimdiki denemenin (egitim_baslangic == training_started_at) satırları."""
    q = db.query(EgitimDegerlendirme).filter(EgitimDegerlendirme.baby_id == baby.id)
    if gun is not None:
        q = q.filter(EgitimDegerlendirme.gun <= gun)
    if yalniz_bu_deneme:
        if baby.training_started_at is None:
            return None
        q = q.filter(EgitimDegerlendirme.egitim_baslangic == baby.training_started_at)
    return q.order_by(EgitimDegerlendirme.created_at.desc(),
                      EgitimDegerlendirme.gun.desc()).first()


def durum(db: Session, baby: Baby, gun: date, toplam: int = PROGRAM_GUN) -> str | None:
    if baby.training_started_at is None:
        return None
    if baby.training_completed_at is not None and baby.training_completed_at <= gun:
        return D_TAMAMLANDI
    if (plan_adapter.egitim_gunu(baby.training_started_at, gun) or 0) < toplam:
        return D_DEVAM
    son = son_degerlendirme(db, baby, gun)
    if son is None or son.sonuc != DEVAM:
        return D_BEKLIYOR
    tarih = son.yeniden_degerlendirme_tarihi
    return D_BEKLIYOR if tarih is not None and gun >= tarih else D_UZATMA


def gece_artis_baslangici(db: Session, baby: Baby, gun: date) -> int | None:
    """Uzatmada gece ilk beklemesinin artmaya başladığı gece (eğitim günü) ya da None.

    Yalnız bu denemenin EN SON değerlendirmesi gece artan türde "devam" ise; başlangıç
    o türdeki İLK "devam"ın günü — yeniden değerlendirmede yine gece türü çıkarsa
    artış sıfırlanmaz (bekleme önceki geceden az olamaz)."""
    son = son_degerlendirme(db, baby, gun)
    if son is None or son.sonuc != DEVAM or son.devam_turu not in GECE_ARTAN_TURLER:
        return None
    ilk = (db.query(EgitimDegerlendirme)
           .filter(EgitimDegerlendirme.baby_id == baby.id,
                   EgitimDegerlendirme.egitim_baslangic == baby.training_started_at,
                   EgitimDegerlendirme.gun <= gun, EgitimDegerlendirme.sonuc == DEVAM,
                   EgitimDegerlendirme.devam_turu.in_(GECE_ARTAN_TURLER))
           .order_by(EgitimDegerlendirme.created_at).first())
    return ilk.egitim_gunu if ilk is not None else None


def uzatma_gece_ilk_dk(taban: int, gece_no: int, artis_bas: int | None) -> int:
    """Uzatmada bu gecenin ilk beklemesi. `taban`: kural gününün (13) değeri.
    artis_bas None → taban (sabit). Aksi hâlde artis_bas gecesinden sonraki her
    gece +GECE_ARTIS_DK, en fazla GECE_UST_SINIR_DK (taban daha büyükse taban)."""
    if artis_bas is None:
        return taban
    return min(taban + GECE_ARTIS_DK * max(0, int(gece_no) - int(artis_bas)),
               max(taban, GECE_UST_SINIR_DK))


def gun_alanlari(db: Session, baby: Baby, gun: date, toplam: int = PROGRAM_GUN) -> dict:
    """{egitim_gunu (eski sürümler için sabitlenmiş), egitim_gunu_gercek, egitim_durumu}."""
    gercek = plan_adapter.egitim_gunu(baby.training_started_at, gun)
    tamam = baby.training_completed_at is not None and baby.training_completed_at <= gun
    gosterilen = gercek if gercek is None or tamam else min(gercek, toplam)
    return {"egitim_gunu": gosterilen, "egitim_gunu_gercek": gercek,
            "egitim_durumu": durum(db, baby, gun, toplam)}


def plan_icerigine_isle(content: dict | None, db: Session, baby: Baby | None,
                        gun: date) -> dict | None:
    """Plan yanıtına (okuma anında, DB'ye YAZMAZ) egitim_durumu ve
    egitim_gunu_gercek ekler, egitim_gunu/egitim_baslangic_gunu'yu sabitler.
    Mobil günü önce adaptation'dan okur — alanlar oraya (yoksa gövdeye) yazılır."""
    if not content or baby is None:
        return content
    from api.services.plan_service import TYPE_EGITIM, tip_turet
    c = dict(content)
    hedef = dict(c["adaptation"]) if isinstance(c.get("adaptation"), dict) else c
    if tip_turet(c) != TYPE_EGITIM or baby.training_started_at is None:
        hedef.update(egitim_gunu_gercek=hedef.get("egitim_gunu"), egitim_durumu=None)
    else:
        alanlar = gun_alanlari(db, baby, gun, program_gun_sayisi(c))
        hedef.update(alanlar)
        if isinstance(hedef.get("egitim_baslangic_gunu"), int):
            hedef["egitim_baslangic_gunu"] = alanlar["egitim_gunu"]
    if hedef is not c:
        c["adaptation"] = hedef
    return c


# --- Yazma -------------------------------------------------------------------
class DegerlendirmeZamaniDegil(Exception):
    """Bebek değerlendirilebilir durumda değil (eğitim yok/sürüyor/tamamlandı)."""

    def __init__(self, egitim_durumu: str | None):
        super().__init__(egitim_durumu)
        self.egitim_durumu = egitim_durumu


def degerlendir(db: Session, user: User, baby: Baby, kendi_donuyor: bool,
                yatir_cik: bool, uzun_aglama: bool | None) -> EgitimDegerlendirme:
    """Kararı uygula ve satırı ekle (commit ÇAĞIRANDA). Değerlendirme yalnız
    degerlendirme_bekliyor ya da uzatma durumunda (erken yeniden değerlendirme
    serbest) yapılır; aksi hâlde DegerlendirmeZamaniDegil."""
    from api.services import plan_service
    bugun = bugun_tr()
    content = _son_plan_icerigi(db, baby)
    toplam = program_gun_sayisi(content)
    simdiki = durum(db, baby, bugun, toplam)
    if (simdiki not in (D_BEKLIYOR, D_UZATMA)
            or plan_service.tip_turet(content or {}) != plan_service.TYPE_EGITIM):
        raise DegerlendirmeZamaniDegil(simdiki)

    sonuc = karar(kendi_donuyor, yatir_cik, uzun_aglama)
    satir = EgitimDegerlendirme(
        devam_turu=devam_turu(kendi_donuyor, yatir_cik, uzun_aglama),
        user_id=user.id, baby_id=baby.id, gun=bugun, deneme_no=deneme_no(db, baby),
        egitim_baslangic=baby.training_started_at,
        egitim_gunu=plan_adapter.egitim_gunu(baby.training_started_at, bugun),
        mevcut_asama=baby.mevcut_asama, kendi_donuyor=kendi_donuyor,
        yatir_cik=yatir_cik, uzun_aglama=uzun_aglama, sonuc=sonuc)
    if sonuc == MEZUN:
        baby.training_completed_at = bugun
    elif sonuc == YENIDEN_BASLA:
        satir.yeni_baslangic = bugun + timedelta(days=1)
        baby.training_started_at = satir.yeni_baslangic
        baby.training_completed_at = None
        baby.mevcut_asama = None
        baby.regresyon_kendi_donuyor = None
        baby.regresyon_cevap_at = None
    else:
        satir.yeniden_degerlendirme_tarihi = bugun + timedelta(days=YENIDEN_DEGERLENDIRME_GUN)
        # Aşama yatır-çık: annenin eski beyanı (ör. "kapı") uzatmayı o aşamada
        # tutardı; beyan satırda saklanır, bebekte boşaltılır → programın son aşaması.
        baby.mevcut_asama = None
    db.add(satir)
    return satir


# --- Sor bağlamı ---------------------------------------------------------------
_SONUC_AD = {MEZUN: "mezun", DEVAM: "devam", YENIDEN_BASLA: "yeniden başla"}
_TUR_AD = {DT_YATIR_CIK: "yatır-çık çalışılıyor",
           DT_GECE: "gece bekleme süresi artırılıyor",
           DT_IKISI: "yatır-çık çalışılıyor ve gece bekleme süresi artırılıyor"}


def baglam_satirlari(db: Session, baby: Baby, gun: date,
                     app_gun: int | None = None) -> list[str]:
    """Sor bağlamı: deneme numarası (2. denemeden itibaren) ve son değerlendirme."""
    satirlar: list[str] = []
    no = deneme_no(db, baby)
    ts = baby.training_started_at
    if no > 1 and ts is not None:
        if ts > gun:
            satirlar.append(f"Deneme: eğitimin {no}. denemesi {ts.isoformat()} tarihinde "
                            f"1. günden başlıyor (önceki denemenin günleri sayılmaz).")
        else:
            satirlar.append(f"Deneme: {no}. deneme, {app_gun or plan_adapter.egitim_gunu(ts, gun)}. gün "
                            f"(eğitim {ts.isoformat()} tarihinde baştan başladı; önceki "
                            f"denemenin günleri sayılmaz).")
    son = son_degerlendirme(db, baby, gun, yalniz_bu_deneme=False)
    if son is not None:
        parca = [_SONUC_AD.get(son.sonuc, son.sonuc),
                 "gece kendi dönüyor" if son.kendi_donuyor else "gece kendi dönmüyor",
                 "yatır-çık yapılabiliyor" if son.yatir_cik else "yatır-çık henüz yok"]
        if son.uzun_aglama is not None:
            parca.append("30 dk üzeri ağlama var" if son.uzun_aglama
                         else "30 dk üzeri ağlama yok")
        metin = (f"{PROGRAM_GUN}. gün değerlendirmesi ({son.gun.isoformat()}, "
                 f"{son.deneme_no}. denemenin {son.egitim_gunu}. günü): " + ", ".join(parca))
        if son.sonuc == DEVAM and son.devam_turu:
            metin += f"; devam türü: {_TUR_AD.get(son.devam_turu, son.devam_turu)}"
        if son.sonuc == DEVAM and son.yeniden_degerlendirme_tarihi:
            metin += f"; yeniden değerlendirme {son.yeniden_degerlendirme_tarihi.isoformat()}"
        elif son.sonuc == YENIDEN_BASLA and son.yeni_baslangic:
            metin += (f"; eğitim {son.yeni_baslangic.isoformat()} tarihinde 1. günden yeniden "
                      f"{'başlıyor' if son.yeni_baslangic > gun else 'başladı'}")
        satirlar.append(metin + ".")
    return satirlar

