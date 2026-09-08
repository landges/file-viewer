from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

from .config import Settings


class SourceUrlPolicy:
    def __init__(self, settings: Settings):
        self.settings = settings

    def validate(self, raw_url: str) -> str:
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректный URL источника") from exc

        if parsed.scheme.lower() not in self.settings.source_allowed_schemes:
            raise HTTPException(status_code=400, detail="Протокол источника не разрешён")
        if not parsed.hostname:
            raise HTTPException(status_code=400, detail="В URL отсутствует имя сервера")
        if parsed.username or parsed.password:
            raise HTTPException(status_code=400, detail="Учётные данные в URL запрещены")

        effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        if effective_port not in self.settings.source_allowed_ports:
            raise HTTPException(status_code=400, detail="Порт источника не разрешён")

        hostname = parsed.hostname.rstrip(".").lower()
        if not any(fnmatch.fnmatch(hostname, pattern.lower()) for pattern in self.settings.source_allowed_hosts):
            raise HTTPException(status_code=403, detail="Сервер источника не входит в разрешённый список")

        return raw_url


def safe_display_name(value: str | None, fallback: str = "file") -> str:
    if not value:
        return fallback
    value = unquote(value).replace("\\", "/")
    name = PurePosixPath(value).name.strip().replace("\x00", "")
    return name[:255] or fallback


def safe_archive_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return bool(normalized) and not path.is_absolute() and ".." not in path.parts

