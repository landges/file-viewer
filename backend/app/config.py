from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, enable_decoding=False
    )

    app_name: str = "File Viewer"
    environment: str = "development"
    api_prefix: str = "/api"
    static_dir: Path = Path("/app/static")
    cache_dir: Path = Path("/var/cache/file-viewer")
    work_dir: Path = Path("/tmp/file-viewer")

    source_allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost"])
    source_allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])
    source_allowed_schemes: list[str] = Field(default_factory=lambda: ["http", "https"])
    source_connect_timeout: float = 10.0
    source_read_timeout: float = 120.0
    source_max_redirects: int = 5

    cache_ttl_seconds: int = 86_400
    failed_ttl_seconds: int = 3_600
    cleanup_interval_seconds: int = 900
    max_source_bytes: int = 512 * 1024 * 1024
    max_office_bytes: int = 200 * 1024 * 1024
    max_archive_bytes: int = 512 * 1024 * 1024
    max_extracted_bytes: int = 2 * 1024 * 1024 * 1024
    max_archive_entries: int = 10_000
    max_nesting_depth: int = 3
    max_text_bytes: int = 20 * 1024 * 1024
    max_sheet_rows: int = 5_000
    max_sheet_columns: int = 256
    max_concurrent_conversions: int = 2
    office_timeout_seconds: int = 180
    archive_timeout_seconds: int = 180

    @field_validator("source_allowed_hosts", "source_allowed_schemes", mode="before")
    @classmethod
    def split_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("source_allowed_ports", mode="before")
    @classmethod
    def split_ports(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        return value

    def prepare_directories(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
