"""Voice (ses) şemaları — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class VoiceCloneResp(BaseModel):
    voiceId: str
    sampleUrl: str | None = None


class VoiceStatusResp(BaseModel):
    status: str                      # pending | ready | none
    voiceId: str | None = None
    sampleUrl: str | None = None
    created_at: datetime | None = None


class StoryItem(BaseModel):
    id: str
    type: str                        # masal | ninni
    title: str
    duration_hint: str | None = None


class StoriesResp(BaseModel):
    masallar: list[StoryItem]
    ninniler: list[StoryItem]


class VoiceGenerateReq(BaseModel):
    voiceId: str = Field(min_length=1)
    text: str | None = Field(default=None, max_length=4000)
    storyId: str | None = None

    @model_validator(mode="after")
    def _need_text_or_story(self):
        if not self.text and not self.storyId:
            raise ValueError("text veya storyId gereklidir")
        return self


class VoiceGenerateResp(BaseModel):
    audio_url: str
    cached: bool
