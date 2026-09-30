"""
Topluluk v2 (2026-10-01) — tasarım v2 sözleşmesi + topluluk bildirimleri.

LLM YOK (Haiku mock), ağ YOK (Expo mock), prod DB YOK: geçici sqlite + TestClient.

  A  Kategoriler: eski `categories` aynen + v2 `kategoriler` [{key, ad, konu_sayisi}]
  B  Konu açma: eski anahtar → v2 sınıflandırma; v2 anahtar → eski anahtar eşlemesi
  C  Liste filtreleri: kategori, filtre=uzman|benim, q
  D  Anonim: "Anonim anne"/"anonim", yazar kimliği dönmez, gerçek yazar DB'de; uzman anonim yazamaz
  E  Avatar: /users/me sabit varsayılan, PATCH, geçersiz 422, listede yansır
  F  Kaydet: POST/DELETE bookmark, kaydedildi_mi, GET /community/bookmarks
  G  Haftanın konusu: mod sabitler, ayrı alanda döner, items'ta tekrar yok; mod değilse 403
  H  Kişi adı yok: uzman "Tavşan Uykusu" + "Uzman"; hiçbir yanıtta takma ad geçmez
  I  Bildirimler: cevap / uzman / cevabına yanıt / kendine yok / tekrar yok / veri
  J  Sessiz saat → kuyruk → 07:00 tek özet
  K  Günlük tavan 5
  L  Faydalı: toplu, sahibin beğenisi sayılmaz, 3 saat kuralı, 0 ise yok, tercih
  M  Tercihler: iki yeni anahtar GET/PATCH, eski/yeni cevap anahtarı eşlenir

Çalıştırma: python tests/test_topluluk_v2.py
"""
import json
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "topluluk_v2_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import CommunityProfile, Like, PushToken, Reply, Thread, User  # noqa: E402
from api.models.community_v2 import ToplulukBildirimi  # noqa: E402
from api.main import app                               # noqa: E402
from api.services import moderation, notifier, topluluk, topluluk_bildirim  # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))
GUNDUZ = datetime(2026, 9, 30, 14, 0, tzinfo=TR)

moderation.classify = lambda text: {"izin": True, "sebep": "temiz", "guven": 0.95}
_PUSH = {"calls": []}


def _fake_push(messages):
    _PUSH["calls"].extend(messages)
    return [{"status": "ok"} for _ in messages]


notifier.send_expo_push = _fake_push


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def reg(email):
    return client.post("/api/v1/auth/register",
                       json={"email": email, "password": "TestPass123!"}).json()["access_token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def uid(tok):
    from api.services.security import decode_access_token
    return _uuid.UUID(decode_access_token(tok)["sub"])


def anne(email, nick, **alan):
    tok = reg(email)
    client.post("/api/v1/community/profile", headers=H(tok), json={"nickname": nick})
    db = SessionLocal()
    try:
        p = db.query(CommunityProfile).filter(CommunityProfile.user_id == uid(tok)).one()
        p.post_count = 5
        for k, v in alan.items():
            setattr(p, k, v)
        db.add(PushToken(user_id=uid(tok), expo_token=f"ExponentPushToken[{nick}]"))
        db.commit()
    finally:
        db.close()
    return tok


def konu(tok, **govde):
    moderation.rate_reset()
    with saat_sabitle(GUNDUZ):
        return client.post("/api/v1/community/threads", headers=H(tok), json=govde)


def cevap(tok, tid, **govde):
    moderation.rate_reset()
    with saat_sabitle(GUNDUZ):
        return client.post(f"/api/v1/community/threads/{tid}/replies", headers=H(tok), json=govde)


def liste(tok, **p):
    q = "&".join(f"{k}={v}" for k, v in p.items())
    return client.get(f"/api/v1/community/threads?{q}", headers=H(tok))


AYSE = anne("ayse@example.com", "AyseAnne")
FATMA = anne("fatma@example.com", "FatmaAnne")
ZEYNEP = anne("zeynep@example.com", "ZeynepAnne")
UZMAN = anne("uzman@example.com", "ilaydakani", is_expert=True)
RESMI = anne("resmi@example.com", "Tavşan Uykusu Ekibi", is_official=True, is_moderator=True)

# --- B — konu açma ----------------------------------------------------------------
r_eski = konu(AYSE, category="uyku", title="Gündüz uykuları 30 dakikada bitiyor",
              body="Oğlum 7 aylık, şekerleme bile yapmıyor")
r_v2 = konu(AYSE, kategori="egitim", title="Eğitimin 4. günündeyiz", body="Ağlama azaldı mı?")
r_v2c = konu(FATMA, category="diger", title="Diş çıkarma dönemi", body="Genel soru")
r_gece = konu(FATMA, category="uyku", title="Gece 5 kez uyanıyor", body="Ne yapmalıyım")
r_bes = konu(ZEYNEP, kategori="beslenme", title="Gece emzirme", body="Sütten kesme")
r_bos = konu(ZEYNEP, title="kategorisiz", body="olmaz")
check("B1) Eski 'uyku' + 'gündüz/şekerleme' → kategori gunduz_uykulari, category uyku",
      r_eski.status_code == 201 and r_eski.json()["kategori"] == "gunduz_uykulari"
      and r_eski.json()["category"] == "uyku", r_eski.text[:200])
check("B2) v2 kategori=egitim → eski category 'uyku'",
      r_v2.json()["kategori"] == "egitim" and r_v2.json()["category"] == "uyku", r_v2.text[:200])
check("B3) category alanında v2 anahtar ('diger') → kategori diger, category oneri",
      r_v2c.json()["kategori"] == "diger" and r_v2c.json()["category"] == "oneri", r_v2c.text[:200])
check("B4) Eski 'uyku' + 'gece/uyan' → gece_uyanmasi",
      r_gece.json()["kategori"] == "gece_uyanmasi", r_gece.text[:200])
check("B5) Kategori hiç yok → 422", r_bos.status_code == 422, r_bos.status_code)
T_GUNDUZ, T_EGITIM, T_DIGER, T_GECE, T_BES = (r.json()["id"] for r in
                                               (r_eski, r_v2, r_v2c, r_gece, r_bes))

# --- A — kategoriler -------------------------------------------------------------
kat = client.get("/api/v1/community/categories", headers=H(AYSE)).json()
check("A1) Eski `categories` aynen: 5 eski anahtar + thread_count",
      [c["key"] for c in kat["categories"]] == ["uyku", "beslenme", "gelisim", "anne_hali", "oneri"]
      and dict((c["key"], c["thread_count"]) for c in kat["categories"])["uyku"] == 3, kat)
check("A2) v2 `kategoriler`: 5 anahtar, ad, konu_sayisi",
      kat["kategoriler"] == [
          {"key": "gece_uyanmasi", "ad": "Gece uyanması", "konu_sayisi": 1},
          {"key": "gunduz_uykulari", "ad": "Gündüz uykuları", "konu_sayisi": 1},
          {"key": "egitim", "ad": "Uyku eğitimi", "konu_sayisi": 1},
          {"key": "beslenme", "ad": "Beslenme", "konu_sayisi": 1},
          {"key": "diger", "ad": "Diğer", "konu_sayisi": 1}], kat["kategoriler"])

# --- H + I (uzman cevabı, bildirim) ---------------------------------------------
_PUSH["calls"].clear()
r_uz = cevap(UZMAN, T_GECE, body="Gece uyanmalarında önce 5 dakika bekleyin")
check("I1) Uzman cevabı → konu sahibine 'Tavşan Uykusu sorunuzu cevapladı' + başlık",
      any(m["title"] == "Tavşan Uykusu sorunuzu cevapladı" and m["body"] == "Gece 5 kez uyanıyor"
          and m["to"] == "ExponentPushToken[FatmaAnne]" for m in _PUSH["calls"]), _PUSH["calls"])
check("H1) Uzman cevabı: yazar Tavşan Uykusu / tavsan / Uzman; nickname da gizli",
      r_uz.json()["yazar"] == {"gorunen_ad": "Tavşan Uykusu", "avatar": "tavsan", "rozet": "Uzman"}
      and r_uz.json()["nickname"] == "Tavşan Uykusu", r_uz.text[:300])

_PUSH["calls"].clear()
r_c1 = cevap(AYSE, T_GECE, body="Bizde de aynı sorun vardı")
check("I2) Normal cevap → 'Sorunuza yeni bir cevap var' + başlığın ilk 40 karakteri",
      any(m["title"] == "Sorunuza yeni bir cevap var" and m["body"] == "Gece 5 kez uyanıyor"
          for m in _PUSH["calls"]), _PUSH["calls"])
check("I3) Push verisi {type: topluluk, thread_id, reply_id}",
      _PUSH["calls"] and _PUSH["calls"][0]["data"] == {
          "type": "topluluk", "thread_id": T_GECE, "reply_id": r_c1.json()["id"]}, _PUSH["calls"][:1])
check("I4) Bildirimde cevap verenin kimliği yok",
      not any("AyseAnne" in m["title"] + m["body"] for m in _PUSH["calls"]))

_PUSH["calls"].clear()
r_c2 = cevap(ZEYNEP, T_GECE, body="Ayşe hanım siz ne yaptınız?", yanitlanan_cevap_id=r_c1.json()["id"])
kime = {m["to"]: m["title"] for m in _PUSH["calls"]}
check("I5) Cevaba cevap → cevap sahibine 'Cevabınıza yanıt geldi', konu sahibine yeni cevap",
      kime.get("ExponentPushToken[AyseAnne]") == "Cevabınıza yanıt geldi"
      and kime.get("ExponentPushToken[FatmaAnne]") == "Sorunuza yeni bir cevap var", kime)
check("I6) Yanıt alanı: yanitlanan_cevap_id döner",
      r_c2.json().get("yanitlanan_cevap_id") == r_c1.json()["id"], r_c2.text[:200])

_PUSH["calls"].clear()
cevap(FATMA, T_GECE, body="Kendi konuma kendim yazıyorum")
check("I7) Kendi konusuna kendi cevabı → bildirim YOK", _PUSH["calls"] == [], _PUSH["calls"])

db = SessionLocal()
t_obj, r_obj = db.get(Thread, _uuid.UUID(T_GECE)), db.get(Reply, _uuid.UUID(r_c1.json()["id"]))
_PUSH["calls"].clear()
tekrar = topluluk_bildirim.cevap_olayi(db, t_obj, r_obj, False, None, an=GUNDUZ)
check("I8) Aynı olay ikinci kez → 'tekrar', push YOK",
      list(tekrar.values()) == ["tekrar"] and _PUSH["calls"] == [], tekrar)
db.close()

# --- H — hiçbir yanıtta takma ad yok ------------------------------------------
tum = json.dumps([liste(AYSE).json(), client.get(f"/api/v1/community/threads/{T_GECE}",
                                                 headers=H(AYSE)).json()], ensure_ascii=False)
check("H2) Liste + detay yanıtlarında uzmanın takma adı ('ilaydakani') GEÇMİYOR",
      "ilaydakani" not in tum.lower() and "ilayda" not in tum.lower())

# --- C — filtreler ----------------------------------------------------------------
ids = lambda r: {i["id"] for i in r.json()["items"]}
check("C1) ?kategori=gece_uyanmasi → yalnız o konu", ids(liste(AYSE, kategori="gece_uyanmasi")) == {T_GECE})
check("C2) ?filtre=uzman → uzmanın cevapladığı konu", ids(liste(AYSE, filtre="uzman")) == {T_GECE})
check("C3) ?filtre=benim → Ayşe'nin iki konusu", ids(liste(AYSE, filtre="benim")) == {T_GUNDUZ, T_EGITIM})
check("C4) ?q=emzirme → metin araması", ids(liste(AYSE, q="emzirme")) == {T_BES})
check("C5) Eski ?category=uyku hâlâ çalışıyor", ids(liste(AYSE, category="uyku")) == {T_GUNDUZ, T_EGITIM, T_GECE})
m = next(i for i in liste(AYSE).json()["items"] if i["id"] == T_GECE)
check("C6) Madde alanları: kategori, uzman_cevapladi, faydali_sayisi, cevap_sayisi, kaydedildi_mi, yazar",
      m["kategori"] == "gece_uyanmasi" and m["uzman_cevapladi"] is True
      and m["cevap_sayisi"] == 4 and m["faydali_sayisi"] == 0 and m["kaydedildi_mi"] is False
      and set(m["yazar"]) == {"gorunen_ad", "avatar", "rozet"}, m)
check("C7) filtre geçersiz → 422", liste(AYSE, filtre="hepsi").status_code == 422)

# --- D — anonim -----------------------------------------------------------------
r_an = konu(ZEYNEP, kategori="diger", title="Kimseye söyleyemedim", body="Çok yorgunum", anonim=True)
an_id = r_an.json()["id"]
bak = next(i for i in liste(AYSE).json()["items"] if i["id"] == an_id)
check("D1) Başkası görür: gorunen_ad 'Anonim anne', avatar 'anonim', author_id null, nickname da anonim",
      bak["yazar"] == {"gorunen_ad": "Anonim anne", "avatar": "anonim", "rozet": None}
      and bak["author_id"] is None and bak["nickname"] == "Anonim anne" and bak["anonim"] is True, bak)
sahibi = next(i for i in liste(ZEYNEP).json()["items"] if i["id"] == an_id)
check("D2) Sahibi görür: benim=true (yine anonim görünür)", sahibi["benim"] is True, sahibi)
db = SessionLocal()
check("D3) Gerçek yazar DB'de duruyor (moderasyon)",
      db.get(Thread, _uuid.UUID(an_id)).user_id == uid(ZEYNEP))
db.close()
r_uzan = konu(UZMAN, kategori="egitim", title="Uzman anonim deneme", body="olmamalı", anonim=True)
check("D4) Uzman anonim yazamaz (anonim=false kaydedilir)",
      r_uzan.json()["anonim"] is False and r_uzan.json()["yazar"]["rozet"] == "Uzman", r_uzan.text[:200])
r_anc = cevap(AYSE, an_id, body="Yalnız değilsiniz", anonim=True)
check("D5) Anonim cevap: 'Anonim anne', author_id null",
      r_anc.json()["yazar"]["gorunen_ad"] == "Anonim anne" and r_anc.json()["author_id"] is None, r_anc.text[:200])

# --- E — avatar -----------------------------------------------------------------
me = client.get("/api/v1/users/me", headers=H(AYSE)).json()
check("E1) GET /users/me: varsayılan avatar kimlikten SABİT, avatar_secildi=false",
      me["avatar"] == topluluk.avatar_of(uid(AYSE)) and me["avatar_secildi"] is False
      and client.get("/api/v1/users/me", headers=H(AYSE)).json()["avatar"] == me["avatar"], me)
pa = client.patch("/api/v1/users/me", headers=H(AYSE), json={"avatar": "kedi"})
check("E2) PATCH /users/me {avatar: kedi} → kedi, avatar_secildi=true",
      pa.status_code == 200 and pa.json()["avatar"] == "kedi" and pa.json()["avatar_secildi"] is True, pa.text)
check("E3) Geçersiz avatar → 422",
      client.patch("/api/v1/users/me", headers=H(AYSE), json={"avatar": "aslan"}).status_code == 422)
check("E4) Listede Ayşe'nin konusu avatar 'kedi'",
      next(i for i in liste(FATMA).json()["items"] if i["id"] == T_GUNDUZ)["yazar"]["avatar"] == "kedi")
check("E5) /community/profile yanıtında yazar alanı",
      client.get("/api/v1/community/profile", headers=H(AYSE)).json()["yazar"]["avatar"] == "kedi")

# --- F — kaydet -----------------------------------------------------------------
b1 = client.post(f"/api/v1/community/threads/{T_BES}/bookmark", headers=H(AYSE))
b1b = client.post(f"/api/v1/community/threads/{T_BES}/bookmark", headers=H(AYSE))
check("F1) POST bookmark → {kaydedildi: true}; ikinci kez de aynı",
      b1.json() == {"kaydedildi": True} and b1b.json() == {"kaydedildi": True}, b1.text)
check("F2) Listede kaydedildi_mi=true (yalnız kaydeden için)",
      next(i for i in liste(AYSE).json()["items"] if i["id"] == T_BES)["kaydedildi_mi"] is True
      and next(i for i in liste(FATMA).json()["items"] if i["id"] == T_BES)["kaydedildi_mi"] is False)
bl = client.get("/api/v1/community/bookmarks", headers=H(AYSE)).json()
check("F3) GET /community/bookmarks → kaydedilen konu", [i["id"] for i in bl["items"]] == [T_BES], bl)
d1 = client.delete(f"/api/v1/community/threads/{T_BES}/bookmark", headers=H(AYSE))
check("F4) DELETE bookmark → {kaydedildi: false}, liste boş",
      d1.json() == {"kaydedildi": False}
      and client.get("/api/v1/community/bookmarks", headers=H(AYSE)).json()["items"] == [])

# --- G — haftanın konusu --------------------------------------------------------
r_hk = konu(RESMI, kategori="egitim", title="Haftanın konusu: ilk gece", body="Deneyimlerinizi paylaşın")
hk = r_hk.json()["id"]
check("G1) Moderatör olmayan sabitleyemez → 403",
      client.post("/api/v1/community/mod/pin", headers=H(AYSE), json={"thread_id": hk}).status_code == 403)
pn = client.post("/api/v1/community/mod/pin", headers=H(RESMI), json={"thread_id": hk, "sabit": True})
ls = liste(AYSE).json()
check("G2) haftanin_konusu ayrı alanda, items'ta YOK",
      pn.status_code == 200 and ls["haftanin_konusu"]["id"] == hk
      and hk not in {i["id"] for i in ls["items"]}, ls.get("haftanin_konusu"))
check("G3) Resmi hesap yazarı: rozet 'Resmi'", ls["haftanin_konusu"]["yazar"]["rozet"] == "Resmi")
client.post("/api/v1/community/mod/pin", headers=H(RESMI), json={"thread_id": hk, "sabit": False})
check("G4) Sabitleme kaldırılınca haftanin_konusu null", liste(AYSE).json()["haftanin_konusu"] is None)

# --- J — sessiz saat + sabah özeti -------------------------------------------------
db = SessionLocal()
GECE = datetime(2026, 9, 30, 23, 30, tzinfo=TR)
t_gunduz = db.get(Thread, _uuid.UUID(T_GUNDUZ))
_PUSH["calls"].clear()
for i in range(2):
    rr = Reply(thread_id=t_gunduz.id, user_id=uid(FATMA), body=f"gece cevabı {i}", status="published")
    db.add(rr); db.commit(); db.refresh(rr)
    topluluk_bildirim.cevap_olayi(db, t_gunduz, rr, False, None, an=GECE + timedelta(minutes=i))
check("J1) 23:30'da cevap → push YOK, kuyrukta 'bekliyor'",
      _PUSH["calls"] == [] and db.query(ToplulukBildirimi).filter(
          ToplulukBildirimi.user_id == uid(AYSE), ToplulukBildirimi.durum == "bekliyor").count() == 2)
check("J2) 06:50'de özet çalışmaz (hâlâ sessiz)",
      topluluk_bildirim.sabah_ozeti(db, datetime(2026, 10, 1, 6, 50, tzinfo=TR)) == 0 and _PUSH["calls"] == [])
n_oz = topluluk_bildirim.sabah_ozeti(db, datetime(2026, 10, 1, 7, 5, tzinfo=TR))
oz = [m for m in _PUSH["calls"] if m["to"] == "ExponentPushToken[AyseAnne]"]
check("J3) 07:05 → TEK özet 'Gece sorunuza 2 cevap geldi', thread_id tek konu",
      n_oz == 1 and len(oz) == 1 and oz[0]["title"] == "Gece sorunuza 2 cevap geldi"
      and oz[0]["data"] == {"type": "topluluk", "thread_id": T_GUNDUZ}, oz)
_PUSH["calls"].clear()
check("J4) Özet ikinci kez gitmez (kuyruk boşaldı)",
      topluluk_bildirim.sabah_ozeti(db, datetime(2026, 10, 1, 7, 20, tzinfo=TR)) == 0 and _PUSH["calls"] == [])

# --- K — günlük tavan 5 ------------------------------------------------------------
GUN2 = datetime(2026, 10, 2, 12, 0, tzinfo=TR)
_PUSH["calls"].clear()
durumlar = [topluluk_bildirim._gonder(db, uid(ZEYNEP), f"tavan:{i}", "cevap", "Sorunuza yeni bir cevap var",
                                      "x", GUN2 + timedelta(minutes=i), None, None) for i in range(6)]
check("K1) Günde 5 gönderim, 6. 'atlandi'",
      durumlar == ["gonderildi"] * 5 + ["atlandi"] and len(_PUSH["calls"]) == 5, durumlar)

# --- L — faydalı -----------------------------------------------------------------
B = datetime(2026, 10, 3, 9, 0, tzinfo=TR)
t_bes = db.get(Thread, _uuid.UUID(T_BES))
t_bes.created_at = (B - timedelta(hours=1)).astimezone(timezone.utc)
t_bes.faydali_bildirim_at = None
db.commit()


def begeni(tok, an):
    # Zamanlar UTC saklanır (üretimle aynı): SQLite saat dilimini atıyor.
    db.add(Like(user_id=uid(tok), target_type="thread", target_id=t_bes.id,
                created_at=an.astimezone(timezone.utc)))
    db.commit()


begeni(AYSE, B); begeni(FATMA, B + timedelta(minutes=5)); begeni(ZEYNEP, B + timedelta(minutes=6))  # Zeynep = sahibi
_PUSH["calls"].clear()
n1 = topluluk_bildirim.faydali_turu(db, B + timedelta(hours=1))
check("L1) 2 anne (sahibi hariç) → 'Sorunuz 2 anneye faydalı geldi 💛' + başlık",
      n1 == 1 and _PUSH["calls"][0]["title"] == "Sorunuz 2 anneye faydalı geldi 💛"
      and _PUSH["calls"][0]["body"] == "Gece emzirme"
      and _PUSH["calls"][0]["data"] == {"type": "topluluk", "thread_id": T_BES}, _PUSH["calls"][:1])
yeni = anne("elif@example.com", "ElifAnne")
begeni(yeni, B + timedelta(hours=1, minutes=30))
_PUSH["calls"].clear()
check("L2) 1 saat sonra yeni beğeni → 3 saat dolmadı, gönderilmez",
      topluluk_bildirim.faydali_turu(db, B + timedelta(hours=2)) == 0 and _PUSH["calls"] == [])
n3 = topluluk_bildirim.faydali_turu(db, B + timedelta(hours=4, minutes=1))
check("L3) 3 saat sonra → yalnız YENİ sayı: 'Sorunuz 1 anneye faydalı geldi 💛'",
      n3 == 1 and _PUSH["calls"][-1]["title"] == "Sorunuz 1 anneye faydalı geldi 💛", _PUSH["calls"][-1:])
_PUSH["calls"].clear()
check("L4) Yeni beğeni yoksa (0) gönderilmez",
      topluluk_bildirim.faydali_turu(db, B + timedelta(hours=8)) == 0 and _PUSH["calls"] == [])
check("L5) Sessiz saatte faydalı turu çalışmaz",
      topluluk_bildirim.faydali_turu(db, datetime(2026, 10, 3, 23, 30, tzinfo=TR)) == 0)
db.close()

# --- M — tercihler ---------------------------------------------------------------
pr = client.get("/api/v1/notifications/preferences", headers=H(FATMA)).json()
check("M1) GET tercihler: iki yeni anahtar varsayılan açık",
      pr.get("topluluk_cevap_bildirimi") is True and pr.get("topluluk_faydali_bildirimi") is True, pr)
pp = client.patch("/api/v1/notifications/preferences", headers=H(FATMA),
                  json={"topluluk_cevap_bildirimi": False}).json()
check("M2) topluluk_cevap_bildirimi=false → eski community_replies de false",
      pp["topluluk_cevap_bildirimi"] is False and pp["community_replies"] is False, pp)
_PUSH["calls"].clear()
cevap(AYSE, T_GECE, body="Tercih kapalıyken cevap")
check("M3) Tercih kapalı → cevap bildirimi YOK",
      not any(m["to"] == "ExponentPushToken[FatmaAnne]" for m in _PUSH["calls"]), _PUSH["calls"])
client.patch("/api/v1/notifications/preferences", headers=H(ZEYNEP),
             json={"topluluk_faydali_bildirimi": False})
db = SessionLocal()
t_bes = db.get(Thread, _uuid.UUID(T_BES))
begeni2 = Like(user_id=uid(FATMA), target_type="reply", target_id=_uuid.uuid4(),
               created_at=B + timedelta(hours=9))
db.add(Like(user_id=uid(RESMI), target_type="thread", target_id=t_bes.id,
            created_at=B + timedelta(hours=9)))
db.commit()
_PUSH["calls"].clear()
check("M4) topluluk_faydali_bildirimi=false → faydalı bildirimi YOK",
      topluluk_bildirim.faydali_turu(db, B + timedelta(hours=12)) == 0 and _PUSH["calls"] == [])
db.close()

print("=" * 78)
print("TOPLULUK v2 + BİLDİRİMLER")
print("=" * 78)
gecen = 0
for ad, ok, detay in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {ad}")
    if not ok and detay:
        print(f"         → {detay[:500]}")
    gecen += ok
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
print("=" * 78)
sys.exit(0 if gecen == len(results) else 1)
