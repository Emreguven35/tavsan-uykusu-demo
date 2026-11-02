"""Paylaşım kartları yanıt şemaları (bkz. api/services/paylasim.py)."""
from datetime import date

from pydantic import BaseModel


class PaylasimGun(BaseModel):
    tarih: date
    toplam_dk: int | None
    gece_dk: int | None
    gunduz_dk: int | None
    tam: bool


class PaylasimOncekiHafta(BaseModel):
    ort_uyku_dk_gun: int | None
    gece_uyanma: float | None
    en_uzun_uyku_dk: int | None


class PaylasimHaric(BaseModel):
    tarih: date
    baslangic: str
    bitis: str | None
    sure_dk: int | None
    sinif: str                          # gece | gunduz
    # acik | otomatik_kapandi | supheli_uzun | supheli_gec_bitis | sure_yok
    sebep: str


class HaftalikKartResp(BaseModel):
    paylasilabilir: bool
    yetersiz_veri_mesaji: str | None
    bebek_adi: str
    hafta_baslangic: date
    hafta_bitis: date
    egitim_gunu: int | None
    kayitli_gun_sayisi: int
    tam_gun_sayisi: int
    ort_uyku_dk_gun: int | None
    gunluk: list[PaylasimGun]
    gece_uyanma: float | None
    en_uzun_uyku_dk: int | None
    gunduz_uyku_ort_adet: float | None
    onceki_hafta: PaylasimOncekiHafta | None
    vurgu_metni: str | None
    haric_tutulan: list[PaylasimHaric]


class MezuniyetOnce(BaseModel):
    # egitim_oncesi | egitim_1_2_gun | beyan | null (karşılaştırma yok)
    kaynak: str | None
    gece_uyanma: float | None
    en_uzun_uyku_dk: int | None
    # gece_uyanma'nın nereden geldiği (en uzun pencereden, uyanma beyandan
    # gelebilir): egitim_oncesi | egitim_1_2_gun | beyan | null
    gece_uyanma_kaynak: str | None


class MezuniyetSimdi(BaseModel):
    kaynak: str | None                  # son_3_gun | null
    gece_uyanma: float | None
    en_uzun_uyku_dk: int | None


class MezuniyetKartResp(BaseModel):
    bebek_adi: str
    tamamlanma_tarihi: date
    once: MezuniyetOnce
    simdi: MezuniyetSimdi
