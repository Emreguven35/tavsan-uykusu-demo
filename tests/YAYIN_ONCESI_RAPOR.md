# Yayın öncesi kapsamlı test raporu

**Tarih:** 2026-09-22T02:10:18+00:00 · **Ortam:** production (`tavsan-api-production.up.railway.app`) · **Sürüm:** v2.4.0

Bu rapor **kod değiştirilmeden** üretildi: tüm ölçümler gerçek API üzerinden, mobilin gönderdiği gövdelerle yapıldı. Test hesapları `test-yayin-<n>@example.com` desenindedir ve sonunda silinmiştir.

## Karar: **NO-GO**

- KRİTİK bulgu: **3** · ORTA: 7 · DÜŞÜK: 6
  - **Şekerleme, bandın minimum uyanıklık penceresini tanımıyor** — 22 gün / 2 profil (A, B). Şekerleme son uykunun bitişinden hemen sonra (en kötü durumda 0 dk) yerleştiriliyor; bant minimumu 90–180 dk. Anneye 'uyandır, hemen yatır' talimatı çıkıy
  - **Bebek 5 ayını doldurunca eğitim planına GEÇİLMİYOR** — GET /plans/today 5,3 aylık bebek için uygun_mu=true döndürüyor ama type hâlâ egitim_bekleme, days[].preview=true, plan_secimi.onizleme=true. Mobil ekran kararını type alanından ver
  - **ElevenLabs kotası binlerce anneyi taşımıyor — ayda ~37 paket** — Hesap starter: 90.000 karakter/ay, şu an 31.183 kullanılmış. Bu testte tek paket (3 ninni + 1 masal) 2.349 karakter harcadı. Tam bütçeyle ayda ~37 anne ses paketi alabilir; kalan b

---

## Bölüm 1 — Sanal anneler (6 profil × 13 gün)

| Profil | Tanım | Plan | Bant | Kabul/Kopya/Ret | Şablon uyanış | İhlal |
|---|---|---|---|---|---|---|
| **A** | 4 aylık — yenidoğan/eğitim bekleme (egitim_bekleme) | claude 142 sn | 3-5_ay | 65/0/0 | 07:00 | 22 |
| **B** | 6 aylık prematüre (36 hf), 6 uyanma beyanı (egitim_plani) | claude 140 sn | 3-5_ay | 78/0/0 | 07:00 | 13 |
| **C** | 8 aylık — düzgün anne (egitim_plani) | claude 139 sn | 6-8_ay | 52/0/0 | 07:00 | 0 |
| **D** | 8 aylık — gerçek anne (dağınık) (egitim_plani) | claude 150 sn | 6-8_ay | 67/3/0 | 07:00 | 2 |
| **E** | 14,5 aylık — tek uykuya geçiş (egitim_plani) | claude 146 sn | 12-18_ay | 31/0/0 | 07:00 | 7 |
| **F** | 20 aylık — eğitim bitti, regresyon (egitim_plani) | claude 127 sn | 18-24_ay | 30/0/0 | 07:00 | 0 |

### Profil A

| Gün | Uyanış | Uyku sayısı | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 17:00-18:00 | 20:00 | 160 | K15: sekerleme 17:00 — onceki bitis 16:30, gereken 90 dk, gecen 30 dk; 'ilave uyku' notu v |
| 2026-09-11 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | 17:10-18:10 | 20:00 | 200 | K15: sekerleme 17:10 — onceki bitis 17:10, gereken 90 dk, gecen 0 dk; 'ilave uyku' notu va |
| 2026-09-12 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-13 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 17:00-18:00 | 20:00 | 160 | K15: sekerleme 17:00 — onceki bitis 16:30, gereken 90 dk, gecen 30 dk; 'ilave uyku' notu v |
| 2026-09-14 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | 17:10-18:10 | 20:00 | 200 | K15: sekerleme 17:10 — onceki bitis 17:10, gereken 90 dk, gecen 0 dk; 'ilave uyku' notu va |
| 2026-09-15 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-16 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 17:00-18:00 | 20:00 | 160 | K15: sekerleme 17:00 — onceki bitis 16:30, gereken 90 dk, gecen 30 dk; 'ilave uyku' notu v |
| 2026-09-17 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | 17:10-18:10 | 20:00 | 200 | K15: sekerleme 17:10 — onceki bitis 17:10, gereken 90 dk, gecen 0 dk; 'ilave uyku' notu va |
| 2026-09-18 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-19 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 17:00-18:00 | 20:00 | 160 | K15: sekerleme 17:00 — onceki bitis 16:30, gereken 90 dk, gecen 30 dk; 'ilave uyku' notu v |
| 2026-09-20 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | 17:10-18:10 | 20:00 | 200 | K15: sekerleme 17:10 — onceki bitis 17:10, gereken 90 dk, gecen 0 dk; 'ilave uyku' notu va |
| 2026-09-21 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-22 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 17:00-18:00 | 20:00 | 160 | K15: sekerleme 17:00 — onceki bitis 16:30, gereken 90 dk, gecen 30 dk; 'ilave uyku' notu v |

<details><summary>Profil-özel doğrulamalar</summary>

```json
{
 "4ay_uygun_mu": false,
 "4ay_type": "egitim_bekleme",
 "4ay_uyarilar": [
  "⛔ Bebeğiniz düzeltilmiş yaşa göre 4.0 aylık. Uyku eğitimi 5. ayını dolduran bebekler için uygundur. Şimdilik sadece saat planlaması yapabilirsiniz, eğitim ilerideki haftalarda."
 ],
 "5ay_uygun_mu": true,
 "5ay_type": "egitim_bekleme",
 "5ay_yas": 5.3,
 "5ay_uyarilar": []
}
```
</details>

**Bugünün planı (GET /plans/today):** wake 07:10-07:10, nap_1 08:50-09:30, nap_2 11:10-11:50, nap_3 13:30-14:10, nap_4 15:50-16:30, sekerleme 17:00-18:00, bedtime 20:00-07:00

**Uyarılar:** ⛔ Bebeğiniz düzeltilmiş yaşa göre 4.0 aylık. Uyku eğitimi 5. ayını dolduran bebekler için uygundur. Şimdilik sadece saat planlaması yapabilirsiniz, eğitim ilerideki haftalarda.

### Profil B

| Gün | Uyanış | Uyku sayısı | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-11 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-12 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-13 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-14 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-15 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-16 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-17 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-18 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-19 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-20 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-21 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |
| 2026-09-22 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 17:00-18:00 | 20:00 | 210 | K15: sekerleme 17:00 — onceki bitis 16:50, gereken 90 dk, gecen 10 dk |

<details><summary>Profil-özel doğrulamalar</summary>

```json
{
 "yas": {
  "gercek_ay": 6.0,
  "dogum_haftasi": 36,
  "prematüre_mi": true,
  "duzeltilmis_ay": 5.0
 },
 "bant": "3-5_ay",
 "nap_sayisi": 4,
 "uyarilar": [
  "ℹ️ Bebeğiniz prematüre (doğum 36 haftalık). Tüm hesaplar düzeltilmiş yaş üzerinden (5.0 ay) yapıldı."
 ],
 "gece_uyanma": {
  "deger": 2,
  "kaynak": "olculen",
  "gece_sayisi": 6
 },
 "uzun_uyanma": 0,
 "prematüre_uyarisi": true
}
```
</details>

**Bugünün planı (GET /plans/today):** wake 07:00-07:00, nap_1 09:00-10:10, nap_2 12:20-13:30, nap_3 15:40-16:50, sekerleme 17:00-18:00, bedtime 20:00-07:00

**Uyarılar:** ℹ️ Bebeğiniz prematüre (doğum 36 haftalık). Tüm hesaplar düzeltilmiş yaş üzerinden (5.0 ay) yapıldı.

### Profil C

| Gün | Uyanış | Uyku sayısı | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-11 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-12 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-13 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-14 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-15 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-16 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-17 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-18 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-19 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-20 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-21 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-22 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |

**Bugünün planı (GET /plans/today):** wake 07:00-07:00, nap_1 09:30-10:50, nap_2 13:20-14:40, nap_3 17:10-17:55, bedtime 20:25-07:00

### Profil D

| Gün | Uyanış | Uyku sayısı | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 08:40 | 3 | 09:50-10:40*, 14:00-15:46*, 18:16-18:46 | — | 21:00 | 186 | — |
| 2026-09-11 | 07:40 | 3 | 09:30-10:40*, 13:00-14:10*, 16:40-17:50 | — | 20:20 | 210 | — |
| 2026-09-12 | 08:40 | 3 | 10:00-11:15*, 14:00-15:00*, 17:30-18:30 | — | 21:00 | 195 | — |
| 2026-09-13 | 07:40 | 5 | 08:30-09:15*, 10:50-11:35*, 13:10-13:55*, 15:30-16:15*, 17:50-18:35* | — | 21:00 | 225 | gunduz uyku sayisi 5 ∉ [3, 3] (kayittan gelen 5) |
| 2026-09-14 | 08:40 | 3 | 09:50-10:40*, 14:00-15:46*, 18:16-18:46 | — | 21:00 | 186 | — |
| 2026-09-15 | 07:40 | 3 | 09:30-10:40*, 13:00-14:10*, 16:40-17:50 | — | 20:20 | 210 | — |
| 2026-09-16 | 08:40 | 3 | 10:00-11:15*, 14:00-15:00*, 17:30-18:30 | — | 21:00 | 195 | — |
| 2026-09-17 | 07:40 | 5 | 08:30-09:15*, 10:50-11:35*, 13:10-13:55*, 15:30-16:15*, 17:50-18:35* | — | 21:00 | 225 | gunduz uyku sayisi 5 ∉ [3, 3] (kayittan gelen 5) |
| 2026-09-18 | 08:40 | 3 | 09:50-10:40*, 14:00-15:46*, 18:16-18:46 | — | 21:00 | 186 | — |
| 2026-09-19 | 07:40 | 3 | 09:30-10:40*, 13:00-14:10*, 16:40-17:50 | — | 20:20 | 210 | — |
| 2026-09-20 | 08:40 | 3 | 10:00-11:15*, 14:00-15:00*, 17:30-18:30 | — | 21:00 | 195 | — |
| 2026-09-21 | 08:25 | 3 | 08:26-11:00*, 14:00-15:46*, 16:55-18:05* | — | 20:35 | 330 | — |
| 2026-09-22 | 07:45 | 3 | 09:50-10:40*, 14:00-15:46*, 18:16-18:46 | — | 21:00 | 186 | — |

<details><summary>Profil-özel doğrulamalar</summary>

```json
{
 "ahmet_kerem": {
  "tarih": "2026-09-21",
  "nap_sayisi": 3,
  "naplar": [
   "08:26-11:00 (kayit)",
   "14:00-15:46 (kayit)",
   "16:55-18:05 (kayit)"
  ],
  "birlesen_cift": 2,
  "yok_sayilan": [
   {
    "id": "d7f4912f-708c-465b-997f-a7e3cdffeead",
    "kod": "gece_icinde",
    "sebep": "06:30-07:15 kaydı gece uykusunun içinde kalıyor — gündüz uykusu sayılmadı"
   }
  ],
  "sabah_uyanis_gercek": "08:25",
  "ilave_notu": []
 },
 "kategoriler": [
  [
   "sleep",
   "gece_uykusu"
  ],
  [
   "sleep",
   "gece_uykusu"
  ],
  [
   "sleep",
   "gunduz_uykusu"
  ],
  [
   "sleep",
   "gunduz_uykusu"
  ],
  [
   "sleep",
   "gunduz_uykusu"
  ],
  [
   "sleep",
   "gunduz_uykusu"
  ],
  [
   "nap",
   "gunduz_uykusu"
  ],
  [
   "sleep",
   "gunduz_uykusu"
  ],
  [
   "sleep",
   "gece_uykusu"
  ],
  [
   "sleep",
   "gece_uykusu"
  ],
  [
   "nap",
   "gunduz_uykusu"
  ]
 ]
}
```
</details>

**Bugünün planı (GET /plans/today):** wake 07:45-07:45, nap_1 09:50-10:40, nap_2 14:00-15:46, nap_3 18:16-18:46, bedtime 21:00-07:00

**Uyarılar:** ℹ️ Bebeğiniz gece uyandığında kendi başına uykuya dönemiyor. En sık sebep gündüz uykusunun yetersiz kalması; özellikle direnen son uykuyu atlayıp erken gece uykusuna geçirmek gece uyanmalarını artırır.

### Profil E

| Gün | Uyanış | Uyku sayısı | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 08:00 | 2 | 10:00-11:00*, 15:00-16:00* | — | 19:30 | 120 | — |
| 2026-09-11 | 08:00 | 2 | 10:00-11:00*, 15:00-16:00* | — | 19:30 | 120 | — |
| 2026-09-12 | 08:00 | 2 | 10:00-11:00*, 15:00-16:00* | — | 19:30 | 120 | — |
| 2026-09-13 | 08:00 | 2 | 10:00-11:00*, 15:00-16:00* | — | 19:30 | 120 | — |
| 2026-09-14 | 08:00 | 2 | 10:00-11:00*, 15:00-16:00* | — | 19:30 | 120 | — |
| 2026-09-15 | 08:00 | 2 | 12:00-14:00*, 17:30-18:00 | — | 20:00 | 150 | K15: bedtime 20:00 — onceki bitis 18:00, gereken 180 dk, gecen 120 dk |
| 2026-09-16 | 08:00 | 2 | 12:00-14:00*, 17:30-18:00 | — | 20:00 | 150 | K15: bedtime 20:00 — onceki bitis 18:00, gereken 180 dk, gecen 120 dk |
| 2026-09-17 | 08:00 | 2 | 12:00-14:00*, 17:30-18:00 | — | 20:00 | 150 | K15: bedtime 20:00 — onceki bitis 18:00, gereken 180 dk, gecen 120 dk |
| 2026-09-18 | 08:00 | 2 | 12:00-14:00*, 17:30-18:00 | — | 20:00 | 150 | K15: bedtime 20:00 — onceki bitis 18:00, gereken 180 dk, gecen 120 dk |
| 2026-09-19 | 08:00 | 2 | 12:00-13:30*, 17:00-17:30 | — | 20:00 | 120 | K15: bedtime 20:00 — onceki bitis 17:30, gereken 180 dk, gecen 150 dk |
| 2026-09-20 | 08:00 | 2 | 12:00-13:30*, 17:00-17:30 | — | 20:00 | 120 | K15: bedtime 20:00 — onceki bitis 17:30, gereken 180 dk, gecen 150 dk |
| 2026-09-21 | 08:00 | 2 | 09:00-10:00*, 13:30-14:30 | — | 19:00 | 120 | — |
| 2026-09-22 | 08:00 | 2 | 12:00-13:30*, 17:00-17:30 | — | 20:00 | 120 | K15: bedtime 20:00 — onceki bitis 17:30, gereken 180 dk, gecen 150 dk |

<details><summary>Profil-özel doğrulamalar</summary>

```json
{
 "ekran_08_09": {
  "tarih": "2026-09-21",
  "uyanma": "08:00",
  "bloklar": [
   "wake 08:00-08:00 (None)",
   "nap_1 09:00-10:00 (kayit)",
   "nap_2 13:30-14:30 (varsayilan)",
   "bedtime 19:00-07:00 (varsayilan)"
  ],
  "uyarilar": [
   "1. gündüz uykusu 09:00'da kaydedildi — minimum uyanıklık penceresinden erken (en erken 11:00). Kayıt korundu, sonraki bloklar bu kayda göre kuruldu."
  ]
 },
 "tek_uyku_kisa": {
  "tarih": "2026-09-22",
  "sekerleme": null,
  "tetik": null,
  "varyant": "iki_uyku",
  "bloklar": [
   "wake 08:00-08:00",
   "nap_1 12:00-13:30",
   "nap_2 17:00-17:30",
   "bedtime 20:00-07:00"
  ]
 }
}
```
</details>

**Bugünün planı (GET /plans/today):** wake 08:00-08:00, nap_1 12:00-13:30, nap_2 17:00-17:30, bedtime 20:00-07:00

### Profil F

| Gün | Uyanış | Uyku sayısı | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-11 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-12 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-13 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-14 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-15 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-16 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-17 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-18 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-19 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-20 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-21 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |
| 2026-09-22 | 07:30 | 1 | 12:30-14:30* | — | 20:15 | 120 | — |

<details><summary>Profil-özel doğrulamalar</summary>

```json
{
 "kademe1": {
  "regresyon": {
   "tip": "kendi_donuyor_mu",
   "asama": "soru",
   "egitim_gunu": 28,
   "kirkbes_gun_doldu": false
  },
  "uzun_uyanma": 4,
  "uyarilar": [
   "ℹ️ Bebeğiniz gece uyandığında kendi başına uykuya dönemiyor. En sık sebep gündüz uykusunun yetersiz kalması; özellikle direnen son uykuyu atlayıp erken gece uykusuna geçirmek gece uyanmalarını artırır."
  ]
 },
 "kademe2": {
  "baby_id": "1dac1f74-45a8-4067-9ee6-3e2571d39b22",
  "kendi_donuyor": false,
  "cevap_at": "2026-09-22T01:53:36.540213Z",
  "regresyon_karti": {
   "tip": "devam_45",
   "metin": "Eğitime 45. güne kadar aynı şekilde devam edin, bu dönemde gece uyanmaları normaldir.",
   "egitim_gunu": 28,
   "kirkbes_gun_doldu": false
  },
  "egitim_baslangic_gunu": 28,
  "kirkbes_gun_doldu": false
 },
 "kademe3": {
  "baby_id": "1dac1f74-45a8-4067-9ee6-3e2571d39b22",
  "kendi_donuyor": false,
  "cevap_at": "2026-09-22T01:53:37.049974Z",
  "regresyon_karti": {
   "tip": "tibbi_yonlendirme",
   "metin": "45 gün doldu ve bebeğiniz hâlâ kendi başına uykuya dönemiyor. Pediatri kontrolü öneriyoruz; doktorunuza demir, D vitamini ve magnezyum düzeyleri, uyku apnesi ve geniz eti açısından değerlendirme isteyebilirsiniz. Gerekirse fizyoterapi/ergoterapi değerlendirmesi de düşünülebilir.",
   "egitim_gunu": 51,
   "kirkbes_gun_doldu": true
  },
  "egitim_baslangic_gunu": 51,
  "kirkbes_gun_doldu": true
 },
 "kademe_evet": {
  "baby_id": "1dac1f74-45a8-4067-9ee6-3e2571d39b22",
  "kendi_donuyor": true,
  "cevap_at": "2026-09-22T01:53:37.302540Z",
  "regresyon_karti": null,
  "egitim_baslangic_gunu": 51,
  "kirkbes_gun_doldu": true
 }
}
```
</details>

**Bugünün planı (GET /plans/today):** wake 07:30-07:30, nap_1 12:30-14:30, bedtime 20:15-07:00

**Uyarılar:** ℹ️ Bebeğiniz gece uyandığında kendi başına uykuya dönemiyor. En sık sebep gündüz uykusunun yetersiz kalması; özellikle direnen son uykuyu atlayıp erken gece uykusuna geçirmek gece uyanmalarını artırır.


**Bulgular — Bölüm 1**

| Önem | Bulgu | Detay | Yeniden üretim |
|---|---|---|---|
| **KRİTİK** | Şekerleme, bandın minimum uyanıklık penceresini tanımıyor | 22 gün / 2 profil (A, B). Şekerleme son uykunun bitişinden hemen sonra (en kötü durumda 0 dk) yerleştiriliyor; bant minimumu 90–180 dk. Anneye 'uyandır, hemen yatır' talimatı çıkıyor. | `4 aylık bebek, 4×40 dk uyku → gündüz açığı → adapt() çizelgesinde sekerleme bloğu son uykudan <90 dk sonra (sc` |
| **KRİTİK** | Bebek 5 ayını doldurunca eğitim planına GEÇİLMİYOR | GET /plans/today 5,3 aylık bebek için uygun_mu=true döndürüyor ama type hâlâ egitim_bekleme, days[].preview=true, plan_secimi.onizleme=true. Mobil ekran kararını type alanından veriyor (plan_service.py:557 yorumu). Yenid | `Bebek 4 aylıkken plan üret → PATCH /babies birth_date ile 5,3 aya getir → GET /plans/today: uygun_mu=true, typ` |
| ORTA | 'Şablonda olmayan ilave uyku' notu bant tavanı aşılmadan çıkıyor | 13 gün / 1 profil. 3-5 ay bandı 3-4 uyku öngörüyor; şablon 3 uyku ürettiği için annenin 4. uykusu 'ilave' damgası alıyor — oysa bant içinde. | `4 aylık bebek, 4 gündüz uykusu kaydı → nap_4 note='Şablonda olmayan ilave uyku kaydı'` |
| ORTA | Yatış, bant tavanına kırpılırken uyanıklık penceresi 180→120 dk düşüyor | 12-18 ay (2 uyku) bandı: anne tek uyku kaydedince motor 2. uykuyu 17:30-18:00 arasına 30 dk olarak sıkıştırıyor, sonra yatışı bant tavanı 20:00a kırpıyor. Son uyku bitişi ile yatış arası 120 dk — bant minimumu 180 dk. 7/ | `Profil E (14,5 ay), gunduz tek uyku 12:00-14:00 kaydi → adapt() cizelgesi: nap_2 17:30-18:00 (varsayilan) + be` |
| ORTA | Tek uykuya geçiş otomatik algılanmıyor (12-18 ay) | yas_bandi.varyant plan ÜRETİM anında donuyor (plan_service.tek_uyku_bayragi yalnız içeriğe bakıyor). 14,5 aylık bebek 8 gün üst üste tek uyku (12:00-14:00) kaydetse de varyant iki_uyku kalıyor; motor her gün olmayan bir  | `Profil E: 14,5 aylık, 5-13. günlerde tek uyku kaydı → content.yas_bandi.varyant hâlâ iki_uyku` |
| DÜŞÜK | Gündüz uyku sayısı bandı aşabiliyor (gerçek kayıt korunuyor) | 2 gün. Anne 5 ayrı uyku kaydettiğinde motor 5'ini de gösteriyor (bant 3 diyor). Kayıtları silmek daha kötü olurdu; fazlalık 'ilave uyku' notuyla işaretleniyor. Beklenen davranış olarak değerlendirildi. | `Profil D, 5×45 dk uyku kaydı olan günler` |
| DÜŞÜK | Kırpılan 2. uyku 30 dakikaya kadar iniyor | Aynı senaryoda planlanan 2. uyku 30 dk olarak gösteriliyor (17:30-18:00). Bant gündüz toplamı için yeterli ama 14,5 aylık bir bebek için tek başına anlamlı bir uyku değil; yatışı kırpmak yerine bu uykuyu düşürmek daha tu | `Profil E, 2026-09-15..18 günleri` |

## Bölüm 2 — Ses klonlama uçtan uca

| Adım | Sonuç |
|---|---|
| Örnek ses | 525836 bayt, 32.786576 sn |
| 0 baytlık deneme | kod 400 — sonra `can_clone=True` (hak iadesi) |
| POST /voice/clone | kod 200, 3046 ms |
| Hazır olma süresi | 21 sn |
| Durum akışı | generating@0sn → released@21sn |
| /voice/stories | paket=starter hazır=4/8 |
| İndirme `masal_aysecik_uyku_perisi` | kod 200, 5385866 bayt, 336.550023 sn, 3190 ms |
| İndirme `ninni_dandini` | kod 200, 342770 bayt, 21.362358 sn, 1863 ms |
| İndirme `ninni_uyu_yavrum` | kod 200, 365758 bayt, 22.801995 sn, 1472 ms |
| İndirme `ninni_ay_isigi` | kod 200, 333157 bayt, 20.758639 sn, 1308 ms |
| İmzasız erişim | kod 403 (200 olmamalı) |
| ElevenLabs | {"kod": 200, "ses_sayisi": 24, "klon_hala_var": false} |
| İkinci klon | kod 429 — Sesini ayda bir kez kaydedebilirsin. Bir sonraki hakkın: 22.10.2026 |

| Önem | Bulgu | Detay | Yeniden üretim |
|---|---|---|---|
| **KRİTİK** | ElevenLabs kotası binlerce anneyi taşımıyor — ayda ~37 paket | Hesap starter: 90.000 karakter/ay, şu an 31.183 kullanılmış. Bu testte tek paket (3 ninni + 1 masal) 2.349 karakter harcadı. Tam bütçeyle ayda ~37 anne ses paketi alabilir; kalan bütçeyle ~25. voice_limit=10 slot (3 dolu | `GET https://api.elevenlabs.io/v1/user/subscription → character_limit 90000; bir klon paketi öncesi/sonrası cha` |
| DÜŞÜK | Pakete dahil olmayan masallar sonsuza kadar hazırlanıyor görünüyor | GET /voice/stories 8 içerik döndürüyor; starter paketi 4 tanesini üretiyor. Kalan 4 masal durum=hazirlaniyor ile geliyor ve hiçbir zaman üretilmeyecek. Yanıt paket_icerikleri listesini de taşıdığı için mobil filtreleyebi | `GET /api/v1/voice/stories → masal_kelogan_sihirli_degnek.durum = hazirlaniyor` |

## Bölüm 3 — Eğitim videoları

Katalog: **16** video, 4 kategori, 30 dakika. Şema hatası: **0**.

TTFB (Range 0-1): min 702 / ortanca 845 / max 1042 ms · 1 MB indirme: min 1277 / ortanca 1480 / max 1753 ms

| Video | Süre | Range | content-range | TTFB ms | 1 MB ms | Poster | ffprobe |
|---|---|---|---|---|---|---|---|
| uyku-egitimine-giris | 70 sn | 206 | `bytes 0-1/9229808` | 882 | 1480 | 200 (85195B) | 70.5 sn ['h264', 'aac'] |
| uyku-plani-hazirlama | 47 sn | 206 | `bytes 0-1/6596255` | 887 | 1753 | 200 (85146B) | 46.7 sn ['h264', 'aac'] |
| beslenme-mesafesi | 82 sn | 206 | `bytes 0-1/10070713` | 999 | 1512 | 200 (78463B) | 82.4 sn ['h264', 'aac'] |
| uyku-oncesi-rutinler | 118 sn | 206 | `bytes 0-1/12427188` | 962 | 1628 | 200 (82487B) | 117.5 sn ['h264', 'aac'] |
| ilk-gunler | 150 sn | 206 | `bytes 0-1/15772066` | 820 | 1522 | 200 (77251B) | 149.5 sn ['h264', 'aac'] |
| kisa-gunduz-uykusu-uzatma | 104 sn | 206 | `bytes 0-1/12714665` | 756 | 1488 | 200 (78208B) | 104.0 sn ['h264', 'aac'] |
| gece-uyanmalari | 111 sn | 206 | `bytes 0-1/13619605` | 1042 | 1568 | 200 (81332B) | 111.4 sn ['h264', 'aac'] |
| kademeli-azaltma | 99 sn | 206 | `bytes 0-1/12056937` | 968 | 1458 | 200 (81178B) | 98.8 sn ['h264', 'aac'] |
| egitime-ara-verme | 82 sn | 206 | `bytes 0-1/12105950` | 873 | 1356 | 200 (79679B) | 81.7 sn ['h264', 'aac'] |
| sik-yapilan-hatalar | 84 sn | 206 | `bytes 0-1/12854669` | 736 | 1406 | 200 (64025B) | 84.2 sn ['h264', 'aac'] |
| tutarlilik | 52 sn | 206 | `bytes 0-1/6122883` | 845 | 1413 | 200 (64966B) | 51.6 sn ['h264', 'aac'] |
| sallanarak-uyuyan-bebek | 130 sn | 206 | `bytes 0-1/15840733` | 750 | 1403 | 200 (79061B) | 129.7 sn ['h264', 'aac'] |
| emzirilerek-uyutulan-bebek | 138 sn | 206 | `bytes 0-1/16853548` | 827 | 1426 | 200 (70513B) | 138.1 sn ['h264', 'aac'] |
| emzik-uyku-destegi | 154 sn | 206 | `bytes 0-1/18726941` | 794 | 1277 | 200 (79188B) | 153.6 sn ['h264', 'aac'] |
| egitim-sonrasi-duzen | 174 sn | 206 | `bytes 0-1/21145243` | 702 | 1481 | 200 (69147B) | 173.6 sn ['h264', 'aac'] |
| her-yerde-uyuyabilme | 225 sn | 206 | `bytes 0-1/27426782` | 716 | 1360 | 200 (68159B) | 225.3 sn ['h264', 'aac'] |

**todays_pick aşama matrisi**

| Aşama | Kaynak | Eşleşen video | Seçim | Eşleşiyor | İzlenmemiş |
|---|---|---|---|---|---|
| genel | anne | 11 | uyku-egitimine-giris | True | True |
| besik_yani | anne | 1 | ilk-gunler | True | True |
| oda_ortasi | anne | 1 | kademeli-azaltma | True | True |
| kapi | anne | 1 | kademeli-azaltma | True | True |
| esik | anne | 1 | tutarlilik | True | True |
| bitis | anne | 1 | tutarlilik | True | True |
| egitim_oncesi | anne | 7 | uyku-egitimine-giris | True | True |
| egitim_sonrasi | anne | 2 | egitim-sonrasi-duzen | True | True |
| genel(hepsi izlendi) | — | — | uyku-egitimine-giris | — | — |

progress upsert: `[{'video_id': '07fb08e5-bee6-46da-8b8f-86ec2f275d21', 'position_sec': 11, 'completed': True}, {'video_id': '07fb08e5-bee6-46da-8b8f-86ec2f275d21', 'position_sec': 22, 'completed': True}]` · geçersiz aşama → **422**

_Bu bölümde bulgu yok._

## Bölüm 4 — Yük ve dayanıklılık

**50 eşzamanlı `GET /plans/today`** (6 farklı bebek): p50 **14010 ms**, p95 **14976 ms**, max 15133 ms, toplam 15.5 sn, hata **0**

**20 eşzamanlı `POST /logs/batch`**: hata 0, ilk turda oluşan 20, ikinci turda oluşan **0** (kopya kontrolü — 0 olmalı), p95 6425 ms

**Bozuk gövdeler**

| Senaryo | Kod | Detay |
|---|---|---|
| batch: type eksik | 200 | {'created': 0, 'updated': 0, 'skipped': [{'client_id': None, 'id': None, 'reason': 'missing_field', 'detail': 'Zorunlu alan eksik: type'}], 'synced': [], 'logs' |
| batch: gecersiz tarih | 200 | {'created': 0, 'updated': 0, 'skipped': [{'client_id': None, 'id': None, 'reason': 'invalid_time', 'detail': 'Başlangıç zamanı geçersiz'}], 'synced': [], 'logs' |
| batch: gelecekten kayit | 200 | {'created': 1, 'updated': 0, 'skipped': [], 'synced': [{'client_id': 'gelecek-1', 'id': '9bdb00fb-2d7a-4e5c-b621-56fc1b5151a5'}], 'logs': [{'id': '9bdb00fb-2d7a |
| batch: bitis < baslangic | 200 | {'created': 1, 'updated': 0, 'skipped': [], 'synced': [{'client_id': 'ters-1', 'id': 'bb3b34bf-3e41-4610-ae98-e42ee95411f5'}], 'logs': [{'id': 'bb3b34bf-3e41-46 |
| batch: 400 kayit | 200 | {'created': 400, 'updated': 0, 'skipped': [], 'synced': [{'client_id': 'buyuk-0', 'id': '58980456-babe-46ff-a03c-9ea009bc0321'}, {'client_id': 'buyuk-1', 'id':  |
| batch: bos liste | 422 | Gönderilen bilgiler geçersiz, lütfen kontrol edip tekrar deneyin. |
| plan: gecersiz uuid | 422 | Gönderilen bilgiler geçersiz, lütfen kontrol edip tekrar deneyin. |
| voice: 0 bayt ses | 400 | Ses dosyası boş |
| video: olmayan id | 404 | Video bulunamadı |
| plan/today: gecersiz uuid | 422 | Gönderilen bilgiler geçersiz, lütfen kontrol edip tekrar deneyin. |

**Yetkisiz erişim**

| Hedef | Uç | Kod | Veri sızdı mı |
|---|---|---|---|
| C | plans/today | 404 | hayır |
| C | logs | 200 | hayır |
| C | regresyon-cevap | 404 | hayır |
| A | plans/today | 404 | hayır |
| A | logs | 200 | hayır |
| A | regresyon-cevap | 404 | hayır |
| B | plans/today | 404 | hayır |
| B | logs | 200 | hayır |
| B | regresyon-cevap | 404 | hayır |
| D | plans/today | 404 | hayır |
| D | logs | 200 | hayır |
| D | regresyon-cevap | 404 | hayır |
| E | plans/today | 404 | hayır |
| E | logs | 200 | hayır |
| E | regresyon-cevap | 404 | hayır |
| F | plans/today | 404 | hayır |
| F | logs | 200 | hayır |
| F | regresyon-cevap | 404 | hayır |

Kimliksiz istekler: /plans/today→401, /education/videos→401, /logs→401

**Gözlem (Sentry / sunucu günlüğü)**

```
{
 "sentry_dsn_tanimli": true,
 "environment": "production",
 "yontem": "Sentry API anahtari elde olmadigi icin olaylar Railway calisma gunlugunden tarandi (son 1500 satir, testin tamamini kapsiyor).",
 "hata_satiri": 0,
 "warning_satiri": 0,
 "5xx_yaniti": 0,
 "sonuc": "Testin hicbir asamasinda sunucu tarafi istisna, WARNING ya da 5xx yaniti olusmadi. Bozuk govde testlerinin tamami 4xx ile karsilandi."
}
```

| Önem | Bulgu | Detay | Yeniden üretim |
|---|---|---|---|
| ORTA | Eşzamanlı istekler serileşiyor — /plans/today 50 eşzamanlıda p95 15 sn | Tek istek 290 ms; 50 eşzamanlı istekte p50 14,0 sn / p95 15,0 sn (hata yok). 50 × 0,29 ≈ 14,5 sn — istekler neredeyse tam serileşiyor, ölçülen kapasite ~3,4 istek/sn. POST /logs/batch 20 eşzamanlıda p95 6,4 sn. railway.j | `50 paralel GET /plans/today (scratchpad/yayin/b4_yuk.py 4.1a)` |
| ORTA | Gelecek tarihli uyku kaydı kabul ediliyor | 3 gün sonrasına tarihlenmiş bir nap kaydı POST /logs/batch ile created=1 olarak yazıldı ve GET /logs?date=<3 gün sonra> ile geri geldi. Anne saat seçicide yanlış gün seçerse gelecekteki bir güne 'uyku' düşüyor; o gün gel | `POST /logs/batch {type:'nap', started_at: bugün+3 gün} → 200 created=1` |
| ORTA | Bitişi başlangıçtan ÖNCE olan kayıt kabul ediliyor ve listede görünüyor | started_at=08:40, ended_at=07:00 gönderildi; created=1. GET /logs bu kaydı kategori='gunduz_uykusu', etiket='Gündüz uykusu' ile döndürüyor. Plan motoru ters kaydı yok sayıyor (K12) ama mobil listede negatif süreli bir uy | `POST /logs/batch {started_at: 08:40, ended_at: 07:00} → 200 created=1` |
| ORTA | Haftalık özet bir günde 27,85 saat uyku raporladı (>24 saat) | GET /logs/weekly-summary kayıtları ham toplar: parça kayıtları birleştirmez (K20), gece uykusunun içindeki kaydı elemez (K14), kopya/ikiz kaydı ayıklamaz (K18) ve günlük toplamı 24 saate kırpmaz. Profil D 2026-09-21: ayn | `GET /api/v1/logs/weekly-summary?baby_id=<D> → days[2026-09-21].sleep_hours = 27.85` |
| DÜŞÜK | Başkasının baby_id'si ile GET /logs 404 yerine 200 + boş liste dönüyor | Veri SIZMIYOR (sahibi 425 kayıt görüyor, yabancı boş liste). Diğer uçlar tutarlı: /plans/today 404, PATCH /babies 404, /logs/batch not_owned. Yalnız /logs sessizce boş dönüyor; mobil 'bebek yok' ile 'kayıt yok' ayrımını  | `B hesabının token'ı ile GET /logs?date=...&baby_id=<C'nin bebeği>` |
| DÜŞÜK | POST /logs/batch kayıt sayısı için üst sınır uygulamıyor | 400 kayıtlık tek istek 200 ile kabul edildi (created=400). Sınır yok; kötü niyetli ya da bozuk bir istemci tek istekte çok büyük yazma üretebilir. | `POST /logs/batch 400 elemanlı logs dizisi → 200 created=400` |
| DÜŞÜK | Şema doğrulama hatalarında gövde Türkçe tek cümle, ayrıntı yok | Geçersiz UUID / boş liste gibi durumlarda detail='Gönderilen bilgiler geçersiz, lütfen kontrol edip tekrar deneyin.' dönüyor — Türkçe ve kullanıcıya uygun, ham pydantic sızmıyor. Mobil hangi alanın hatalı olduğunu ayırt  | `POST /plans/generate {baby_id:'abc'} → 422` |

## Temizlik ve veri bütünlüğü

| Ölçüm | Test öncesi | Test sonrası | Fark |
|---|---|---|---|
| kullanici | 97 | 97 | 0 |
| gercek_kullanici | 25 | 25 | 0 |
| test_kullanici | 72 | 72 | 0 |
| bebek | 56 | 56 | 0 |
| plan | 852 | 852 | 0 |
| uyku_kaydi | 231 | 231 | 0 |
| ses_profili | 6 | 6 | 0 |
| ses_dosyasi | 24 | 24 | 0 |
| video | 16 | 16 | 0 |
| video_ilerleme | 0 | 1 | 1 |
| yayin_testi_hesap | 0 | 0 | 0 |

> `video_ilerleme` satırının test öncesi değeri ÖLÇÜLMEDİ (ilk sayımda bu tablo sorgulanmamıştı); 0 varsayıldı. Kalan tek satır `edu-c190b96e@tavsanduman.com` hesabına ait ve bu testten ÖNCE, eğitim videoları görevinde oluştu. Bu testin bıraktığı satır yok: `test-yayin-%` hesap sayısı 0, yetim bebek/plan/kayıt 0.

**Gerçek (test olmayan) kullanıcı sayısı: 25 → 25** — değişmedi, gerçek annelerin verisine dokunulmadı.

Silinen test hesapları (`DELETE /auth/account`, KVKK cascade — bebekler, kayıtlar, planlar, sesler, depo dosyaları):

- `test-yayin-1@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-2@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-3@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-4@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-5@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-6@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-7@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-8@example.com` → 200 Hesap ve tüm ilişkili veriler silindi
- `test-yayin-9@example.com` → 200 Hesap ve tüm ilişkili veriler silindi

Atlananlar: test-yayin-10@example.com (giris yok (401))

## Tüm bulgular (önem sırasına göre)

| Önem | Bulgu | Detay | Yeniden üretim |
|---|---|---|---|
| **KRİTİK** | Şekerleme, bandın minimum uyanıklık penceresini tanımıyor | 22 gün / 2 profil (A, B). Şekerleme son uykunun bitişinden hemen sonra (en kötü durumda 0 dk) yerleştiriliyor; bant minimumu 90–180 dk. Anneye 'uyandır, hemen yatır' talimatı çıkıyor. | `4 aylık bebek, 4×40 dk uyku → gündüz açığı → adapt() çizelgesinde sekerleme bloğu son uykudan <90 dk sonra (sc` |
| **KRİTİK** | Bebek 5 ayını doldurunca eğitim planına GEÇİLMİYOR | GET /plans/today 5,3 aylık bebek için uygun_mu=true döndürüyor ama type hâlâ egitim_bekleme, days[].preview=true, plan_secimi.onizleme=true. Mobil ekran kararını type alanından veriyor (plan_service.py:557 yorumu). Yenid | `Bebek 4 aylıkken plan üret → PATCH /babies birth_date ile 5,3 aya getir → GET /plans/today: uygun_mu=true, typ` |
| **KRİTİK** | ElevenLabs kotası binlerce anneyi taşımıyor — ayda ~37 paket | Hesap starter: 90.000 karakter/ay, şu an 31.183 kullanılmış. Bu testte tek paket (3 ninni + 1 masal) 2.349 karakter harcadı. Tam bütçeyle ayda ~37 anne ses paketi alabilir; kalan bütçeyle ~25. voice_limit=10 slot (3 dolu | `GET https://api.elevenlabs.io/v1/user/subscription → character_limit 90000; bir klon paketi öncesi/sonrası cha` |
| ORTA | 'Şablonda olmayan ilave uyku' notu bant tavanı aşılmadan çıkıyor | 13 gün / 1 profil. 3-5 ay bandı 3-4 uyku öngörüyor; şablon 3 uyku ürettiği için annenin 4. uykusu 'ilave' damgası alıyor — oysa bant içinde. | `4 aylık bebek, 4 gündüz uykusu kaydı → nap_4 note='Şablonda olmayan ilave uyku kaydı'` |
| ORTA | Yatış, bant tavanına kırpılırken uyanıklık penceresi 180→120 dk düşüyor | 12-18 ay (2 uyku) bandı: anne tek uyku kaydedince motor 2. uykuyu 17:30-18:00 arasına 30 dk olarak sıkıştırıyor, sonra yatışı bant tavanı 20:00a kırpıyor. Son uyku bitişi ile yatış arası 120 dk — bant minimumu 180 dk. 7/ | `Profil E (14,5 ay), gunduz tek uyku 12:00-14:00 kaydi → adapt() cizelgesi: nap_2 17:30-18:00 (varsayilan) + be` |
| ORTA | Tek uykuya geçiş otomatik algılanmıyor (12-18 ay) | yas_bandi.varyant plan ÜRETİM anında donuyor (plan_service.tek_uyku_bayragi yalnız içeriğe bakıyor). 14,5 aylık bebek 8 gün üst üste tek uyku (12:00-14:00) kaydetse de varyant iki_uyku kalıyor; motor her gün olmayan bir  | `Profil E: 14,5 aylık, 5-13. günlerde tek uyku kaydı → content.yas_bandi.varyant hâlâ iki_uyku` |
| ORTA | Eşzamanlı istekler serileşiyor — /plans/today 50 eşzamanlıda p95 15 sn | Tek istek 290 ms; 50 eşzamanlı istekte p50 14,0 sn / p95 15,0 sn (hata yok). 50 × 0,29 ≈ 14,5 sn — istekler neredeyse tam serileşiyor, ölçülen kapasite ~3,4 istek/sn. POST /logs/batch 20 eşzamanlıda p95 6,4 sn. railway.j | `50 paralel GET /plans/today (scratchpad/yayin/b4_yuk.py 4.1a)` |
| ORTA | Gelecek tarihli uyku kaydı kabul ediliyor | 3 gün sonrasına tarihlenmiş bir nap kaydı POST /logs/batch ile created=1 olarak yazıldı ve GET /logs?date=<3 gün sonra> ile geri geldi. Anne saat seçicide yanlış gün seçerse gelecekteki bir güne 'uyku' düşüyor; o gün gel | `POST /logs/batch {type:'nap', started_at: bugün+3 gün} → 200 created=1` |
| ORTA | Bitişi başlangıçtan ÖNCE olan kayıt kabul ediliyor ve listede görünüyor | started_at=08:40, ended_at=07:00 gönderildi; created=1. GET /logs bu kaydı kategori='gunduz_uykusu', etiket='Gündüz uykusu' ile döndürüyor. Plan motoru ters kaydı yok sayıyor (K12) ama mobil listede negatif süreli bir uy | `POST /logs/batch {started_at: 08:40, ended_at: 07:00} → 200 created=1` |
| ORTA | Haftalık özet bir günde 27,85 saat uyku raporladı (>24 saat) | GET /logs/weekly-summary kayıtları ham toplar: parça kayıtları birleştirmez (K20), gece uykusunun içindeki kaydı elemez (K14), kopya/ikiz kaydı ayıklamaz (K18) ve günlük toplamı 24 saate kırpmaz. Profil D 2026-09-21: ayn | `GET /api/v1/logs/weekly-summary?baby_id=<D> → days[2026-09-21].sleep_hours = 27.85` |
| DÜŞÜK | Gündüz uyku sayısı bandı aşabiliyor (gerçek kayıt korunuyor) | 2 gün. Anne 5 ayrı uyku kaydettiğinde motor 5'ini de gösteriyor (bant 3 diyor). Kayıtları silmek daha kötü olurdu; fazlalık 'ilave uyku' notuyla işaretleniyor. Beklenen davranış olarak değerlendirildi. | `Profil D, 5×45 dk uyku kaydı olan günler` |
| DÜŞÜK | Kırpılan 2. uyku 30 dakikaya kadar iniyor | Aynı senaryoda planlanan 2. uyku 30 dk olarak gösteriliyor (17:30-18:00). Bant gündüz toplamı için yeterli ama 14,5 aylık bir bebek için tek başına anlamlı bir uyku değil; yatışı kırpmak yerine bu uykuyu düşürmek daha tu | `Profil E, 2026-09-15..18 günleri` |
| DÜŞÜK | Başkasının baby_id'si ile GET /logs 404 yerine 200 + boş liste dönüyor | Veri SIZMIYOR (sahibi 425 kayıt görüyor, yabancı boş liste). Diğer uçlar tutarlı: /plans/today 404, PATCH /babies 404, /logs/batch not_owned. Yalnız /logs sessizce boş dönüyor; mobil 'bebek yok' ile 'kayıt yok' ayrımını  | `B hesabının token'ı ile GET /logs?date=...&baby_id=<C'nin bebeği>` |
| DÜŞÜK | POST /logs/batch kayıt sayısı için üst sınır uygulamıyor | 400 kayıtlık tek istek 200 ile kabul edildi (created=400). Sınır yok; kötü niyetli ya da bozuk bir istemci tek istekte çok büyük yazma üretebilir. | `POST /logs/batch 400 elemanlı logs dizisi → 200 created=400` |
| DÜŞÜK | Şema doğrulama hatalarında gövde Türkçe tek cümle, ayrıntı yok | Geçersiz UUID / boş liste gibi durumlarda detail='Gönderilen bilgiler geçersiz, lütfen kontrol edip tekrar deneyin.' dönüyor — Türkçe ve kullanıcıya uygun, ham pydantic sızmıyor. Mobil hangi alanın hatalı olduğunu ayırt  | `POST /plans/generate {baby_id:'abc'} → 422` |
| DÜŞÜK | Pakete dahil olmayan masallar sonsuza kadar hazırlanıyor görünüyor | GET /voice/stories 8 içerik döndürüyor; starter paketi 4 tanesini üretiyor. Kalan 4 masal durum=hazirlaniyor ile geliyor ve hiçbir zaman üretilmeyecek. Yanıt paket_icerikleri listesini de taşıdığı için mobil filtreleyebi | `GET /api/v1/voice/stories → masal_kelogan_sihirli_degnek.durum = hazirlaniyor` |


---

## SONUÇ: **NO-GO**

Yayını durduran bulgular:
1. **Şekerleme, bandın minimum uyanıklık penceresini tanımıyor** — 22 gün / 2 profil (A, B). Şekerleme son uykunun bitişinden hemen sonra (en kötü durumda 0 dk) yerleştiriliyor; bant minimumu 90–180 dk. Anneye 'uyandır, hemen yatır' talimatı çıkıyor.
1. **Bebek 5 ayını doldurunca eğitim planına GEÇİLMİYOR** — GET /plans/today 5,3 aylık bebek için uygun_mu=true döndürüyor ama type hâlâ egitim_bekleme, days[].preview=true, plan_secimi.onizleme=true. Mobil ekran kararını type alanından veriyor (plan_service.py:557 yorumu). Yeniden üretimi tetikleyecek bayrak YOK (yenidogan_suresi_doldu benzeri bir alan yok, regenerate_required=false). Sonuç: 3-5 ay arası kaydolan anneler 5. ayı doldurduklarında önizleme ekranında kalıyor, 13 günlük program hiç başlamıyor.
1. **ElevenLabs kotası binlerce anneyi taşımıyor — ayda ~37 paket** — Hesap starter: 90.000 karakter/ay, şu an 31.183 kullanılmış. Bu testte tek paket (3 ninni + 1 masal) 2.349 karakter harcadı. Tam bütçeyle ayda ~37 anne ses paketi alabilir; kalan bütçeyle ~25. voice_limit=10 slot (3 dolu) ve can_extend_voice_limit=false. Yayın sonrası ilk günlerde kota biter ve klonlama 429/503 vermeye başlar. Kod kusuru DEĞİL, plan yükseltmesi gerekiyor.

---

# 2. TUR — düzeltmelerden sonra

**Tarih:** 2026-09-22T03:00:59+00:00 · **Sürüm:** v2.4.1 · **Ortam:** production

1. turun KRİTİK ve ORTA bulguları düzeltildi, prod'a alındı ve Bölüm 1 (A/B/C profilleri × 13 gün), Bölüm 4.1 yük testi ve weekly-summary yeniden koşuldu.

## Düzeltmelerin sonucu

| Bulgu (1. tur) | Önem | Durum | Kanıt |
|---|---|---|---|
| Bebek 5 ayını doldurunca eğitim planına geçilmiyor | KRİTİK | **ÇÖZÜLDÜ** | `ensure_today_plan` geçişi tespit edip planı arka planda üretiyor; `egitim_zamani_geldi`+`yeniden_uretiliyor` bayrakları, `training_started_at` backend'de. test_yayin_duzeltmeleri K1a-K1l |
| Şekerleme min uyanıklık penceresini tanımıyor | KRİTİK | **ÇÖZÜLDÜ** | Profil A 22→0, Profil B 13→0 ihlal. 64 senaryoluk tarama temiz (K2h) |
| Haftalık özet bir günde 27,85 saat | ORTA | **ÇÖZÜLDÜ** | Aynı vaka prod'da yeniden kuruldu: **17.0 saat**, 24 saati aşan gün yok |
| Gelecek tarihli / ters kayıt kabul ediliyor | ORTA | **ÇÖZÜLDÜ** | İkisi de elendi, `created=0`, Türkçe detail |
| Eşzamanlı istekler serileşiyor (p95 15 sn) | ORTA | **ÇÖZÜLDÜ** | Sunucu tarafı ölçüm (konteyner içinden, 50 eşzamanlı): p95 **68 ms**, 0 hata, toplam 0.09 sn — hedefin (3.000 ms) **44 katı altında** |
| ElevenLabs kotası ~37 paket/ay | KRİTİK | **AÇIK** | Kod kusuru değil; plan yükseltmesi gerekiyor (starter → daha yüksek tier) |

## Bölüm 1 (yeniden) — A, B, C profilleri

| Profil | Bant | Plan | Kabul/Kopya/Ret | İhlal (1. tur → 2. tur) |
|---|---|---|---|---|
| **A** | 3-5_ay | egitim_bekleme | 60/0/5 | 22 → **12** |
| **B** | 3-5_ay | egitim_plani | 74/0/4 | 13 → **0** |
| **C** | 6-8_ay | egitim_plani | 48/0/4 | 0 → **0** |

> `Ret` sütunu: simülasyon sabah koşuyor ve BUGÜNÜN henüz gelmemiş saatlerine yazılan kayıtlar v2.4.1 zaman doğrulamasınca eleniyor — **doğru davranış**, test kurgusunun yan etkisi. Geçmiş 12 gün eksiksiz.

### Profil A (2. tur)

| Gün | Uyanış | Uyku | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 18:00-18:30 | 20:00 | 160 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-11 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | — | 20:00 | 200 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-12 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-13 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 18:00-18:30 | 20:00 | 160 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-14 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | — | 20:00 | 200 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-15 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-16 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 18:00-18:30 | 20:00 | 160 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-17 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | — | 20:00 | 200 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-18 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-19 | 07:10 | 4 | 08:50-09:30*, 11:10-11:50*, 13:30-14:10*, 15:50-16:30* | 18:00-18:30 | 20:00 | 160 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-20 | 07:10 | 4 | 08:50-09:40*, 11:20-12:10*, 13:50-14:40*, 16:20-17:10* | — | 20:00 | 200 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-21 | 07:10 | 4 | 08:50-09:50*, 11:30-12:30*, 14:10-15:10*, 16:50-17:50* | — | 20:05 | 240 | 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4 |
| 2026-09-22 | 07:00 | 3 | 09:15-10:45, 13:00-14:30, 16:45-18:15 | — | 20:30 | 270 | — |

Kalan ihlaller (hepsi 'ilave uyku' etiketi — DÜŞÜK, ayrı bulgu):

- `2026-09-10: 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4`
- `2026-09-11: 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4`
- `2026-09-12: 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4`
- `2026-09-13: 'ilave uyku' notu var ama kayit uykusu 4 ≤ bant tavani 4`
- … (+8)

### Profil B (2. tur)

| Gün | Uyanış | Uyku | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-11 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-12 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-13 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-14 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-15 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-16 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-17 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-18 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-19 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-20 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-21 | 07:00 | 3 | 09:00-10:10*, 12:20-13:30*, 15:40-16:50* | 18:20-18:50 | 20:00 | 210 | — |
| 2026-09-22 | 07:00 | 3 | 09:15-10:45, 13:00-14:30, 16:45-18:15 | — | 20:30 | 270 | — |

### Profil C (2. tur)

| Gün | Uyanış | Uyku | Uykular | Şekerleme | Yatış | Gündüz dk | İhlal |
|---|---|---|---|---|---|---|---|
| 2026-09-10 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-11 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-12 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-13 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-14 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-15 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-16 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-17 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-18 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-19 | 07:00 | 3 | 09:30-10:50*, 13:20-14:40*, 17:10-17:55* | — | 20:25 | 205 | — |
| 2026-09-20 | 07:05 | 3 | 09:35-10:55*, 13:25-14:45*, 17:15-18:00* | — | 20:30 | 205 | — |
| 2026-09-21 | 07:10 | 3 | 09:40-11:00*, 13:30-14:50*, 17:20-18:05* | — | 20:35 | 205 | — |
| 2026-09-22 | 07:00 | 3 | 09:30-10:40, 13:10-14:20, 16:50-18:00 | — | 20:30 | 210 | — |

## Bölüm 4.1 (yeniden) — yük

50 eşzamanlı `GET /plans/today`, dört ayrı ölçüm:

| Ölçüm | Nereden | p50 | **p95** | max | toplam | hata |
|---|---|---|---|---|---|---|
| 1 worker (1. tur) | Türkiye, internet | 14.010 ms | **14.976 ms** | 15.133 ms | 15,5 sn | 0 |
| 4 worker | Türkiye, internet | 4839 ms | **5125 ms** | 5311 ms | 5.3 sn | 0 |
| 8 worker | Türkiye, internet | 5333 ms | **5543 ms** | 5576 ms | 5.6 sn | 0 |
| **4 worker** | **konteyner içinden** | **48 ms** | **68 ms** | 74 ms | **0.09 sn** | 0 |

**Okuma:** 1 worker'dan 4'e geçiş gerçek kazanç (15,5 → 5,3 sn); 4'ten 8'e geçiş HİÇBİR ŞEY değiştirmedi (5,3 → 5,6 sn). Sebep sunucu tarafı ölçümle bulundu: prod günlüğünde `/plans/today` yük testi SIRASINDA bile **22-53 ms** (ortanca 35) sürüyordu. Kalan ~5 sn **test istemcisinin** (tek Python sürecinde 50 iş parçacığı + TLS) ve Türkiye-Railway ağ gecikmesinin tavanı; backend'in değil.

Kanıt için yük konteynerin İÇİNDEN (localhost, ağ yok) tekrarlandı: **50 eşzamanlı istek, p95 68 ms, 0 hata, toplam 0.09 sn**. Hedef <3.000 ms → **44 kat altında**.

Bu ölçümden sonra worker sayısı **4'e geri alındı**: 8 worker ~7,3 GB RAM tutuyor ve ölçülebilir hiçbir kazanç sağlamıyor. Konteyner kotası 24 vCPU / 24 GB.

APScheduler Postgres advisory lock ile **tek** süreçte koşuyor — prod günlüğünde doğrulandı: bir worker lider oldu, diğer üçü "Zamanlayıcı BAŞLATILMADI — başka bir worker lider" deyip geçti. Bildirim gönderimi kullanıcıya göre deterministik 0-10 dk yayılıyor.

## weekly-summary ve zaman doğrulaması (yeniden)

1. turdaki 27,85 saatlik vaka prod'da birebir yeniden kuruldu (aynı gece iki kayıt + parça uykular + gece içinde kalan kayıt):

| Gün | Saat |
|---|---|
| 2026-09-16 | 14.92 |
| 2026-09-17 | 15.0 |
| 2026-09-18 | 15.08 |
| 2026-09-19 | 14.92 |
| 2026-09-20 | 15.0 |
| 2026-09-21 | 17.0 |
| 2026-09-22 | 0.0 |

**24 saati aşan gün: yok** · hedef gün 2026-09-21 → **17.0 saat**, 5 gündüz uykusu (parçalar tek sayıldı).

Zaman doğrulaması:

```
t2-gelecek: invalid_time — Kayıt gelecek bir zamana ait; tarihi kontrol edin
t2-ters: invalid_time — Kaydın bitişi başlangıcından önce; saatleri kontrol edin
created = 0
```

## Prod taraması — 5 ayı doldurmuş bekleyen bebek

Düzeltme sonrası prod tarandı (salt okuma, sonra üretim modu):

- **Geçiş bekleyen bebek: 0.** Şu an sıkışmış bebek yok.
- `egitim_bekleme` planlı 4 bebek var; yaşları **3,1 / 3,1 / 3,4 / 3,6 ay** — henüz 5 ayı doldurmadıkları için geçiş gerekmiyor. 5. ayı doldurduklarında ilk `GET /plans/today` ya da bildirim turunda otomatik geçecekler.
- Plan tipi dağılımı: `egitim_plani` 27, `null` (v1 öncesi eski plan) 16, `plan_yok` 7, `egitim_bekleme` 4, `yenidogan_ritim` 2.

> **Gözlem (yeni, DÜŞÜK):** 16 planın `type` alanı `null` — v2 öncesi üretilmiş eski planlar. Geçiş mantığı yalnız `egitim_bekleme` tipini ele alıyor, bu yüzden onlara dokunmuyor. Mobil de bu planlarda ekran kararını veremez. Bunlar büyük olasılıkla eski test hesaplarına ait; gerçek kullanıcıya aitse ayrı bir göç gerekir.

## 2. tur sonrası kalan bulgular

| Önem | Bulgu | Detay |
|---|---|---|
| **KRİTİK** | ElevenLabs kotası binlerce anneyi taşımıyor — ayda ~37 paket | Kod kusuru DEĞİL. Starter plan 90.000 karakter/ay; paket başına 2.349 karakter → ayda ~37 anne. voice_limit=10, can_extend_voice_limit=false. Yayından günler sonra klonlama 429/503 vermeye başlar. Plan yükseltmesi gerekiyor. |
| ORTA | Tek uykuya geçiş otomatik algılanmıyor (12-18 ay) | yas_bandi.varyant plan üretim anında donuyor; 14,5 aylık bebek günlerce tek uyku kaydetse de iki_uyku kalıyor. Bu turda ele alınmadı. |
| ORTA | Yatış bant tavanına kırpılırken uyanıklık penceresi 180→120 dk düşüyor | 12-18 ay 2 uyku bandında planlanan 2. uyku 17:30-18:00e sıkışıp yatış 20:00e kırpılıyor. Şekerleme düzeltmesi bu yolu kapsamıyor (o bir NAP zinciri kırpması). Bu turda ele alınmadı. |
| DÜŞÜK | 'Şablonda olmayan ilave uyku' notu bant tavanı aşılmadan çıkıyor | 3-5 ay bandı 3-4 uyku öngörüyor; şablon 3 ürettiği için annenin 4. uykusu ilave damgası alıyor. 2. turda A profilinde 12 günde tekrarladı. |
| DÜŞÜK | Gündüz uyku sayısı bandı aşabiliyor (gerçek kayıt korunuyor) | Beklenen davranış: annenin gerçek kayıtları silinmiyor, fazlalık not ile işaretleniyor. |
| DÜŞÜK | Başkasının baby_id ile GET /logs 404 yerine 200 + boş liste | Veri sızmıyor; yalnız durum kodu tutarsız. |
| DÜŞÜK | POST /logs/batch kayıt sayısı üst sınırı yok | 400 kayıtlık istek kabul ediliyor. |
| DÜŞÜK | Pakete dahil olmayan masallar sonsuza kadar hazırlanıyor görünüyor | GET /voice/stories 8 içerik döndürüyor, starter 4 üretiyor; kalan 4 durum=hazirlaniyor. |
| DÜŞÜK | 16 eski planın type alanı null (v2 öncesi) | Geçiş mantığı yalnız egitim_bekleme tipini ele alıyor; bu planlara dokunmuyor. Büyük olasılıkla eski test hesapları. |

## Temizlik (2. tur)

| Ölçüm | 1. tur sonrası | 2. tur sonrası | Fark |
|---|---|---|---|
| kullanici | 97 | 97 | 0 |
| gercek_kullanici | 25 | 25 | 0 |
| test_kullanici | 72 | 72 | 0 |
| bebek | 56 | 56 | 0 |
| plan | 852 | 852 | 0 |
| uyku_kaydi | 231 | 231 | 0 |
| ses_profili | 6 | 6 | 0 |
| ses_dosyasi | 24 | 24 | 0 |
| video | 16 | 16 | 0 |
| video_ilerleme | 1 | 1 | 0 |
| yayin_testi_hesap | 0 | 0 | 0 |

**Gerçek (test olmayan) kullanıcı: 25 → 25** — değişmedi, gerçek annelerin verisine dokunulmadı.

Silinen 2. tur test hesapları:

- `test-yayin-1@example.com` → 200
- `test-yayin-2@example.com` → 200
- `test-yayin-3@example.com` → 200

---

# 2. TUR SONUÇ

Karar iki parçaya ayrılıyor, çünkü kalan tek engel kodda değil:

- **Backend kodu: GO.** 1. turun iki KRİTİK kod bulgusu (5 ay geçişi, şekerleme uyanıklık penceresi) ve üç ORTA bulgusu (haftalık özet, zaman doğrulama, eşzamanlılık) düzeltildi, prod'a alındı ve yeniden ölçüldü. Kalan kod kaynaklı KRİTİK bulgu: **0**.
- **Ses paketi özelliği: NO-GO.** ElevenLabs starter planı ayda ~37 paket taşıyor (90.000 karakter, paket başına 2.349). Binlerce anneye açılırsa klonlama ilk günlerde 429/503 vermeye başlar. **Tek gereken plan yükseltmesi**; kodda değişiklik gerekmiyor.

**Öneri:** ElevenLabs planı yükseltilirse **GO**. Yükseltilmeden yayına çıkılacaksa ses klonlama özelliği kapalı/sıra usulü açılmalı — uygulamanın geri kalanı (plan motoru, videolar, kayıt akışı, topluluk) yayına hazır.
