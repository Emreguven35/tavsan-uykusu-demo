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
import os
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from api.models import Baby, SleepLog, SleepPlan, User
from api.services import plan_adapter
from api.services import usage
from engine import plan_generator, plan_gunleri, yas_bantlari, yenidogan
from engine.parameter_engine import hesapla_yas_ay, load_kb, parametre_uret, yas_bucket_sec

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


class PlanError(RuntimeError):
    """Plan üretilemedi/yeniden üretilemedi (dış servis, motor hatası vb.)."""


# =============================================================================
# Profil / parametre yardımcıları
# =============================================================================
def profile_from_baby(baby: Baby, overrides: dict | None,
                      dogum_haftasi: int | None) -> dict:
    """Baby satırından motorun beklediği profil sözlüğünü kur. profile_overrides
    (mobil onboarding'in 37 cevabı) üzerine yazılır."""
    profile = {
        "bebek_ad": baby.name,
        "dogum_tarihi": baby.birth_date.isoformat(),   # çağıran öncesinde doğruladı
        "dogum_haftasi": dogum_haftasi or 40,
        "beslenme": baby.feeding_type or "",
        "destek": baby.sleep_method or "",
        "oda": baby.sleep_environment or "",
        # crying_tolerance = ebeveynin ağlamaya dayanma sınırı → motor 'dayanma_siniri'.
        "dayanma_siniri": baby.crying_tolerance or "",
        "deneyim": baby.parent_experience or "",
        "gece_uyanma": str(baby.night_wakes) if baby.night_wakes is not None else "",
    }
    if overrides:
        profile.update(overrides)
    return profile


def bucket_params(baby: Baby, dogum_haftasi: int | None = None
                  ) -> tuple[str, dict, float]:
    """Bebeğin yaşına karşılık gelen bant parametreleri.

    Dönen: (kb_bucket_key, kb_bucket, duzeltilmis_ay). Sayısal çizelge kararları
    duzeltilmis_ay üzerinden yas_bantlari.json'dan alınır (Faz Y); kb_bucket
    yalnız yardımcı içerik (örnek program, görsel referans) taşır."""
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), int(dogum_haftasi or 40))
    key = yas_bucket_sec(yas["duzeltilmis_ay"])
    return key, load_kb()["yas_buckets"].get(key, {}), yas["duzeltilmis_ay"]


def tek_uyku_bayragi(content: dict | None) -> bool | None:
    """Plan içeriğinden 12-18 ay tek/çift uyku bayrağını çöz.

    True → tek uyku; None → bandın varsayılanı (2 uyku). False DÖNMEZ: varsayılan
    zaten 2 uyku olduğu için ikisi aynı sonucu verir, None niyeti daha nettir
    ("bilgi yok" ile "iki uyku ölçüldü" karışmasın)."""
    return ((content or {}).get("yas_bandi") or {}).get("varyant") == "tek_uyku" or None


# =============================================================================
# Sorgular
# =============================================================================
def recent_logs(db: Session, user: User, baby: Baby, today: date,
                lookback_days: int = plan_adapter.LOOKBACK_DAYS) -> list[SleepLog]:
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
    if plan is None:
        plan = SleepPlan(user_id=user.id, baby_id=baby.id,
                         plan_date=plan_date, content=content)
        db.add(plan)
    elif _ayni_icerik(plan.content, content):
        return plan                          # değişmedi → DB'ye dokunma
    else:
        plan.content = dict(content)
    db.commit()
    db.refresh(plan)
    return plan


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
    eski = content.get("schedule") or []
    yeni = plan_adapter.normalize_schedule(eski)
    degisti = yeni != eski

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
        "restart_program_suggested": result["restart_program_suggested"],
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
            "restart_program_suggested": False, "adaptation": None,
            "kestirme": None, "toplam_uyku": None,
            "reasons": ["0-3 ay yenidoğan ritim rehberi adapte edilmez: bu yaşta "
                        "katı uyku programı uygulanmaz, hesaplanacak çizelge yok."],
            "schedule": [], "schedule_template": [],
        }

    base_content = dict(base_plan.content or {})
    dogum_haftasi = base_content.get("dogum_haftasi", 40)
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
        log_summary=summary)

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
    (restart_program_suggested ile aynı desen)."""
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

    # Şablon garantisi: v1 planlarında schedule_template yok; okuma yolunda bir
    # kez yükselt ki taban dünün hesaplanmış çizelgesi olmasın (K1).
    base_plan = ensure_current_schema(db, base_plan)

    logs = recent_logs(db, user, baby, today)
    plan, _ = run_adaptation(db, user, baby, base_plan, logs, today,
                             now_minute=now_minute)
    return plan
