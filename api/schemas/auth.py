"""Auth istek/yanıt şemaları — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --- İstekler ----------------------------------------------------------------
class RegisterReq(BaseModel):
    email: EmailStr
    # bcrypt ilk 72 byte'ı kullanır; makul üst sınır + min güvenlik alt sınırı.
    password: str = Field(min_length=8, max_length=128)


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
