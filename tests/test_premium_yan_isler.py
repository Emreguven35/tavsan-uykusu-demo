"""
Premium yan işleri (2026-10-13) — ağ YOK, LLM YOK, ElevenLabs/Resend/RevenueCat MOCK.

  V  Videolar: premium olmayana (deneme dahil) yalnız sabit 3 başlık açık;
     kilitli videoda locked=true ve adres YOK; adres imzalı ve 24 saatlik.
  M  MP3 kesme: çerçeve sınırında 30 sn, Xing notu atılır.
  S  Anne Sesi: ücretsiz kayıt → tek masaldan 30 sn önizleme (onizleme=true);
     amac=paket premium ister; premium alınca BİR KEZ beklemesiz yeniden kayıt;
     paket 5 ninni + 5 masal (ayardan), katalogda olmayan yer boş;
     ücretsiz dandini yok; eski paket sahibinin dosyaları aynen.
  T  Topluluk: şikâyette moderatöre e-posta; günde 20 sınırı; oto-gizleme
     yalnız 24 saatten eski hesapları sayar; kuyrukta yazar + ban_author;
     engellenenin cevabı engelleyene bildirim göndermez; kurallar onayı.
  H  Hesap silme: RevenueCat aboneyi sil (anahtar yoksa/başarısızsa silme
     sürer); üretim sürerken silinen hesaptan sahipsiz MP3 kalmaz.

Çalıştırma: python tests/test_premium_yan_isler.py
"""
import io
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

_DB = Path(tempfile.gettempdir()) / "premium_yan_isler_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "premium_yan_medya"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.pop("BETA_MODE", None)                  # KAPALI: kilitler etkin
os.environ.pop("BETA_PREMIUM_ALL", None)
os.environ.pop("REVENUECAT_API_KEY", None)
for _k in ("VOICE_PAKET_NINNILER", "VOICE_PAKET_MASALLAR", "VOICE_ONIZLEME_ICERIK",
           "MODERATOR_EPOSTA", "PUBLIC_BASE_URL"):
    os.environ.pop(_k, None)

from fastapi.testclient import TestClient                      # noqa: E402
from api.config import get_settings                            # noqa: E402
from api.db import Base, SessionLocal, engine                  # noqa: E402
import api.models                                              # noqa: E402,F401
from api.models import (Block, CommunityProfile, EducationVideo, Report,  # noqa: E402
                        Subscription, Thread, User, VoiceAudio, VoiceProfile)
from api.main import app                                       # noqa: E402
from api.services import (education, mailer, moderation, mp3,  # noqa: E402
                          revenuecat_silme, storage, topluluk_bildirim,
                          ucretsiz_erisim, voice_paket, voice_uretim)
from api.services import voice as voice_svc                    # noqa: E402
from api.services.security import decode_access_token          # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("" if cond else f"   → {detail}"))


def reg(email):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "TestPass123!"})
    tok = r.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, _uuid.UUID(decode_access_token(tok)["sub"])


def abonelik(uid, period_type):
    s = SessionLocal()
    try:
        s.add(Subscription(user_id=uid, platform="ios", product_id="tu_aylik",
                           status="active", period_type=period_type,
                           expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
        s.commit()
    finally:
        s.close()


def mp3_uret(saniye: float, xing: bool = False) -> bytes:
    """MPEG-1 Layer III 128 kbps 44.1 kHz sahte çerçeveler (417 bayt / 26.12 ms)."""
    baslik = b"\xff\xfb\x90\x00"
    cerceve = baslik + b"\x00" * (417 - 4)
    n = int(saniye / (1152 / 44100)) + 1
    veri = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10
    if xing:
        veri += baslik + b"\x00" * 32 + b"Xing" + b"\x00" * (417 - 40)
    return veri + cerceve * n


# =============================================================================
# V — Videolar
# =============================================================================
print("V) Videolar")
VIDEOLAR = [
    ("uyku-egitimine-giris", "Uyku eğitimine giriş ve genel bilgilendirme", "baslarken"),
    ("uyku-plani-hazirlama", "Bebeğe uygun uyku planı nasıl hazırlanır?", "baslarken"),
    ("beslenme-mesafesi", "Uykuya geçmeden önce beslenme mesafesi nasıl olmalı?", "baslarken"),
    ("baslarken-dorduncu", "Uyku ortamı nasıl olmalı?", "baslarken"),
    ("ilk-gunler", "İlk günler", "egitim_sirasinda"),
]
_s = SessionLocal()
try:
    education.katalog_upsert(
        _s, [dict(slug=sl, title=t, description=None, category=k, order_in_category=i,
                  stage_tags=["genel"]) for i, (sl, t, k) in enumerate(VIDEOLAR, 1)],
        {sl: 60 for sl, _, _ in VIDEOLAR})
finally:
    _s.close()
storage.depo().yaz(storage.video_yolu("beslenme-mesafesi"), b"\x00" * 2048)

ACIK = {"uyku-egitimine-giris", "uyku-plani-hazirlama", "beslenme-mesafesi"}


def katalog(h):
    j = client.get("/api/v1/education/videos", headers=h).json()
    return {v["slug"]: v for c in j["categories"] for v in c["videos"]}


HU, UU = reg("ucretsiz@test.com")
k = katalog(HU)
check("V1) Ücretsiz: yalnız 3 sabit başlık açık (kategori değil başlık belirler)",
      {s for s, v in k.items() if not v["locked"]} == ACIK,
      str({s: v["locked"] for s, v in k.items()}))
check("V2) Kilitli videoda locked=true, premium_required=true ve adres YOK",
      all(v["video_url"] is None and v["premium_required"]
          for s, v in k.items() if s not in ACIK), str(k["ilk-gunler"]))
check("V3) Açık videonun adresi imzalı (exp+sig)",
      all("exp=" in k[s]["video_url"] and "sig=" in k[s]["video_url"] for s in ACIK),
      k["beslenme-mesafesi"]["video_url"])
_exp = int(k["beslenme-mesafesi"]["video_url"].split("exp=")[1].split("&")[0])
_kalan = _exp - int(datetime.now(timezone.utc).timestamp())
check("V4) Adres 24 saat geçerli", 86400 - 60 <= _kalan <= 86400, str(_kalan))
r = client.get(k["beslenme-mesafesi"]["video_url"], headers={"Range": "bytes=0-1"})
check("V5) İmzalı adres videoyu açıyor (206)", r.status_code == 206, str(r.status_code))
check("V6) İmzasız adres 403",
      client.get("/media/videos/beslenme-mesafesi.mp4").status_code == 403, "")
_kilitli_yol = storage.video_yolu("ilk-gunler")
check("V7) Kilitli videonun dosyası imzasız açılmıyor",
      client.get("/media/videos/ilk-gunler.mp4").status_code == 403, "")

HD, UD = reg("deneme@test.com")
abonelik(UD, "TRIAL")
kd = katalog(HD)
check("V8) Deneme (TRIAL) kullanıcı premium SAYILMAZ: yalnız 3 açık",
      {s for s, v in kd.items() if not v["locked"]} == ACIK, "")
HP, UP = reg("premium@test.com")
abonelik(UP, "NORMAL")
kp = katalog(HP)
check("V9) Premium (NORMAL): hepsi açık ve adresli",
      all(not v["locked"] and v["video_url"] for v in kp.values()), "")
check("V10) Başlık eşleşmesi: büyük/küçük harf ve noktalama önemsiz, başka başlık açılmaz",
      ucretsiz_erisim.video_acik_mi("UYKU EĞİTİMİNE GİRİŞ VE GENEL BİLGİLENDİRME")
      and ucretsiz_erisim.video_acik_mi("Bebeğe uygun uyku planı nasıl hazırlanır")
      and not ucretsiz_erisim.video_acik_mi("Uykuya geçmeden önce beslenme")
      and not ucretsiz_erisim.video_acik_mi("Uyku eğitimine giriş ve genel bilgilendirmeler"),
      "")

# =============================================================================
# M — MP3 kesme
# =============================================================================
print("M) MP3 kesme")
_uzun = mp3_uret(60, xing=True)
_kisa = mp3.kes(_uzun, 30)
check("M1) 60 sn → en fazla 30 sn (çerçeve sınırında)",
      29.9 <= mp3.sure(_kisa) <= 30.0, f"{mp3.sure(_kisa):.2f}")
check("M2) Xing (eski toplam süre) çerçevesi atıldı", b"Xing" not in _kisa, "")
check("M3) Kesilen dosya geçerli çerçeveyle başlıyor", _kisa[:2] == b"\xff\xfb", _kisa[:4])
check("M4) Zaten kısa dosya aynı süreyle döner",
      abs(mp3.sure(mp3.kes(mp3_uret(10), 30)) - mp3.sure(mp3_uret(10))) < 0.01, "")

# =============================================================================
# S — Anne Sesi
# =============================================================================
print("S) Anne Sesi")
_liste = voice_paket.paket_listesi()
check("S1) Paket yapısı 5 ninni + 5 masal (2026-10-16 ninni listesi)",
      len(_liste["masallar"]) == 5 and len(_liste["ninniler"]) == 5
      and voice_paket.PAKET_NINNI_SAYISI == 5 and voice_paket.PAKET_MASAL_SAYISI == 5,
      str(_liste))
os.environ["VOICE_PAKET_NINNILER"] = ("ninni_dandini,ninni_ay_isigi,ninni_uyu_yavrum,"
                                      "ninni_yeni_bir,ninni_yeni_iki")
_ids = [x["id"] for x in voice_paket.paket_icerikleri()]
check("S2) Liste ayardan; katalogda olmayan ninni üretilmez, referans kaydı "
      "olmayan ninni pakete girmez (STS kapalı: hiç ninni yok)",
      "ninni_yeni_bir" not in _ids and len(_ids) == 5
      and not any(i.startswith("ninni_") for i in _ids)
      and len(voice_paket.paket_listesi()["ninniler"]) == 5, str(_ids))
os.environ.pop("VOICE_PAKET_NINNILER")
check("S3) Önizleme metni kısa (tam masal seslendirilmez)",
      len(voice_paket.onizleme_icerigi()["text"]) <= voice_paket.onizleme_karakter(), "")

voice_svc.clone_voice = lambda *a, **k: {"ok": True, "voice_id": f"V{_uuid.uuid4().hex[:6]}"}
voice_svc.delete_voice = lambda v: {"ok": True, "error": None, "status": 200}
voice_svc.list_cloned_voices = lambda: {"ok": True, "voices": []}
KUYRUK: list[str] = []
voice_uretim.kuyruga_al = lambda pid: KUYRUK.append(str(pid))
TTS_METIN: list[str] = []


def _sahte_seslendir(voice_id, metin, user_id):
    TTS_METIN.append(metin)
    return mp3_uret(90)


voice_uretim._seslendir = _sahte_seslendir


def kayit(h, amac=None):
    data = {"name": "Ses"}
    if amac:
        data["amac"] = amac
    return client.post("/api/v1/voice/clone", headers=h, data=data,
                       files={"audio": ("s.mp3", io.BytesIO(b"A" * 900), "audio/mpeg")})


def profil(uid):
    """Güncel profil — router'ın kullandığı sıralamayla (aynı saniyede açılan
    iki profilde created_at berabere kalır)."""
    from api.routers.voice import _son_profil
    s = SessionLocal()
    try:
        return _son_profil(s, s.get(User, uid))
    finally:
        s.close()


HS, US = reg("sesli-ucretsiz@test.com")
r = kayit(HS)
check("S4) Ücretsiz kullanıcı kaydedip klonlayabiliyor (200) ve onizleme=true",
      r.status_code == 200 and r.json().get("onizleme") is True, r.text[:200])
p = profil(US)
check("S5) Profil amacı 'onizleme', üretilecek içerik 1", p.amac == "onizleme"
      and p.progress_total == 1, f"{p.amac} {p.progress_total}")
sonuc = voice_uretim.uret(KUYRUK[-1])
_on_id = voice_paket.onizleme_icerik_id()
s = SessionLocal()
try:
    _sesler = s.query(VoiceAudio).filter(VoiceAudio.voice_profile_id == p.id).all()
finally:
    s.close()
_dosya = storage.yerel_dosya(_sesler[0].storage_path) if _sesler else None
check("S6) Yalnız önizleme masalı üretildi", [a.content_id for a in _sesler] == [_on_id],
      str([a.content_id for a in _sesler]))
check("S7) Ses SUNUCUDA 30 sn'de kesildi (TTS 90 sn döndürdü)",
      _dosya is not None and mp3.sure(_dosya.read_bytes()) <= 30.0,
      f"{mp3.sure(_dosya.read_bytes()) if _dosya else None}")
check("S8) TTS'e yalnız masalın baş kısmı gitti",
      len(TTS_METIN) == 1 and len(TTS_METIN[0]) < 1000, str([len(t) for t in TTS_METIN]))
st = client.get("/api/v1/voice/stories", headers=HS).json()
_hepsi = {x["id"]: x for x in st["masallar"] + st["ninniler"]}
_o = _hepsi[_on_id]
check("S9) /stories: önizleme kilitsiz, onizleme=true, imzalı bağlantılı",
      _o["onizleme"] is True and _o["locked"] is False and "sig=" in (_o["audio_url"] or ""),
      str(_o))
check("S10) Diğer her içerik kilitli ve bağlantısız; kaydı olmayan ninni listede yok",
      all(x["locked"] and x["audio_url"] is None and not x["is_free"]
          for k_, x in _hepsi.items() if k_ != _on_id)
      and st["ninniler"] == [], str(sorted(_hepsi)))
vs = client.get("/api/v1/voice/voice-status", headers=HS).json()
check("S11) voice-status: onizleme=true, ücretsizde paket_kaydi_hakki=false",
      vs["onizleme"] is True and vs["paket_kaydi_hakki"] is False, str(vs))
r = kayit(HS, "paket")
check("S12) Ücretsiz amac=paket → 403 (premium gerekli)", r.status_code == 403, r.text[:120])

# --- Satın alma → bir kez beklemesiz paket kaydı
abonelik(US, "NORMAL")
vs = client.get("/api/v1/voice/voice-status", headers=HS).json()
check("S13) Premium sonrası: 30 gün dolmadı ama paket_kaydi_hakki=true",
      vs["can_clone"] is False and vs["paket_kaydi_hakki"] is True, str(vs))
r = kayit(HS)
check("S14) amac'sız yeniden kayıt hâlâ 429 (bekleme sürer)", r.status_code == 429,
      str(r.status_code))
r = kayit(HS, "paket")
p2 = profil(US)
check("S15) amac=paket → 200, bekleme UYGULANMADI, tam paket",
      r.status_code == 200 and r.json().get("onizleme") is False and p2.amac == "paket"
      and p2.progress_total == len(voice_paket.paket_icerikleri()), r.text[:200])
s = SessionLocal()
try:
    _u = s.get(User, US)
    check("S16) Tek seferlik hak yandı (users.ses_paket_kaydi_at dolu)",
          _u.ses_paket_kaydi_at is not None, "")
finally:
    s.close()
r = kayit(HS, "paket")
check("S17) İkinci amac=paket → 429 (hak bir kez)", r.status_code == 429, str(r.status_code))

# --- Deneme kullanıcısı premium sayılmaz
HT, UT = reg("deneme-ses@test.com")
abonelik(UT, "TRIAL")
check("S18) Deneme kullanıcısı amac=paket → 403", kayit(HT, "paket").status_code == 403, "")
r = kayit(HT)
check("S19) Deneme kullanıcısının kaydı önizleme", r.status_code == 200
      and r.json().get("onizleme") is True, r.text[:120])

# --- Mevcut paket sahibi (önizleme öncesi, amac NULL) bozulmadı
HE, UE = reg("eski-paket@test.com")
abonelik(UE, "NORMAL")
s = SessionLocal()
try:
    pe = VoiceProfile(user_id=UE, elevenlabs_voice_id=None, status="released",
                      progress_done=4, progress_total=4)
    s.add(pe)
    s.flush()
    for cid in ("ninni_dandini", "ninni_ay_isigi", "ninni_uyu_yavrum",
                "masal_aysecik_uyku_perisi"):
        yol = storage.ses_yolu(UE, pe.id, cid)
        storage.depo().yaz(yol, b"ID3eski")
        s.add(VoiceAudio(voice_profile_id=pe.id, content_id=cid, storage_path=yol, bytes=7))
    s.commit()
finally:
    s.close()
st = client.get("/api/v1/voice/stories", headers=HE).json()
_es = {x["id"]: x for x in st["masallar"] + st["ninniler"]}
check("S20) Eski paket sahibi: 4 dosyası hazır, kilitsiz, bağlantılı, önizleme değil",
      all(_es[c]["hazir"] and not _es[c]["locked"] and _es[c]["audio_url"]
          and not _es[c]["onizleme"] and _es[c]["ses_kaynagi"] == "anne"
          for c in ("ninni_dandini", "ninni_ay_isigi", "ninni_uyu_yavrum",
                    "masal_aysecik_uyku_perisi")), str(_es["ninni_dandini"]))
vs = client.get("/api/v1/voice/voice-status", headers=HE).json()
check("S21) Eski paket sahibine paket geçiş hakkı verilmez (önizlemesi yok)",
      vs["onizleme"] is False and vs["paket_kaydi_hakki"] is False, str(vs))

# =============================================================================
# T — Topluluk
# =============================================================================
print("T) Topluluk")
_HAIKU = {"verdict": {"izin": True, "sebep": "temiz", "guven": 0.95}}
moderation.classify = lambda text: _HAIKU["verdict"]
moderation.check_rate = lambda *a, **k: (False, 0)
EPOSTA: list[tuple] = []
mailer.send_email = lambda to, subject, text, html=None: (
    EPOSTA.append((to, subject, text)) or {"ok": True, "provider": "test"})


def hesap_eskit(uid, gun=2):
    s = SessionLocal()
    try:
        s.get(User, uid).created_at = datetime.now(timezone.utc) - timedelta(days=gun)
        s.commit()
    finally:
        s.close()


def topluluk_hesabi(email, ad, **kw):
    h, uid = reg(email)
    r = client.post("/api/v1/community/profile", headers=h, json={"nickname": ad, **kw})
    return h, uid, r


def konu(h, baslik, anonim=False):
    r = client.post("/api/v1/community/threads", headers=h, json={
        "title": baslik, "body": f"{baslik} hakkında bir soru yazıyorum.",
        "kategori": "gece_uyanmasi", "anonim": anonim})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def sikayet(h, tid, sebep="hakaret"):
    return client.post("/api/v1/community/report", headers=h, json={
        "target_type": "thread", "target_id": tid, "reason": sebep, "note": "kaba dil"})


# --- Kurallar onayı
_, _, r = topluluk_hesabi("kural-yok@test.com", "KuralYok", rules_accepted=False)
check("T1) rules_accepted=false → rules_accepted_at BOŞ", r.status_code == 201
      and r.json()["rules_accepted_at"] is None, r.text[:150])
hK = {"Authorization": r.request.headers["Authorization"]}
r = client.post("/api/v1/community/rules/accept", headers=hK)
check("T2) POST /community/rules/accept → dolar", r.status_code == 200
      and r.json()["rules_accepted_at"], r.text[:150])
_ilk = r.json()["rules_accepted_at"]
r = client.post("/api/v1/community/rules/accept", headers=hK)
check("T3) Kabul idempotent (ilk an korunur)", r.json()["rules_accepted_at"] == _ilk, "")
_, _, r = topluluk_hesabi("kural-evet@test.com", "KuralEvet", rules_accepted=True)
check("T4) rules_accepted=true → dolu", r.json()["rules_accepted_at"] is not None, "")
_, _, r = topluluk_hesabi("kural-eski@test.com", "KuralEski")
check("T5) Alan yok (bugünkü mobil, kabul düğmesinden) → dolu; mobil bozulmaz",
      r.json()["rules_accepted_at"] is not None, "")

# --- Şikâyet e-postası + kuyruk + yasaklama
HY, UY, _ = topluluk_hesabi("yazar@test.com", "YazarAnne")
H1, U1, _ = topluluk_hesabi("sk1@test.com", "Sikayetci1")
H2, U2, _ = topluluk_hesabi("sk2@test.com", "Sikayetci2")
t1 = konu(HY, "Anonim kaba konu", anonim=True)
EPOSTA.clear()
r = sikayet(H1, t1)
check("T6) Şikâyet 200 ve moderatöre TEK e-posta", r.status_code == 200 and len(EPOSTA) == 1,
      f"{r.status_code} {len(EPOSTA)}")
_to, _konu, _metin = EPOSTA[0] if EPOSTA else ("", "", "")
check("T7) E-posta tavsanuykusu@hotmail.com adresine", _to == "tavsanuykusu@hotmail.com", _to)
check("T8) E-postada içerik, yazar (anonim olsa da) ve sebep var",
      "Anonim kaba konu hakkında" in _metin and "YazarAnne" in _metin
      and "ANONİM" in _metin and "Hakaret" in _metin and "kaba dil" in _metin, _metin[:400])
check("T9) Tekrar şikâyet (409) e-posta göndermez",
      sikayet(H1, t1).status_code == 409 and len(EPOSTA) == 1, str(len(EPOSTA)))

# --- Oto-gizleme: yeni hesaplar sayılmaz
r = sikayet(H2, t1)
check("T10) İki YENİ hesabın şikâyeti içeriği gizlemez",
      client.get(f"/api/v1/community/threads/{t1}", headers=H1).status_code == 200, "")
H3, U3, _ = topluluk_hesabi("sk3@test.com", "Sikayetci3")
H4, U4, _ = topluluk_hesabi("sk4@test.com", "Sikayetci4")
hesap_eskit(U3); hesap_eskit(U4)
t2 = konu(HY, "İkinci konu")
sikayet(H3, t2)
check("T11) Tek eski hesap şikâyeti + Haiku temiz → yayında",
      client.get(f"/api/v1/community/threads/{t2}", headers=H1).status_code == 200, "")
sikayet(H1, t2)
check("T12) Eski + yeni hesap → yine yayında (yeni sayılmaz)",
      client.get(f"/api/v1/community/threads/{t2}", headers=H1).status_code == 200, "")
sikayet(H4, t2)
check("T13) İki ESKİ hesap → oto-gizlendi",
      client.get(f"/api/v1/community/threads/{t2}", headers=H1).status_code == 404, "")

# --- Moderatör kuyruğu
HM, UM, _ = topluluk_hesabi("mod@test.com", "Moderator")
s = SessionLocal()
try:
    s.query(CommunityProfile).filter(CommunityProfile.user_id == UM).one().is_moderator = True
    s.commit()
finally:
    s.close()
q = client.get("/api/v1/community/mod/reports", headers=HM).json()["reports"]
_r1 = next(x for x in q if x["target_id"] == t1)
check("T14) Kuyrukta yazar görünüyor (kimlik, gerçek takma ad, durum, anonim)",
      _r1["author_user_id"] == str(UY) and _r1["author_nickname"] == "YazarAnne"
      and _r1["author_status"] == "active" and _r1["author_anonim"] is True, str(_r1))
r = client.post("/api/v1/community/mod/action", headers=HM,
                json={"target_type": "thread", "target_id": t1, "action": "ban_author"})
s = SessionLocal()
try:
    _py = s.query(CommunityProfile).filter(CommunityProfile.user_id == UY).one()
    _th = s.get(Thread, _uuid.UUID(t1))
    _acik = s.query(Report).filter(Report.target_id == _uuid.UUID(t1),
                                   Report.resolved.is_(False)).count()
finally:
    s.close()
check("T15) Kuyruktan ban_author: yazar banned, içerik gizli, şikâyetler çözüldü",
      r.status_code == 200 and _py.status == "banned" and _th.status == "hidden"
      and _acik == 0, f"{r.status_code} {_py.status} {_th.status} {_acik}")
r = client.post("/api/v1/community/threads", headers=HY, json={
    "title": "Yasaklıyken", "body": "yazmaya çalışıyorum burada", "kategori": "gece_uyanmasi"})
check("T16) Yasaklanan yazar artık gönderemiyor (403)", r.status_code == 403, str(r.status_code))
check("T17) Moderatör olmayan ban_author yapamaz",
      client.post("/api/v1/community/mod/action", headers=H1,
                  json={"target_type": "thread", "target_id": t2,
                        "action": "ban_author"}).status_code == 403, "")

# --- Günde 20 şikâyet sınırı
HG, UG, _ = topluluk_hesabi("cok-sikayet@test.com", "CokSikayet")
HZ, UZ, _ = topluluk_hesabi("hedef@test.com", "HedefAnne")
hedefler = [konu(HZ, f"Hedef konu {i}") for i in range(21)]
kodlar = [sikayet(HG, t, "spam").status_code for t in hedefler]
check("T18) İlk 20 şikâyet 200, 21. → 429",
      kodlar[:20] == [200] * 20 and kodlar[20] == 429, str(kodlar[-3:]))
check("T19) 429 gövdesi kod taşıyor",
      sikayet(HG, hedefler[20]).json()["detail"].get("reason") == "cok_sik_sikayet", "")

# --- Engellenen kişinin cevabı engelleyene bildirim göndermez
PUSH: list = []
from api.services import notifier                              # noqa: E402
notifier.push_to_user = lambda db, uid, baslik, govde, data=None: PUSH.append(uid)
HA, UA, _ = topluluk_hesabi("engelleyen@test.com", "Engelleyen")
HB, UB, _ = topluluk_hesabi("engellenen@test.com", "Engellenen")
HC, UC, _ = topluluk_hesabi("serbest@test.com", "Serbest")
tA = konu(HA, "Engelleyenin konusu")
s = SessionLocal()
try:
    s.add(Block(user_id=UA, blocked_user_id=UB))
    s.commit()
    _t = s.get(Thread, _uuid.UUID(tA))
    _gun = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)      # TR 12:00, sessiz değil
    _rb = type("R", (), {"id": _uuid.uuid4(), "user_id": UB})()
    _rc = type("R", (), {"id": _uuid.uuid4(), "user_id": UC})()
    s1 = topluluk_bildirim.cevap_olayi(s, _t, _rb, False, None, _gun)
    s2 = topluluk_bildirim.cevap_olayi(s, _t, _rc, False, None, _gun)
finally:
    s.close()
check("T20) Engellenenin cevabı → bildirim YOK ('engelli')",
      s1.get(str(UA)) == "engelli", str(s1))
check("T21) Engellenmemiş kişinin cevabı → bildirim gider",
      s2.get(str(UA)) == "gonderildi" and PUSH == [UA], f"{s2} {PUSH}")

# =============================================================================
# H — Hesap silme
# =============================================================================
print("H) Hesap silme")
import requests as _requests                                   # noqa: E402
RC: list = []
_gercek_delete = _requests.delete


def _rc_delete(url, timeout=None, headers=None):
    RC.append((url, (headers or {}).get("Authorization")))
    return type("Y", (), {"status_code": 200})()


_requests.delete = _rc_delete
HX, UX = reg("silinecek-anahtarsiz@test.com")
r = client.delete("/api/v1/auth/account", headers=HX)
check("H1) REVENUECAT_API_KEY yokken silme sürüyor, RevenueCat çağrılmıyor",
      r.status_code == 200 and RC == [], f"{r.status_code} {RC}")
get_settings().revenuecat_api_key = "sk_test_anahtar"
HX, UX = reg("silinecek@test.com")
r = client.delete("/api/v1/auth/account", headers=HX)
check("H2) Anahtar varken DELETE /v1/subscribers/{users.id} çağrıldı",
      r.status_code == 200 and RC == [(f"https://api.revenuecat.com/v1/subscribers/{UX}",
                                       "Bearer sk_test_anahtar")], str(RC))


def _rc_patlar(*a, **k):
    raise _requests.ConnectionError("ağ yok")


_requests.delete = _rc_patlar
HX, UX = reg("silinecek-hata@test.com")
r = client.delete("/api/v1/auth/account", headers=HX)
s = SessionLocal()
try:
    _kaldi = s.get(User, UX)
finally:
    s.close()
check("H3) RevenueCat hatası silmeyi ENGELLEMEZ", r.status_code == 200 and _kaldi is None,
      str(r.status_code))
check("H4) revenuecat_silme.aboneyi_sil exception fırlatmaz",
      revenuecat_silme.aboneyi_sil(UX) == "hata", "")
_requests.delete = _gercek_delete
get_settings().revenuecat_api_key = ""


# --- Üretim sürerken hesap silinirse sahipsiz MP3 kalmaz
def _uretim_hazirla(email):
    h, uid = reg(email)
    abonelik(uid, "NORMAL")
    r = kayit(h)
    assert r.status_code == 200, r.text
    return h, uid, profil(uid)


def _klasor_dosyalari(uid, pid):
    kok = _MEDYA / storage.ses_klasoru(uid, pid)
    return sorted(p.name for p in kok.rglob("*") if p.is_file()) if kok.exists() else []


# (a) Silme TTS sırasında gelir (ikinci içerikte).
HX, UX, PX = _uretim_hazirla("uretimde-sil-a@test.com")
_sayac = {"n": 0}


def _tts_sil(voice_id, metin, user_id):
    _sayac["n"] += 1
    if _sayac["n"] == 2:
        assert client.delete("/api/v1/auth/account", headers=HX).status_code == 200
    return mp3_uret(5)


voice_uretim._seslendir = _tts_sil
voice_uretim.uret(PX.id)
s = SessionLocal()
try:
    _satir = s.query(VoiceAudio).filter(VoiceAudio.voice_profile_id == PX.id).count()
finally:
    s.close()
check("H5) TTS sırasında silinen hesap: klasörde dosya YOK, satır YOK",
      _klasor_dosyalari(UX, PX.id) == [] and _satir == 0,
      f"{_klasor_dosyalari(UX, PX.id)} {_satir}")
check("H6) Silmeden sonra TTS'e gidilmedi (en fazla bir içerik daha)",
      _sayac["n"] <= 2, str(_sayac["n"]))

# (b) Silme dosya yazıldıktan HEMEN sonra, satır açılmadan gelir.
HX, UX, PX = _uretim_hazirla("uretimde-sil-b@test.com")
voice_uretim._seslendir = lambda v, m, u: mp3_uret(5)
_gercek_depo = storage.depo()


class _SilenDepo:
    def __init__(self):
        self.n = 0

    def __getattr__(self, ad):
        return getattr(_gercek_depo, ad)

    def yaz(self, yol, veri, tur=None):
        b = _gercek_depo.yaz(yol, veri, tur) if tur else _gercek_depo.yaz(yol, veri)
        self.n += 1
        if self.n == 2:                     # ikinci dosya yazıldı, satırı yok
            assert client.delete("/api/v1/auth/account", headers=HX).status_code == 200
        return b


_eski_depo_fn = voice_uretim.storage.depo
voice_uretim.storage.depo = lambda: _SilenDepo()
try:
    voice_uretim.uret(PX.id)
finally:
    voice_uretim.storage.depo = _eski_depo_fn
check("H7) Yazımla satır arasında silinen hesap: sahipsiz dosya kalmadı",
      _klasor_dosyalari(UX, PX.id) == [], str(_klasor_dosyalari(UX, PX.id)))


# =============================================================================
print("=" * 78)
gecen = sum(1 for _, ok, _ in results if ok)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
