"""
Sor cevabını bildirme (2026-11-01) — deterministik, Anthropic istemcisi sahte.

  C  POST /chat yanıtında mesaj_id: bebekli cevap ve ÖNBELLEKTEN dönen cevap da
     kullanıcıya ait asistan satırı; satır soru ve bebeğe bağlı
  B  Bildirim: 200 {ok}, satır (soru/cevap kopyası, bebek, durum yeni); tekrar →
     200 ve aynı satır güncellenir (durum yeniden yeni)
  N  404: olmayan id, başkasının mesajı, kullanıcı (soru) satırı
  V  422: geçersiz/eksik sebep, 500+ karakter not
  L  429: günde 20 yeni bildirim; güncelleme sınıra takılmaz
  A  Admin listesi: tokensız 401, moderatör olmayan 403, moderatörde bebek adı maskeli
  K  KVKK dökümü; hesap silmede bildirimler de silinir

Çalıştırma: python tests/test_sor_bildirim.py
"""
import os
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "sor_bildirim_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.models import (AiCevapBildirimi, ChatMessage, CommunityProfile,  # noqa: E402
                        User)
from api.routers import sor_bildirim                        # noqa: E402
from api.services import kvkk                               # noqa: E402
from api.zaman import bugun_tr                              # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
chatbot.HAS_ANTHROPIC = True
chatbot.CACHE_PATH = Path(tempfile.gettempdir()) / "sor_bildirim_cache.json"
if chatbot.CACHE_PATH.exists():
    chatbot.CACHE_PATH.unlink()
chatbot._cache_state.update(loaded=False, entries=[], emb_matrix=None, emb_idx=[])
CEVAP = {"metin": "Ada için gece yatışı 19:30 uygundur."}


class _SahteIstemci:
    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kw):
        return SimpleNamespace(
            content=[SimpleNamespace(text=CEVAP["metin"])],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))


chatbot.Anthropic = _SahteIstemci

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


BUGUN = bugun_tr()


def kayit(eposta: str) -> dict:
    tok = client.post("/api/v1/auth/register", json={
        "email": eposta, "password": "TestPass123!"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


H = kayit("bildirim@gercek.com")
H2 = kayit("baska@gercek.com")
BID = client.post("/api/v1/babies", headers=H, json={
    "name": "Ada", "birth_date": (BUGUN - timedelta(days=216)).isoformat(),
    **TAM_PROFIL}).json()["id"]
db = SessionLocal()


def sor(h, mesaj, **ek):
    r = client.post("/api/v1/chat", headers=h, json={"message": mesaj, **ek})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def bildir(h, mesaj_id, **govde):
    return client.post(f"/api/v1/sor/mesajlar/{mesaj_id}/bildir", headers=h, json=govde)


# =============================================================================
# C — mesaj_id
# =============================================================================
j = sor(H, "Ada kaçta yatmalı?", baby_id=BID)
MID = j.get("mesaj_id")
db.expire_all()
m = db.get(ChatMessage, uuid.UUID(MID)) if MID else None
check("C) /chat yanıtında mesaj_id var", bool(MID), j)
check("C) mesaj_id kullanıcının asistan satırı, bebeğe ve soruya bağlı",
      m is not None and m.role == "assistant" and str(m.baby_id) == BID
      and m.content == j["answer"] and m.soru_id is not None
      and db.get(ChatMessage, m.soru_id).content == "Ada kaçta yatmalı?",
      (m.role, m.baby_id, m.soru_id) if m else None)

sor(H, "Uyku eğitimi kaç gün sürer?")
j2 = sor(H, "Uyku eğitimi kaç gün sürer?")
m2 = db.get(ChatMessage, uuid.UUID(j2["mesaj_id"])) if j2.get("mesaj_id") else None
check("C) Önbellekten dönen cevapta da mesaj_id (cached=true, kullanıcıya ait satır)",
      j2.get("cached") is True and m2 is not None and m2.role == "assistant"
      and m2.cached and str(m2.user_id) == str(db.query(User).filter_by(
          email="bildirim@gercek.com").one().id), (j2.get("cached"), j2.get("mesaj_id")))
check("C) Her cevabın ayrı id'si", len({MID, j2["mesaj_id"]}) == 2)

# =============================================================================
# B — bildirim ve idempotent tekrar
# =============================================================================
r = bildir(H, MID, sebep="programla_uyusmuyor", **{"not": "Planda 19:43 yazıyor"})
check("B) 200 {ok: true}", r.status_code == 200 and r.json() == {"ok": True}, r.text[:200])
db.expire_all()
b = db.query(AiCevapBildirimi).filter_by(mesaj_id=uuid.UUID(MID)).one()
check("B) Satır: sebep, not, soru/cevap kopyası, bebek, durum yeni",
      (b.sebep, b.not_, b.soru_metni, b.cevap_metni, str(b.baby_id), b.durum)
      == ("programla_uyusmuyor", "Planda 19:43 yazıyor", "Ada kaçta yatmalı?",
          j["answer"], BID, "yeni"), (b.sebep, b.not_, b.soru_metni))
b.durum = "incelendi"
db.commit()
r = bildir(H, MID, sebep="yanlis_bilgi", **{"not": None})
db.expire_all()
satirlar = db.query(AiCevapBildirimi).filter_by(mesaj_id=uuid.UUID(MID)).all()
check("B) Tekrar → 200, tek satır güncellendi (sebep, not boş, durum yeni)",
      r.status_code == 200 and len(satirlar) == 1
      and (satirlar[0].sebep, satirlar[0].not_, satirlar[0].durum) == ("yanlis_bilgi", None, "yeni"),
      (r.status_code, len(satirlar)))
r = bildir(H, j2["mesaj_id"], sebep="diger")
check("B) Önbellekli cevap da bildirilebilir (not alanı yok)", r.status_code == 200, r.text[:200])

# =============================================================================
# N — 404
# =============================================================================
r = bildir(H, uuid.uuid4(), sebep="diger")
check("N) Olmayan id → 404", r.status_code == 404, r.status_code)
r = bildir(H2, MID, sebep="diger")
check("N) Başkasının mesajı → 404", r.status_code == 404, r.status_code)
r = bildir(H, m.soru_id, sebep="diger")
check("N) Kullanıcının soru satırı → 404 (yalnız asistan cevabı)", r.status_code == 404,
      r.status_code)
r = client.post(f"/api/v1/sor/mesajlar/{MID}/bildir", json={"sebep": "diger"})
check("N) Tokensız → 401", r.status_code == 401, r.status_code)

# =============================================================================
# V — 422
# =============================================================================
for govde, ad in (({"sebep": "baska_bir_sey"}, "geçersiz sebep"), ({}, "sebep eksik"),
                  ({"sebep": "diger", "not": "x" * 501}, "not 501 karakter")):
    r = bildir(H, MID, **govde)
    check(f"V) 422: {ad}", r.status_code == 422, (r.status_code, r.text[:120]))
r = bildir(H, MID, sebep="diger", **{"not": "x" * 500})
check("V) not tam 500 karakter → 200", r.status_code == 200, r.status_code)

# =============================================================================
# L — günlük 20
# =============================================================================
onceki = db.query(AiCevapBildirimi).count()            # bugün 2 yeni bildirim
idler = [sor(H, f"Soru {i}?", baby_id=BID)["mesaj_id"]
         for i in range(sor_bildirim.SOR_BILDIRIM_GUNLUK - onceki + 1)]
kodlar = [bildir(H, i, sebep="diger").status_code for i in idler]
check("L) 20 yeni bildirime kadar 200, 21. → 429",
      kodlar[:-1] == [200] * (len(kodlar) - 1) and kodlar[-1] == 429, kodlar)
check("L) 429 yanıtı GUNLUK_LIMIT", bildir(H, idler[-1], sebep="diger").json().get("hata")
      == "GUNLUK_LIMIT")
r = bildir(H, MID, sebep="uygunsuz")
check("L) Sınırdayken mevcut bildirimi güncellemek → 200", r.status_code == 200, r.status_code)
check("L) Başka kullanıcının sınırı ayrı",
      bildir(H2, sor(H2, "Merhaba, uyku?")["mesaj_id"], sebep="diger").status_code == 200)

# =============================================================================
# A — admin listesi
# =============================================================================
r = client.get("/api/v1/admin/ai-bildirimleri")
check("A) Tokensız → 401", r.status_code == 401, r.status_code)
r = client.get("/api/v1/admin/ai-bildirimleri", headers=H2)
check("A) Moderatör olmayan → 403", r.status_code == 403, r.status_code)
mod = db.query(User).filter_by(email="baska@gercek.com").one()
db.add(CommunityProfile(user_id=mod.id, nickname="mod", is_moderator=True))
db.commit()
r = client.get("/api/v1/admin/ai-bildirimleri", headers=H2)
liste = r.json() if r.status_code == 200 else []
ilk = next((x for x in liste if x["mesaj_id"] == MID), {})
check("A) Moderatör → 200, en fazla 100, yeniden eskiye",
      r.status_code == 200 and 0 < len(liste) <= 100
      and [x["created_at"] for x in liste] == sorted((x["created_at"] for x in liste), reverse=True),
      (r.status_code, len(liste)))
check("A) Bebek adı maskeli: 'A***', metinlerde 'Ada' yok, '[bebek]' var",
      ilk.get("bebek") == "A***" and "Ada" not in (ilk.get("soru_metni") or "")
      and "Ada" not in (ilk.get("cevap_metni") or "") and "[bebek]" in (ilk.get("soru_metni") or ""),
      ilk)
check("A) Listede e-posta yok", "@" not in r.text, r.text[:200])

# =============================================================================
# K — KVKK ve hesap silme
# =============================================================================
u1 = db.query(User).filter_by(email="bildirim@gercek.com").one()
UID1 = u1.id
dokum = kvkk.disa_aktar(db, u1)
check("K) KVKK dökümünde ai_cevap_bildirimleri",
      len(dokum.get("ai_cevap_bildirimleri") or []) >= 20,
      len(dokum.get("ai_cevap_bildirimleri") or []))
r = client.delete("/api/v1/auth/account", headers=H)
db.expire_all()
check("K) Hesap silindi", r.status_code == 200, r.text[:200])
check("K) Hesap silmede bildirimler ve mesajlar da silindi",
      db.query(AiCevapBildirimi).filter_by(user_id=UID1).count() == 0
      and db.query(ChatMessage).filter_by(user_id=UID1).count() == 0)
check("K) Diğer kullanıcının bildirimi duruyor",
      db.query(AiCevapBildirimi).filter_by(user_id=mod.id).count() == 1)

db.close()
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(("✅ " if ok else "❌ ") + ad + ("" if ok else f"\n     → {detay[:600]}"))
print(f"\n{gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
