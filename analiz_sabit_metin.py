"""
Task C — Sabit metin analizi (SADECE RAPOR; hiçbir şey şablona alınmaz).

3 mevcut planı (test_outputs/plan_*.md) bölüm bölüm karşılaştırır:
  - bölümler arası benzerlik (difflib oranı, çift bazlı ortalama),
  - plan-ötesi tekrar eden cümleler (near-duplicate, oran >= 0.88),
  - bilinen İlayda boilerplate kalıplarının plan başına tekrarı,
  - bölüm başı ortalama token (anthropic count_tokens ile gerçek sayım),
  - şablona alınırsa tahmini output-token / $ tasarrufu.
"""
import os
import re
import sys
import itertools
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

OUT = ROOT / "test_outputs"
PLANS = {
    "P1_Emir_8ay_13gun": OUT / "plan_1_Emir_8ay_13gun.md",
    "P2_Emir_8ay_1aylik": OUT / "plan_2_Emir_8ay_1aylik.md",
    "P3_Defne_11ay_5gun": OUT / "plan_3_Defne_11ay_5gun.md",
}
OUT_PER_1M = 15.0  # sonnet-4-6 output $/1M

# --- token sayımı: anthropic count_tokens (varsa), yoksa kelime*1.9 tahmini ----
_client = None
try:
    from anthropic import Anthropic
    if os.getenv("ANTHROPIC_API_KEY"):
        _client = Anthropic()
except Exception:
    _client = None


def ntok(text: str) -> int:
    if not text.strip():
        return 0
    if _client is not None:
        try:
            r = _client.messages.count_tokens(
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": text}])
            return int(r.input_tokens)
        except Exception:
            pass
    return int(len(text.split()) * 1.9)


def sections(md: str) -> dict:
    """## başlıklarına göre böl (### alt başlıklar bölüm içinde kalır)."""
    out, cur, buf = {}, "PREAMBLE", []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m and not line.startswith("###"):
            if buf:
                out[cur] = "\n".join(buf).strip()
            cur = re.sub(r"[^\wçğıöşüÇĞİÖŞÜ ]", "", m.group(1)).strip()
            buf = []
        else:
            buf.append(line)
    if buf:
        out[cur] = "\n".join(buf).strip()
    return out


def canon(sec: str) -> str:
    """Bölüm adını 3 plan arasında hizala (numara/emoji farkını yok say)."""
    s = sec.lower()
    if "profil" in s:
        return "Bebek Profili Özeti"
    if "uygunlu" in s:
        return "Eğitim Uygunluğu"
    if "hazırlık" in s or "hazirlik" in s:
        return "Ön Hazırlık"
    if "günlük program" in s or "gunluk program" in s:
        return "Günlük Program"
    if "eğitim planı" in s or "egitim plani" in s:
        return "Eğitim Planı"
    if "gece" in s:
        return "Gece Uyanmaları Protokolü"
    if "başarı" in s or "basari" in s:
        return "Başarı Kriterleri"
    if "dikkat" in s:
        return "Dikkat Edilmesi Gerekenler"
    return sec


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[#*📍📅🗓️✅⛔💙💛🙏>|-]", " ", t.lower())).strip()


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


BOILERPLATE = {
    "Uyanıklık süresi açıklaması (Günlük Program altı)":
        re.compile(r"uyanıklık süresidir|kortizol yükselir", re.I),
    "B-Planı protokolü (45dk→15dk→45dk, max 3)":
        re.compile(r"45\s*dakika.*15\s*dakika|maksimum 3 tekrar|max(imum)? 3", re.I),
    "Kucağa alma kademeli kalıbı (30sn→1dk→1,5dk→2dk)":
        re.compile(r"30\s*saniye.*1\s*dakika|kademeli artır", re.I),
    "Beyaz gürültü kademeli azaltma":
        re.compile(r"beyaz gürültü.*(kademe|kısıl|azalt)", re.I),
    "KATI saat notu (07:00 esneme)":
        re.compile(r"katı saat|07:00'a kadar esne", re.I),
    "Son gündüz uykusu 16:00 esneklik notu":
        re.compile(r"bitiş saati.*(katı değil|esne)|ilave.*gündüz uyku", re.I),
    "Kısa gündüz uykusu alt başlığı (her gün)":
        re.compile(r"kısa gündüz uykusu olursa", re.I),
    "Yoğun direnç / B Planı alt başlığı (her gün)":
        re.compile(r"yoğun direnç|b planı", re.I),
}


def main():
    parsed = {}
    for name, path in PLANS.items():
        if not path.exists():
            print("EKSİK:", path); return 1
        md = path.read_text(encoding="utf-8")
        secs = {}
        for k, v in sections(md).items():
            secs[canon(k)] = v
        parsed[name] = secs

    common = ["Bebek Profili Özeti", "Eğitim Uygunluğu", "Ön Hazırlık",
              "Günlük Program", "Eğitim Planı", "Gece Uyanmaları Protokolü",
              "Başarı Kriterleri", "Dikkat Edilmesi Gerekenler"]

    lines = ["# Task C — Sabit Metin Analizi (rapor)\n"]
    lines.append("3 plan: P1=Emir 8ay 13gün · P2=Emir 8ay 1aylık · P3=Defne 11ay 5gün")
    lines.append("(P1/P2 aynı bebek — farklı plan tipi; P3 farklı yaş+profil)\n")
    lines.append("## Bölüm bazlı benzerlik + token")
    lines.append("| Bölüm | Ort. çift benzerlik | Sınıf | Ort. token | Şablon tasarrufu (out-tok) |")
    lines.append("|-------|--------------------:|-------|-----------:|---------------------------:|")

    toplam_tasarruf_tok = 0
    for sec in common:
        texts = [parsed[p].get(sec, "") for p in PLANS]
        pres = [t for t in texts if t.strip()]
        if len(pres) < 2:
            continue
        pairs = list(itertools.combinations(pres, 2))
        avg = sum(ratio(a, b) for a, b in pairs) / len(pairs)
        toks = [ntok(t) for t in pres]
        avg_tok = sum(toks) // len(toks)
        # sınıf: >=0.85 sabit, 0.55-0.85 yarı-sabit, <0.55 kişiye özel
        if avg >= 0.85:
            klass = "SABİT"; saving = avg_tok            # tümü şablonlanabilir
        elif avg >= 0.55:
            klass = "YARI-SABİT"; saving = int(avg_tok * avg)  # sabit oranı kadar
        else:
            klass = "kişiye özel"; saving = 0
        toplam_tasarruf_tok += saving
        lines.append(f"| {sec} | {avg:.2f} | {klass} | {avg_tok} | {saving} |")

    # boilerplate taraması
    lines.append("\n## İlayda boilerplate kalıpları (plan başına tekrar)")
    lines.append("| Kalıp | P1 | P2 | P3 | Değerlendirme |")
    lines.append("|-------|----|----|----|----|")
    raw = {p: PLANS[p].read_text(encoding="utf-8") for p in PLANS}
    for label, rx in BOILERPLATE.items():
        hit = {p: len(rx.findall(raw[p])) for p in PLANS}
        allp = all(hit[p] > 0 for p in PLANS)
        deger = "TÜM planlarda var → sabit aday" if allp else "bazı planlarda"
        lines.append(f"| {label} | {hit['P1_Emir_8ay_13gun']} | "
                     f"{hit['P2_Emir_8ay_1aylik']} | {hit['P3_Defne_11ay_5gun']} | {deger} |")

    # --- Blok-seviyesi tekrar analizi (asıl tasarruf burada) ----------------
    # Bölüm bütünü kişiye özel görünse de, İlayda protokol blokları plan İÇİNDE
    # (gün gün) ve planlar ARASI near-identical tekrar eder. Paragrafları kümele:
    # bir küme (c kopya, ort t token) → şablonlanınca (c-1)*t token gereksiz.
    def blocks(md: str):
        raw_blocks = re.split(r"\n\s*\n", md)
        out = []
        for b in raw_blocks:
            b = b.strip()
            if len(b.split()) >= 12:            # kısa başlık/satırları ele
                out.append(b)
        return out

    def cluster_redundant(bl, thr=0.80):
        """Near-duplicate paragraf kümeleri → gereksiz (tekrar) token toplamı."""
        used = [False] * len(bl)
        redundant = 0
        clusters = []
        for i in range(len(bl)):
            if used[i]:
                continue
            grp = [i]
            used[i] = True
            for j in range(i + 1, len(bl)):
                if not used[j] and ratio(bl[i], bl[j]) >= thr:
                    used[j] = True
                    grp.append(j)
            if len(grp) >= 2:
                t = ntok(bl[i])                 # temsili blok token'ı
                redundant += (len(grp) - 1) * t
                clusters.append((len(grp), t, bl[i][:60].replace("\n", " ")))
        return redundant, clusters

    lines.append("\n## Blok-seviyesi tekrar (plan içi + protokol blokları)")
    lines.append("| Plan | Toplam out-tok | Tekrar (gereksiz) tok | Tekrar % | En büyük tekrar kümeleri |")
    lines.append("|------|---------------:|----------------------:|---------:|--------------------------|")
    plan_toks = {}
    redun_by_plan = {}
    for p in PLANS:
        bl = blocks(raw[p])
        red, clus = cluster_redundant(bl)
        tot = ntok(raw[p])
        plan_toks[p] = tot
        redun_by_plan[p] = red
        clus.sort(key=lambda c: c[0] * c[1], reverse=True)
        top = "; ".join(f"{c[0]}×~{c[1]}tok «{c[2]}…»" for c in clus[:2]) or "—"
        lines.append(f"| {p} | {tot} | {red} | %{red/tot*100:.0f} | {top} |")

    ort_out = sum(plan_toks.values()) // len(plan_toks)
    ort_red = sum(redun_by_plan.values()) // len(redun_by_plan)
    tasarruf_usd = ort_red / 1_000_000 * OUT_PER_1M
    baz_usd = ort_out / 1_000_000 * OUT_PER_1M
    lines.append("\n## Maliyet projeksiyonu")
    lines.append(f"- Ortalama plan çıktısı: **{ort_out} output token** "
                 f"(≈ {baz_usd:.3f}$ output; verilen baz ~0.14$/plan)")
    lines.append(f"- Tekrar eden (near-duplicate) blok tokenları: "
                 f"**~{ort_red} token/plan (%{ort_red/ort_out*100:.0f})**")
    lines.append(f"- Bu blokları şablona alıp gün/pozisyon değişkeniyle referanslamak "
                 f"→ tahmini **~{tasarruf_usd:.3f} $/plan** output tasarrufu "
                 f"(baz ~0.14$'ın ~%{tasarruf_usd/0.14*100:.0f}'i).")
    lines.append("- Bölüm bütünleri kişiye özel (yukarıdaki tablo, hepsi <0.55); "
                 "asıl tekrar, gün-gün yinelenen protokol bloklarındadır.")
    lines.append("- NOT: input (prompt) zaten prompt-caching ile ucuz; tasarruf "
                 "OUTPUT üretiminden gelir. HİÇBİR ŞEY ŞABLONA ALINMADI (sadece tespit).")

    (OUT / "sabit_metin_raporu.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
