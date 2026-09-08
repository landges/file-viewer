from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


PreviewStatus = Literal["queued", "processing", "ready", "failed"]
Renderer = Literal[
    "proxy", "pdf", "image", "text", "spreadsheet", "archive", "email", "unsupported"
]


class OriginStep(BaseModel):
    container: Literal["archive", "email"]
    locator: str
    filename: str


class PreviewManifest(BaseModel):
    id: str
    source_url: str
    display_name: str
    status: PreviewStatus = "queued"
    renderer: Renderer = "unsupported"
    detected_type: str = "unknown"
    mime_type: str = "application/octet-stream"
    asset_name: str | None = None
    error: str | None = None
    source_etag: str | None = None
    source_last_modified: str | None = None
    source_size: int | None = None
    origin_chain: list[OriginStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    accessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PreviewCreateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    filename: str | None = Field(default=None, max_length=512)


class EmbeddedPreviewRequest(BaseModel):
    entry_id: str = Field(min_length=1, max_length=128)


class PreviewPublic(BaseModel):
    id: str
    display_name: str
    status: PreviewStatus
    renderer: Renderer
    detected_type: str
    mime_type: str
    error: str | None = None
    content_url: str | None = None
    data_url: str | None = None
    download_url: str | None = None
    depth: int = 0


class SourceProbe(BaseModel):
    final_url: str
    size: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    filename: str | None = None

