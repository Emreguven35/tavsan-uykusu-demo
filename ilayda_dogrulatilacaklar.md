# İlayda'ya Doğrulatılacaklar — 2026-06-09 Boşluk Raporu İşlemesi

Bu listedeki maddeler bilgi tabanına **eklenmedi** ya da **eksik kaldı**; İlayda'nın
onayı/ek kaydı gelince tamamlanacak.

## 1. Q80 — Beşikten bebek yatağına/yer yatağına ne zaman geçilir? (ŞÜPHELİ — eklenmedi)
- Transkriptte (K2) bu soru "**Beşinci yaşta**" diye geçti.
- Bu büyük olasılıkla yanlış duyum / dil sürçmesi (beşik→yatak geçişi genelde ~18 ay – 3 yaş aralığında konuşulur, 5 yaş değil).
- **Karar:** Doğru yaş netleşene kadar chunk üretilmedi. Soru `KISMEN` olarak bırakıldı.
- **İlayda'ya soru:** Beşikten yatağa geçiş için söylemek istediğiniz yaş aralığı tam olarak nedir?

## 2. Q100 — "Uykusuzluktan tükendim / kendimi kötü hissediyorum" (BEKLENİYOR)
- K2 kaydının sonunda açıkça belirtildi: *"Tıbbi sınırı yarın cevaplayacağım… sağlıksız ya da yanlış bir şey iletmiş olmak istemiyorum."*
- Bu nedenle lohusa depresyonu / anne ruh sağlığı **emniyet cümlesi bu turda eklenmedi**.
- Boşluk raporu Bölüm 4 önerisi: *"Bu hisler sürüyor ya da şiddetliyse mutlaka bir uzmana (doktor/psikolog) başvurun."*
- **Karar:** Q100 `KISMEN` (mevcut motivasyon içeriği var, emniyet cümlesi yok). İlayda'nın yarın göndereceği **tıbbi sınır kaydı** beklendiğinde işlenecek.

## 3. Q73 — Oda sıcaklığı / giydirme (TOG) tablosu (KISMEN — tablo bekleniyor)
- K2'de "22-23°C ideal" denildi **ama** giydirme/TOG için: *"ben oda sıcaklığı giydirme için bir tablo ileteyim"* dendi.
- Ayrıca: yeni kayıttaki **22-23°C**, mevcut `master_knowledge_base.json → oda_kosullari` değeriyle (**kış 19-22°C / yaz max 25°C**) birebir aynı değil.
- **Karar:** Çelişki yaratmamak için oda sıcaklığı chunk'ı üretilmedi, master KB değeri ezilmedi. Q73 `KISMEN`.
- **İlayda'ya soru:** (a) Giydirme/TOG tablosunu iletebilir misiniz? (b) İdeal oda sıcaklığı 19-22°C mi yoksa 22-23°C mi — hangisini resmi değer alalım?

## 4. Tıbbi sınır kaydı geldiğinde birlikte tamamlanacaklar
Yarınki tıbbi sınır kaydıyla şu sorular gözden geçirilecek:
- **Q66** — Diş çıkarırken ağrı kesici (tıbbi kısım bu turda gelmedi, `KISMEN`).
- **Q99** — Gece uyanma demir/magnezyum/D-vit eksikliği (nedenler zenginleşti ama tıbbi yönlendirme gereği `KISMEN` bırakıldı).
- **Q100** — yukarıda (madde 2).

---

### Bilgilendirme: uygulanan güvenlik kapıları (onaylandı)
- **Q86 reflü/gaz:** "eğitime engel değil" + **"ağlamalı eğitime başlamadan önce mutlaka çocuk doktorundan onay alın"** + anne odasında yatma detayı eklendi.
- **Q76 SIDS/yüzüstü:** "dönmeye başlayınca engellenemez" korundu + **"ilk 6 ayda sırtüstü yatırma; nefes/pozisyon endişesinde çocuk doktoruna danışın"** eklendi.
- **Q13 başlama yaşı:** "min 5 ay eğitim" kuralı KORUNDU; "4. ay = hazırlık/giriş, üst sınır ~6 yaş" katmanı eklendi.
