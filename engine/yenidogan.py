"""
Yenidoğan ritim rehberi (0-3 ay) — uyku EĞİTİMİ yerine geçen deterministik çıktı.

KRİTİK KURAL (İlayda, 2026-09-14): 0-3 ayda katı uyku programı veya
yapılandırılmış uyku eğitimi UYGULANMAZ. Bu yaştaki bebeğe 13 günlük kademeli
uzaklaşma merdiveni, bekleme süreleri, yatır-çık ya da saat saat bir çizelge
VERİLMEZ. Bunların yerine YENİDOĞAN RİTİM REHBERİ üretilir:
uyanıklık penceresi · uyku sinyalleri · 5-10 dakikalık mini rutin ·
gece/gündüz ayrımı · güvenli uyku (NHS) · 6-8. haftadan itibaren ritim sabitleme.

NEDEN AYRI BİR MODÜL: plan_generator LLM'e yazdırıyor ve çıktısı 13 günlük
merdivenin gün başlıklarını taşımak ZORUNDA (plan_gunleri.build_days doğruluyor).
Yenidoğanda böyle bir merdiven yok; rehber LLM'siz, tamamen tablodan üretilir.
Böylece bu yaşa yanlışlıkla bir eğitim tekniği sızamaz.

SAYISAL TEK KAYNAK: data/yas_bantlari.json > yenidogan_ritim. Metinsel kurallar
master_knowledge_base.json > global_rules > yenidogan_uyku_0_3_ay altındadır ve
chat korpusuna oradan girer (engine/chatbot.build_corpus).

Ana API:
    yenidogan_mi(ay)                    -> bool      # ay < 3 (düzeltilmiş yaş)
    alt_bant(ay)                        -> dict      # 0-1 / 1-2 / 2-3 ay
    ritim_rehberi(profile, yas)         -> dict      # content["yenidogan"] gövdesi
    rehber_markdown(rehber, bebek_ad)   -> str       # content["markdown"]
    tahmini_dogum_tarihi(...)           -> date      # atak hesabının referansı
    atak_durumu(...)                    -> dict      # hangi atak haftasındayız
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from engine import yas_bantlari
from engine.yas_bantlari import _aralik            # süre biçimleyici tek yerde

# Uyku eğitiminin alt sınırı (kayıt37 — parameter_engine ile AYNI eşik).
EGITIM_ALT_SINIRI_AY = 5
# Bir ay kaç gün sayılır (hesapla_yas_ay ile aynı sabit — ayrışmasın).
GUN_PER_AY = 30.44


class YenidoganHatasi(RuntimeError):
    """yenidogan_ritim bölümü tabloda yok ya da bozuk."""


# =============================================================================
# Tablo erişimi
# =============================================================================

def _bugun_tr():
    """B6 — Türkiye günü (konteyner saati UTC)."""
    from api.zaman import bugun_tr
    return bugun_tr()

def _bolum() -> dict:
    bolum = yas_bantlari.tablo().get("yenidogan_ritim")
    # Faz P: alt_bantlar artık bu bölümde DEĞİL, 0-3 ay bandının içinde.
    # Bölümün geçerliliği niteliksel içerikten (uyku sinyalleri) anlaşılır.
    if not isinstance(bolum, dict) or not bolum.get("uyku_sinyalleri"):
        raise YenidoganHatasi(
            "data/yas_bantlari.json içinde 'yenidogan_ritim' bölümü yok/bozuk")
    return bolum


def ust_sinir_ay() -> float:
    """Rehberin geçerli olduğu üst yaş sınırı (ay). Varsayılan 3."""
    return float(_bolum().get("ust_sinir_ay", 3))


def yenidogan_mi(ay: float) -> bool:
    """Bu yaş (DÜZELTİLMİŞ ay) yenidoğan rehberi kapsamında mı?

    Sınır yarı açıktır: [0, ust_sinir_ay). Tam 3.0 ay ARTIK rehber almaz —
    3-5 ay arası eğitim de almaz ama rehber de değildir (bkz. plan_service)."""
    try:
        return 0 <= float(ay) < ust_sinir_ay()
    except (TypeError, ValueError):
        return False


def alt_bantlar() -> list[dict]:
    """Yenidoğan alt bantları — TEK KAYNAK: 0-3 ay bandının içi (Faz P).

    Önceden bu liste yenidogan_ritim altında AYRI duruyordu ve `bantlar`
    listesindeki 0-2_ay bandıyla çakışıyordu (40-80 dk vs 30-60/45-75/60-90);
    canlı cevapta iki küme yan yana çıkıyordu. Artık tek yerde."""
    bantlar = yas_bantlari.alt_bantlar()
    if not bantlar:
        raise YenidoganHatasi(
            "yas_bantlari.json > bantlar > 0-2_ay > alt_bantlar bulunamadı")
    return bantlar


def alt_bant(ay: float) -> dict:
    """Yaşa (düzeltilmiş ay) karşılık gelen yenidoğan alt bandı.

    Bantlar yarı açıktır: [ay_min, ay_max). Üst sınırın üstündeki yaşlar SON
    banda düşer — çağıran zaten yenidogan_mi() ile eliyor, burada sessizce
    boş dönmek yerine en yakın bandı vermek daha güvenli."""
    ay = max(0.0, float(ay))
    bantlar = alt_bantlar()
    for b in bantlar:
        if b["ay_min"] <= ay < b["ay_max"]:
            return b
    return bantlar[-1] if ay >= bantlar[-1]["ay_max"] else bantlar[0]


# =============================================================================
# Eğitime ne kadar kaldı — "5. ay" kuralının takvime çevrilmiş hâli
# =============================================================================
def egitim_uygunluk_tarihi(dogum_tarihi: str | date, duzeltilmis_ay: float,
                           bugun: date | None = None) -> dict:
    """Uyku eğitiminin açılacağı tahmini tarih (düzeltilmiş yaş 5 ayı doldurunca).

    Prematürede düzeltilmiş yaş takvim yaşından geri olduğu için tarih İLERİ
    kayar; hesap doğrudan düzeltilmiş yaş farkından yapılır, doğum tarihine
    5 ay eklemekle DEĞİL."""
    bugun = bugun or _bugun_tr()
    kalan_ay = max(0.0, EGITIM_ALT_SINIRI_AY - float(duzeltilmis_ay))
    kalan_gun = int(round(kalan_ay * GUN_PER_AY))
    return {
        "alt_sinir_ay": EGITIM_ALT_SINIRI_AY,
        "kalan_gun": kalan_gun,
        "tahmini_tarih": (bugun + timedelta(days=kalan_gun)).isoformat(),
        "aciklama": (
            "Uyku eğitimi düzeltilmiş yaşa göre 5. ayın dolmasıyla başlar. "
            "Bu tarih bir hedef değil, tahmini bir başlangıçtır."
        ),
    }


# =============================================================================
# Atak haftaları (Wonder Weeks) — TAHMİNİ DOĞUM TARİHİNDEN hesaplanır
# =============================================================================
def atak_tablosu() -> dict:
    """Atak haftaları tablosu + İlayda uyarısı (salt okunur kopya)."""
    atak = dict(_bolum().get("atak_haftalari") or {})
    atak["haftalar"] = [dict(h) for h in atak.get("haftalar", [])]
    return atak


def tahmini_dogum_tarihi(dogum_tarihi: str | date, dogum_haftasi: int = 40) -> date:
    """Tahmini doğum tarihi (TDT) = gerçek doğum + erkenlik kadar hafta.

    36 haftalık doğan bebekte TDT doğumdan 4 hafta SONRADIR; atak tablosu bu
    tarihten sayılır, dolayısıyla prematürede ataklar takvim yaşına göre geç
    gelir. Zamanında (>=40 hafta) doğumda TDT doğum tarihinin kendisidir."""
    d = (datetime.strptime(dogum_tarihi, "%Y-%m-%d").date()
         if isinstance(dogum_tarihi, str) else dogum_tarihi)
    erken_hafta = max(0, 40 - int(dogum_haftasi or 40))
    return d + timedelta(weeks=erken_hafta)


def atak_durumu(dogum_tarihi: str | date, dogum_haftasi: int = 40,
                bugun: date | None = None) -> dict:
    """Bugün bir atak haftasına denk geliyor mu? (TAHMİNİ DOĞUM TARİHİNDEN)

    Dönen:
        {
          "tahmini_dogum_tarihi": "2026-08-01",
          "duzeltilmis_hafta": 6.3,          # TDT'den bugüne geçen hafta
          "aktif_atak": {...} | None,        # tolerans (±1 hafta) içindeyse
          "sonraki_atak": {...} | None,
          "sonraki_ataga_kalan_hafta": 1.7,
          "uyari": "..."                     # bilimsel kesinlik uyarısı
        }

    MOTOR BUNU YAPABİLİR: gereken iki girdi (doğum tarihi + doğum haftası) zaten
    profilde var ve prematüre kayması TDT ile doğrudan modellenir. Hesap
    deterministiktir, LLM'e sorulmaz. Kullanıcıya GÖSTERİLİP gösterilmeyeceği
    ayrı bir karardır — bkz. FAZ_0_3_RAPORU.md."""
    bugun = bugun or _bugun_tr()
    tdt = tahmini_dogum_tarihi(dogum_tarihi, dogum_haftasi)
    hafta = (bugun - tdt).days / 7.0

    atak = atak_tablosu()
    tolerans = float(atak.get("tolerans_hafta", 1))
    haftalar = atak.get("haftalar", [])

    aktif = next((dict(h) for h in haftalar
                  if abs(hafta - h["hafta"]) <= tolerans), None)
    sonraki = next((dict(h) for h in haftalar if h["hafta"] > hafta), None)
    return {
        "tahmini_dogum_tarihi": tdt.isoformat(),
        "duzeltilmis_hafta": round(hafta, 1),
        "aktif_atak": aktif,
        "sonraki_atak": sonraki,
        "sonraki_ataga_kalan_hafta": (round(sonraki["hafta"] - hafta, 1)
                                      if sonraki else None),
        "uyari": atak.get("uyari", ""),
    }


# =============================================================================
# Rehberin kendisi
# =============================================================================
def ritim_rehberi(yas: dict, kb: dict | None = None) -> dict:
    """0-3 ay ritim rehberinin YAPILANDIRILMIŞ gövdesi (content["yenidogan"]).

    `yas`: parameter_engine.hesapla_yas_ay çıktısı.
    Tüm sayılar data/yas_bantlari.json > yenidogan_ritim'den gelir; burada
    hiçbir değer sabit yazılmaz."""
    bolum = _bolum()
    bant = alt_bant(yas["duzeltilmis_ay"])
    rutin_dk = bolum.get("mini_rutin_dk", [5, 10])
    sabitleme = bolum.get("ritim_sabitleme_hafta", [6, 8])

    return {
        "alt_bant": bant,
        "uyaniklik_penceresi": _aralik(bant["uyaniklik_penceresi_dk"]),
        "uyaniklik_penceresi_dk": list(bant["uyaniklik_penceresi_dk"]),
        "tum_alt_bantlar": [
            {"ad": b["ad"], "uyaniklik_penceresi": _aralik(b["uyaniklik_penceresi_dk"])}
            for b in alt_bantlar()
        ],
        "uyku_sinyalleri": list(bolum.get("uyku_sinyalleri", [])),
        "mini_rutin": {
            "sure": f"{rutin_dk[0]}-{rutin_dk[1]} dakika",
            "sure_dk": list(rutin_dk),
            "sira": list(bolum.get("mini_rutin_sirasi", [])),
        },
        "gece_gunduz_ayrimi": {
            k: list(v) for k, v in (bolum.get("gece_gunduz_ayrimi") or {}).items()
        },
        "guvenli_uyku": {
            "kaynak": (bolum.get("guvenli_uyku") or {}).get("kaynak", ""),
            "kurallar": list((bolum.get("guvenli_uyku") or {}).get("kurallar", [])),
        },
        "ritim_sabitleme": {
            "baslangic_hafta": list(sabitleme),
            "metin": (
                f"{sabitleme[0]}-{sabitleme[1]}. haftadan itibaren güne aynı saatte "
                "başlamak ve akşam rutinini aynı saatlerde uygulamak ritmin "
                "oturmasına yardım eder. Gün içindeki uykular yine sinyale göre akar."
            ),
        },
    }


# --- Markdown gövdesi --------------------------------------------------------
def _madde(baslik: str, maddeler: list[str]) -> list[str]:
    if not maddeler:
        return []
    return [f"## {baslik}", ""] + [f"- {m}" for m in maddeler] + [""]


def rehber_markdown(rehber: dict, bebek_ad: str, yas: dict,
                    kurallar: dict | None = None,
                    egitim: dict | None = None) -> str:
    """Rehberin okunabilir metni (content["markdown"]).

    LLM ÇAĞRILMAZ: metin tablodan deterministik kurulur. Böylece bu yaşa bir
    eğitim tekniğinin sızması yapısal olarak imkânsızdır."""
    k = kurallar or {}
    bant = rehber["alt_bant"]
    L: list[str] = [
        f"# {bebek_ad} İçin Yenidoğan Ritim Rehberi",
        "",
        f"*{bant['ad']} · düzeltilmiş yaş {yas['duzeltilmis_ay']:.1f} ay*",
        "",
        "## Bu Yaşta Uyku Eğitimi Uygulanmaz",
        "",
        "Bebeğiniz henüz yenidoğan döneminde. Bu dönemde katı bir uyku programı "
        "ya da yapılandırılmış bir uyku eğitimi uygulanmaz — bu bir eksiklik "
        "değil, bebeğinizin gelişim dönemi gereğidir. Bunun yerine size bir "
        "**ritim rehberi** hazırladık: bebeğinizin ne kadar uyanık kalabileceği, "
        "uykusunun geldiğini nasıl anlayacağınız ve uykuya nasıl hazırlayacağınız.",
        "",
    ]
    if egitim:
        L += [
            f"Uyku eğitimi düzeltilmiş yaşa göre {egitim['alt_sinir_ay']}. ayın "
            f"dolmasıyla başlar — bebeğiniz için tahminen "
            f"**{egitim['tahmini_tarih']}** (yaklaşık {egitim['kalan_gun']} gün). "
            "O güne kadar aşağıdaki ritim yeterlidir.",
            "",
        ]

    L += [
        "## Uyanıklık Penceresi",
        "",
        f"**{bant['ad']}: {rehber['uyaniklik_penceresi']}**",
        "",
        "Bu süre bebeğinizin iki uyku arasında rahatça uyanık kalabileceği "
        "aralıktır. Katı bir hedef değildir: bebeğiniz sürenin alt ucunda uyku "
        "sinyali veriyorsa beklemeyin, üst ucuna dayanmadan yatırın.",
        "",
        "Yenidoğan döneminde pencere ay ay uzar:",
        "",
    ]
    L += [f"- {b['ad']}: {b['uyaniklik_penceresi']}" for b in rehber["tum_alt_bantlar"]]
    L += [""]

    L += _madde("Uyku Sinyalleri — Saate Değil Bunlara Bakın",
                rehber["uyku_sinyalleri"])
    L += [
        "Sinyal geldiğinde uyanıklık penceresi dolmamış olsa bile uykuya "
        "hazırlanın. Sinyali kaçırırsanız bebeğiniz aşırı yorulur ve uykuya "
        "geçişi zorlaşır — geç kalmak, erken yatırmaktan daha risklidir.",
        "",
    ]

    L += [f"## Mini Rutin ({rehber['mini_rutin']['sure']})", "",
          "Her uyku öncesi, her seferinde **aynı sırayla**:", ""]
    L += [f"{i}. {adim}" for i, adim in enumerate(rehber["mini_rutin"]["sira"], 1)]
    L += ["",
          "Bu bir uyku eğitimi tekniği değildir; bebeğinize \"şimdi uyku zamanı\" "
          "sinyalini veren kısa bir çağrışım zinciridir. Kısa tutun, uzatmayın.",
          ""]

    ayrim = rehber.get("gece_gunduz_ayrimi") or {}
    if ayrim:
        L += ["## Gece ve Gündüz Ayrımı", "",
              "Yenidoğanda gece-gündüz ayrımı henüz gelişmemiştir; zamanla oturur. "
              "Desteklemek için:", ""]
        if ayrim.get("gunduz"):
            L += ["**Gündüz uykularında**", ""] + [f"- {m}" for m in ayrim["gunduz"]] + [""]
        if ayrim.get("gece"):
            L += ["**Gece uykusunda**", ""] + [f"- {m}" for m in ayrim["gece"]] + [""]

    guvenli = rehber.get("guvenli_uyku") or {}
    if guvenli.get("kurallar"):
        L += [f"## Güvenli Uyku Kuralları ({guvenli.get('kaynak', 'NHS')})", ""]
        L += [f"- {m}" for m in guvenli["kurallar"]]
        L += ["", "Bu kurallar uyku eğitiminden bağımsız olarak her bebekte geçerlidir.", ""]

    L += ["## Ritim Sabitleme", "", rehber["ritim_sabitleme"]["metin"], ""]

    # --- Bu yaşın kritik uyarıları (KB'den; metin tek yerde yaşasın) ---------
    uyarilar = [k.get(anahtar) for anahtar in (
        "destekle_uyuma_bu_yasta_normal",
        "beslenmeyi_uyku_programina_uydurma",
        "uzun_uyanik_tutma_yanlisi",
    )]
    uyarilar = [u for u in uyarilar if u]
    if uyarilar:
        L += ["## Bu Dönemde Bilmeniz Gerekenler", ""]
        L += [f"- {u}" for u in uyarilar]
        L += [""]

    L += ["---", "",
          f"*Bu rehber {bebek_ad} için üretildi. Bebeğiniz "
          f"{EGITIM_ALT_SINIRI_AY}. ayını doldurduğunda uyku eğitimi planınız "
          "hazırlanabilir. Sorularınız için Soru-Cevap bölümünü kullanın.*"]
    return "\n".join(L)


def headline(bebek_ad: str, rehber: dict) -> str:
    """Mobilin plan kartında gösterdiği tek satır."""
    bant = rehber["alt_bant"]
    return (f"{bebek_ad} için yenidoğan ritim rehberi — {bant['ad']}, "
            f"uyanıklık penceresi {rehber['uyaniklik_penceresi']}")
