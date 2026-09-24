"""Auth istek/yanıt şemaları — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --- İstekler ----------------------------------------------------------------
class KayitOnayi(BaseModel):
    tur: str = Field(pattern="^(aydinlatma|acik_riza_saglik|pazarlama)$")
    onay: bool
    metin_surumu: str | None = Field(default=None, max_length=40)


class RegisterReq(BaseModel):
    email: EmailStr
    # bcrypt ilk 72 byte'ı kullanır; makul üst sınır + min güvenlik alt sınırı.
    password: str = Field(min_length=8, max_length=128)
    # B5 — KVKK onayları. ŞİMDİLİK İSTEĞE BAĞLI: gelirse kaydedilir, gelmezse
    # kayıt yine olur. Mobil açık rızayı zorunlu yapınca burada zorlanacak.
    consents: list[KayitOnayi] | None = Field(default=None, max_length=10)


class LoginReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshReq(BaseModel):
    refresh_token: str


class ResetPasswordRequestReq(BaseModel):
    email: EmailStr


class ResetPasswordReq(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# --- Yanıtlar ----------------------------------------------------------------
class UserResp(BaseModel):
    id: uuid.UUID
    email: EmailStr
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    """register + login + refresh (rotasyon) yanıtı."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int              # access token saniye cinsinden ömrü


class MessageResp(BaseModel):
    detail: str


class ResetPasswordRequestResp(BaseModel):
    """reset-password-request yanıtı. Faz 5R: reset_token alanı KALDIRILDI — token
    HTTP yanıtında dönmez (e-postasını bilen herkes hesabı ele geçirebiliyordu).

    E-posta gönderimi Faz 6'da (Resend) bağlanana kadar token yalnızca DB'de
    (hash'li) durur; kullanıcıya ulaşacak bir kanal yoktur → mobilde "şifremi
    unuttum" akışı "yakında" olarak işaretlenir."""
    detail: str
