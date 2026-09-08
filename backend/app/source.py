from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import unquote, urljoin, urlsplit

import aiofiles
import httpx
from fastapi import HTTPException

from .config import Settings
from .models import SourceProbe
from .security import SourceUrlPolicy, safe_display_name


class SourceClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.policy = SourceUrlPolicy(settings)
        timeout = httpx.Timeout(
            connect=settings.source_connect_timeout,
            read=settings.source_read_timeout,
            write=30.0,
            pool=30.0,
        )
        self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)

    async def close(self) -> None:
        await self.client.aclose()

    async def _open(self, method: str, url: str, headers: dict[str, str] | None = None) -> tuple[str, httpx.Response]:
        current = url
        request_headers = {"Accept-Encoding": "identity", **(headers or {})}
        for _ in range(self.settings.source_max_redirects + 1):
            self.policy.validate(current)
            try:
                request = self.client.build_request(method, current, headers=request_headers)
                response = await self.client.send(request, stream=True)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail="Источник файла недоступен") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise HTTPException(status_code=502, detail="Источник вернул redirect без адреса")
                current = urljoin(current, location)
                continue
            return current, response
        raise HTTPException(status_code=502, detail="Слишком много перенаправлений источника")

    @asynccontextmanager
    async def stream(
        self, method: str, url: str, headers: dict[str, str] | None = None
    ) -> AsyncIterator[tuple[str, httpx.Response]]:
        final_url, response = await self._open(method, url, headers)
        try:
            yield final_url, response
        finally:
            await response.aclose()

    async def probe(self, url: str) -> SourceProbe:
        self.policy.validate(url)
        final_url = url
        headers: httpx.Headers | None = None
        async with self.stream("HEAD", url) as (resolved, response):
            final_url = resolved
            if response.status_code < 400:
                headers = response.headers
            elif response.status_code not in {403, 405, 501}:
                self._raise_for_status(response)

        if headers is None:
            async with self.stream("GET", url, {"Range": "bytes=0-0"}) as (resolved, response):
                final_url = resolved
                self._raise_for_status(response)
                headers = response.headers

        size = _content_size(headers)
        if size is not None and size > self.settings.max_source_bytes:
            raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
        return SourceProbe(
            final_url=final_url,
            size=size,
            etag=headers.get("etag"),
            last_modified=headers.get("last-modified"),
            content_type=headers.get("content-type", "").split(";", 1)[0] or None,
            filename=_content_disposition_name(headers.get("content-disposition"))
            or safe_display_name(urlsplit(final_url).path, "file"),
        )

    async def read_prefix(self, url: str, limit: int = 262_144) -> bytes:
        data = bytearray()
        async with self.stream("GET", url, {"Range": f"bytes=0-{limit - 1}"}) as (_, response):
            self._raise_for_status(response)
            async for chunk in response.aiter_raw():
                needed = limit - len(data)
                data.extend(chunk[:needed])
                if len(data) >= limit:
                    break
        return bytes(data)

    async def download(self, url: str, destination: Path, max_bytes: int | None = None) -> int:
        limit = max_bytes or self.settings.max_source_bytes
        written = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self.stream("GET", url) as (_, response):
            self._raise_for_status(response)
            declared_size = _content_size(response.headers)
            if declared_size is not None and declared_size > limit:
                raise HTTPException(status_code=413, detail="Файл превышает лимит обработчика")
            async with aiofiles.open(destination, "wb") as output:
                async for chunk in response.aiter_raw():
                    written += len(chunk)
                    if written > limit:
                        raise HTTPException(status_code=413, detail="Файл превышает лимит обработчика")
                    await output.write(chunk)
        return written

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Исходный файл не найден")
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Источник вернул HTTP {response.status_code}")


def _content_size(headers: httpx.Headers) -> int | None:
    content_range = headers.get("content-range")
    if content_range and "/" in content_range:
        total = content_range.rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total)
    value = headers.get("content-length")
    return int(value) if value and value.isdigit() else None


def _content_disposition_name(value: str | None) -> str | None:
    if not value:
        return None
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", value, re.IGNORECASE)
    if encoded:
        return safe_display_name(unquote(encoded.group(1)))
    plain = re.search(r'filename="?([^";]+)', value, re.IGNORECASE)
    return safe_display_name(plain.group(1)) if plain else None

