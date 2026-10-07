"""
Topluluk: İÇERİKTEN engelleme + şikâyet (App Store 1.2 — kullanıcı içeriği).

NEDEN VAR (2026-10-07): mobil yazarın kullanıcı kimliğini bilmiyor, /block'u takma
adla deniyor ve 422 alıyordu — engelleme hiç çalışmıyordu. Artık engelleme konu ya
da cevap kimliğiyle yapılır, yazarı sunucu bulur.

Kapsam:
  E1  Konudan engelle → yazarın konuları listede, cevapları detayda gizlenir
  E2  Cevaptan engelle → aynı
  E3  Anonim içerikten engel: yanıt ve liste yazarı GÖSTERMEZ (ad "Anonim anne",
      blocked_user_id null); engel_id ile kaldırılır, içerik geri görünür
  E4  Kendi içeriği → 400; olmayan içerik → 404; hesabı silinmiş yazar → 404;
      tekrar engelleme idempotent (aynı engel_id)
  E5  Eski uç (/block user_id) çalışıyor; listede engel_id da var; eski kaldırma
      ucu (/block/{user_id}) çalışıyor
  E6  Şikâyet: konu ve cevap 200; aynı kişiden tekrar 409; olmayan 404

Çalıştırma: python tests/test_engelleme.py
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "engelleme_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "engelleme_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, engine                             # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.services import moderation                         # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle    # noqa: E402
muhurle()
# Şikâyet tek kişiden gelince senkron yapay zekâ değerlendirmesi yapar — burada
# ağ yok: "izin ver" kararı dönen sahte.
moderation.classify = lambda metin: {"izin": True, "guven": 0.9, "sebep": None}

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def anne(email, ad):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    client.post("/api/v1/community/profile", headers=h, json={"nickname": ad})
    return h


def konu(h, baslik, anonim=False):
    r = client.post("/api/v1/community/threads", headers=h, json={
        "title": baslik, "body": f"{baslik} hakkında bir soru yazıyorum.",
        "kategori": "gece_uyanmasi", "anonim": anonim})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def cevap(h, tid, metin="Bizde de böyle oldu, sabırla geçti.", anonim=False):
    r = client.post(f"/api/v1/community/threads/{tid}/replies", headers=h,
                    json={"body": metin, "anonim": anonim})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def basliklar(h):
    return {x["title"] for x in client.get("/api/v1/community/threads", headers=h).json()["items"]}


def cevaplar(h, tid):
    return {x["id"] for x in client.get(f"/api/v1/community/threads/{tid}", headers=h).json()["replies"]}


def engelle(h, tur, hedef):
    return client.post("/api/v1/community/block/icerik", headers=h,
                       json={"target_type": tur, "target_id": hedef})


A = anne("a@test.com", "AyseAnne")      # engelleyen
B = anne("b@test.com", "BerilAnne")     # engellenen (konu)
C = anne("c@test.com", "CerenAnne")     # engellenen (cevap)
D = anne("d@test.com", "DefneAnne")     # anonim yazar
E = anne("e@test.com", "ElifAnne")      # eski uç

tA = konu(A, "Ayşe'nin sorusu")
tB = konu(B, "Beril'in sorusu")
rC = cevap(C, tA)
rB = cevap(B, tA, "Beril'in cevabı burada.")

# E1 -------------------------------------------------------------------------
r = engelle(A, "thread", tB)
check("E1a) Konudan engelle 200 + engel_id + görünen ad",
      r.status_code == 200 and r.json().get("engel_id") and r.json().get("gorunen_ad") == "BerilAnne",
      r.text[:160])
check("E1b) Engellenenin konusu listede gizli", "Beril'in sorusu" not in basliklar(A), basliklar(A))
check("E1c) Engellenenin cevabı detayda gizli", rB not in cevaplar(A, tA), "")
check("E1d) Başkası için görünür kalıyor", "Beril'in sorusu" in basliklar(C), "")

# E2 -------------------------------------------------------------------------
r = engelle(A, "reply", rC)
check("E2) Cevaptan engelle 200; cevabı gizlendi",
      r.status_code == 200 and rC not in cevaplar(A, tA), r.text[:160])

# E3 -------------------------------------------------------------------------
tD = konu(D, "Anonim bir annenin sorusu", anonim=True)
r = engelle(A, "thread", tD)
j = r.json()
check("E3a) Anonim içerikten engel: yanıtta ad 'Anonim anne', kimlik yok",
      r.status_code == 200 and j.get("gorunen_ad") == "Anonim anne"
      and "user_id" not in j and "blocked_user_id" not in j, j)
liste = client.get("/api/v1/community/blocks", headers=A).json()
anon = [x for x in liste if x.get("anonim")]
check("E3b) Listede anonim engel: ad 'Anonim anne', blocked_user_id null, engel_id var",
      len(anon) == 1 and anon[0]["nickname"] == "Anonim anne"
      and anon[0]["blocked_user_id"] is None and anon[0]["engel_id"] == j.get("engel_id"), anon)
check("E3c) Anonim yazarın konusu gizli", "Anonim bir annenin sorusu" not in basliklar(A), "")
r = client.delete(f"/api/v1/community/block/kayit/{j['engel_id']}", headers=A)
check("E3d) engel_id ile kaldırıldı, içerik geri görünür",
      r.status_code == 200 and "Anonim bir annenin sorusu" in basliklar(A), r.text[:120])
r2 = client.delete(f"/api/v1/community/block/kayit/{j['engel_id']}", headers=A)
check("E3e) Kaldırma idempotent", r2.status_code == 200, r2.status_code)

# E4 -------------------------------------------------------------------------
check("E4a) Kendi içeriği → 400", engelle(A, "thread", tA).status_code == 400, "")
check("E4b) Olmayan içerik → 404", engelle(A, "reply", str(uuid.uuid4())).status_code == 404, "")
r1, r2 = engelle(A, "thread", tB), engelle(A, "reply", rB)
check("E4c) Aynı yazarı tekrar engelleme idempotent (aynı engel_id)",
      r1.status_code == 200 and r2.status_code == 200
      and r1.json()["engel_id"] == r2.json()["engel_id"], (r1.text[:80], r2.text[:80]))
Z = anne("z@test.com", "ZeynepAnne")
tZ = konu(Z, "Silinecek annenin sorusu")
client.delete("/api/v1/auth/account", headers=Z)
check("E4d) Hesabı silinmiş yazar → 404", engelle(A, "thread", tZ).status_code == 404, "")
check("E4e) Geçersiz tür → 422",
      client.post("/api/v1/community/block/icerik", headers=A,
                  json={"target_type": "user", "target_id": tB}).status_code == 422, "")

# E5 -------------------------------------------------------------------------
b_uid = next((x["author_id"] for x in client.get("/api/v1/community/threads", headers=E)
              .json()["items"] if x["title"] == "Beril'in sorusu"), None)
r = client.post("/api/v1/community/block", headers=E, json={"user_id": b_uid})
eski = [x for x in client.get("/api/v1/community/blocks", headers=E).json()
        if x["blocked_user_id"] == b_uid]
check("E5a) Eski /block (user_id) çalışıyor; listede engel_id ve kimlik var",
      r.status_code == 200 and len(eski) == 1 and eski[0]["engel_id"]
      and eski[0]["anonim"] is False, (r.text[:100], eski))
r = client.delete(f"/api/v1/community/block/{b_uid}", headers=E)
check("E5b) Eski kaldırma ucu çalışıyor", r.status_code == 200
      and "Beril'in sorusu" in basliklar(E), r.text[:100])

# E6 -------------------------------------------------------------------------
def sikayet(h, tur, hedef):
    return client.post("/api/v1/community/report", headers=h,
                       json={"target_type": tur, "target_id": hedef, "reason": "hakaret",
                             "note": "uygunsuz dil"})


tE = konu(E, "Şikâyet edilecek konu")
rE = cevap(E, tE, "Şikâyet edilecek cevap metni.")
r1, r2 = sikayet(A, "thread", tE), sikayet(A, "reply", rE)
check("E6a) Konu ve cevap şikâyeti 200", r1.status_code == 200 and r2.status_code == 200,
      (r1.text[:100], r2.text[:100]))
check("E6b) Aynı kişiden tekrar şikâyet 409", sikayet(A, "thread", tE).status_code == 409, "")
check("E6c) Olmayan içerik 404", sikayet(A, "thread", str(uuid.uuid4())).status_code == 404, "")
r3 = sikayet(C, "thread", tE)
check("E6d) İkinci farklı kişi şikâyet edince içerik incelemeye alınır (gizlenir)",
      r3.status_code == 200 and "Şikâyet edilecek konu" not in basliklar(A), r3.text[:100])

check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

print("\n" + "=" * 74)
print("TOPLULUK — İÇERİKTEN ENGELLEME + ŞİKÂYET")
print("=" * 74)
_gecen = 0
for ad, ok, detay in results:
    print(f"  {'[PASS]' if ok else '[FAIL]'} {ad}")
    if not ok:
        print(f"         → {detay}")
    _gecen += int(ok)
print("-" * 74)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
print("=" * 74)
sys.exit(0 if _gecen == len(results) else 1)
