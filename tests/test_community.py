"""
Faz T — anne topluluğu testleri. FastAPI TestClient, sqlite temp DB.
Haiku (moderation.classify) ve Expo push (notifier.send_expo_push) MOCK'lanır →
deterministik, ücretsiz, ağ YOK.

Çalıştırma: python tests/test_community.py

Kapsam: K0 filtre (varyant/leetspeak/false-positive), K1 flag, K2 (izin=false→hidden,
timeout→published), K3 (2 şikayet→oto-hide), mute/ban eşikleri, engelleme filtresi,
pagination, yetki (başkasının konusu / moderatör olmayan /mod → 403), hesap silme →
"Silinmiş kullanıcı", bildirim (İlayda / pref kapalı / kendine yok).
"""
import os
import sys
import tempfile
import uuid as _uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "faz_t_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient          # noqa: E402
from api.db import Base, SessionLocal, engine      # noqa: E402
import api.models                                  # noqa: E402,F401
from api.models import CommunityProfile, PushToken, Reply, Thread  # noqa: E402
from api.main import app                           # noqa: E402
from api.services import moderation, notifier      # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []

# --- MOCK: Haiku sınıflandırma (test her senaryoda ayarlar) ------------------
_HAIKU = {"verdict": {"izin": True, "sebep": "temiz", "guven": 0.95}}
moderation.classify = lambda text: _HAIKU["verdict"]

# --- MOCK: Expo push (ağ yok; çağrıları yakala) ------------------------------
_PUSH = {"calls": []}
def _fake_push(messages):
    _PUSH["calls"].extend(messages)
    return [{"status": "ok"} for _ in messages]
notifier.send_expo_push = _fake_push


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def reg(email, pw="TestPass123!"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": pw})
    return r.json()["access_token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def mkprofile(tok, nick):
    return client.post("/api/v1/community/profile", headers=H(tok), json={"nickname": nick})


def uid_of(tok):
    from api.services.security import decode_access_token
    return _uuid.UUID(decode_access_token(tok)["sub"])


def set_profile(user_id, **fields):
    db = SessionLocal()
    try:
        p = db.query(CommunityProfile).filter(CommunityProfile.user_id == user_id).one()
        for k, v in fields.items():
            setattr(p, k, v)
        db.commit()
    finally:
        db.close()


def mkthread(tok, title="Bebeğim geceleri sık uyanıyor ne yapmalıyım", body="Yardım lütfen",
             category="uyku"):
    moderation.rate_reset()                       # test hızını rate limitten ayır
    return client.post("/api/v1/community/threads", headers=H(tok),
                       json={"category": category, "title": title, "body": body})


# ===========================================================================
# K0 — içerik filtresi (API üzerinden)
# ===========================================================================
def test_k0():
    tok = reg("t_k0@example.com"); mkprofile(tok, "K0Anne")
    set_profile(uid_of(tok), post_count=5)        # yeni_hesap flag'ini devre dışı bırak

    # küfür / varyant / leetspeak → 400 content_blocked
    for body in ["orospu herif", "s1kt1r git", "s.i.k.t.i.r", "yavşak"]:
        moderation.rate_reset()
        r = client.post("/api/v1/community/threads", headers=H(tok),
                        json={"category": "uyku", "title": "başlık buraya", "body": body})
        ok = r.status_code == 400 and r.json()["detail"].get("code") == "content_blocked"
        check(f"K0 blok: {body[:16]!r} → 400", ok, f"{r.status_code} {r.text[:80]}")

    # false-positive OLMAMALI (ebeveyn bağlamı) → 201
    for body in ["bebeğim memeyi bırakmıyor ne yapmalıyım", "hıyar turşusu tarifi paylaşır mısınız"]:
        moderation.rate_reset()
        r = client.post("/api/v1/community/threads", headers=H(tok),
                        json={"category": "beslenme", "title": "soru var", "body": body})
        check(f"K0 masum geçer: {body[:16]!r} → 201", r.status_code == 201,
              f"{r.status_code} {r.text[:80]}")

    # iletişim bilgisi + spam
    moderation.rate_reset()
    r = client.post("/api/v1/community/threads", headers=H(tok),
                    json={"category": "oneri", "title": "satış", "body": "ara beni 05321234567"})
    check("K0 telefon → 400", r.status_code == 400 and
          r.json()["detail"]["reason"] == "iletisim_bilgisi", r.text[:80])


# ===========================================================================
# K1 — risk skorlama (flagged)
# ===========================================================================
def test_k1():
    # yeni hesap (post_count<3) flagged; tıbbi/ticari flagged
    f1, _ = moderation.risk_flags("merhaba herkese nasılsınız", 0)
    f2, r2 = moderation.risk_flags("bebeğe 5 ml ateş şurubu verdim", 10)
    f3, r3 = moderation.risk_flags("uygun fiyata satıyorum dm atın", 10)
    f4, _ = moderation.risk_flags("uyku düzeni nasıl kurulur bebekte", 10)
    check("K1 yeni hesap flagged", f1, "")
    check("K1 tıbbi flagged", f2 and "tibbi_risk" in r2, str(r2))
    check("K1 ticari flagged", f3 and "ticari" in r3, str(r3))
    check("K1 temiz+eski flagsiz", not f4, "")


# ===========================================================================
# K2 — Haiku async (izin=false→hidden, timeout/None→published)
# ===========================================================================
def test_k2():
    # K2 yalnız FLAGGED içerikte koşar → içeriğe tıbbi tetikleyici ("ateş") koyarak
    # her postu flagged yap (post_count'tan bağımsız).
    tok = reg("t_k2@example.com"); mkprofile(tok, "K2Anne")
    set_profile(uid_of(tok), post_count=5)

    # izin=false, güven yüksek → K2 background hide
    _HAIKU["verdict"] = {"izin": False, "sebep": "uygunsuz", "guven": 0.9}
    r = mkthread(tok, title="şüpheli içerik başlığı", body="bebekte ateş çıkınca ne yapmalı")
    tid = r.json()["id"]
    # background task TestClient'ta senkron koştu → içerik gizlenmiş olmalı
    det = client.get(f"/api/v1/community/threads/{tid}", headers=H(tok))
    check("K2 izin=false → hidden (detay 404)", det.status_code == 404,
          f"{det.status_code}")

    # timeout/None (fail-open) → published KALIR
    _HAIKU["verdict"] = None
    r2 = mkthread(tok, title="ikinci başlık deneme", body="bebekte ateş için ne verilir")
    tid2 = r2.json()["id"]
    det2 = client.get(f"/api/v1/community/threads/{tid2}", headers=H(tok))
    check("K2 timeout/None → published (fail-open)", det2.status_code == 200,
          f"{det2.status_code}")
    _HAIKU["verdict"] = {"izin": True, "sebep": "temiz", "guven": 0.95}   # sıfırla


# ===========================================================================
# K3 — iki farklı kullanıcı şikayeti → oto-hide
# ===========================================================================
def test_k3():
    author = reg("t_k3_a@example.com"); mkprofile(author, "K3Yazar")
    set_profile(uid_of(author), post_count=5)
    u1 = reg("t_k3_u1@example.com"); mkprofile(u1, "Sikayetci1")
    u2 = reg("t_k3_u2@example.com"); mkprofile(u2, "Sikayetci2")

    tid = mkthread(author, title="şikayet edilecek konu başlık", body="içerik metni burada").json()["id"]
    _HAIKU["verdict"] = {"izin": True, "sebep": "temiz", "guven": 0.9}   # Haiku temiz dese bile

    r1 = client.post("/api/v1/community/report", headers=H(u1),
                     json={"target_type": "thread", "target_id": tid, "reason": "uygunsuz"})
    # 1 şikayet + Haiku temiz → hâlâ yayında
    still = client.get(f"/api/v1/community/threads/{tid}", headers=H(u1))
    check("K3 tek şikayet + Haiku temiz → yayında", r1.status_code == 200 and still.status_code == 200,
          f"rep={r1.status_code} get={still.status_code}")
    # aynı kullanıcı tekrar → 409
    rdup = client.post("/api/v1/community/report", headers=H(u1),
                       json={"target_type": "thread", "target_id": tid, "reason": "spam"})
    check("K3 aynı şikayetçi tekrar → 409", rdup.status_code == 409, f"{rdup.status_code}")
    # 2. FARKLI kullanıcı → oto-hide
    client.post("/api/v1/community/report", headers=H(u2),
                json={"target_type": "thread", "target_id": tid, "reason": "uygunsuz"})
    gone = client.get(f"/api/v1/community/threads/{tid}", headers=H(u1))
    check("K3 iki farklı şikayet → oto-hide (404)", gone.status_code == 404, f"{gone.status_code}")


# ===========================================================================
# mute / ban eşikleri (3 hidden → muted, 5 → banned)
# ===========================================================================
def test_mute_ban():
    tok = reg("t_mb@example.com"); mkprofile(tok, "MuteBanAnne")
    uid = uid_of(tok); set_profile(uid, post_count=10)
    _HAIKU["verdict"] = {"izin": False, "sebep": "uygunsuz", "guven": 0.95}  # her post hidden
    # Her post FLAGGED olsun diye tıbbi tetikleyici ("ateş") kullan (post_count'tan bağımsız).

    # 3 konu → hepsi K2 ile hidden → 3. hide'da muted
    for i in range(3):
        mkthread(tok, title=f"mute test konu {i} baslik", body=f"bebekte ateş var {i} ne yapmali")
    db = SessionLocal()
    prof = db.query(CommunityProfile).filter(CommunityProfile.user_id == uid).one()
    st_muted = prof.status
    db.close()
    check("3 hidden → muted", st_muted == "muted", f"status={st_muted}")

    # muted iken gönderi → 403 posting_blocked (mute süresi dolmadı)
    moderation.rate_reset()
    rb = client.post("/api/v1/community/threads", headers=H(tok),
                     json={"category": "uyku", "title": "muted deneme", "body": "olmamali metin"})
    check("muted → 403", rb.status_code == 403 and rb.json()["detail"]["reason"] == "muted",
          f"{rb.status_code} {rb.text[:80]}")

    # her posttan önce aktife çek (eskalasyon 4. hide'da tekrar muted yapar) → 5 → banned
    for i in range(2):
        set_profile(uid, status="active", muted_until=None)
        mkthread(tok, title=f"ban test konu {i} baslik", body=f"bebekte ateş {i} ban icin metin")
    db = SessionLocal()
    prof = db.query(CommunityProfile).filter(CommunityProfile.user_id == uid).one()
    st_banned = prof.status
    db.close()
    check("5 hidden → banned", st_banned == "banned", f"status={st_banned}")
    _HAIKU["verdict"] = {"izin": True, "sebep": "temiz", "guven": 0.95}


# ===========================================================================
# Engelleme filtresi + pagination
# ===========================================================================
def test_block_and_pagination():
    a = reg("t_blk_a@example.com"); mkprofile(a, "BlokAnneA"); set_profile(uid_of(a), post_count=5)
    b = reg("t_blk_b@example.com"); mkprofile(b, "BlokAnneB"); set_profile(uid_of(b), post_count=5)

    # B, gelisim kategorisinde 3 konu açar
    for i in range(3):
        mkthread(b, title=f"B nin konusu numara {i}", body=f"B icerik {i}", category="gelisim")
    # A, gelisim'de 1 konu açar
    mkthread(a, title="A nin tek konusu gelisim", body="A icerik", category="gelisim")

    # A, B'yi engeller
    client.post("/api/v1/community/block", headers=H(a), json={"user_id": str(uid_of(b))})
    lst = client.get("/api/v1/community/threads?category=gelisim", headers=H(a)).json()
    nicks = {it["nickname"] for it in lst["items"]}
    check("engellenen kullanıcı konuları listede YOK",
          "BlokAnneB" not in nicks and "BlokAnneA" in nicks, str(nicks))

    # pagination: limit=2 → next_cursor gelir, sayfalar çakışmaz
    p1 = client.get("/api/v1/community/threads?category=gelisim&limit=2", headers=H(b)).json()
    check("pagination limit=2 → 2 kayıt + cursor",
          len(p1["items"]) == 2 and p1["next_cursor"], f"{len(p1['items'])} cur={bool(p1['next_cursor'])}")
    p2 = client.get(f"/api/v1/community/threads?category=gelisim&limit=2&cursor={p1['next_cursor']}",
                    headers=H(b)).json()
    ids1 = {i["id"] for i in p1["items"]}; ids2 = {i["id"] for i in p2["items"]}
    check("pagination sayfaları çakışmaz", ids1.isdisjoint(ids2), f"{ids1 & ids2}")


# ===========================================================================
# Yetki: başkasının konusunu silememe, moderatör olmayan /mod → 403
# ===========================================================================
def test_authz():
    a = reg("t_az_a@example.com"); mkprofile(a, "YetkiA"); set_profile(uid_of(a), post_count=5)
    b = reg("t_az_b@example.com"); mkprofile(b, "YetkiB")
    tid = mkthread(a, title="A nin silinmeyecek konusu", body="icerik").json()["id"]
    # B, A'nın konusunu silemez → 404
    rd = client.delete(f"/api/v1/community/threads/{tid}", headers=H(b))
    check("başkasının konusunu silememe → 404", rd.status_code == 404, f"{rd.status_code}")
    # moderatör olmayan /mod → 403
    rm = client.get("/api/v1/community/mod/reports", headers=H(b))
    check("moderatör olmayan /mod/reports → 403", rm.status_code == 403, f"{rm.status_code}")
    # sahibi siler → 200, sonra liste/detayda yok
    ro = client.delete(f"/api/v1/community/threads/{tid}", headers=H(a))
    det = client.get(f"/api/v1/community/threads/{tid}", headers=H(a))
    check("sahibi siler → 200 + detay 404", ro.status_code == 200 and det.status_code == 404,
          f"{ro.status_code}/{det.status_code}")


# ===========================================================================
# Moderatör uçları çalışır (is_moderator)
# ===========================================================================
def test_moderator():
    mod = reg("t_mod@example.com"); mkprofile(mod, "ModAnne")
    set_profile(uid_of(mod), is_moderator=True, is_expert=True, post_count=5)
    author = reg("t_mod_auth@example.com"); mkprofile(author, "ModYazar"); set_profile(uid_of(author), post_count=5)
    tid = mkthread(author, title="moderatör gizleyecek konu", body="icerik metni").json()["id"]

    ra = client.post("/api/v1/community/mod/action", headers=H(mod),
                     json={"target_type": "thread", "target_id": tid, "action": "hide"})
    hidden = client.get(f"/api/v1/community/threads/{tid}", headers=H(author))
    check("moderatör hide → içerik gizli", ra.status_code == 200 and hidden.status_code == 404,
          f"{ra.status_code}/{hidden.status_code}")
    rr = client.post("/api/v1/community/mod/action", headers=H(mod),
                     json={"target_type": "thread", "target_id": tid, "action": "restore"})
    back = client.get(f"/api/v1/community/threads/{tid}", headers=H(author))
    check("moderatör restore → geri gelir", rr.status_code == 200 and back.status_code == 200,
          f"{rr.status_code}/{back.status_code}")
    # mod/user ban
    client.post("/api/v1/community/mod/user", headers=H(mod),
                json={"user_id": str(uid_of(author)), "action": "ban"})
    moderation.rate_reset()
    banned_post = client.post("/api/v1/community/threads", headers=H(author),
                              json={"category": "uyku", "title": "banlı deneme", "body": "olmaz metin"})
    check("mod/user ban → yazar gönderemez 403", banned_post.status_code == 403, f"{banned_post.status_code}")


# ===========================================================================
# Bildirim: İlayda(uzman) cevabı + pref kapalı + kendi cevabına yok
# ===========================================================================
def test_notifications():
    _PUSH["calls"].clear()
    owner = reg("t_nt_owner@example.com"); mkprofile(owner, "KonuSahibi"); set_profile(uid_of(owner), post_count=5)
    # owner için push token
    db = SessionLocal()
    db.add(PushToken(user_id=uid_of(owner), expo_token="ExponentPushToken[owner]"))
    db.commit(); db.close()
    tid = mkthread(owner, title="cevap bekleyen konu başlık", body="icerik").json()["id"]

    expert = reg("t_nt_expert@example.com"); mkprofile(expert, "İlayda")
    set_profile(uid_of(expert), is_expert=True, post_count=5)
    moderation.rate_reset()
    client.post(f"/api/v1/community/threads/{tid}/replies", headers=H(expert),
                json={"body": "İlayda cevabı buraya geliyor kısa metin"})
    ilayda_push = [m for m in _PUSH["calls"] if "İlayda" in m.get("body", "")]
    check("uzman cevabı → 'İlayda' bildirimi", len(ilayda_push) >= 1, str(_PUSH["calls"][-1:]))

    # thread.expert_replied true olmalı
    db = SessionLocal(); th = db.get(Thread, _uuid.UUID(tid)); er = th.expert_replied; db.close()
    check("uzman cevabı → expert_replied=true", er is True, f"{er}")

    # kendi cevabına bildirim YOK
    _PUSH["calls"].clear()
    moderation.rate_reset()
    client.post(f"/api/v1/community/threads/{tid}/replies", headers=H(owner),
                json={"body": "kendi konuma kendim cevap veriyorum metin"})
    check("kendi cevabına bildirim YOK", len(_PUSH["calls"]) == 0, str(_PUSH["calls"]))

    # pref kapalı → bildirim yok (gerçek PATCH endpoint'i ile)
    _PUSH["calls"].clear()
    pr = client.patch("/api/v1/notifications/preferences", headers=H(owner),
                      json={"community_replies": False})
    check("pref PATCH community_replies=false → 200",
          pr.status_code == 200 and pr.json().get("community_replies") is False, pr.text[:100])
    other = reg("t_nt_other@example.com"); mkprofile(other, "BaskaAnne"); set_profile(uid_of(other), post_count=5)
    moderation.rate_reset()
    client.post(f"/api/v1/community/threads/{tid}/replies", headers=H(other),
                json={"body": "başka anneden cevap metni buraya"})
    check("pref kapalı → bildirim yok", len(_PUSH["calls"]) == 0, str(_PUSH["calls"]))


# ===========================================================================
# Hesap silme → "Silinmiş kullanıcı"
# ===========================================================================
def test_account_deletion_render():
    a = reg("t_del@example.com"); mkprofile(a, "SilinecekAnne"); set_profile(uid_of(a), post_count=5)
    viewer = reg("t_del_v@example.com"); mkprofile(viewer, "İzleyenAnne"); set_profile(uid_of(viewer), post_count=5)
    tid = mkthread(a, title="silinen kullanıcının konusu kalır", body="icerik metni").json()["id"]
    # A hesabını sil
    dr = client.delete("/api/v1/auth/account", headers=H(a))
    # konu KALMALI, yazar "Silinmiş kullanıcı"
    det = client.get(f"/api/v1/community/threads/{tid}", headers=H(viewer))
    ok = (dr.status_code == 200 and det.status_code == 200
          and det.json()["nickname"] == "Silinmiş kullanıcı")
    check("hesap silme → konu kalır + 'Silinmiş kullanıcı'", ok,
          f"del={dr.status_code} det={det.status_code} nick={det.json().get('nickname') if det.status_code==200 else '-'}")


def main() -> int:
    for fn in (test_k0, test_k1, test_k2, test_k3, test_mute_ban,
               test_block_and_pagination, test_authz, test_moderator,
               test_notifications, test_account_deletion_render):
        try:
            fn()
        except Exception as e:
            import traceback
            check(f"{fn.__name__} ÇALIŞTI", False, f"EXC: {e}\n{traceback.format_exc()[-500:]}")

    print("\n" + "=" * 74)
    print("FAZ T (TOPLULUK) TEST SONUÇLARI")
    print("=" * 74)
    passed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        line = f"[{mark}] {name}"
        if not ok and detail:
            line += f"\n       {detail}"
        print(line)
    print("-" * 74)
    print(f"TOPLAM: {passed}/{len(results)} geçti")
    print("=" * 74)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
