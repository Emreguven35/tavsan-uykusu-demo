"""
Bildirim altyapısı testleri (Faz 6.2) — Expo Push API MOCK'lanır, ağ YOK.

Kapsam:
  1-4. Pencere hesaplama (erken/tam/geç blok, 'wake' bildirilmez, gece sarması)
  5.   Uçtan uca tur: bildirim gönderilir
  6.   İdempotency: aynı blok İKİNCİ turda gönderilmez
  7.   Tercih kapalıysa (plan_reminders=false) gönderilmez
  8.   DeviceNotRegistered → token SİLİNİR
  9.   Diğer Expo hatası → token KORUNUR
 10.   Çok cihaz → hepsine gider
 11.   Expo ağ hatası → çökme yok
 12.   Zamanlayıcı production dışında BAŞLAMAZ
 13.   Endpoint'ler: register-token (upsert + sahiplik devri), delete, preferences
 15.   "Bebeğiniz uyandı mı?" (v2.4.3): gecikmiş AÇIK uyku kaydı için hatırlatma
       — 20 dk kuralı, gece sessizliği, günlük 3 kota, uygulama açıksa susma

Çalıştırma: python tests/test_notifier.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "faz62_notifier_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"      # zamanlayıcı başlamamalı
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from api.db import SessionLocal, engine          # noqa: E402
from api.db.base import Base                     # noqa: E402
from api.models import (                         # noqa: E402
    Baby, PushToken, SentNotification, SleepLog, SleepPlan, User,
)
from api.services import notifier                # noqa: E402
from api.services import plan_adapter            # noqa: E402
from api.services.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --- Expo MOCK ---------------------------------------------------------------
SENT: list[list[dict]] = []
NEXT_TICKETS: list[dict] = []          # bir sonraki çağrının döneceği ticket'lar
RAISE_NEXT = {"on": False}


# Token bazlı ticket eşlemesi: zamanlayıcı artık birden çok bebeği tarayıp
# birden çok batch gönderdiği için "sıradaki ticket" kuyruğu yanlış batch'e
# denk gelebiliyordu. Hangi token'ın ne döneceği açıkça tanımlanır.
TICKET_BY_TOKEN: dict[str, dict] = {}


def fake_send(messages):
    if RAISE_NEXT["on"]:
        RAISE_NEXT["on"] = False
        return []                       # ağ hatası → boş liste (gerçek davranış)
    SENT.append(messages)
    return [TICKET_BY_TOKEN.get(m.get("to"), {"status": "ok"}) for m in messages]


notifier.send_expo_push = fake_send     # ağ ÇAĞRILMAZ


# --- Fixture -----------------------------------------------------------------
TZ = plan_adapter.TZ_OFFSET_MIN
BUCKET = {
    "uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "2.5 - 3.5 Saat"},
    "uyku_sayisi": {"RESMI_DEGER": "2-3"},
    "gunduz_uyku_total": "2.5-3.5 Saat",
    "yatma_vakti": "18:00 - 20:00",
}
SCHEDULE = plan_adapter.build_schedule(BUCKET, 7 * 60)   # nap_1 10:00, nap_2 14:30, bed 19:00

db = SessionLocal()


def new_user(email: str, prefs=None) -> User:
    u = User(email=email, password_hash=hash_password("GucluParola123!"),
             notification_prefs=prefs)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def new_plan(user: User, baby_name: str, plan_date, schedule=None) -> tuple[Baby, SleepPlan]:
    b = Baby(user_id=user.id, name=baby_name)
    db.add(b)
    db.commit()
    db.refresh(b)
    p = SleepPlan(user_id=user.id, baby_id=b.id, plan_date=plan_date,
                  content={"schedule": schedule or SCHEDULE})
    db.add(p)
    db.commit()
    db.refresh(p)
    return b, p


def add_token(user: User, token: str) -> PushToken:
    t = PushToken(user_id=user.id, expo_token=token, platform="ios",
                  last_seen_at=datetime.now(timezone.utc))
    db.add(t)
    db.commit()
    return t


def utc_at(local_h: int, local_m: int, day: int = 3) -> datetime:
    """Yerel (UTC+3) duvar saatini UTC datetime'a çevir."""
    return (datetime(2026, 8, day, tzinfo=timezone.utc)
            + timedelta(hours=local_h, minutes=local_m) - timedelta(minutes=TZ))



def sent_to(token: str) -> int:
    """Belirli cihaz token'ına kaç mesaj gitti (SENT içindeki tüm turlarda).

    NOT: Faz 6.6'dan sonra zamanlayıcı PLANI OLAN TÜM bebekleri tarar (yalnız
    bugünün planı olanları değil). Paylaşılan test DB'sinde bu, önceki testlerin
    bebeklerinin de bildirim almasına yol açar — bu YENİ ve DOĞRU davranıştır.
    Bu yüzden iddialar global sayaç yerine TOKEN bazında yapılır."""
    return sum(1 for tur in SENT for m in tur if m.get("to") == token)


# =============================================================================
# 1-4) Pencere hesaplama
# =============================================================================
# nap_1 10:00 → 09:30'da "30 dk sonra" = pencere içi (25-40)
blocks = notifier.upcoming_blocks(SCHEDULE, 9 * 60 + 30)
check("1) 30dk sonrası blok pencereye girer (pencere 25-40)",
      len(blocks) == 1 and blocks[0]["key"] == "nap_1", str([b['key'] for b in blocks]))

# 09:45 → 15 dk sonra = ÇOK YAKIN (artık pencere 25-40) → yok
check("2) 15dk sonrası blok pencereye GİRMEZ (rutin payı korunur)",
      notifier.upcoming_blocks(SCHEDULE, 9 * 60 + 45) == [], "")

# 09:15 → 45 dk sonra = ÇOK UZAK → yok
check("3) 45dk sonrası blok pencereye GİRMEZ (çok uzak)",
      notifier.upcoming_blocks(SCHEDULE, 9 * 60 + 15) == [], "")

# 'wake' bloğu asla bildirilmez: 06:40'ta 07:00 uyanışı 20 dk sonra ama tip 'wake'
check("4) 'wake' bloğu bildirilmez",
      notifier.upcoming_blocks(SCHEDULE, 6 * 60 + 30) == [], "")

# gece bloğu (19:00) → 18:40'ta pencere içi
_night = notifier.upcoming_blocks(SCHEDULE, 18 * 60 + 30)
check("4b) Gece uykusu bloğu da bildirilir",
      len(_night) == 1 and _night[0]["key"] == "bedtime", str(_night))

# =============================================================================
# 4c) GERİYE UYUMLULUK: eski şemalı plan da bildirilir (Faz 6.5R)
# =============================================================================
ESKI_SCHEDULE = [
    {"end": "07:00", "key": "wake", "type": "wake", "label": "Sabah uyanış",
     "start": "07:00", "end_minute": 420, "start_minute": 420},
    {"end": "10:45", "key": "nap_1", "type": "nap", "label": "1. gündüz uykusu",
     "start": "09:30", "end_minute": 645, "start_minute": 570},
    {"end": "07:00", "key": "bedtime", "type": "night", "label": "Gece uykusu",
     "start": "19:00", "end_minute": 1860, "start_minute": 1140},
]
_eski_gece = notifier.upcoming_blocks(ESKI_SCHEDULE, 18 * 60 + 30)
check("4c) Eski şemalı (type='night') gece bloğu da bildirilir",
      len(_eski_gece) == 1 and _eski_gece[0]["key"] == "bedtime",
      f"bulunan={[b.get('key') for b in _eski_gece]}")
_eski_nap = notifier.upcoming_blocks(ESKI_SCHEDULE, 9 * 60)
check("4d) Eski şemalı nap bloğu mesajında saat DOLU (None değil)",
      len(_eski_nap) == 1 and _eski_nap[0].get("time") == "09:30",
      f"time={_eski_nap[0].get('time') if _eski_nap else None}")

# =============================================================================
# 5) Uçtan uca: bildirim gönderilir
# =============================================================================
u1 = new_user("n1@tavsansmoke.com")
b1, p1 = new_plan(u1, "Emir", datetime(2026, 8, 3).date())
add_token(u1, "ExponentPushToken[AAA]")

SENT.clear()
stats = notifier.run_reminder_cycle(db, now=utc_at(9, 30))
check("5) Uçtan uca: bildirim gönderildi",
      sent_to("ExponentPushToken[AAA]") == 1, f"stats={stats} sent={SENT}")
_msg = next((m for tur in SENT for m in tur
             if m.get("to") == "ExponentPushToken[AAA]"), {})
check("5b) Mesaj içeriği: emoji + bebek adı + saat",
      _msg.get("body", "").startswith("🌙")
      and "Emir" in _msg.get("body", "") and "10:00" in _msg.get("body", ""),
      f"body={_msg.get('body')}")

# =============================================================================
# 6) İdempotency — aynı blok ikinci turda gönderilmez
# =============================================================================
SENT.clear()
stats2 = notifier.run_reminder_cycle(db, now=utc_at(9, 31))
check("6) İdempotency: aynı blok 2. turda gönderilmez",
      sent_to("ExponentPushToken[AAA]") == 0 and stats2["skipped_duplicate"] >= 1,
      f"stats={stats2} sent={SENT}")

_n_rows = db.query(SentNotification).count()
check("6b) Defterde kayıt oluştu", _n_rows >= 1, f"rows={_n_rows}")

# Ertesi gün AYNI blok yeniden bildirilebilir (block_key'de tarih var)
_, p1b = new_plan(u1, "Emir2", datetime(2026, 8, 4).date())
SENT.clear()
stats2c = notifier.run_reminder_cycle(db, now=utc_at(9, 30, day=4))
check("6c) Ertesi gün aynı blok YENİDEN bildirilir",
      sent_to("ExponentPushToken[AAA]") >= 1, f"stats={stats2c}")

# =============================================================================
# 7) Tercih kapalı → gönderilmez
# =============================================================================
u2 = new_user("n2@tavsansmoke.com", prefs={"plan_reminders": False,
                                           "daily_summary": True})
b2, p2 = new_plan(u2, "Defne", datetime(2026, 8, 5).date())
add_token(u2, "ExponentPushToken[BBB]")
SENT.clear()
stats3 = notifier.run_reminder_cycle(db, now=utc_at(9, 30, day=5))
check("7) plan_reminders=false → o kullanıcıya bildirim YOK",
      sent_to("ExponentPushToken[BBB]") == 0, f"stats={stats3} sent={SENT}")

# =============================================================================
# 8) DeviceNotRegistered → token silinir
# =============================================================================
u3 = new_user("n3@tavsansmoke.com")
b3, p3 = new_plan(u3, "Ada", datetime(2026, 8, 6).date())
add_token(u3, "ExponentPushToken[DEAD]")
TICKET_BY_TOKEN["ExponentPushToken[DEAD]"] = {
    "status": "error", "message": "not registered",
    "details": {"error": "DeviceNotRegistered"}}
SENT.clear()
notifier.run_reminder_cycle(db, now=utc_at(9, 30, day=6))
_left = db.query(PushToken).filter(PushToken.expo_token == "ExponentPushToken[DEAD]").count()
check("8) DeviceNotRegistered → token SİLİNDİ", _left == 0, f"kalan={_left}")

# =============================================================================
# 9) Diğer Expo hatası → token korunur
# =============================================================================
u4 = new_user("n4@tavsansmoke.com")
b4, p4 = new_plan(u4, "Can", datetime(2026, 8, 7).date())
add_token(u4, "ExponentPushToken[KEEP]")
TICKET_BY_TOKEN["ExponentPushToken[KEEP]"] = {
    "status": "error", "message": "MessageRateExceeded",
    "details": {"error": "MessageRateExceeded"}}
notifier.run_reminder_cycle(db, now=utc_at(9, 30, day=7))
_kept = db.query(PushToken).filter(PushToken.expo_token == "ExponentPushToken[KEEP]").count()
check("9) Diğer hata → token KORUNDU", _kept == 1, f"kalan={_kept}")

# =============================================================================
# 10) Çok cihaz → hepsine gider
# =============================================================================
u5 = new_user("n5@tavsansmoke.com")
b5, p5 = new_plan(u5, "Zeynep", datetime(2026, 8, 8).date())
add_token(u5, "ExponentPushToken[D1]")
add_token(u5, "ExponentPushToken[D2]")
SENT.clear()
stats5 = notifier.run_reminder_cycle(db, now=utc_at(9, 30, day=8))
check("10) Çok cihaz → hepsine gönderilir",
      sent_to("ExponentPushToken[D1]") == 1 and sent_to("ExponentPushToken[D2]") == 1,
      f"stats={stats5} sent={SENT}")

# =============================================================================
# 11) Expo ağ hatası → çökme yok
# =============================================================================
u6 = new_user("n6@tavsansmoke.com")
b6, p6 = new_plan(u6, "Mert", datetime(2026, 8, 9).date())
add_token(u6, "ExponentPushToken[NET]")
RAISE_NEXT["on"] = True
try:
    stats6 = notifier.run_reminder_cycle(db, now=utc_at(9, 30, day=9))
    _crashed = False
except Exception as e:
    _crashed = True
    stats6 = str(e)
check("11) Expo ağ hatası → çökme YOK", not _crashed, f"stats={stats6}")

# =============================================================================
# 12) Zamanlayıcı production dışında başlamaz
# =============================================================================
check("12) ENVIRONMENT=development → zamanlayıcı başlamaz",
      notifier.start_scheduler() is False, "")

# =============================================================================
# 13) Endpoint'ler
# =============================================================================
from fastapi.testclient import TestClient   # noqa: E402
from api.main import app                    # noqa: E402

c = TestClient(app)
r = c.post("/api/v1/auth/register",
           json={"email": "napi@tavsansmoke.com", "password": "GucluParola123!"})
H = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = c.post("/api/v1/notifications/register-token", headers=H,
           json={"expo_token": "ExponentPushToken[EP1]", "platform": "ios",
                 "device_name": "iPhone 15"})
check("13) register-token -> 200", r.status_code == 200, r.text[:200])

# Aynı token tekrar → UPSERT (yeni satır yok)
r2 = c.post("/api/v1/notifications/register-token", headers=H,
            json={"expo_token": "ExponentPushToken[EP1]", "platform": "ios"})
_cnt = db.query(PushToken).filter(PushToken.expo_token == "ExponentPushToken[EP1]").count()
check("13b) register-token idempotent (upsert)",
      r2.status_code == 200 and _cnt == 1, f"count={_cnt}")

# Başka kullanıcı aynı cihazı kaydederse token DEVREDİLİR
r = c.post("/api/v1/auth/register",
           json={"email": "napi2@tavsansmoke.com", "password": "GucluParola123!"})
H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
c.post("/api/v1/notifications/register-token", headers=H2,
       json={"expo_token": "ExponentPushToken[EP1]", "platform": "ios"})
db.expire_all()
_row = db.query(PushToken).filter(PushToken.expo_token == "ExponentPushToken[EP1]").one()
_owner_email = db.get(User, _row.user_id).email
check("13c) Cihaz başka hesaba geçerse token devredilir",
      _owner_email == "napi2@tavsansmoke.com", f"sahip={_owner_email}")

# preferences GET (varsayılan ikisi de true)
r = c.get("/api/v1/notifications/preferences", headers=H)
check("13d) preferences varsayılanı: üçü de açık",
      r.status_code == 200 and r.json() == {
          "plan_reminders": True, "daily_summary": True, "community_replies": True},
      r.text[:200])

# preferences PATCH (kısmi)
r = c.patch("/api/v1/notifications/preferences", headers=H,
            json={"plan_reminders": False})
check("13e) preferences PATCH kısmi güncelleme",
      r.status_code == 200 and r.json() == {
          "plan_reminders": False, "daily_summary": True, "community_replies": True},
      r.text[:200])

# delete token
r = c.request("DELETE", "/api/v1/notifications/token", headers=H,
              json={"expo_token": "ExponentPushToken[EP1]"})
check("13f) delete token -> 200", r.status_code == 200, r.text[:200])

# auth'suz erişim
r = c.get("/api/v1/notifications/preferences")
check("13g) preferences auth'suz -> 401/403", r.status_code in (401, 403), str(r.status_code))

db.close()

# =============================================================================
# 14) ADAPTASYON SENKRONU (Faz 6.6) — kullanıcı uygulamayı hiç açmasa bile
#     bildirim KAYDIRILMIŞ saate göre gider
# =============================================================================
from api.models import SleepLog                      # noqa: E402
from api.services import plan_service                # noqa: E402

# Bebek 8 aylık; plan 07:00 uyanışa göre kurulu.
# Faz Y: çizelge YAŞ BANDI TABLOSUNDAN kurulur — 6-8 ay bandı SABİT 3 uyku
# öngörür, dolayısıyla bu bebeğe 2 uykuluk çizelge verilemez (verilseydi
# adaptasyon "bant uyuşmuyor" deyip planı yeniden üretirdi, kaydırmazdı).
# 8 aylık için tablo: pencere 150 dk, 3 uyku × 70 dk → nap_1 09:30, bedtime 20:30.
u14 = new_user("n14@tavsansmoke.com")
_yas14 = 8 * 30 / 30.44                       # gün → ay (motorun kullandığı çevrim)
SCHEDULE_14 = plan_adapter.build_schedule({}, 7 * 60, yas_ay=_yas14)
# YAŞ GERÇEK BUGÜNE GÖRE: hesapla_yas_ay() `today` parametresi almaz, yaşı
# daima gerçek bugünden hesaplar. Doğum tarihi 2026-08-14'e sabitlenirse
# takvim ilerledikçe bebek yaş bandından çıkar, adaptasyon "bant uyuşmuyor"
# deyip planı YENİDEN ÜRETİR ve `adaptation` hiç yazılmaz — test sessizce
# kalıcı hâle gelirdi (aynı tuzak test_baby_context 8b'de de yaşandı).
b14 = Baby(user_id=u14.id, name="Zeynep14",
           birth_date=(datetime.now(timezone.utc).date()
                       - timedelta(days=8 * 30)))
db.add(b14); db.commit(); db.refresh(b14)
p14 = SleepPlan(user_id=u14.id, baby_id=b14.id, plan_date=datetime(2026, 8, 14).date(),
                content={"schedule": SCHEDULE_14, "bucket": "8_ay",
                         "dogum_haftasi": 40, "adapted": False})
db.add(p14); db.commit()
add_token(u14, "ExponentPushToken[SYNC]")

# Son 3 gün: gerçek uyanış 07:45 → plandaki 07:00'den +45dk sapma
for d in range(3):
    st = (datetime(2026, 8, 14, tzinfo=timezone.utc) - timedelta(days=d)
          + timedelta(hours=7, minutes=45) - timedelta(minutes=TZ))
    db.add(SleepLog(user_id=u14.id, baby_id=b14.id, type="wake", started_at=st))
db.commit()

# nap_1 kaydırılınca 09:30 → 10:15 olur. Hatırlatma penceresi v2.4.1'den beri
# KULLANICIYA ÖZEL kayar (yayma_dakikasi; 0-10 dk ERKENE). Sabit bir tur saati
# seçilirse test, kullanıcı id'sinin md5'ine bağlı olarak rastgele kalırdı.
# Tur saatini kaymadan türetiyoruz: 10:15'e tam (30 − kayma) dakika kala →
# pencere [25 − kayma, 40 − kayma] için HER kaymada geçerli. Kaydırılmamış
# 09:30 ise o an çoktan geçmiştir; yani bildirim ancak adaptasyon koştuysa gider.
_kayma14 = notifier.yayma_dakikasi(u14.id)
_tur14 = 9 * 60 + 45 + _kayma14
SENT.clear()
stats14 = notifier.run_reminder_cycle(
    db, now=utc_at(_tur14 // 60, _tur14 % 60, day=14))
_m14 = next((m for tur in SENT for m in tur
             if m.get("to") == "ExponentPushToken[SYNC]"), None)
check("14) Uygulama açılmadan adaptasyon koştu ve bildirim gitti",
      _m14 is not None, f"stats={stats14} sent={SENT}")
check("14b) Bildirim KAYDIRILMIŞ saatle gitti (10:15, 09:30 değil)",
      _m14 is not None and "10:15" in _m14.get("body", "")
      and "09:30" not in _m14.get("body", ""),
      f"body={_m14.get('body') if _m14 else None}")
check("14c) Mesaj biçimi: 🌙 {ad} için uyku vakti yaklaşıyor (saat)",
      _m14 is not None and _m14["body"].startswith("🌙 Zeynep14 için uyku vakti"),
      f"body={_m14.get('body') if _m14 else None}")

_plan14 = plan_service.plan_for_date(db, u14, b14, datetime(2026, 8, 14).date())
_ad14 = (_plan14.content or {}).get("adaptation") or {}
# v2: "shift_minutes" kavramı kaldırıldı (K1). Gün, gerçek uyanıştan zincirlenir;
# izi `sabah_uyanis_gercek` + `yeniden_hesaplanan_bloklar` taşır.
check("14d) Plan bugün için yeniden hesaplanmış olarak kaydedildi",
      (_plan14.content or {}).get("adapted") is True
      and _ad14.get("sabah_uyanis_gercek") == "07:45"
      and "nap_1" in (_ad14.get("yeniden_hesaplanan_bloklar") or []),
      f"gercek={_ad14.get('sabah_uyanis_gercek')} "
      f"yeniden={_ad14.get('yeniden_hesaplanan_bloklar')}")

# --- İKİNCİ TUR: K8 ile kilit yok ama SONUÇ AYNI → gereksiz DB yazımı yok ---
_updated_before = str(_ad14.get("log_summary"))
_sched_before = _plan14.content.get("schedule")
stats14b = notifier.run_reminder_cycle(db, now=utc_at(9, 45, day=14))
check("14e) İkinci tur çizelgeyi DEĞİŞTİRMEDİ (idempotent, K8)",
      stats14b.get("adapted", 0) == 0, f"stats={stats14b}")

db.expire_all()
_plan14b = plan_service.plan_for_date(db, u14, b14, datetime(2026, 8, 14).date())
check("14f) Plan içeriği değişmedi (yazma olmadı)",
      _plan14b.content.get("schedule") == _sched_before
      and str((_plan14b.content.get("adaptation") or {}).get("log_summary"))
      == _updated_before,
      "içerik değişti")

# 14g) plan_service tekilliği: aynı gün için tek satır
_cnt14 = (db.query(SleepPlan)
          .filter(SleepPlan.baby_id == b14.id,
                  SleepPlan.plan_date == datetime(2026, 8, 14).date()).count())
check("14g) Aynı güne tek plan satırı (UPSERT)", _cnt14 == 1, f"adet={_cnt14}")

# =============================================================================
# 15) "BEBEĞİNİZ UYANDI MI?" — gecikmiş açık uyku kaydı (v2.4.3)
# =============================================================================
# NEDEN: açık kalan kayıt yalnız kendini bozmaz; çizelge zincir olduğu için
# günün geri kalanı tahmine döner (adaptation.siradaki_blok.guven='tahmini').
# Anne sayacı kapatmayı unutmuştur; tek ihtiyacı küçük bir dürtme.

# --- 15a) Saf fonksiyon: 20 dakika eşiği --------------------------------------
_ACIK = [{"key": "nap_1", "type": "nap", "start_minute": 600,
          "end_minute": 690, "devam": True},
         {"key": "nap_2", "type": "nap", "start_minute": 870,
          "end_minute": 960}]
check("15a) Planlanan bitişten 19 dk sonra SORULMAZ",
      notifier.geciken_acik_bloklar(_ACIK, 690 + 19) == [], "")
check("15b) Tam 20 dk sonra sorulur",
      [b["key"] for b in notifier.geciken_acik_bloklar(_ACIK, 690 + 20)]
      == ["nap_1"], "")
check("15c) Kapanmış blok (devam yok) hiç sorulmaz",
      notifier.geciken_acik_bloklar(
          [dict(_ACIK[0], devam=False)], 690 + 60) == [], "")
check("15d) Sayacı motorun kapattığı kayıt sorulmaz (otomatik_kapandi)",
      notifier.geciken_acik_bloklar(
          [{"key": "nap_1", "type": "nap", "start_minute": 600,
            "end_minute": 690, "otomatik_kapandi": "bayat"}], 690 + 60) == [],
      "")

# --- 15e) Gece sessizliği: 23:00-06:00 ---------------------------------------
check("15e) 22:59 sessiz DEĞİL, 23:00 sessiz",
      not notifier.sessiz_saat(22 * 60 + 59) and notifier.sessiz_saat(23 * 60),
      "")
check("15f) 05:59 sessiz, 06:00 sessiz DEĞİL",
      notifier.sessiz_saat(5 * 60 + 59) and not notifier.sessiz_saat(6 * 60),
      "")

# --- Uçtan uca kurulum -------------------------------------------------------
def acik_uyku(user: User, baby: Baby, day: int, local_h: int, local_m: int = 0):
    """Bitişi GİRİLMEMİŞ uyku kaydı (anne sayacı kapatmamış)."""
    row = SleepLog(user_id=user.id, baby_id=baby.id, type="sleep",
                   started_at=utc_at(local_h, local_m, day=day), ended_at=None)
    db.add(row)
    db.commit()
    return row


# nap_1 10:00-11:30; kayıt 10:00'da açılıyor, tur 12:00'de koşuyor:
# planlanan bitişi 30 dk aşmış, K17 otomatik kapatma eşiğinin (3 saat) altında.
u15 = new_user("n15@tavsansmoke.com")
b15, p15 = new_plan(u15, "Zeynep", datetime(2026, 8, 20).date())
acik_uyku(u15, b15, 20, 10, 0)
_t15 = add_token(u15, "ExponentPushToken[UYANDI]")
_t15.last_seen_at = utc_at(6, 0, day=20)          # uygulama saatlerdir kapalı
db.commit()

SENT.clear()
_s15 = notifier.run_reminder_cycle(db, now=utc_at(12, 0, day=20))
check("15g) Gecikmiş açık kayıt → hatırlatma gönderildi",
      sent_to("ExponentPushToken[UYANDI]") == 1, f"stats={_s15}")
_m15 = next((m for tur in SENT for m in tur
             if m.get("to") == "ExponentPushToken[UYANDI]"), {})
check("15h) Metin birebir sözleşmedeki gibi",
      _m15.get("title") == "Bebeğiniz uyandı mı?"
      and _m15.get("body") == ("Bir sonraki uykuyu hesaplayabilmemiz için "
                               "uyanma saatini girin."),
      f"title={_m15.get('title')!r} body={_m15.get('body')!r}")
check("15i) data.type='uyandi_mi' (mobil kayıt ekranına yönlendirir)",
      (_m15.get("data") or {}).get("type") == "uyandi_mi",
      str(_m15.get("data")))

# --- 15j) İdempotency: aynı blok ikinci turda sorulmaz -----------------------
SENT.clear()
notifier.run_reminder_cycle(db, now=utc_at(12, 15, day=20))
check("15j) Aynı açık kayıt İKİNCİ turda sorulmaz",
      sent_to("ExponentPushToken[UYANDI]") == 0, str(SENT))

# --- 15k) Gece sessizliğinde gönderilmez -------------------------------------
u15b = new_user("n15b@tavsansmoke.com")
b15b, p15b = new_plan(u15b, "Mert", datetime(2026, 8, 21).date())
acik_uyku(u15b, b15b, 21, 10, 0)
_t15b = add_token(u15b, "ExponentPushToken[GECE]")
_t15b.last_seen_at = utc_at(6, 0, day=21)
db.commit()

SENT.clear()
notifier.run_reminder_cycle(db, now=utc_at(23, 30, day=21))
check("15k) 23:30'da hatırlatma gönderilmez (gece sessizliği)",
      sent_to("ExponentPushToken[GECE]") == 0, str(SENT))

# --- 15l) Uygulama AÇIKSA backend susar (mobil yerel bildirimi gösterir) -----
u15c = new_user("n15c@tavsansmoke.com")
b15c, p15c = new_plan(u15c, "Ayaz", datetime(2026, 8, 22).date())
acik_uyku(u15c, b15c, 22, 10, 0)
_t15c = add_token(u15c, "ExponentPushToken[ACIK]")
_t15c.last_seen_at = utc_at(11, 55, day=22)       # 5 dk önce görüldü
db.commit()

SENT.clear()
_s15c = notifier.run_reminder_cycle(db, now=utc_at(12, 0, day=22))
check("15l) Uygulama az önce görüldüyse backend sormaz",
      sent_to("ExponentPushToken[ACIK]") == 0, f"stats={_s15c}")
check("15m) Susma sebebi istatistikte görünür",
      _s15c.get("uyandimi_uygulama_acik", 0) >= 1, str(_s15c))

# --- 15n) Günlük kota: kullanıcı başına en çok 3 -----------------------------
u15d = new_user("n15d@tavsansmoke.com")
b15d, p15d = new_plan(u15d, "Poyraz", datetime(2026, 8, 23).date())
acik_uyku(u15d, b15d, 23, 10, 0)
_t15d = add_token(u15d, "ExponentPushToken[KOTA]")
_t15d.last_seen_at = utc_at(6, 0, day=23)
for _i in range(3):                       # bugün zaten 3 kez soruldu
    db.add(SentNotification(user_id=u15d.id, plan_id=p15d.id,
                            block_key=f"2026-08-23:uyandimi:onceki_{_i}"))
db.commit()

SENT.clear()
_s15d = notifier.run_reminder_cycle(db, now=utc_at(12, 0, day=23))
check("15n) Günde 3 soru dolduysa dördüncüsü gönderilmez",
      sent_to("ExponentPushToken[KOTA]") == 0, f"stats={_s15d}")
check("15o) Kota aşımı istatistikte görünür",
      _s15d.get("uyandimi_kota", 0) >= 1, str(_s15d))

# --- 15p) Kayıt KAPALIYSA hiç sorulmaz ---------------------------------------
u15e = new_user("n15e@tavsansmoke.com")
b15e, p15e = new_plan(u15e, "Defne", datetime(2026, 8, 24).date())
db.add(SleepLog(user_id=u15e.id, baby_id=b15e.id, type="sleep",
                started_at=utc_at(10, 0, day=24),
                ended_at=utc_at(11, 20, day=24)))
_t15e = add_token(u15e, "ExponentPushToken[KAPALI]")
_t15e.last_seen_at = utc_at(6, 0, day=24)
db.commit()

SENT.clear()
notifier.run_reminder_cycle(db, now=utc_at(12, 0, day=24))
check("15p) Bitişi girilmiş kayıt için hatırlatma YOK",
      sent_to("ExponentPushToken[KAPALI]") == 0, str(SENT))

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("BİLDİRİM ALTYAPISI TEST SONUÇLARI (Faz 6.2)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    print(f"[{mark}] {name}")
    if detail and not ok:
        print(f"       {detail}")
print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} gecti")
print("=" * 74)
sys.exit(0 if passed == len(results) else 1)
