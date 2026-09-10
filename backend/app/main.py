from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .cache import LocalPreviewCache
from .config import get_settings
from .models import EmbeddedPreviewRequest, PreviewCreateRequest, PreviewPublic
from .service import PreviewService
from .source import SourceClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.prepare_directories()
    source = SourceClient(settings)
    cache = LocalPreviewCache(settings)
    service = PreviewService(settings, source, cache)
    app.state.source = source
    app.state.cache = cache
    app.state.preview_service = service
    cleanup_task = asyncio.create_task(_cleanup_loop(cache), name="local-cache-cleanup")
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await service.stop()
        await source.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


def service(request: Request) -> PreviewService:
    return request.app.state.preview_service


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/previews", response_model=PreviewPublic, status_code=202)
async def create_preview(payload: PreviewCreateRequest, request: Request) -> PreviewPublic:
    preview = await service(request).create(payload.url, payload.filename)
    return service(request).to_public(preview)


@app.post("/api/uploads", response_model=PreviewPublic, status_code=202)
async def upload_preview(request: Request, file: UploadFile = File(...)) -> PreviewPublic:
    preview = await service(request).create_upload(file)
    return service(request).to_public(preview)


@app.get("/api/previews/{preview_id}", response_model=PreviewPublic)
async def get_preview(preview_id: str, request: Request) -> PreviewPublic:
    preview = await service(request).require(preview_id)
    return service(request).to_public(preview)


@app.post("/api/previews/{preview_id}/entries", response_model=PreviewPublic, status_code=202)
async def open_embedded(
    preview_id: str, payload: EmbeddedPreviewRequest, request: Request
) -> PreviewPublic:
    preview = await service(request).create_embedded(preview_id, payload.entry_id)
    return service(request).to_public(preview)


@app.get("/api/previews/{preview_id}/data")
async def preview_data(preview_id: str, request: Request) -> FileResponse:
    preview = await service(request).require(preview_id)
    if preview.status != "ready":
        raise HTTPException(status_code=409, detail="Предпросмотр ещё не готов")
    asset = request.app.state.cache.asset_path(preview)
    if not asset or not asset.is_file():
        raise HTTPException(status_code=404, detail="Данные предпросмотра не найдены")
    return FileResponse(asset, media_type=preview.mime_type, headers={"Cache-Control": "private, max-age=60"})


@app.get("/api/previews/{preview_id}/content")
async def preview_content(preview_id: str, request: Request):
    preview = await service(request).require(preview_id)
    if preview.status != "ready":
        raise HTTPException(status_code=409, detail="Предпросмотр ещё не готов")
    asset = request.app.state.cache.asset_path(preview)
    headers = _safe_content_headers(preview.display_name, inline=True)
    if asset and asset.is_file():
        return FileResponse(asset, media_type=preview.mime_type, filename=preview.display_name, content_disposition_type="inline", headers=headers)
    if preview.origin_chain:
        raise HTTPException(status_code=404, detail="Файл предпросмотра не найден")
    return await _proxy_source(request, preview.source_url, preview.mime_type, preview.display_name, inline=True)


@app.get("/api/previews/{preview_id}/download")
async def download(preview_id: str, request: Request):
    preview = await service(request).require(preview_id)
    if preview.status != "ready":
        raise HTTPException(status_code=409, detail="Предпросмотр ещё не готов")
    if not preview.origin_chain and not service(request).is_uploaded(preview):
        return await _proxy_source(
            request,
            preview.source_url,
            "application/octet-stream",
            preview.display_name,
            inline=False,
        )
    path, work_dir = await service(request).materialize_for_download(preview)
    return FileResponse(
        path,
        filename=preview.display_name,
        media_type="application/octet-stream",
        background=BackgroundTask(shutil.rmtree, work_dir, True),
    )


async def _proxy_source(
    request: Request,
    url: str,
    media_type: str,
    filename: str,
    inline: bool,
) -> StreamingResponse:
    source: SourceClient = request.app.state.source
    upstream_headers: dict[str, str] = {}
    if value := request.headers.get("range"):
        upstream_headers["Range"] = value
    final_url, response = await source._open("GET", url, upstream_headers)
    del final_url
    if response.status_code not in {200, 206}:
        try:
            SourceClient._raise_for_status(response)
        finally:
            await response.aclose()

    async def body():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    headers = _safe_content_headers(filename, inline)
    for key in ("content-length", "content-range", "accept-ranges", "etag", "last-modified"):
        if value := response.headers.get(key):
            headers[key] = value
    return StreamingResponse(body(), status_code=response.status_code, media_type=media_type, headers=headers)


def _safe_content_headers(filename: str, inline: bool) -> dict[str, str]:
    disposition = "inline" if inline else "attachment"
    encoded = quote(filename, safe="")
    return {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}",
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: cid:",
    }


async def _cleanup_loop(cache: LocalPreviewCache) -> None:
    while True:
        try:
            removed = await cache.cleanup()
            if removed:
                logger.info("Removed %d expired local preview entries", removed)
        except Exception:
            logger.exception("Local cache cleanup failed")
        await asyncio.sleep(settings.cleanup_interval_seconds)


static_dir = settings.static_dir
if static_dir.is_dir():
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        candidate = (settings.static_dir / path).resolve()
        if settings.static_dir.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(settings.static_dir / "index.html")
