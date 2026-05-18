"""
Plan Generator — parameter_engine çıktısından Markdown plan üretir.

Önemli mimari kural: SAYISAL DEĞERLER ASLA DEĞİŞTİRİLMEZ.
LLM sadece "yazar" rolünde — açıklama, biçim, dil. Sayılar deterministik motordan gelir.
"""
import os
import json
from typing import Any

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

MODEL_NAME = "claude-opus-4-7"
MAX_TOKENS = 4096


def _format_dict(d: dict, indent: int = 0) -> str:
    """Profil ya da parametre sözlüğünü okunabilir biçime çevir."""
    lines = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(_format_dict(v, indent + 1))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            for item in v:
                if isinstance(item, dict):
                    lines.append(_format_dict(item, indent + 1))
                else:
                    lines.append(f"{pad}  - {item}")
        else:
            lines.append(f"{pad}{k}: {v}")
    return "\n".join(lines)


SYSTEM_PROMPT = """Sen, Tavşan Uykusu uyku eğitimi programının kişisel plan yazarı olarak çalışıyorsun. \
Annelere profesyonel, sıcak ama net Türkçe ile hitap edersin. Sayısal değerler kesindir, asla değiştirilmez."""


def _build_user_prompt(param: dict) -> str:
    return f"""Aşağıdaki PARAMETRELERİ kullanarak anneye yönelik bir uyku eğitimi planı yaz.

KESIN KURALLAR (BUNLARI İHLAL ETME):
1. Sayısal değerleri (saatler, dakikalar, uyku sayısı, gün sayısı) ASLA değiştirme.
2. Markdown formatı kullan, başlıkları ## ile aç.
3. Profesyonel ama sıcak Türkçe yaz.
4. Görsel referansı, ders/kayıt adı, "İlayda Hanım kaydından" gibi ifadeler YASAK.
5. Net, uygulanabilir adımlar ver — soyut tavsiye değil.
6. Eğer eğitim uygun değilse, sebebini açıkça yaz ve plan yazma; sadece bekleyiş notu ve hazırlık önerileri ver.
7. Anneye 2. tekil şahıs ile hitap et ("yapacaksınız", "uygulayın").
8. Açıkça belirtilmemiş bilgi varsa UYDURMA — "İlerleyen günlerde detaylanacak" veya "danışmanlık sürecinde belirlenecek" diyebilirsin.

PROFIL:
{_format_dict(param['profile_summary'])}

YAŞ BİLGİSİ:
- Gerçek yaş: {param['yas']['gercek_ay']} ay
- Düzeltilmiş yaş: {param['yas']['duzeltilmis_ay']} ay
- Yaş bucket: {param['bucket']}
- Prematüre mi: {param['yas']['prematüre_mi']}

EĞİTİM UYGUNLUĞU:
- Uygun mu: {param['uygun_mu']}
- Uyarılar:
{chr(10).join(f"  - {u}" for u in param['uyarilar']) or "  (yok)"}

ÖN HAZIRLIK:
{_format_dict({"hazirliklar": param['on_hazirlik']})}

SEÇİLEN PLAN:
- Tip: {param['plan_secimi']['tip']}
- Gün sayısı: {param['plan_secimi']['gunler']}
- Açıklama: {param['plan_secimi']['aciklama']}

YAŞ PARAMETRELERİ (bucket'tan):
{_format_dict(param['parametreler'])}

GECE BESLENMESİ:
{_format_dict(param.get('gece_beslenme', {}) or {})}

BEKLEME SÜRELERİ VE KADEMELİ UZAKLAŞMA:
{_format_dict(param['bekleme_sureleri'])}

GLOBAL KURALLAR:
{_format_dict(param['global_rules'])[:3000]}

PLANIN İÇERMESİ GEREKEN BÖLÜMLER:
1. ## Bebek Profili Özeti (yaş, beslenme, mevcut destek, oda durumu, kısa özet)
2. ## Eğitim Uygunluğu (uygunsa "Uygun", değilse sebep ve hazırlık)
3. ## Ön Hazırlık (varsa gün gün)
4. ## Günlük Program (saat saat — yaş bucket'ından gelen "ornek_gunluk_program_basit" varsa kullan)
5. ## Eğitim Planı (gün gün, plan_secimi['gunler'] kadar gün)
6. ## Gece Uyanmaları Protokolü
7. ## Başarı Kriterleri
8. ## Dikkat Edilmesi Gerekenler (kaçınılması gereken hatalar)

Plan yaklaşık 1500-2500 kelime arası olsun. Profesyonel, net, eyleme dönük.

Markdown ile yaz, hiçbir görsel referans yok, hiçbir ders adı geçmesin.
"""


def plan_uret(param: dict) -> str:
    """
    Claude API ile plan üret. API key yoksa fallback olarak deterministik markdown çıktı verir.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key or not HAS_ANTHROPIC:
        return _fallback_plan(param)

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(param)}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Fallback — API key olmadığında deterministik markdown plan
# ---------------------------------------------------------------------------
def _fallback_plan(param: dict) -> str:
    """API yoksa parametrelerden doğrudan Markdown plan üret."""
    profile = param["profile_summary"]
    yas = param["yas"]
    p = param["parametreler"]
    plan = param["plan_secimi"]
    bekleme = param["bekleme_sureleri"]
    gece = param.get("gece_beslenme") or {}

    bebek_adi = profile.get("bebek_ad", "Bebeğiniz")

    lines = []
    lines.append(f"# {bebek_adi} İçin Uyku Eğitimi Planı")
    lines.append("")
    lines.append(f"*Not: Bu plan API anahtarı bulunmadığı için kural-tabanlı yedek motordan üretildi. Tam metin için ANTHROPIC_API_KEY eklendiğinde Claude tarafından yazılır.*")
    lines.append("")

    # Bölüm 1: Profil
    lines.append("## Bebek Profili Özeti")
    lines.append("")
    lines.append(f"- **Ad:** {bebek_adi}")
    lines.append(f"- **Yaş:** {yas['gercek_ay']:.1f} ay (düzeltilmiş: {yas['duzeltilmis_ay']:.1f} ay)")
    if yas["prematüre_mi"]:
        lines.append(f"- **Prematüre:** {yas['dogum_haftasi']} haftalık doğum (tüm hesaplar düzeltilmiş yaşa göre)")
    lines.append(f"- **Beslenme:** {profile.get('beslenme', '-')}")
    lines.append(f"- **Mevcut destek:** {profile.get('destek', '-')}")
    lines.append(f"- **Oda durumu:** {profile.get('oda', '-')}")
    lines.append(f"- **Mizaç:** {profile.get('mizac', '-')}")
    lines.append("")

    # Bölüm 2: Uygunluk
    lines.append("## Eğitim Uygunluğu")
    lines.append("")
    if param["uygun_mu"]:
        lines.append("✅ **Bebeğiniz uyku eğitimi için uygun.**")
    else:
        lines.append("⛔ **Bebeğiniz şu an için uyku eğitimine uygun değil.** Aşağıdaki uyarılara bakın.")
    for u in param["uyarilar"]:
        lines.append(f"- {u}")
    lines.append("")

    if not param["uygun_mu"]:
        lines.append("> Eğitim verilemeyeceği için günlük program ve plan adımları bu rapora dahil edilmedi. Bekleyiş ve hazırlık önerileri için ön hazırlık bölümünü kontrol edin.")
        if not param["on_hazirlik"]:
            return "\n".join(lines)

    # Bölüm 3: Ön Hazırlık
    if param["on_hazirlik"]:
        lines.append("## Ön Hazırlık")
        lines.append("")
        for h in param["on_hazirlik"]:
            lines.append(f"- **{h['konu']}** ({h['sure']}): {h['aksiyon']}")
        lines.append("")

    # Devam: sadece uygun ise
    if not param["uygun_mu"]:
        return "\n".join(lines)

    # Bölüm 4: Günlük Program
    lines.append("## Günlük Program (Saat Saat)")
    lines.append("")
    prog = p.get("ornek_gunluk_program_basit") if isinstance(p, dict) else None
    if isinstance(prog, dict):
        for saat, akt in prog.items():
            lines.append(f"- **{saat}** — {akt}")
    else:
        lines.append("- Yaş aralığınız için ekli görsel programı (ileride app içinde) referans alabilirsiniz.")
        # Bucket'tan parametre özetini ver
        uy = p.get("uyaniklik_penceresi", {}) if isinstance(p, dict) else {}
        if isinstance(uy, dict):
            for k, v in uy.items():
                lines.append(f"- **{k}**: {v}")
    lines.append("")

    # Bölüm 5: Eğitim Planı
    lines.append(f"## Eğitim Planı — {plan['gunler']} Günlük {plan['tip']}")
    lines.append("")
    lines.append(plan["aciklama"])
    lines.append("")
    lines.append("**Kademeli uzaklaşma:**")
    for k, v in bekleme["kademeli_uzaklasma"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("**Kucağa alma bekleme süreleri:** " + bekleme["kucaktan_almak"])
    lines.append("")
    lines.append("**45 dakika kuralı:** " + bekleme["egitim_seans_max"])
    lines.append("")
    lines.append("**B planı (1 saat direnç):** " + bekleme["B_plan_1_saat_direnç"])
    lines.append("")

    # Bölüm 6: Gece Uyanmaları
    lines.append("## Gece Uyanmaları Protokolü")
    lines.append("")
    lines.append(bekleme["gece_uyanma_dis_bekleme"])
    lines.append("")
    if gece:
        lines.append("**Gece beslenme:**")
        for k, v in gece.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    lines.append("**Yatır-çık sonrası:** " + bekleme["yatir_cik_sonrasi"])
    lines.append("")
    lines.append("**Kısa gündüz uykusu uzatma:** " + bekleme["kisa_gunduz_uykusu"])
    lines.append("")

    # Bölüm 7: Başarı Kriterleri
    lines.append("## Başarı Kriterleri")
    lines.append("")
    lines.append("- 1. gün: bebek en az 1 kez yatağında uykuya geçti (ağlayarak da olsa)")
    lines.append("- 2. gün: direnç süresi 10 dakikaya düşmüş olmalı")
    lines.append("- Yatır-çık günü: desteksiz, sessiz, en az 1 kez kendi başına uyumuş")
    lines.append("- 21 gün: min 1 kez kendi başına uykuya geri dönmüş")
    lines.append("- Eğitim sürdürülebilirliği: yatır-çık'a devam ediliyor, pış-pış'a dönülmedi")
    lines.append("")

    # Bölüm 8: Dikkat
    lines.append("## Dikkat Edilmesi Gerekenler")
    lines.append("")
    lines.append("⚠️ Aşağıdakileri YAPMAYIN — eğitim 3-6 hafta içinde %80 oranında bozulur:")
    lines.append("")
    lines.append("- Pat-pat / pış-pış desteğine geri dönmek (gece uyanma bitti diye)")
    lines.append("- Mayıştırıp yatırma (sallayıp, masaj sonrası yatırma)")
    lines.append("- Uykudan önce su verme (su bağımlılığı oluşur)")
    lines.append("- Yatır-çık adımını atlamak (5. veya 13. günde yatır-çık ZORUNLU)")
    lines.append("- Bebeği uykudan uyandırıp gece beslemek (her zaman uykudayken)")
    lines.append("- Histerik ağlamayı bekleme süresiyle yönetmek (histerik anında doğrudan müdahale)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Bu plan {bebek_adi} için kişisel olarak üretildi. Sorularınız için Soru-Cevap bölümünü kullanın.*")

    return "\n".join(lines)
