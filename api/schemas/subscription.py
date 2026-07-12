"""Subscription (IAP) şemaları — receipt doğrulama (şimdilik kaydet + active)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionVerifyReq(BaseModel):
    platform: str = Field(pattern="^(ios|android)$")
    product_id: str = Field(min_length=1, max_length=120)
    receipt_data: str = Field(min_length=1)


class SubscriptionResp(BaseModel):
    id: uuid.UUID
    platform: str
    product_id: str
    status: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
