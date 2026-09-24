"""
Plan servisi — plan üretimi, adaptasyon ve "bugünün planı" mantığının TEK yeri.

Faz 6.6: bu mantık önce plans router'ında yaşıyordu; bildirim zamanlayıcısı da
aynı lazy-adapt yolunu koşturması gerektiği için ortak servise çıkarıldı.
KOD ÇİFTLENMEZ: hem GET /plans/today hem notifier ensure_today_plan()'ı çağırır.

HTTP bilinmez: hatalar PlanError olarak yükselir, router bunu 502'ye çevirir.
Zamanlayıcı ise yakalayıp loglar (tek bebek yüzünden tur düşmesin).
"""
from __future__ import annotations

import logging
import math
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from api.db import upsert
from api.models import Baby, SleepLog, SleepPlan, User
from api.services import plan_adapter
from api.services import usage
from engine import plan_generator, plan_gunleri, yas_bantlari, yenidogan
from engine.parameter_engine import (
    egitim_uygunlugu_kontrol, hesapla_yas_ay, ilk_tam_sayi, load_kb,
    parametre_uret, yas_bucket_sec,
)

logger = logging.getLogger("tavsan.plan_service")

# Gün bölümleri ayrıştırılamazsa plan kaç kez yeniden ürettirilir (Claude yolu).
# Her deneme ~130 sn ve ayrı bir Sonnet faturası; ikiden fazlası hem pahalı hem
# de anlamsız — biçim iki kez tutmuyorsa sorun prompt'ta, tekrarda değil.
PLAN_DAYS_MAX_DENEME = 2

# ---------------------------------------------------------------------------
# content["type"] — mobil bu alana bakarak HANGİ EKRANI açacağını bilir.
# ---------------------------------------------------------------------------
# Faz 0-3 öncesi bu alan YOKTU çünkü tek bir çıktı türü vardı (eğitim planı).
# Artık üç tür var ve ikisinde `days` boştur; mobil `days` doluluğuna değil bu
# alana bakmalıdır.
TYPE_EGITIM = "egitim_plani"        # 5+ ay, 13 günlük merdiven — days DOLU
TYPE_YENIDOGAN = "yenidogan_ritim"  # 0-3 ay, ritim rehberi     — days BOŞ, schedule BOŞ
TYPE_BEKLEME = "egitim_bekleme"     # eğitim uygun değil (3-5 ay, doktor onayı vb.)


def tip_turet(content: dict) -> str:
    """`type` alanı olmayan (Faz 0-3 öncesi) planın türü.

    O dönemde tek çıktı türü vardı; yenidoğan rehberi `type` alanıyla BİRLİKTE
    geldi, dolayısıyla tipsiz plan ya eğitim planıdır ya da (uygun_mu=False)
    beklemedir. Prod taraması (2026-09-24): 493 tipsiz planın hepsi
    uygun_mu=True, 5+ ay, markdown dolu."""
    if content.get("type"):
        return content["type"]
    return TYPE_BEKLEME if content.get("uygun_mu") is False else TYPE_EGITIM


class PlanError(RuntimeError):
    """Plan üretilemedi/yeniden üretilemedi (dış servis, motor hatası vb.)."""


# =============================================================================
# Profil / parametre yardımcıları
# =============================================================================
def etkin_dogum_haftasi(baby: Baby, istekten: int | None = None) -> int:
    """Prematüre düzeltmesinde kullanılacak doğum haftası (v2.1).

    Öncelik: istek gövdesi > Baby satırı > 40 (miadında). İstekten gelen değer
    Baby'ye YAZILIR (bkz. profili_kalicilastir) — böylece yeniden üretimlerde
    kaybolmaz."""
    # getattr: motor test fixture'ları (SahteBebek) ORM satırı değil, düz nesne.
    # Yeni kolonu olmayan bir nesne buraya gelirse çökmek yerine varsayılana düşer.
    return int(istekten or getattr(baby, "dogum_haftasi", None) or 40)


def profili_kalicilastir(db: Session, baby: Baby, overrides: dict | None,
                         dogum_haftasi: int | None) -> None:
    """v2.1 — istekle gelen kalıcı profil alanlarını Baby'ye yaz.

    Eskiden `profile_overrides` hiçbir yerde saklanmıyordu; bant atlaması sonrası
    yeniden üretimde sağlık uyarısı sessizce kayboluyordu (ölçüldü). Artık bu iki
    alan bebeğe ait. Diğer override anahtarları (onboarding'in serbest cevapları)
    yine geçici — onların kalıcı karşılığı zaten Baby kolonlarıdır."""
    degisti = False
    if dogum_haftasi is not None and baby.dogum_haftasi != int(dogum_haftasi):
        baby.dogum_haftasi = int(dogum_haftasi)
        degisti = True
    saglik = (overrides or {}).get("saglik_problemi")
    if saglik is not None and baby.saglik_problemi != saglik:
        baby.saglik_problemi = saglik or None
        degisti = True
    nw = (overrides or {}).get("gece_uyanma")
    if nw is not None:
        sayi = ilk_tam_sayi(nw)
        if sayi is not None and baby.night_wakes != sayi:
            baby.night_wakes = sayi
            degisti = True
    if degisti:
        db.commit()
        db.refresh(baby)


# D-3 — plan üretimi için ZORUNLU profil alanları: (Baby sütunu, motorun
# profil anahtarı, anneye gösterilecek ad). Boş profille üretim 202 dönüp
# arka planda boşa gidiyordu; artık istek anında 422 + eksik liste döner.
# night_wakes BİLEREK yok: None meşru bir durum (beyan yok → kart üretilmez,
# bkz. gece_uyanma_kaynagi). 0-3 ay muaf: rehber bu alanları kullanmıyor.
ZORUNLU_PROFIL = (
    ("feeding_type", "beslenme", "beslenme şekli"),
    ("sleep_method", "destek", "uykuya dalma şekli"),
    ("sleep_environment", "oda", "uyku ortamı"),
    ("crying_tolerance", "dayanma_siniri", "ağlamaya dayanma süresi"),
    ("parent_experience", "deneyim", "ebeveyn deneyimi"),
)
ALAN_ETIKETI = {"birth_date": "doğum tarihi",
                **{alan: etiket for alan, _k, etiket in ZORUNLU_PROFIL}}


def eksik_profil_alanlari(baby: Baby, overrides: dict | None = None,
                          dogum_haftasi: int | None = None) -> list[str]:
    """Plan üretimini engelleyen eksik alanlar (Baby sütun adlarıyla).

    İstekle gelen profile_overrides da sayılır: mobil onboarding cevaplarını
    bazen yalnız orada gönderiyor."""
    if baby.birth_date is None:
        return ["birth_date"]
    ay = hesapla_yas_ay(baby.birth_date.isoformat(),
                        etkin_dogum_haftasi(baby, dogum_haftasi))["duzeltilmis_ay"]
    if yenidogan.yenidogan_mi(ay):
        return []
    overrides = overrides or {}
    return [alan for alan, anahtar, _e in ZORUNLU_PROFIL
            if not str(getattr(baby, alan, None) or "").strip()
            and not str(overrides.get(anahtar) or "").strip()]


def profile_from_baby(baby: Baby, overrides: dict | None,
                      dogum_haftasi: int | None) -> dict:
    """Baby satırından motorun beklediği profil sözlüğünü kur.

    v2.1: `saglik_problemi` ve `dogum_haftasi` artık BABY'DEN okunur (kalıcı).
    profile_overrides (mobil onboarding'in 37 cevabı) hâlâ üzerine yazabilir ama
    artık tek kaynak değildir — override verilmese de değerler korunur."""
    profile = {
        "bebek_ad": baby.name,
        "dogum_tarihi": baby.birth_date.isoformat(),   # çağıran öncesinde doğruladı
        "dogum_haftasi": etkin_dogum_haftasi(baby, dogum_haftasi),
        "beslenme": baby.feeding_type or "",
        "destek": baby.sleep_method or "",
        "oda": baby.sleep_environment or "",
        # crying_tolerance = ebeveynin ağlamaya dayanma sınırı → motor 'dayanma_siniri'.
        "dayanma_siniri": baby.crying_tolerance or "",
        "deneyim": baby.parent_experience or "",
        "gece_uyanma": str(baby.night_wakes) if baby.night_wakes is not None else "",
        # v2.1 — KALICI: yeniden üretimde artık kaybolmuyor.
        "saglik_problemi": getattr(baby, "saglik_problemi", None) or "",
    }
    if overrides:
        profile.update(overrides)
    return profile


# =============================================================================
# K11 — gece uyanma sayısının KAYNAĞI: beyan mı, ölçülen mi?
# =============================================================================
# Onboarding'de bir kez girilen sayı (beyan) ilk günlerin tek verisidir; ama
# anne kayıt tutmaya başladıysa GERÇEK ölçüm beyanı gölgede bırakmalıdır.
# v2.0'da yalnız beyan vardı ve hiç güncellenmiyordu: bebek düzelse bile
# "gece çok uyanıyor" kartı ekranda kalıyordu (ölçüldü).
GECE_UYANMA_PENCERE_GUN = 7        # son kaç gece taranır
GECE_UYANMA_MIN_GECE = 3           # ölçülene geçmek için gereken en az gece sayısı


def gece_uyanma_kaynagi(baby: Baby, logs: Iterable[SleepLog],
                        today: date) -> dict:
    """K11 — kartın besleneceği gece uyanma sayısı ve nereden geldiği.

    Son `GECE_UYANMA_PENCERE_GUN` gecede uyanma kaydı olan gece sayısı
    `GECE_UYANMA_MIN_GECE`'den azsa beyan (baby.night_wakes) kullanılır;
    yeterliyse veri olan gecelerin ORTALAMASI (yukarı yuvarlanmış).

    K12.2 — `ended_at` ŞARTI KALDIRILDI. Eskiden süresi girilmemiş kayıt
    sayılmıyordu; anneler gece 03:00'te uyanma kaydını açıp bitişini
    girmediği için bu kart beta boyunca hiç ölçülene geçmedi ve onboarding'de
    bir kez beyan edilen sayı ekranda donup kaldı (beta verisinde ölçüldü).
    Ölçüt plan_adapter.summarize_logs ile aynı — iki yerde ayrışmasın.

    06:00 öncesi `wake` kayıtları da gece uyanması sayılır (K12.2).

    Dönen: {"kaynak": "beyan"|"olculen", "deger": int|None, "gece_sayisi": int}
    """
    basla = today - timedelta(days=GECE_UYANMA_PENCERE_GUN - 1)
    gece_sayaci: dict[date, int] = {}
    for lg in logs or []:
        tip = getattr(lg, "type", None)
        if tip not in ("night_wake", "wake"):
            continue
        gun, dakika = plan_adapter._local_minute(lg.started_at,
                                                 plan_adapter.TZ_OFFSET_MIN)
        if tip == "wake" and dakika >= plan_adapter.GUN_BASLANGICI_EN_ERKEN:
            continue                      # sabah uyanışı — gece uyanması değil
        # Gece anahtarı: öğleden önceki uyanmalar BİR ÖNCEKİ gecenin sayılır —
        # detect_regression ile aynı kural, iki yerde ayrışmasın.
        gece = gun - timedelta(days=1) if dakika < 12 * 60 else gun
        if basla <= gece <= today:
            gece_sayaci[gece] = gece_sayaci.get(gece, 0) + 1

    if len(gece_sayaci) >= GECE_UYANMA_MIN_GECE:
        ortalama = sum(gece_sayaci.values()) / len(gece_sayaci)
        return {"kaynak": "olculen", "deger": math.ceil(ortalama),
                "gece_sayisi": len(gece_sayaci)}
    return {"kaynak": "beyan", "deger": baby.night_wakes,
            "gece_sayisi": len(gece_sayaci)}


# Uygulamanın kapsadığı yaş aralığının üst ucu (ay). Doğum tarihi doğrulaması
# (babies router) ve eski kayıtlar için plan uyarısı AYNI sınırı kullanır.
DOGUM_TARIHI_UST_AY = 36
DOGUM_TARIHI_KONTROL_UYARISI = "Bebeğinin doğum tarihini kontrol eder misin?"


def dogum_tarihi_hatasi(dogum: date | None, bugun: date | None = None) -> str | None:
    """Türkçe hata metni ya da None. Gelecek tarih ve 36 aydan eski tarih
    reddedilir (prod'da 77 aylık görünen bir bebek vardı — yanlış giriş)."""
    if dogum is None:
        return None
    bugun = bugun or datetime.now(timezone.utc).date()
    if dogum > bugun:
        return ("Doğum tarihi ileri bir tarih olamaz. Lütfen bebeğinizin doğum "
                "tarihini kontrol edin.")
    if (bugun - dogum).days / 30.44 > DOGUM_TARIHI_UST_AY:
        return ("Tavşan Uykusu 0-36 aylık bebekler için hazırlandı; girilen "
                "doğum tarihi 36 aydan eski. Lütfen tarihi kontrol edin.")
    return None


def uyarilari_turet(baby: Baby, logs: Iterable[SleepLog], today: date,
                    dogum_haftasi: int | None = None) -> dict:
    """Faz 3 — `content.uyarilar` + `content.uygun_mu`'yu GÜNCEL veriden türet.

    ASLA kopyalanmaz: üretim anındaki liste yalnız LLM prompt bağlamıdır.
    Her GET'te bu fonksiyon koşar, dolayısıyla koşul düşünce kart kaybolur,
    koşul oluşunca kart kendiliğinden gelir (v2.0'da ikisi de olmuyordu).

    Dönen: {"uygun_mu", "uyarilar", "gece_uyanma", "yas"}
    """
    hafta = etkin_dogum_haftasi(baby, dogum_haftasi)
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), hafta)
    gu = gece_uyanma_kaynagi(baby, logs, today)
    # v1.4 — kartın asıl ölçütü: son 7 gecede kaç gecede 20 dk+ süren ve
    # müdahale gerektiren uyanma oldu. Kayıt yoksa None → beyana düşülür.
    uzun = uzun_uyanma_gece_sayisi(logs, today) if gu["gece_sayisi"] else None
    sonuc = egitim_uygunlugu_kontrol(
        yas["duzeltilmis_ay"], hafta, getattr(baby, "saglik_problemi", None),
        ilk_tam_sayi(gu["deger"]), gu["kaynak"],
        uzun_uyanma_gece_sayisi=uzun)
    uyarilar = list(sonuc["uyarilar"])
    # Denetim B3 — 36 aydan büyük görünen bebek (prod'da 77 aylık bir kayıt):
    # büyük olasılıkla doğum tarihi yanlış girilmiş. Artık yeni girişte 422
    # veriliyor; ESKİ kayıtlar için anneye uygulama içinde sorulur.
    if yas["gercek_ay"] > DOGUM_TARIHI_UST_AY:
        uyarilar.insert(0, DOGUM_TARIHI_KONTROL_UYARISI)
    return {"uygun_mu": sonuc["uygun_mu"], "uyarilar": uyarilar,
            "gece_uyanma": gu, "yas": yas,
            "uzun_uyanma_gece_sayisi": uzun}


# Anne "evet, kendi dönüyor" dediyse kart bu kadar gün gösterilmez.
REGRESYON_SESSIZLIK_GUN = 7


def regresyon_cevabi(baby: Baby, today: date) -> bool | None:
    """Annenin "kendi uykuya dönüyor mu?" cevabı — süresi geçmişse None.

    "Evet" cevabı REGRESYON_SESSIZLIK_GUN boyunca kartı kapatır; sonra durum
    değişmiş olabileceği için yeniden sorulur. "Hayır" cevabının süresi yoktur:
    akış 45 gün kapısına göre ilerler."""
    cevap = getattr(baby, "regresyon_kendi_donuyor", None)
    if cevap is not True:
        return cevap
    verildi = getattr(baby, "regresyon_cevap_at", None)
    if verildi is None:
        return True
    if verildi.tzinfo is None:
        verildi = verildi.replace(tzinfo=timezone.utc)
    if (today - verildi.date()).days >= REGRESYON_SESSIZLIK_GUN:
        return None                      # süre doldu → yeniden sor
    return True


def uzun_uyanma_gece_sayisi(logs: Iterable[SleepLog], today: date,
                            gun: int = GECE_UYANMA_PENCERE_GUN) -> int:
    """Son `gun` gecede, 20 dk+ süren gece uyanmasının görüldüğü GECE sayısı.

    İlayda (S6): "Bu uyanmada 20 dakikanın üzerinde uyanık kalıp kendi
    dönemiyorsa sorundur bizim için." Süre girilmemişse
    GECE_UYANMA_VARSAYILAN_DK (10 dk) varsayılır ve eşiğin altında kalır —
    yani "bilinmiyor" otomatik olarak sorun sayılmaz.

    Gece anahtarı: öğleden önceki uyanmalar BİR ÖNCEKİ gecenin sayılır
    (detect_regression ile aynı kural)."""
    basla = today - timedelta(days=gun - 1)
    geceler: set = set()
    for lg in logs or []:
        if getattr(lg, "type", None) != "night_wake":
            continue
        bitis = getattr(lg, "ended_at", None)
        if bitis is None:
            sure = plan_adapter.GECE_UYANMA_VARSAYILAN_DK
        else:
            bas = lg.started_at
            if bas.tzinfo is None:
                bas = bas.replace(tzinfo=timezone.utc)
            if bitis.tzinfo is None:
                bitis = bitis.replace(tzinfo=timezone.utc)
            sure = (bitis - bas).total_seconds() / 60
        if sure < plan_adapter.UZUN_UYANMA_MIN_DK:
            continue
        g, dakika = plan_adapter._local_minute(lg.started_at,
                                               plan_adapter.TZ_OFFSET_MIN)
        gece = g - timedelta(days=1) if dakika < 12 * 60 else g
        if basla <= gece <= today:
            geceler.add(gece)
    return len(geceler)


def bucket_params(baby: Baby, dogum_haftasi: int | None = None
                  ) -> tuple[str, dict, float]:
    """Bebeğin yaşına karşılık gelen bant parametreleri.

    Dönen: (kb_bucket_key, kb_bucket, duzeltilmis_ay). Sayısal çizelge kararları
    duzeltilmis_ay üzerinden yas_bantlari.json'dan alınır (Faz Y); kb_bucket
    yalnız yardımcı içerik (örnek program, görsel referans) taşır."""
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), int(dogum_haftasi or 40))
    key = yas_bucket_sec(yas["duzeltilmis_ay"])
    return key, load_kb()["yas_buckets"].get(key, {}), yas["duzeltilmis_ay"]


# --- Bant süreleri: TEK kaynak (K16.1, K17, bakım betikleri) ----------------
# Aynı hesap üç yerde ayrı ayrı yazılmıştı; biri güncellenip diğeri unutulunca
# "batch'te 60 dk, betikte 90 dk" gibi sessiz ayrışmalar oluyordu.
VARSAYILAN_NAP_DK = 60
VARSAYILAN_GECE_DK = 12 * 60


def uyku_sureleri(baby: Baby | None) -> tuple[int, int]:
    """(planlanan gündüz uykusu dk, gece uykusu ÜST sınırı dk).

    Bant çözülemezse (doğum tarihi yok / tablo sorunlu) makul varsayılanlara
    düşülür — uydurma bir 16 saat yerine. ASLA istisna fırlatmaz: bu değer
    kayıt senkronunun ortasında kullanılıyor, senkronu düşüremez."""
    nap_dk, gece_dk = VARSAYILAN_NAP_DK, VARSAYILAN_GECE_DK
    try:
        if baby is not None and baby.birth_date is not None:
            hafta = int(getattr(baby, "dogum_haftasi", None) or 40)
            ay = hesapla_yas_ay(baby.birth_date.isoformat(), hafta)["duzeltilmis_ay"]
            bant = yas_bantlari.yas_bandi_getir(ay)
            nap_dk = int(yas_bantlari.cizelge_parametreleri(bant)["uyku_suresi_dk"])
            ust = (bant.get("gece_uykusu_dk") or [None, None])[1]
            if ust:
                gece_dk = int(ust)
    except Exception:
        pass
    return max(15, nap_dk), max(60, gece_dk)


def tek_uyku_bayragi(content: dict | None) -> bool | None:
    """Plan içeriğinden 12-18 ay tek/çift uyku bayrağını çöz.

    True → tek uyku; None → bandın varsayılanı (2 uyku). False DÖNMEZ: varsayılan
    zaten 2 uyku olduğu için ikisi aynı sonucu verir, None niyeti daha nettir
    ("bilgi yok" ile "iki uyku ölçüldü" karışmasın)."""
    return ((content or {}).get("yas_bandi") or {}).get("varyant") == "tek_uyku" or None


# =============================================================================
# Sorgular
# =============================================================================
# Hesaplama yollarının İHTİYAÇ DUYDUĞU EN GENİŞ pencere. Tek bir sorguyla
# çekilir, her tüketici kendi penceresine göre süzer:
#   • recompute_day → yalnız BUGÜN,
#   • summarize_logs / detect_regression → son 3 gün / 3 gece,
#   • gece_uyanma_kaynagi (K11) → son 7 gece.
# Dar çekilirse K11 sessizce eksik gece görür (ölçüldü: 7 gece yazıldı, 4 sayıldı).
LOG_PENCERE_GUN = max(plan_adapter.LOOKBACK_DAYS, GECE_UYANMA_PENCERE_GUN)


def recent_logs(db: Session, user: User, baby: Baby, today: date,
                lookback_days: int = LOG_PENCERE_GUN) -> list[SleepLog]:
    """Son `lookback_days` günün kayıtları. Yerel gün sınırı kayması ve gece
    uykusunun bitişi için pencere bir gün geniş tutulur."""
    start = datetime.combine(today - timedelta(days=lookback_days),
                             datetime.min.time(), tzinfo=timezone.utc)
    return (db.query(SleepLog)
            .filter(SleepLog.user_id == user.id,
                    SleepLog.baby_id == baby.id,
                    SleepLog.started_at >= start)
            .order_by(SleepLog.started_at)
            .all())


def latest_plan(db: Session, user: User, baby: Baby) -> SleepPlan | None:
    return (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id, SleepPlan.baby_id == baby.id)
            .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc())
            .first())


def plan_for_date(db: Session, user: User, baby: Baby, gun: date) -> SleepPlan | None:
    return (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id, SleepPlan.baby_id == baby.id,
                    SleepPlan.plan_date == gun)
            .order_by(SleepPlan.created_at.desc())
            .first())


def upsert_plan(db: Session, user: User, baby: Baby, plan_date: date,
                content: dict) -> SleepPlan:
    """Aynı (user, baby, plan_date) varsa İÇERİĞİ GÜNCELLE, yoksa oluştur.

    Aynı güne plan yığılmasını önler. JSONB değişikliğinin görülmesi için content
    YENİ bir sözlük olarak atanır.

    K8 (kilit kaldırıldı) hesaplamayı HER istekte çalıştırıyor. Bu yüzden içerik
    gerçekten değişmediyse YAZILMAZ: aksi hâlde her GET /plans/today bir UPDATE
    üretirdi. Karşılaştırma `adaptation.hesaplandi_at` hariç yapılır — o damga
    "bu çizelge ne zaman hesaplandı"yı değil "en son ne zaman DEĞİŞTİ"yi gösterir."""
    plan = plan_for_date(db, user, baby, plan_date)
    if plan is not None:
        if _ayni_icerik(plan.content, content):
            return plan                      # değişmedi → DB'ye dokunma
        plan.content = dict(content)
        db.commit()
        db.refresh(plan)
        return plan

    # YENİ SATIR — ATOMİK (v2.4.4). Eskiden düz INSERT'ti: aynı bebeğin bugünkü
    # planını iki yol aynı anda kurabiliyor (GET /plans/today + bildirim turu +
    # POST /logs/batch sonrası tazeleme) ve AYNI GÜNE İKİ SATIR yazılıyordu.
    # Tekillik kısıtı olmadığı için hata da vermiyordu: sessiz çoğalma, sonra
    # `plan_for_date` iki satırdan birini seçiyordu. Kısıt eklendi
    # (uq_sleep_plans_user_baby_date) ve yazım ON CONFLICT DO UPDATE'e çevrildi.
    st = upsert.insert(SleepPlan).values(
        id=uuid.uuid4(), user_id=user.id, baby_id=baby.id,
        plan_date=plan_date, content=dict(content))
    db.execute(st.on_conflict_do_update(
        index_elements=["user_id", "baby_id", "plan_date"],
        set_={"content": st.excluded.content}))
    db.commit()
    return plan_for_date(db, user, baby, plan_date)


def _ayni_icerik(a: dict | None, b: dict | None) -> bool:
    """İki plan içeriği anlamlı olarak aynı mı? (zaman damgası yok sayılır)"""
    def _sadelestir(c: dict | None) -> dict:
        c = dict(c or {})
        ad = c.get("adaptation")
        if isinstance(ad, dict):
            c["adaptation"] = {k: v for k, v in ad.items() if k != "hesaplandi_at"}
        return c
    return _sadelestir(a) == _sadelestir(b)


# =============================================================================
# Şema yükseltme (geriye uyumluluk)
# =============================================================================
def ensure_current_schema(db: Session, plan: SleepPlan | None) -> SleepPlan | None:
    """Saklanmış planı GÜNCEL sözleşmeye yükselt (okuma yolunda, kalıcı).

    Şema değişikliğinden önce üretilmiş planlarda bloklar {start, label,
    type:"night"} biçimindeydi ve headline/night_wake_protocol yoktu. Burada bir
    kez yükseltilip DB'ye yazılır (değişiklik yoksa yazılmaz)."""
    if plan is None:
        return None
    # Yenidoğan rehberinde yükseltilecek bir şey YOK: çizelge, gün bölümleri,
    # gece uyanma protokolü ve kestirme kuralı bu yaşta BİLEREK boştur. Aşağıdaki
    # geriye uyumluluk doldurmaları buraya uygulanırsa rehbere bir eğitim
    # protokolü enjekte edilir — tam da engellemeye çalıştığımız şey.
    if is_yenidogan(plan):
        return plan
    content = dict(plan.content or {})
    # Tipsiz eski plan: mobil hangi ekranı açacağını bilemiyordu. Bir kez yazılır.
    tip_eksik = not content.get("type")
    if tip_eksik:
        content["type"] = tip_turet(content)
    eski = content.get("schedule") or []
    yeni = plan_adapter.normalize_schedule(eski)
    degisti = yeni != eski or tip_eksik

    # K1/K2 — v1 planlarında DEĞİŞMEZ ŞABLON yok. İlk okumada mevcut çizelgeden
    # bir kez türetilip kalıcı yazılır; bundan sonra günlük hesap hep bunu taban
    # alır ve şablon bir daha değişmez. (v1'de bu alanın olmaması, dünün
    # kaydırılmış çizelgesinin bugünün tabanı olmasının sebebiydi.)
    if yeni and not content.get("schedule_template"):
        content["schedule_template"] = yeni
        degisti = True

    if yeni and not content.get("headline"):
        baby = db.get(Baby, plan.baby_id)
        content["headline"] = plan_adapter.headline(
            baby.name if baby is not None else "Bebeğiniz",
            content.get("bucket"), yeni)
        degisti = True
    if not content.get("night_wake_protocol"):
        content["night_wake_protocol"] = dict(plan_adapter.NIGHT_WAKE_PROTOCOL)
        degisti = True
    # Faz Y: evrensel kestirme kuralı tüm planlarda bulunmalı (eski planlarda yok).
    if not content.get("kestirme_protokolu"):
        content["kestirme_protokolu"] = yas_bantlari.kestirme_protokolu()
        degisti = True
    # Yapısal gün bölümleri: eski planlarda yok, markdown'dan bir kez türetilip yazılır.
    degisti = days_backfill(content) or degisti

    if degisti:
        content["schedule"] = yeni
        plan.content = content
        db.commit()
        db.refresh(plan)
        logger.info("Plan güncel şemaya yükseltildi: plan_id=%s", plan.id)
    return plan


def days_backfill(content: dict) -> bool:
    """content'te yapısal gün bölümleri yoksa markdown'dan türet (yerinde yazar).

    Dönen: içerik değişti mi. OKUMA yolunda kullanılır — üretimdeki gibi reddedip
    yeniden üretmeyiz: eski bir planın GET'i 502 olmamalı ve kullanıcının elindeki
    plan bir okuma yüzünden değişmemeli. Ayrıştırılamazsa days YAZILMAZ ve
    plan_gunleri UYARI loglar (sessiz kalmaz, Sentry'de görünür)."""
    if content.get("days"):
        return False
    days = plan_gunleri.days_from_content(content)
    if not days:
        return False
    content["days"] = days
    return True


# =============================================================================
# Üretim + adaptasyon
# =============================================================================
def generate_content(baby: Baby, req_overrides: dict | None,
                     dogum_haftasi: int | None,
                     operation: str = usage.OP_PLAN_GENERATE) -> dict:
    """parameter_engine + plan_generator ile plan içeriği üret (+ yapısal çizelge).

    operation: maliyet defterinde bu üretimin hangi başlığa yazılacağı —
    plan_generate (yeni plan) ya da plan_adapt (yaş bandı ihlali sonrası yeniden
    üretim). İkisi ayrı tutulur çünkü adaptasyon çok daha seyrek ama aynı pahalı
    Sonnet çağrısını yapıyor; tek başlıkta toplanırsa hangisinin maliyeti şişirdiği
    görünmez."""
    profile = profile_from_baby(baby, req_overrides, dogum_haftasi)
    try:
        param = parametre_uret(profile)                 # deterministik parametreler
    except Exception as e:
        raise PlanError(str(e)) from e

    # --- 0-3 AY: EĞİTİM PLANI DEĞİL, YENİDOĞAN RİTİM REHBERİ -----------------
    # İlayda kuralı: bu yaşta katı program ve yapılandırılmış eğitim UYGULANMAZ.
    # LLM'e hiç gidilmez (maliyet yok) ve merdiven metni üretilemez — bu yaşa bir
    # eğitim tekniğinin sızması yapısal olarak imkânsız.
    if yenidogan.yenidogan_mi(param["yas"]["duzeltilmis_ay"]):
        return _yenidogan_content(baby, param, dogum_haftasi)

    used_claude = bool(os.getenv("ANTHROPIC_API_KEY")) and plan_generator.HAS_ANTHROPIC
    tip = param["plan_secimi"]["tip"]
    gunler = int(param["plan_secimi"]["gunler"])

    # --- Gün bölümleri: eğitim planında ZORUNLU, beklemede ÖNİZLEME -----------
    # uygun_mu True  → merdiven bugün uygulanıyor. Ayrıştırma başarısızsa plan
    #                  REDDEDİLİR ve yeniden üretilir (eğitim ekranı boş kalmasın).
    # uygun_mu False → 3-5 ay. Merdiven bugün uygulanMIYOR ama metinde yazılı
    #                  (anne 5. ayda ne olacağını görmek istiyor). days ÖNİZLEME
    #                  olarak doldurulur; ayrıştırma başarısız olursa plan
    #                  REDDEDİLMEZ — sadece önizleme eksik kalır, çünkü bugün
    #                  uygulanacak bir şey yok. Bu asimetri kasıtlı: 502'yi geri
    #                  getirmemek için bekleme yolunda ASLA PlanError yükselmez.
    gun_bolumu_zorunlu = bool(param["uygun_mu"])

    # Eğitimin açılacağı tarih PROMPT'A da verilir ki model kendi hesap
    # yapmasın. Aynı sözlük hem prompt'a hem content'e gider — metindeki tarih
    # ile mobilin gösterdiği tarih AYRIŞAMAZ.
    if not gun_bolumu_zorunlu:
        param["egitim_baslangic"] = yenidogan.egitim_uygunluk_tarihi(
            baby.birth_date.isoformat(), param["yas"]["duzeltilmis_ay"])

    # Gün bölümleri ayrıştırılamazsa plan REDDEDİLİR ve yeniden üretilir: eğitim
    # ekranının sessizce boş kalmasının sebebi buydu (başlık biçimi LLM'e bağlıydı,
    # aynı sürümden 5 ayrı kalıp ölçüldü). Boş days ile plan KAYDEDİLMEZ.
    markdown: str | None = None
    days: list[dict] | None = None
    son_hata: Exception | None = None
    for deneme in range(1, PLAN_DAYS_MAX_DENEME + 1):
        _kullanim: dict = {}
        _t0 = time.perf_counter()
        try:
            markdown = plan_generator.plan_uret(param, usage_sink=_kullanim)
        except Exception as e:
            raise PlanError(str(e)) from e
        # _kullanim yalnız GERÇEK Claude çağrısında dolar; fallback yolunda boş kalır.
        # HER denemenin maliyeti ayrı yazılır — yeniden üretim bedava değil.
        if _kullanim.get("usage"):
            usage.kaydet(usage.SERVIS_ANTHROPIC, operation,
                         model=_kullanim.get("model"), usage=_kullanim["usage"],
                         user_id=baby.user_id,
                         duration_ms=int((time.perf_counter() - _t0) * 1000))
        if not gun_bolumu_zorunlu:
            # ÖNİZLEME yolu: bir kez dene, olmazsa boş bırak ve DEVAM ET.
            try:
                days = _onizleme_isaretle(
                    plan_gunleri.build_days(markdown, tip, gunler))
            except plan_gunleri.DayParseError as e:
                days = []
                logger.warning("Eğitim önizlemesi ayrıştırılamadı (baby=%s): %s "
                               "— plan yine de veriliyor (bugün uygulanacak bir "
                               "merdiven yok)", baby.id, e)
            break
        try:
            days = plan_gunleri.build_days(markdown, tip, gunler)
            break
        except plan_gunleri.DayParseError as e:
            son_hata = e
            logger.warning("Plan gün bölümleri ayrıştırılamadı (deneme %d/%d, baby=%s): %s",
                           deneme, PLAN_DAYS_MAX_DENEME, baby.id, e)
            if not used_claude:
                # Yedek motor deterministiktir: aynı metni yeniden üretmek anlamsız.
                # Buraya düşmek bizim fallback şablonumuzun bozuk olduğunu gösterir.
                break
    if days is None:
        raise PlanError(f"Plan gün bölümleri ayrıştırılamadı "
                        f"({'yedek motor' if not used_claude else f'{PLAN_DAYS_MAX_DENEME} deneme'}): "
                        f"{son_hata}")

    # Faz Y: çizelge YAŞ BANDI TABLOSUNDAN kurulur (düzeltilmiş ay üzerinden).
    schedule = plan_adapter.build_schedule(
        param.get("parametreler", {}), plan_adapter.DEFAULT_WAKE_MIN,
        yas_ay=param["yas"]["duzeltilmis_ay"],
        tek_uyku=tek_uyku_bayragi(param))

    uygun = bool(param["uygun_mu"])
    content = {
        # Mobil hangi ekranı açacağını BU alandan bilir (days doluluğundan değil).
        "type": TYPE_EGITIM if uygun else TYPE_BEKLEME,
        "markdown": markdown,                       # KALIR (geriye uyum + detay metni)
        # Yapısal gün bölümleri — istemci markdown'ı regex'lemez (bkz. plan_gunleri).
        # egitim_bekleme'de bunlar ÖNİZLEMEDİR: her kayıtta preview=true.
        "days": days,
        "headline": plan_adapter.headline(baby.name, param["bucket"], schedule),
        "bucket": param["bucket"],
        "yas": param["yas"],
        # Faz Y — mobilin gösterdiği yapılandırılmış bant + evrensel kestirme kuralı.
        "yas_bandi": param["yas_bandi"],
        "kestirme_protokolu": param["kestirme_protokolu"],
        # plan_secimi `days` ile TUTARLI olmalı: beklemede merdiven bugün
        # uygulanmıyor, bu yüzden kayıt "önizleme" olarak işaretlenir. Mobil
        # aksi hâlde gunler=13'e bakıp "13 günlük program" rozeti basardı.
        "plan_secimi": (dict(param["plan_secimi"]) if uygun
                        else _onizleme_plan_secimi(param["plan_secimi"])),
        # FAZ N-A: 24+ ay "büyük çocuk" içeriği (motivasyon panosu, 5 oyuncak,
        # pozitif teşvik…). Merdiven değişmez; bu ek bölümdür. Yaş altındaysa None.
        "yas_ozel_notlar": param.get("yas_ozel_notlar"),
        "uygun_mu": param["uygun_mu"],
        "uyarilar": param["uyarilar"],
        "generated_with": "claude" if used_claude else "fallback",
        # K1 — ŞABLON: üretimde bir kez yazılır, eğitim boyunca DEĞİŞMEZ.
        # Günlük çizelge her gün bundan türetilir; şablon asla güncellenmez.
        "schedule_template": schedule,
        # Bugünün çizelgesi. Kayıt geldikçe recompute_day yeniden yazar (K2/K3).
        "schedule": schedule,
        "dogum_haftasi": int(dogum_haftasi or 40),
        "baseline_night_wakes": baby.night_wakes,
        "adapted": False,
    }

    if uygun:
        # Gece uyanma protokolü (45 dk direnç / 15 dk rutin molası) bir EĞİTİM
        # protokolüdür. Eğitime uygun OLMAYAN bebeğe verilmez — yenidoğan
        # rehberindeki gerekçenin aynısı. 3-5 ayda gece uyanan bebek yatıştırılır.
        content["night_wake_protocol"] = dict(plan_adapter.NIGHT_WAKE_PROTOCOL)
    else:
        # Eğitim ne zaman açılır? Tarih MOTORDAN gelir, LLM hesaplamaz.
        # Ölçülen hata: prompt'ta doğum tarihi olduğu için model "doğum + 5 ay"
        # yapıyordu; 34 haftalık prematürede bu DÜZELTİLMİŞ yaşa göre doğru
        # tarihten 46 GÜN ERKEN çıkıyordu. Yukarıda param'a da konuldu →
        # prompt'taki tarih ile buradaki AYNI sözlüktür, ayrışamaz.
        content["egitim_baslangic"] = param["egitim_baslangic"]
        # Merdiven metinde var ama BUGÜN UYGULANMAZ — mobil kilitli/soluk gösterir.
        content["egitim_onizleme"] = True
    return content


def _onizleme_plan_secimi(secim: dict) -> dict:
    """plan_secimi'nin önizleme biçimi — merdiven bugün uygulanmıyor.

    `tip`/`gunler` KORUNUR (5. ayda hangi programın başlayacağını mobil
    gösterebilsin) ama `onizleme: True` ile işaretlenir ve `gunler` yanında
    "bugün uygulanmıyor" bilgisi taşınır."""
    out = dict(secim or {})
    out["onizleme"] = True
    out["aciklama"] = (
        "ÖNİZLEME — bu program bebek 5. ayını doldurduğunda başlayacak; "
        "bugün uygulanmaz. " + str(out.get("aciklama") or "")).strip()
    return out


def _onizleme_isaretle(days: list[dict]) -> list[dict]:
    """Her gün kaydına preview bayrağı koy (mobil kilitli/soluk gösterir)."""
    return [{**g, "preview": True} for g in days]


def _yenidogan_content(baby: Baby, param: dict, dogum_haftasi: int | None) -> dict:
    """0-3 ay YENİDOĞAN RİTİM REHBERİ içeriği (LLM YOK, tamamen deterministik).

    Eğitim planından farkları — bunlar kasıtlıdır, eksiklik değildir:
      • `days` BOŞ: bu yaşta kademeli uzaklaşma merdiveni uygulanmaz.
      • `schedule` BOŞ: katı saat çizelgesi kurulmaz; belirleyici uyku
        sinyalleridir. Çizelge üretilseydi adaptasyon onu her gün kaydırır ve
        yenidoğana fiilen bir program dayatılırdı.
      • `kestirme_protokolu` YOK: "30 dk kestirme yaptır, sonra uyandır"
        müdahalesi yapılandırılmış bir uyku yönetimidir; 0-3 aya verilmez."""
    yas = param["yas"]
    kurallar = (param.get("global_rules") or {}).get("yenidogan_uyku_0_3_ay") or {}
    try:
        rehber = yenidogan.ritim_rehberi(yas)
    except yenidogan.YenidoganHatasi as e:
        raise PlanError(str(e)) from e

    egitim = yenidogan.egitim_uygunluk_tarihi(
        baby.birth_date.isoformat(), yas["duzeltilmis_ay"])
    markdown = yenidogan.rehber_markdown(
        rehber, baby.name or "Bebeğiniz", yas, kurallar=kurallar, egitim=egitim)

    return {
        "type": TYPE_YENIDOGAN,
        "markdown": markdown,
        "days": [],
        "schedule": [],
        "headline": yenidogan.headline(baby.name or "Bebeğiniz", rehber),
        "bucket": param["bucket"],
        "yas": yas,
        "yas_bandi": param["yas_bandi"],
        # Rehberin yapısal gövdesi — mobil bunu kendi ekranında gösterir.
        "yenidogan": rehber,
        "egitim_baslangic": egitim,
        "plan_secimi": {"tip": TYPE_YENIDOGAN, "gunler": 0,
                        "aciklama": "0-3 ay: uyku eğitimi uygulanmaz, ritim rehberi verilir."},
        "uygun_mu": False,
        "uyarilar": param["uyarilar"],
        "generated_with": "deterministik",
        "dogum_haftasi": int(dogum_haftasi or 40),
        "baseline_night_wakes": baby.night_wakes,
        "adapted": False,
        # night_wake_protocol EKLENMEZ: 45 dk direnç / 15 dk rutin molası bir
        # EĞİTİM protokolüdür. Bu yaşta ağlayan bebek kucağa alınır.
    }


def is_yenidogan(plan: SleepPlan | dict | None) -> bool:
    """Plan (ya da içeriği) yenidoğan ritim rehberi mi?

    Adaptasyon, çizelge kaydırma ve şema yükseltme yollarının hepsi buna bakar:
    rehberde kaydırılacak çizelge YOKTUR ve üretilmesi de yanlış olur."""
    if plan is None:
        return False
    content = plan if isinstance(plan, dict) else (plan.content or {})
    return (content or {}).get("type") == TYPE_YENIDOGAN


def _adaptation_meta(result: dict, summary: dict, required: bool) -> dict:
    """Plan içeriğine gömülen adaptasyon izi (mobil/denetim için).

    Gövde recompute_day'den gelir (K4 şeması: hesaplandi_at, sabah_uyanis_*,
    varsayilan_bloklar, yeniden_hesaplanan_bloklar, yok_sayilan_kayitlar,
    gece_bolunmeleri, uyarilar). Servis katmanı üstüne regresyon ve istatistik
    özetini ekler."""
    meta = dict(result.get("adaptation") or {})
    meta.update({
        "regenerate_required": required,
        "regression_detected": result["regression_detected"],
        "regresyon_karti": result.get("regresyon_karti"),
        "egitim_baslangic_gunu": result.get("egitim_baslangic_gunu"),
        # Mobilin gün kartı `egitim_gunu` adını okuyor — AYNI değer (eğitimin
        # kaçıncı günü, 1'den). Bekleme/yenidoğanda ikisi de None.
        "egitim_gunu": result.get("egitim_baslangic_gunu"),
        "kirkbes_gun_doldu": bool(result.get("kirkbes_gun_doldu")),
        "reasons": result["reasons"],
        "log_summary": summary,
    })
    meta.setdefault("uyarilar", [])
    return meta


def run_adaptation(db: Session, user: User, baby: Baby, base_plan: SleepPlan,
                   logs: list[SleepLog], today: date,
                   now_minute: int | None = None) -> tuple[SleepPlan, dict]:
    """Gün içi kayma motorunu koştur, sonucu bugünün planı olarak upsert et.

    K1/K2: taban artık base_plan.schedule_template'tir — DÜNÜN HESAPLANMIŞ
    ÇİZELGESİ DEĞİL. Böylece bugünün sapması yarına taşınmaz.
    regenerate_required (yaş bandı ihlali / bant atlama) → planı TAM YENİDEN
    ÜRETİR (K8: bu mekanizma korundu).

    Yenidoğan rehberi ADAPTE EDİLMEZ (0-3 ayda katı program yok): rehber bugüne
    taşınır ve adaptasyon sonucu "hiçbir şey yapılmadı" olarak döner. Bu kontrol
    savunma amaçlıdır — /plans/adapt bu yaştaki bir bebek için çağrılırsa
    plan_adapter boş çizelgeyi yaş bandından DOLDURUR ve rehbere fiilen bir
    saat programı basardı."""
    if is_yenidogan(base_plan):
        plan = _yenidogan_bugune_tasi(db, user, baby, base_plan, today)
        return plan, {
            "regenerate_required": False, "regression_detected": False,
            "regresyon_karti": None, "egitim_baslangic_gunu": None,
            "kirkbes_gun_doldu": False, "adaptation": None,
            "kestirme": None, "toplam_uyku": None,
            "reasons": ["0-3 ay yenidoğan ritim rehberi adapte edilmez: bu yaşta "
                        "katı uyku programı uygulanmaz, hesaplanacak çizelge yok."],
            "schedule": [], "schedule_template": [],
        }

    base_content = dict(base_plan.content or {})
    # v2.1: doğum haftası artık BEBEĞE ait; eski planlar için içerikteki değere
    # düşülür (o da yoksa 40). Böylece yeniden üretimde prematüre düzeltmesi
    # kaybolmaz.
    dogum_haftasi = (baby.dogum_haftasi
                     if baby.dogum_haftasi is not None
                     else base_content.get("dogum_haftasi", 40))
    if baby.birth_date is None:
        # Doğum tarihi yoksa yaş bandı ÇÖZÜLEMEZ. Eskiden bu yola hiç girilmiyordu
        # (kayıt yoksa erken dönülüyordu); K8 ile hesap her çağrıda koştuğu için
        # artık girilebiliyor. Çökmek yerine bantsız yola düşülür: gün, şablonun
        # kendi penceresiyle hesaplanır, kestirme/toplam değerlendirmesi YAPILMAZ.
        params, yas_ay = {}, None
    else:
        _, params, yas_ay = bucket_params(baby, dogum_haftasi)
    # 12-18 ay tek/çift uyku ayrımı planla birlikte saklanır; bant değişmedikçe korunur.
    tek_uyku = tek_uyku_bayragi(base_content)

    summary = plan_adapter.summarize_logs(logs, today=today)
    result = plan_adapter.adapt(
        base_content, params, logs,
        training_completed_at=baby.training_completed_at, today=today,
        now_minute=now_minute, yas_ay=yas_ay, tek_uyku=tek_uyku,
        log_summary=summary,
        # Denetim B3: bekleme/yenidoğan planında eğitim günü YOK — mobil
        # training_started_at'i Eğitim sekmesi açılınca her bebek için yazıyordu.
        training_started_at=(egitim_baslangicini_tamamla(db, baby)
                             if tip_turet(base_content) == TYPE_EGITIM else None),
        regresyon_kendi_donuyor=regresyon_cevabi(baby, today))

    if result["regenerate_required"]:
        content = generate_content(baby, None, dogum_haftasi,
                                   operation=usage.OP_PLAN_ADAPT)  # PlanError yükselebilir
        content.update({
            "adapted": True,
            "regenerated": True,
            "base_plan_id": str(base_plan.id),
            "adaptation": _adaptation_meta(result, summary, required=True),
        })
    else:
        content = base_content
        # Tipsiz taban planın kopyası da tipsiz doğuyordu — her gün yeni bir
        # type=null plan (prod'da 2026-09-24 tarihli olanlar vardı).
        content["type"] = tip_turet(content)
        days_backfill(content)        # eski taban planda days yoksa şimdi türet
        content.update({
            # K1 — şablon taşınır, ASLA günlük sonuçla üzerine yazılmaz.
            "schedule_template": result["schedule_template"],
            # K2/K3 — bugünün çizelgesi (yalnız bugünü bağlar).
            "schedule": result["schedule"],
            # Kestirme kuralı her hesaplamada yeniden değerlendirilir (gündüz
            # uyku minimumu tutmadıysa mobil kartı gösterir).
            "kestirme_protokolu": (base_content.get("kestirme_protokolu")
                                   or yas_bantlari.kestirme_protokolu()),
            "kestirme_degerlendirme": result["kestirme"],
            # "Bebeğim yeterince uyuyor mu?" — 24 saatlik toplam değerlendirmesi.
            "toplam_uyku_degerlendirme": result["toplam_uyku"],
            # Çizelge değiştiyse başlıktaki yatış saati de güncellenmeli.
            "headline": plan_adapter.headline(
                baby.name, base_content.get("bucket"), result["schedule"]),
            "adapted": True,
            "regenerated": False,
            "base_plan_id": str(base_plan.id),
            "night_wake_protocol": base_content.get("night_wake_protocol")
            or dict(plan_adapter.NIGHT_WAKE_PROTOCOL),
            "adaptation": _adaptation_meta(result, summary, required=False),
        })

    # --- Faz 3: uyarılar + yaş HER hesaplamada güncel veriden türetilir -------
    # Bunlar `content`'ten KOPYALANMAZ. v2.0'da liste üretim anında donuyordu:
    # night_wakes 6→1 düzeltilse bile kart kalıyor, bebek 6 ayı geçse bile kart
    # gelmiyordu (ikisi de ölçüldü). Artık kaynak her zaman BEBEĞİN ŞU ANKİ
    # verisi + son 7 gecenin kayıtları.
    if baby.birth_date is not None:
        turetilmis = uyarilari_turet(baby, logs, today, dogum_haftasi)
        content["uyarilar"] = turetilmis["uyarilar"]
        content["uygun_mu"] = turetilmis["uygun_mu"]
        content["yas"] = turetilmis["yas"]
        # Yaş ilerledikçe bant/bucket de tazelenir (rozet ve tablo eskimesin).
        # regenerate_required mantığı DEĞİŞMEZ: şablon ihlali yukarıda ayrıca
        # kontrol edilir ve gerekirse plan zaten tam yeniden üretilmiştir.
        content["yas_bandi"] = yas_bantlari.yas_bandi_getir(
            turetilmis["yas"]["duzeltilmis_ay"], tek_uyku=tek_uyku)
        content["bucket"] = yas_bucket_sec(turetilmis["yas"]["duzeltilmis_ay"])
        content["dogum_haftasi"] = etkin_dogum_haftasi(baby, dogum_haftasi)
        # K11 izi — mobil "beyan mı ölçüm mü" ayrımını gösterebilsin.
        content.setdefault("adaptation", {})
        if isinstance(content["adaptation"], dict):
            content["adaptation"]["gece_uyanma"] = turetilmis["gece_uyanma"]
            # v1.4 — kartın ASIL ölçütü bu: son 7 gecede kaç gecede 20 dk+
            # süren, kendi dönemediği uyanma oldu. None = kayıt yok (beyana
            # düşüldü). Mobil kartın neden çıktığını buradan gösterebilir.
            content["adaptation"]["uzun_uyanma_gece_sayisi"] =                 turetilmis["uzun_uyanma_gece_sayisi"]

    plan = upsert_plan(db, user, baby, today, content)
    return plan, result


# =============================================================================
# ORTAK GİRİŞ NOKTASI — GET /plans/today ve bildirim zamanlayıcısı bunu kullanır
# =============================================================================
# K8 — `already_adapted_today` KALDIRILDI.
# v1'de bugünün planı bir kez hesaplandıktan sonra kilitleniyordu: anne sabah
# sekmeyi açtıysa, gün içinde girdiği kayıtlar o gün plana HİÇ yansımıyordu
# (ölçüldü). Artık hesap her GET /plans/today ve her POST /logs/batch sonrasında
# çalışır. Gereksiz DB yazımı `upsert_plan` içindeki içerik karşılaştırmasıyla
# önlenir — kilitle değil.


def _yenidogan_bugune_tasi(db: Session, user: User, baby: Baby,
                           base_plan: SleepPlan, today: date) -> SleepPlan:
    """Yenidoğan rehberini bugüne taşı — adaptasyon YOK, LLM çağrısı YOK.

    Bebek hâlâ 0-3 aydaysa rehber YENİDEN ÜRETİLİR: üretimi deterministik ve
    bedava olduğu için alt bant (0-1 → 1-2 → 2-3 ay) ve uyanıklık penceresi
    bebek büyüdükçe kendiliğinden güncellenir; bayat pencere gösterilmez.

    Bebek 3 ayı geçtiyse rehber OLDUĞU GİBİ kalır ve `yenidogan_suresi_doldu`
    bayrağı eklenir. Burada sessizce eğitim planı ÜRETİLMEZ: o üretim ücretli
    bir Sonnet çağrısıdır ve bir GET isteğinin yan etkisi olamaz — mobil bayrağı
    görüp kullanıcıya "yeni planınızı oluşturalım mı?" kartını gösterir
    (regresyon kartıyla aynı desen: sunucu bayrağı basar, üretimi kullanıcı
    onayı tetikler)."""
    content = dict(base_plan.content or {})
    dogum_haftasi = content.get("dogum_haftasi", 40)
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), int(dogum_haftasi or 40))
    guncel_bant = yenidogan.alt_bant(yas["duzeltilmis_ay"])["id"]

    # GEREKSİZ YAZIM YOK: bugünün rehberi zaten güncelse dokunma.
    # (upsert_plan'ın içerik karşılaştırması burada yetmiyor — `egitim_baslangic
    # .kalan_gun` her gün değiştiği için içerik DAİMA farklı çıkar ve her
    # GET /plans/today bir DB yazımı tetiklerdi. Bu yüzden alt bant kimliği
    # üzerinden ayrı bir tazelik kontrolü yapılır.)
    bugunku = plan_for_date(db, user, baby, today)
    if is_yenidogan(bugunku):
        b_icerik = bugunku.content or {}
        if yenidogan.yenidogan_mi(yas["duzeltilmis_ay"]):
            guncel = ((b_icerik.get("yenidogan") or {}).get("alt_bant")
                      or {}).get("id") == guncel_bant
        else:                       # yaşlanmış rehber: bayrak bir kez yazılır
            guncel = bool(b_icerik.get("yenidogan_suresi_doldu"))
        if guncel:
            return bugunku

    if yenidogan.yenidogan_mi(yas["duzeltilmis_ay"]):
        try:
            content = _yenidogan_content(baby, parametre_uret(
                profile_from_baby(baby, None, dogum_haftasi)), dogum_haftasi)
        except Exception as e:                  # rehber üretilemezse eskisi kalsın
            logger.warning("Yenidoğan rehberi tazelenemedi (baby=%s): %s", baby.id, e)
        content["base_plan_id"] = str(base_plan.id)
    else:
        content["yenidogan_suresi_doldu"] = True
        content["base_plan_id"] = str(base_plan.id)
        logger.info("Yenidoğan rehberi yaşlandı (baby=%s, %.1f ay) — yeni plan önerilecek",
                    baby.id, yas["duzeltilmis_ay"])
    return upsert_plan(db, user, baby, today, content)


# --- 5 AY GEÇİŞİ (v2.4.1) ---------------------------------------------------
# Eğitim uygunluğu YAŞA bağlıdır ve her gün yeniden hesaplanır; ama
# `content.type` plan ÜRETİM anında donuyordu. 4 aylıkken kaydolan bir anne
# 5. ayı doldurunca `uygun_mu` true oluyor, `type` ise `egitim_bekleme`
# kalıyordu — mobil ekran kararını type'tan verdiği için (bkz. generate_content)
# önizleme ekranında kalıyor ve 13 günlük program HİÇ başlamıyordu.
# Yayın öncesi testte KRİTİK olarak raporlandı.
#
# Çözüm: okuma yolunda tespit et, üretimi ARKA PLANA al (GET bloklanmaz),
# mevcut planı iki bayrakla döndür. Üretim bitince sonraki GET yeni planı verir.
EGITIM_YAS_ALT_SINIRI = 5.0          # parameter_engine ile aynı eşik


def egitim_zamani_geldi_mi(baby: Baby, plan: SleepPlan | None,
                           dogum_haftasi: int | None = None) -> bool:
    """Bebek eğitim yaşına geldi ama planı hâlâ 'bekleme/önizleme' mi?"""
    if plan is None or baby.birth_date is None:
        return False
    icerik = plan.content or {}
    if icerik.get("type") != TYPE_BEKLEME:
        return False
    # Sağlık onayı gibi YAŞ DIŞI sebeplerle bekleyen planlar dokunulmaz:
    # uygunluk kontrolünün tamamı yeniden koşturulur, yalnız yaş bakılmaz.
    hafta = etkin_dogum_haftasi(baby, dogum_haftasi or icerik.get("dogum_haftasi"))
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), hafta)
    if yas["duzeltilmis_ay"] < EGITIM_YAS_ALT_SINIRI:
        return False
    sonuc = egitim_uygunlugu_kontrol(
        yas["duzeltilmis_ay"], hafta, getattr(baby, "saglik_problemi", None),
        ilk_tam_sayi(baby.night_wakes), "beyan")
    return bool(sonuc["uygun_mu"])


def egitim_baslangicini_tamamla(db: Session, baby: Baby) -> date:
    """Eğitim planındaki bebeğin training_started_at'i BOŞSA doldur ve döndür.

    2026-09-25: eğitim planı üretilen bebekte alan boş kalıyordu (sunucu yalnız
    5 ay geçişinde yazıyordu, mobil ise Eğitim sekmesi açılınca) → gün kartı
    ve aşama boştu. Kural mobilinkiyle aynı: başlangıç = bebeğin İLK eğitim
    planının tarihi (yoksa bugün)."""
    if baby.training_started_at is not None:
        return baby.training_started_at
    ilk = None
    for p in (db.query(SleepPlan).filter(SleepPlan.baby_id == baby.id)
              .order_by(SleepPlan.plan_date).all()):
        if tip_turet(p.content or {}) == TYPE_EGITIM:
            ilk = p.plan_date
            break
    baby.training_started_at = ilk or datetime.now(timezone.utc).date()
    db.commit()
    logger.info("training_started_at tamamlandı: baby=%s → %s",
                baby.id, baby.training_started_at)
    return baby.training_started_at


def egitim_aktif_mi(db: Session, baby: Baby) -> bool:
    """Bu bebekte EĞİTİM PROGRAMI yürüyebilir mi? (eğitim günü/aşama anlamlı mı)

    Denetim B3 — kural: egitim_bekleme ve yenidogan_ritim planlarında eğitim
    günü ve aşama YOKTUR. Karar bebeğin en güncel planının türüdür (sağlık
    sebebiyle bekleyen 7 aylık bebek de bekleme planındadır). Plan henüz yoksa
    yaşa bakılır; doğum tarihi yoksa karar verilemez → engellenmez."""
    plan = (db.query(SleepPlan).filter(SleepPlan.baby_id == baby.id)
            .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc())
            .first())
    if plan is not None:
        return tip_turet(plan.content or {}) == TYPE_EGITIM
    if baby.birth_date is None:
        return True
    ay = hesapla_yas_ay(baby.birth_date.isoformat(),
                        etkin_dogum_haftasi(baby))["duzeltilmis_ay"]
    return ay >= EGITIM_YAS_ALT_SINIRI


def egitim_gecisini_baslat(db: Session, user: User, baby: Baby,
                           plan: SleepPlan) -> SleepPlan:
    """Gerçek eğitim planını ARKA PLANDA üret; bu isteği bloklamadan dön.

    `training_started_at` BURADA set edilir: program sunucu tarafında başlar,
    mobilin ayrıca PATCH atmasına gerek kalmaz (atarsa da üzerine yazmaz)."""
    from api.services import plan_jobs

    icerik = dict(plan.content or {})
    if not icerik.get("yeniden_uretiliyor"):
        job_id = plan_jobs.create_job(user.id, baby.id)
        plan_jobs.submit(job_id, baby.id, None,
                         etkin_dogum_haftasi(baby, icerik.get("dogum_haftasi")),
                         ek_icerik={"egitim_gecisi": True})
        logger.info("5 ay geçişi: eğitim planı üretimi başlatıldı "
                    "baby=%s job=%s", baby.id, job_id)
    if baby.training_started_at is None:
        baby.training_started_at = datetime.now(timezone.utc).date()
        db.commit()
    # Mobil bu iki bayrakla "program hazırlanıyor" ekranını gösterir; içerik
    # hâlâ eski önizleme planıdır, bir sonraki GET gerçek planı getirir.
    icerik["egitim_zamani_geldi"] = True
    icerik["yeniden_uretiliyor"] = True
    # K11 GEÇİŞTE DE GEÇERLİ: uyarılar/yaş/uygunluk her GET'te GÜNCEL veriden
    # türetilir. Bu satırlar olmadan anne, plan hazırlanırken hâlâ "4.5 aylık,
    # eğitim uygun değil" uyarısını görüyordu (üretim anındaki donmuş metin).
    if baby.birth_date is not None:
        turetilmis = uyarilari_turet(baby, [], datetime.now(timezone.utc).date(),
                                     icerik.get("dogum_haftasi"))
        icerik["uyarilar"] = turetilmis["uyarilar"]
        icerik["uygun_mu"] = turetilmis["uygun_mu"]
        icerik["yas"] = turetilmis["yas"]
    plan.content = icerik
    return plan


def ensure_today_plan(db: Session, user: User, baby: Baby,
                      today: date | None = None,
                      now_minute: int | None = None) -> SleepPlan | None:
    """Bugünün planını döndür — hesap HER ÇAĞRIDA çalışır (K8, kilit yok).

    Akış:
      1. Hiç plan yok → None (çağıran 404/atlama kararını verir).
      2. Yenidoğan rehberi → adapte edilmez, bugüne taşınır.
      3. Aksi hâlde: taban = bugünün planı (varsa) ya da en güncel plan; çizelge
         ŞABLONDAN + BUGÜNÜN kayıtlarından yeniden hesaplanır. Kayıt yoksa bile
         çalışır: sonuç şablonun aynısı olur (K6) ve içerik değişmediği için
         DB'ye yazılmaz.

    Zamanlayıcı da bunu çağırır: kullanıcı uygulamayı hiç açmasa bile bildirim
    güncel hesaplanmış saate göre gider."""
    today = today or datetime.now(timezone.utc).date()
    bugunku = plan_for_date(db, user, baby, today)

    base_plan = bugunku or latest_plan(db, user, baby)
    if base_plan is None:
        return None                                   # hiç plan üretilmemiş

    # Yenidoğan rehberi ADAPTE EDİLMEZ — hesaplanacak çizelge yoktur ve
    # üretilmesi bu yaşa program dayatmak olur (bkz. _yenidogan_content).
    if is_yenidogan(base_plan):
        return _yenidogan_bugune_tasi(db, user, baby, base_plan, today)

    # 5 AY GEÇİŞİ — eğitim yaşı geldiyse gerçek planı arka planda üret.
    # Adaptasyondan ÖNCE: bekleme planının çizelgesi önizlemedir, onu bugüne
    # hesaplamanın anlamı yok; bayraklı hâliyle döndürülür.
    if egitim_zamani_geldi_mi(baby, base_plan):
        return egitim_gecisini_baslat(db, user, baby, base_plan)

    # Şablon garantisi: v1 planlarında schedule_template yok; okuma yolunda bir
    # kez yükselt ki taban dünün hesaplanmış çizelgesi olmasın (K1).
    base_plan = ensure_current_schema(db, base_plan)

    logs = recent_logs(db, user, baby, today)
    plan, _ = run_adaptation(db, user, baby, base_plan, logs, today,
                             now_minute=now_minute)
    return plan
