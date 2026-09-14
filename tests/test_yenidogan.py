"""
0-3 ay yenidoğan ritim rehberi testleri (Faz 0-3) — LLM/DB/ağ YOK, deterministik.

Kapsam:
  1. Yaş sınırı — 0-3 ay rehber alır, 3 ay ve üstü ALMAZ; alt bant doğru seçilir.
  2. İÇERİK YASAĞI — rehberde hiçbir uyku eğitimi tekniği geçmez (merdiven,
     bekleme süresi, yatır-çık, kapı eşiği), schedule ve days BOŞTUR,
     gece uyanma protokolü ve kestirme kuralı EKLENMEZ.
  3. Rehberin altı zorunlu bölümü tam — pencere, sinyaller, mini rutin,
     gece/gündüz ayrımı, güvenli uyku, ritim sabitleme.
  4. content["type"] sözleşmesi — üç tür ve hangisinde days dolu.
  5. 3-5 ay BOŞLUĞU — eğitim uygun değilken plan üretimi ARTIK 502 vermiyor.
  6. Adaptasyon rehbere DOKUNMAZ (çizelge uydurmaz, protokol enjekte etmez).
  7. Atak haftaları — TAHMİNİ DOĞUM TARİHİNDEN hesap, prematüre kayması.
  8. KB içeriği korpusa giriyor (uyku + gelişim + atak) ve marka kuralı bozulmuyor.
  9. TEK KAYNAK — 0-2_ay bandının değerleri DEĞİŞMEDİ (İlayda onayı bekliyor).

Çalıştırma: python tests/test_yenidogan.py
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.pop("ANTHROPIC_API_KEY", None)      # yedek motor: LLM çağrısı YOK

from api.services import plan_service          # noqa: E402
from engine.chatbot import tr_lower_safe as chatbot_tr_lower   # noqa: E402
from engine import yas_bantlari as yb          # noqa: E402
from engine import yenidogan as yd             # noqa: E402
from engine.parameter_engine import parametre_uret   # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


class SahteBebek:
    """Baby satırının plan_service'in okuduğu alanları (DB yok)."""
    feeding_type = "anne sütü"
    sleep_method = "kucakta"
    sleep_environment = "aynı oda"
    crying_tolerance = "orta"
    parent_experience = "ilk bebeğim"
    night_wakes = 3
    user_id = "test-user"
    id = "test-baby"

    def __init__(self, gun_once: int, ad: str = "Deniz"):
        self.name = ad
        self.birth_date = date.today() - timedelta(days=gun_once)


def _icerik(gun_once: int, dogum_haftasi: int = 40) -> dict:
    b = SahteBebek(gun_once)
    return plan_service.generate_content(b, None, dogum_haftasi)


# =============================================================================
# 1) YAŞ SINIRI + ALT BANT SEÇİMİ
# =============================================================================
check("1a) 0-3 ay rehber kapsamında, 3 ay ve üstü DEĞİL",
      all(yd.yenidogan_mi(a) for a in (0, 0.5, 1, 2, 2.9))
      and not any(yd.yenidogan_mi(a) for a in (3, 3.1, 5, 9)),
      [(a, yd.yenidogan_mi(a)) for a in (0, 2.9, 3, 3.1)])

_beklenen_bant = {0.0: "0-1_ay", 0.9: "0-1_ay", 1.0: "1-2_ay", 1.9: "1-2_ay",
                  2.0: "2-3_ay", 2.9: "2-3_ay"}
_bant_hata = [(a, yd.alt_bant(a)["id"]) for a, bek in _beklenen_bant.items()
              if yd.alt_bant(a)["id"] != bek]
check("1b) Alt bant sınırları yarı açık: [ay_min, ay_max)",
      not _bant_hata, f"hatalı={_bant_hata}")

_pencereler = {b["id"]: b["uyaniklik_penceresi_dk"] for b in yd.alt_bantlar()}
check("1c) Uyanıklık pencereleri tablodaki değerler (30-60 / 45-75 / 60-90)",
      _pencereler == {"0-1_ay": [30, 60], "1-2_ay": [45, 75], "2-3_ay": [60, 90]},
      _pencereler)

# Prematüre: düzeltilmiş yaş kullanılır — 34 haftalık 4 aylık bebek hâlâ yenidoğan.
_prem = _icerik(gun_once=120, dogum_haftasi=34)
check("1d) Prematüre düzeltilmiş yaşla değerlendirilir (34 hf, 4 aylık → rehber)",
      _prem["type"] == plan_service.TYPE_YENIDOGAN,
      f"type={_prem['type']} duzeltilmis={_prem['yas']['duzeltilmis_ay']}")


# =============================================================================
# 2) İÇERİK YASAĞI — bu yaşa eğitim tekniği SIZAMAZ
# =============================================================================
_reh = _icerik(45)                      # ~1.5 aylık
_md = _reh["markdown"].lower()

_YASAK = ("yatır-çık", "yatır çık", "kademeli uzaklaşma", "beşik yanı",
          "oda ortası", "kapı eşiği", "bekleme süresi", "13 günlük",
          "b planı", "45 dakika direnç", "pış-pış", "pat-pat")
_sizan = [y for y in _YASAK if y in _md]
check("2a) Rehberde HİÇBİR uyku eğitimi tekniği geçmiyor",
      not _sizan, f"sızan={_sizan}")

check("2b) schedule BOŞ — bu yaşta katı saat çizelgesi kurulmaz",
      _reh["schedule"] == [], _reh["schedule"])
check("2c) days BOŞ — merdiven yok",
      _reh["days"] == [], _reh["days"])
check("2d) gece uyanma protokolü EKLENMEMİŞ (eğitim protokolüdür)",
      "night_wake_protocol" not in _reh, list(_reh.keys()))
check("2e) kestirme protokolü EKLENMEMİŞ (yapılandırılmış uyku yönetimidir)",
      "kestirme_protokolu" not in _reh, list(_reh.keys()))
check("2f) uygun_mu False ve üretim LLM'siz (deterministik)",
      _reh["uygun_mu"] is False and _reh["generated_with"] == "deterministik",
      f"uygun={_reh['uygun_mu']} motor={_reh['generated_with']}")
check("2g) Markdown 'uyku eğitimi uygulanmaz' bilgisini AÇIKÇA veriyor",
      "uyku eğitimi uygulanmaz" in _md or "uyku eğitimi" in _md and "uygulanmaz" in _md,
      _md[:200])
check("2h) Marka kuralı: rehberde kişi adı geçmiyor",
      "ilayda" not in _md, [s for s in _md.split("\n") if "ilayda" in s])


# =============================================================================
# 3) REHBERİN ZORUNLU BÖLÜMLERİ
# =============================================================================
_r = _reh["yenidogan"]
check("3a) Uyanıklık penceresi bebeğin alt bandından (1-2 ay → 45-75 dk)",
      _r["alt_bant"]["id"] == "1-2_ay" and _r["uyaniklik_penceresi_dk"] == [45, 75],
      f"{_r['alt_bant']['id']} {_r['uyaniklik_penceresi_dk']}")
check("3b) Uyku sinyalleri listesi dolu (esneme + bakış donuklaşması dahil)",
      len(_r["uyku_sinyalleri"]) >= 5
      and any("esne" in s.lower() for s in _r["uyku_sinyalleri"])
      and any("donuk" in s.lower() for s in _r["uyku_sinyalleri"]),
      _r["uyku_sinyalleri"])
check("3c) Mini rutin 5-10 dk ve sıra doğru (alt → tulum/kundak → loş → beyaz gürültü → sakinleştirme)",
      _r["mini_rutin"]["sure_dk"] == [5, 10] and len(_r["mini_rutin"]["sira"]) == 5
      and "alt" in _r["mini_rutin"]["sira"][0].lower()
      and "gürültü" in _r["mini_rutin"]["sira"][3].lower(),
      _r["mini_rutin"])
check("3d) Gece/gündüz ayrımı iki taraflı dolu",
      _r["gece_gunduz_ayrimi"].get("gunduz") and _r["gece_gunduz_ayrimi"].get("gece"),
      _r["gece_gunduz_ayrimi"])

# Türkçe I/İ tuzağı: "SIRTÜSTÜ".lower() → "sirtüstü" (noktasız ı olmaz).
_guv = chatbot_tr_lower(" ".join(_r["guvenli_uyku"]["kurallar"]))
check("3e) Güvenli uyku: sırtüstü + sert düz yatak + yastık/yorgan/oyuncak yok + ilk 6 ay aynı oda ayrı yatak",
      all(k in _guv for k in ("sırtüstü", "sert", "yastık", "yorgan", "oyuncak"))
      and "6 ay" in _guv and "aynı odada" in _guv and "ayrı yatakta" in _guv,
      _r["guvenli_uyku"]["kurallar"])
check("3f) Güvenli uyku kaynağı NHS olarak işaretli",
      "nhs" in _r["guvenli_uyku"]["kaynak"].lower(), _r["guvenli_uyku"]["kaynak"])
check("3g) Ritim sabitleme 6-8. haftadan itibaren",
      _r["ritim_sabitleme"]["baslangic_hafta"] == [6, 8],
      _r["ritim_sabitleme"])
check("3h) Eğitimin ne zaman başlayacağı takvime çevrilmiş (5. ay)",
      _reh["egitim_baslangic"]["alt_sinir_ay"] == 5
      and _reh["egitim_baslangic"]["kalan_gun"] > 0,
      _reh["egitim_baslangic"])
# Rehberin tamamı markdown'a da düşmeli — mobil eski sürümse metne düşecek.
_bolum_eksik = [b for b in ("Uyanıklık Penceresi", "Uyku Sinyalleri", "Mini Rutin",
                            "Gece ve Gündüz Ayrımı", "Güvenli Uyku", "Ritim Sabitleme")
                if b not in _reh["markdown"]]
check("3i) Altı zorunlu bölüm markdown'da da var",
      not _bolum_eksik, f"eksik={_bolum_eksik}")


# =============================================================================
# 4) content["type"] SÖZLEŞMESİ
# =============================================================================
_tipler = {gun: _icerik(gun)["type"] for gun in (20, 75, 100, 130, 170, 250)}
check("4a) 0-3 ay → yenidogan_ritim",
      all(_tipler[g] == plan_service.TYPE_YENIDOGAN for g in (20, 75)), _tipler)
check("4b) 3-5 ay → egitim_bekleme (eğitim henüz açılmadı)",
      all(_tipler[g] == plan_service.TYPE_BEKLEME for g in (100, 130)), _tipler)
check("4c) 5+ ay → egitim_plani",
      all(_tipler[g] == plan_service.TYPE_EGITIM for g in (170, 250)), _tipler)

_egitim = _icerik(250)
check("4d) Yalnız egitim_plani türünde days DOLU",
      len(_egitim["days"]) == 5 and _icerik(130)["days"] == [],
      f"egitim={len(_egitim['days'])}")


# =============================================================================
# 5) 3-5 AY BOŞLUĞU — eskiden 502 veriyordu
# =============================================================================
# REGRESYON: eğitim uygun değilken plan metninde "## Eğitim Planı" bölümü hiç
# yazılmıyor (prompt 6. kuralı), build_days DayParseError yükseltiyor ve iki
# denemenin ardından router 502 dönüyordu. Yani 0-5 ay arasındaki HER bebekte
# plan üretimi hata veriyordu.
_hata = None
try:
    _bekleme = _icerik(130)                 # ~4.3 aylık
except plan_service.PlanError as e:
    _hata = str(e)
check("5a) 3-5 ay planı PlanError (502) VERMİYOR", _hata is None, f"hata={_hata}")
check("5b) 3-5 ay planında saat çizelgesi VAR (eğitim yok ama planlama var)",
      _hata is None and len(_bekleme["schedule"]) > 0,
      "" if _hata else f"blok={len(_bekleme['schedule'])}")
check("5c) 3-5 ay uyarısı '5. ayını dolduran' kuralını söylüyor",
      _hata is None and any("5. ayını" in u for u in _bekleme["uyarilar"]),
      "" if _hata else _bekleme["uyarilar"])


# =============================================================================
# 6) ADAPTASYON REHBERE DOKUNMAZ
# =============================================================================
check("6a) is_yenidogan içerikten de plandan da çözülüyor",
      plan_service.is_yenidogan(_reh) and not plan_service.is_yenidogan(_egitim)
      and not plan_service.is_yenidogan(None),
      "")


class SahtePlan:
    def __init__(self, content):
        self.content = content
        self.id = "plan-1"
        self.baby_id = "test-baby"


# ensure_current_schema rehbere hiçbir şey EKLEMEMELİ (db'ye dokunmadan döner).
_plan = SahtePlan(dict(_reh))
_donen = plan_service.ensure_current_schema(None, _plan)
check("6b) ensure_current_schema rehbere protokol/çizelge ENJEKTE ETMİYOR",
      _donen is _plan
      and "night_wake_protocol" not in _plan.content
      and "kestirme_protokolu" not in _plan.content
      and _plan.content["schedule"] == [],
      list(_plan.content.keys()))


# =============================================================================
# 7) ATAK HAFTALARI — TAHMİNİ DOĞUM TARİHİNDEN
# =============================================================================
_atak = yd.atak_tablosu()
check("7a) On atak tablosu tam ve haftalar doğru",
      [h["hafta"] for h in _atak["haftalar"]] == [5, 8, 12, 19, 26, 37, 46, 55, 64, 75],
      [h["hafta"] for h in _atak["haftalar"]])
check("7b) İlayda uyarısı tabloyla BİRLİKTE duruyor",
      "kesinliği kanıtlanmış" in _atak["uyari"] and "her atağı yaşamaz" in _atak["uyari"],
      _atak["uyari"][:120])

_BUGUN = date(2026, 9, 14)
# Zamanında doğum: TDT = doğum tarihi.
_t = yd.tahmini_dogum_tarihi("2026-08-10", 40)
check("7c) Zamanında doğumda tahmini doğum tarihi = doğum tarihi",
      _t == date(2026, 8, 10), _t)
# 34 haftalık doğumda TDT 6 hafta İLERİ kayar.
_tp = yd.tahmini_dogum_tarihi("2026-08-10", 34)
check("7d) 34 haftalık doğumda tahmini doğum tarihi 6 hafta ileri kayıyor",
      _tp == date(2026, 8, 10) + timedelta(weeks=6), _tp)

_d1 = yd.atak_durumu("2026-08-10", 40, bugun=_BUGUN)          # 5 hafta önce doğdu
check("7e) 5. haftadaki bebekte 1. atak aktif",
      _d1["aktif_atak"] and _d1["aktif_atak"]["sira"] == 1
      and _d1["aktif_atak"]["hafta"] == 5, _d1["aktif_atak"])
_d2 = yd.atak_durumu("2026-08-10", 34, bugun=_BUGUN)          # aynı gün, prematüre
check("7f) Aynı gün doğan 34 haftalık bebekte atak KAYIYOR (aktif atak yok)",
      _d2["aktif_atak"] is None and _d2["duzeltilmis_hafta"] < 0,
      f"hafta={_d2['duzeltilmis_hafta']} aktif={_d2['aktif_atak']}")
check("7g) Sonraki atak ve kalan hafta hesaplanıyor",
      _d2["sonraki_atak"]["hafta"] == 5 and _d2["sonraki_ataga_kalan_hafta"] > 0,
      f"{_d2['sonraki_atak']} kalan={_d2['sonraki_ataga_kalan_hafta']}")
check("7h) Atak durumu uyarıyı HER ZAMAN taşıyor",
      "kesinliği kanıtlanmış" in _d1["uyari"], _d1["uyari"][:80])


# =============================================================================
# 8) KB İÇERİĞİ KORPUSA GİRİYOR
# =============================================================================
from engine import chatbot                     # noqa: E402

_units = chatbot.build_corpus()
_ids = [u["chunk_id"] for u in _units]


def _var(onek: str) -> int:
    return sum(1 for i in _ids if i.startswith(onek))


check("8a) 0-3 ay uyku kuralları korpusta",
      _var("global_rule:yenidogan_uyku_0_3_ay") >= 10,
      _var("global_rule:yenidogan_uyku_0_3_ay"))
check("8b) Gelişim bölümü korpusta (yeni alan)",
      _var("global_rule:gelisim_0_3_ay") >= 10, _var("global_rule:gelisim_0_3_ay"))
check("8c) Atak haftaları korpusta",
      _var("global_rule:atak_haftalari_wonder_weeks") >= 4,
      _var("global_rule:atak_haftalari_wonder_weeks"))

_yeni = [u for u in _units if u["chunk_id"].startswith(
    ("global_rule:yenidogan_uyku_0_3_ay", "global_rule:gelisim_0_3_ay",
     "global_rule:atak_haftalari_wonder_weeks"))]
check("8d) Yeni birimlerin hiçbirinde kişi adı yok (marka kuralı)",
      not [u["chunk_id"] for u in _yeni if chatbot.KISI_ADI_DESENI.search(u["text"])],
      [u["chunk_id"] for u in _yeni if chatbot.KISI_ADI_DESENI.search(u["text"])])

_metin = chatbot.tr_lower_safe(" ".join(u["text"] for u in _yeni))
for _ad, _ara in (("tummy time", "tummy time"),
                  ("refleksler", "moro"),
                  ("doktora danışma", "asimetrik"),
                  ("38°C acil", "38°c"),
                  ("CDC/NHS kaynak", "cdc"),
                  ("güvenli uyku NHS", "nhs")):
    check(f"8e) KB metninde '{_ad}' var", _ara in _metin, _ara)

# Alan sözlüğü: gelişim soruları K4'e (kapsam dışı) DÜŞMEMELİ.
_k4_test = ["tummy time nedir", "bebeğim gülümsüyor mu olmalı",
            "5. hafta atağı", "moro refleksi ne zaman kaybolur",
            "yenidoğan güvenli uyku kuralları"]
_k4_dusen = [s for s in _k4_test
             if not chatbot._alan_sinyali(s, chatbot.yas_ay_tespit(s))]
check("8f) Gelişim/atak soruları alan içi sayılıyor (K4'e düşmüyor)",
      not _k4_dusen, f"alan dışı kalan={_k4_dusen}")
check("8g) Gerçekten alakasız soru hâlâ kapsam dışı",
      chatbot._kapsam_disi_sinyali("kek tarifi ver")
      and not chatbot._alan_sinyali("kek tarifi ver", None), "")


# =============================================================================
# 9) TEK KAYNAK — 0-2_ay bandı DEĞİŞMEDİ (İlayda onayı bekliyor)
# =============================================================================
# İlayda'nın yeni 0-6 hafta tablosu mevcut bantla çelişiyor; karar gelene kadar
# tabloya DOKUNULMADI. Bu kontrol bir "yanlışlıkla güncelleme" alarmıdır.
_b02 = yb.yas_bandi_getir(1.0)
check("9a) 0-2_ay bandının değerleri korundu (40-80 dk / 8-10 saat / 5-7 saat)",
      _b02["uyaniklik_penceresi_dk"] == [40, 80]
      and _b02["gece_uykusu_dk"] == [480, 600]
      and _b02["gunduz_uyku_toplam_dk"] == [300, 420],
      f"{_b02['uyaniklik_penceresi_dk']} {_b02['gece_uykusu_dk']} "
      f"{_b02['gunduz_uyku_toplam_dk']}")

_cakisma = [k for k in parametre_uret({
    "bebek_ad": "x", "dogum_tarihi": (date.today() - timedelta(days=400)).isoformat(),
    "dogum_haftasi": 40})["global_rules"]]
check("9b) Çakışma kaydı KB'ye yazıldı (tutarsizlik_raporu)",
      any("0-6_hafta" in str(r.get("yas", ""))
          for r in chatbot._load_kb_safe().get("tutarsizlik_raporu", [])),
      "")
check("9c) Yenidoğan alt bantları yas_bantlari.json'dan okunuyor (kodda sabit yok)",
      yb.tablo().get("yenidogan_ritim", {}).get("alt_bantlar") is not None
      and yb.tablo()["version"] == "1.2",
      yb.tablo().get("version"))


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("YENİDOĞAN RİTİM REHBERİ TEST SONUÇLARI (Faz 0-3)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
        print(f"[{mark}] {name}")
    else:
        print(f"[{mark}] {name}\n       {detail}")

print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} geçti")
sys.exit(0 if passed == len(results) else 1)
