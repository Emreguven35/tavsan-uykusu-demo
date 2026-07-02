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

from engine.config import PLAN_MODEL  # plan üretici modeli (sonnet — merkezi)
# 16384: 1 aylık program (4 haftalık + 13 eğitim günü × gün-altı B-Planı protokolleri)
# 4096 ve 8192'de yarıda kesiliyordu; bu sınır en uzun planın bile tamamlanmasını sağlar.
MAX_TOKENS = 16384


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


def _bir_ay_program_blok(param: dict) -> str:
    """1 aylık program seçiliyse prompt'a eklenecek özel takvim talimatı (aksi halde boş)."""
    plan = param.get("plan_secimi", {})
    if plan.get("tip") != "1_ay_program":
        return ""
    alt = plan.get("alt_yontem_aciklama", "bebeğe uygun deterministik yöntem")
    return f"""
1 AYLIK PROGRAM — ZORUNLU TAKVİM YAPISI (BU PLANDA UYGULA):
Bu plan 4 haftaya yayılır ve "Eğitim Planı" bölümünü iki ayrı döneme ayırman ZORUNLU:
- **Hafta 1-2 — "Düzen Oturtma Dönemi":** Bu iki haftada uyku eğitimi tekniği UYGULANMAZ. Bebek alıştığı şekilde DESTEKLE uyutulmaya devam eder. Sadece günlük program (uyku saatleri, beslenme saatleri, rutinler) uygulanır; amaç biyolojik saati ve düzeni oturtmaktır. Anneye bu dönemde AÇIKÇA belirt: henüz bir uyku eğitimi tekniği uygulanmıyor, bebek alıştığı şekilde uyutulmaya devam ediliyor, yalnızca saat ve rutin düzeni kuruluyor. Kademeli uzaklaşma adımlarını (yatak yanı, oda ortası, kapı vb.) bu iki haftada BAŞLATMA.
- **Hafta 3-4 — "Eğitim Dönemi":** 3. haftadan itibaren asıl uyku eğitimi başlar. "BEKLEME SÜRELERİ VE KADEMELİ UZAKLAŞMA" bölümündeki yöntem ({alt}) bu dönemde gün gün uygulanır.
"Eğitim Planı" bölümünü bu 4 haftalık takvim olarak kur: önce Hafta 1-2 düzen dönemi (teknik yok), sonra Hafta 3-4 eğitim günleri. Amaç ailelere daha yumuşak bir geçiş sunmaktır.
ÖNEMLİ: Hafta 3-4'teki HER eğitim günü için, diğer planlarda olduğu gibi o günün bloğunun sonunda "Bu gün kısa gündüz uykusu olursa" ve "Bu gün yoğun direnç olursa (B Planı)" alt başlıklarını AÇIKÇA yaz. B Planı zamanlamasını (45 dakika direnç → 15 dakika rutin molası → 45 dakika yeniden deneme; çok dirençli bebekte rutin molası 30 dakika; maksimum 3 tekrar; 3 tekrar sonrası uyanık tut + sonraki uykuda devam) her gün için yaz, özetleyip geçme.
"""


def _build_user_prompt(param: dict) -> str:
    bir_ay_blok = _bir_ay_program_blok(param)
    return f"""Aşağıdaki PARAMETRELERİ kullanarak anneye yönelik bir uyku eğitimi planı yaz.

KESIN KURALLAR (BUNLARI İHLAL ETME):
1. Sayısal değerleri (saatler, dakikalar, uyku sayısı, gün sayısı) ASLA değiştirme.
2. Markdown formatı kullan, başlıkları ## ile aç.
3. Profesyonel ama sıcak Türkçe yaz.
4. Görsel referansı, ders/kayıt adı, "İlayda Hanım kaydından" gibi ifadeler YASAK.
5. Net, uygulanabilir adımlar ver — soyut tavsiye değil.
6. Eğer eğitim uygun değilse, sebebini açıkça yaz ve plan yazma; sadece bekleyiş notu ve hazırlık önerileri ver.
7. Anneye doğrudan ve nazik bir dille hitap et; emir kipi değil öneri kipi kullan (aşağıdaki ÜSLUP KURALI'na uy).
8. Açıkça belirtilmemiş bilgi varsa UYDURMA — "İlerleyen günlerde detaylanacak" veya "danışmanlık sürecinde belirlenecek" diyebilirsin.
9. GÜNE BAŞLAMA SAATİ NOTU: Günlük program tablosunda güne başlama saati "KATI saat" olarak belirtildiğinde, tablonun hemen altına şu içerikte bir not ekle: "Not: Bu katı saat kuralı eğitim sürecine özeldir. Eğitim başarıyla tamamlandıktan sonra güne başlama saati 07:00'a kadar esnetilebilir." (Saat değerini bebeğin yaşına ve plana göre uyarlayabilirsin, ama eğitim sonrası esneme imkanı mutlaka belirtilmeli.)

ÜSLUP KURALI: Ebeveyne hitap ederken sert emir kipi ve kesin gelecek zaman kullanma. 'takmayacaksınız', 'yapmayacaksınız', 'vermeyeceksiniz' gibi ifadeler yerine 'takmamalısınız', 'yapmamanızı öneriyorum', 'vermemeniz gerekiyor' gibi öneri/gereklilik kipi kullan. Ton her zaman destekleyici ve nazik olmalı, asla buyurgan olmamalı.

İÇERİK KURALLARI (İLAYDA GÜNCELLEMELERİ — KESİNLİKLE UY):

A) GÜNLÜK PROGRAM = UYANIKLIK SÜRESİ MANTIĞI: Günlük program tablosunun başlığına "(Örnek Akış — Saatler Uyanıklık Süresine Göre Kayar)" ibaresini ekle. Tablonun hemen ALTINA mutlaka şu açıklama bloğunu yaz: "ÖNEMLİ — Bu tablo örnek bir akıştır, saatler sabit değildir. Belirleyici olan bebeğin UYANIKLIK SÜRESİDİR. Örnek: Güne 06:00'da başlandı, 06:15 ilk beslenme, 07:30 ek gıda, 09:00'da uykuya yatırıldı. Bebek 10:30'da kalktıysa (1,5 saat uyuduysa) bir sonraki uykuya yatış saati buna göre hesaplanır; ama 09:30'da kalktıysa (30 dakika uyuduysa) bir sonraki uyku saati DAHA ERKENE çekilir. Her uykudan sonra bir sonraki yatış saatini bebeğin gerçek uyanma saatine ve yaşına uygun uyanıklık süresine göre güncelleyin. Bir sonraki yatış saatini, bebeğin UYANDIĞI saatin üzerine yaşına uygun uyanıklık süresini ekleyerek hesaplayın. Kısa uyuyup gereğinden uzun uyanık kalan bebekte kortizol yükselir ve uykuya geçiş zorlaşır." Açıklamadaki uyanıklık süresi değerini, YAŞ PARAMETRELERİ bölümündeki bu bebeğin yaşına ait gerçek uyanıklık penceresi değeriyle doldur.

B) "KUCAĞA ALMAYIN" İFADESİ TAMAMEN YASAK: "Kucağa almayın", "kucağa alma yok" veya kucağa almayı yasaklayan hiçbir ifade KULLANILAMAZ. Bu uygulama güven bağı temelli uyku eğitimi sunar. Bunun yerine şu kalıbı kullan: "Kucağa alabilirsiniz; ancak ağlaması çok yoğunsa kucağa alma aralığını kademeli artırın: 30 saniye → 1 dakika → 1,5 dakika → 2 dakika. Sakinleşince yatağa geri yatırın."

C) "TEMAS YOK" İFADESİ YASAK: "Temas YOK" gibi katı ifadeler kullanma. Oda ortası (Gün 4-6) aşamasının AMACI temas ihtiyacını kademeli azaltmaktır; bunu ebeveyne açıkla. Şöyle anlat: "Bu aşamada teması azaltmak için odanın ortasına geçiyoruz. Temas etmeniz gerekirse temas edebilirsiniz; ardından tekrar odanın ortasındaki sandalyenize dönün. Ağlama yoğunlaşırsa 30 saniye → 1 dakika → 1,5 dakika aralıklarla kucağa alabilirsiniz; ancak bebeği yatağa yatırdıktan sonra beklemenize odanın ortasından devam edin."

D) BEYAZ GÜRÜLTÜ = KADEMELİ AZALTMA: "Beyaz gürültüyü kapatın/kapatabilirsiniz" gibi ifadeler kullanma. Kural: "Beyaz gürültü her uykuda BİR KADEME kısılarak azaltılır. İhtiyaç varsa kullanılmaya devam edilebilir; ancak her uykuda ses bir önceki uykudan daha kısık olmalı ve bir sonraki uykuda daha da azalmalıdır."

E) "YARI GÖRÜNÜR" KAVRAMI YOK: "Yarı görünür", "yarı yarıya görsün" gibi ifadeler KULLANMA. Kural: "12. güne kadar ebeveyn bebeğe TAM GÖRÜNÜR olmalıdır — odanız buna müsaitse. Odanız müsait değilse, eğitim öncesinde oda düzenini tam görünürlüğe imkan verecek şekilde planlayın."

F) PROTOKOLLERİN YERİ — HER GÜNÜN ALTINDA: "Kısa Gündüz Uykusu Protokolü" ve "B Planı"nı AYRI/genel bölümler olarak YAZMA. Her eğitim gününün (veya aşamasının) bloğunun sonunda, O GÜNÜN pozisyonuna uyarlanmış iki alt başlık ekle: "Bu gün kısa gündüz uykusu olursa" ve "Bu gün yoğun direnç olursa (B Planı)". Davranış güne göre değişir: kısa gündüz uykusunda dışarıda bekleme sonrası içeri girilince o günkü pozisyondan devam edilir — örneğin 1. günde BEŞİK YANINDAN, 4. günde ODA ORTASINDAN, kapı günlerinde KAPIDAN. B Planı için '1 saat' ifadesini ASLA kullanma; BEKLEME SÜRELERİ bölümündeki "B_plan_direnç" kuralını (45 dk direnç → 15 dk rutin molası → 45 dk yeniden deneme; çok dirençli bebekte rutin molası 30 dk; max 3 tekrar) o güne uyarla.

G) BEBEK ARABASI GÜVENLİK KURALI: Bebek arabasıyla (veya hareketle/sallayarak) uyutma önerisi HİÇBİR koşulda yapılmaz. 3 tekrar sonrasında da uyumuyorsa: o uyku denemesi sonlandırılır, bebek yaşına uygun uyanıklık süresi kadar uyanık tutulur ve bir sonraki uyku denemesinde eğitime devam edilir.

H) SON GÜNDÜZ UYKUSU BİTİŞ SAATİ ESNEKTİR: Günlük programda son gündüz uykusu için bir bitiş saati (örn. 16:00) belirtildiğinde, o satırın/tablonun altına şu içerikte bir not ekle: "Not: Bu bitiş saati katı değildir. Önceliğimiz, bebeğin gün içinde uyuması gereken minimum uykuyu tamamlamasıdır. Bebek yaşına uygun minimum gündüz uyku süresini (YAŞ PARAMETRELERİ'ndeki gunduz_uyku_total değeri) ya da günlük toplam uyku hedefini dolduramadıysa, bu bitiş saati aşılsa bile İLAVE bir gündüz uykusu yaptırın; yani bebek yeterince uyumadıysa uykuyu 16:00'da bitirmenin bir kıymeti yoktur. Bu ilave uykudan sonra, bebeği yaşına uygun uyanıklık penceresi kadar uyanık tutup gece uykusuna geçin — gece uykusu biraz gecikse bile minimum gündüz/toplam uyku önceliklidir." Bu kural yalnızca alışma evresinde değil, eğitim tamamlandıktan sonra da geçerlidir. İlave uyku gerektiğinde, o yaşın normal gündüz uyku sayısının bir fazlasına çıkılabilir (ör. 8 ay: 3 yerine 4 uyku). Bu nottaki sayısal değerleri YAŞ PARAMETRELERİ bölümündeki gerçek değerlerden al; tabloda olmayan bir yaş için değer uydurma.

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
{bir_ay_blok}
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
4. ## Günlük Program (saat saat — yaş bucket'ından gelen "ornek_gunluk_program_basit" varsa kullan; A kuralını uygula: başlık ibaresi + tablonun altına uyanıklık süresi açıklaması)
5. ## Eğitim Planı (gün gün, plan_secimi['gunler'] kadar gün; F kuralı: her günün altına o güne uyarlanmış "Bu gün kısa gündüz uykusu olursa" ve "Bu gün yoğun direnç olursa (B Planı)" alt başlıkları — AYRI genel protokol bölümü AÇMA)
6. ## Gece Uyanmaları Protokolü
7. ## Başarı Kriterleri
8. ## Dikkat Edilmesi Gerekenler (kaçınılması gereken hatalar)

Plan yaklaşık 1500-2500 kelime arası olsun. Profesyonel, net, eyleme dönük.

Markdown ile yaz, hiçbir görsel referans yok, hiçbir ders adı geçmesin.
"""


# Prompt caching ayracı: user promptu PROFIL'den önce ikiye bölünür.
# Öncesi (İlayda kuralları + üslup + KESIN KURALLAR) HER planda birebir aynıdır → cache'lenir.
# Sonrası (bu bebeğe özel PROFIL/parametreler) değişkendir → cache'lenMEZ.
_CACHE_SPLIT_MARKER = "\n\nPROFIL:\n"


def _build_cached_content(param: dict) -> list[dict]:
    """User mesajını iki content bloğuna böler:
      1) Sabit kurallar ön-eki — cache_control: ephemeral (her planda aynı, cache'lenir).
      2) Bu bebeğe özel değişken kısım — cache'lenmez.
    İki bloğun metin birleşimi _build_user_prompt(param) ile BİREBİR aynıdır;
    model girdisi DEĞİŞMEZ, yalnızca faturalama (input token maliyeti) düşer.
    """
    full = _build_user_prompt(param)
    idx = full.index(_CACHE_SPLIT_MARKER)
    static_prefix = full[:idx] + "\n\n"   # İlayda kuralları + ayraç (sabit ön-ek)
    variable_part = full[idx + 2:]        # "PROFIL:" ve sonrası (değişken)
    return [
        {"type": "text", "text": static_prefix,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": variable_part},
    ]


def plan_uret(param: dict) -> str:
    """
    Claude API ile plan üret. API key yoksa fallback olarak deterministik markdown çıktı verir.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key or not HAS_ANTHROPIC:
        return _fallback_plan(param)

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=PLAN_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_cached_content(param)}],
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
    lines.append("**B planı (yoğun direnç):** " + bekleme["B_plan_direnç"])
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
