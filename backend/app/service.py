from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import rarfile
from fastapi import HTTPException

from .cache import LocalPreviewCache
from .config import Settings
from .container_formats import (
    extract_7z_entry,
    extract_email_entry,
    extract_rar_entry,
    extract_zip_entry,
)
from .detection import detect_prefix, inspect_ole, inspect_zip
from .models import OriginStep, PreviewManifest, PreviewPublic
from .processors import process_materialized_file, zipfile_is_zip
from .security import safe_display_name
from .source import SourceClient

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self, settings: Settings, source: SourceClient, cache: LocalPreviewCache):
        self.settings = settings
        self.source = source
        self.cache = cache
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.conversion_slots = asyncio.Semaphore(settings.max_concurrent_conversions)

    async def create(self, source_url: str, filename: str | None) -> PreviewManifest:
        probe = await self.source.probe(source_url)
        display_name = safe_display_name(filename or probe.filename, "file")
        preview_id = _preview_id(
            probe.final_url,
            probe.etag,
            probe.last_modified,
            str(probe.size or ""),
        )
        existing = await self.cache.load(preview_id)
        if existing and self.cache.is_expired(existing):
            await self.cache.remove(preview_id)
            existing = None
        if existing:
            existing.accessed_at = datetime.now(UTC)
            await self.cache.save(existing)
            if existing.status in {"queued", "processing"} and preview_id not in self.tasks:
                existing.status = "queued"
                await self.cache.save(existing)
                self._schedule(existing)
            return existing

        manifest = PreviewManifest(
            id=preview_id,
            source_url=probe.final_url,
            display_name=display_name,
            source_etag=probe.etag,
            source_last_modified=probe.last_modified,
            source_size=probe.size,
        )
        await self.cache.save(manifest)
        self._schedule(manifest)
        return manifest

    async def create_embedded(self, parent_id: str, entry_id: str) -> PreviewManifest:
        parent = await self.require(parent_id)
        if parent.status != "ready" or parent.renderer not in {"archive", "email"}:
            raise HTTPException(status_code=409, detail="Контейнер ещё не готов")
        if len(parent.origin_chain) >= self.settings.max_nesting_depth:
            raise HTTPException(status_code=413, detail="Достигнута максимальная глубина вложений")
        data = await self.read_data(parent)
        if parent.renderer == "archive":
            item = next((entry for entry in data.get("entries", []) if entry.get("id") == entry_id), None)
            if not item:
                raise HTTPException(status_code=404, detail="Элемент архива не найден")
            if item.get("is_dir"):
                raise HTTPException(status_code=400, detail="Каталог нельзя открыть как файл")
            if item.get("encrypted"):
                raise HTTPException(status_code=422, detail="Элемент защищён паролем")
            step = OriginStep(container="archive", locator=item["path"], filename=item["name"])
        else:
            item = next((entry for entry in data.get("attachments", []) if entry.get("id") == entry_id), None)
            if not item:
                raise HTTPException(status_code=404, detail="Вложение не найдено")
            step = OriginStep(container="email", locator=item["locator"], filename=item["name"])

        chain = [*parent.origin_chain, step]
        chain_key = json.dumps([value.model_dump() for value in chain], ensure_ascii=False, sort_keys=True)
        preview_id = _preview_id(
            parent.source_url,
            parent.source_etag,
            parent.source_last_modified,
            chain_key,
        )
        existing = await self.cache.load(preview_id)
        if existing and self.cache.is_expired(existing):
            await self.cache.remove(preview_id)
            existing = None
        if existing:
            existing.accessed_at = datetime.now(UTC)
            await self.cache.save(existing)
            if existing.status in {"queued", "processing"} and preview_id not in self.tasks:
                existing.status = "queued"
                await self.cache.save(existing)
                self._schedule(existing)
            return existing
        manifest = PreviewManifest(
            id=preview_id,
            source_url=parent.source_url,
            display_name=step.filename,
            source_etag=parent.source_etag,
            source_last_modified=parent.source_last_modified,
            source_size=parent.source_size,
            origin_chain=chain,
        )
        await self.cache.save(manifest)
        self._schedule(manifest)
        return manifest

    async def require(self, preview_id: str, touch: bool = True) -> PreviewManifest:
        try:
            manifest = await self.cache.load(preview_id, touch=touch)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Предпросмотр не найден") from exc
        if manifest is None:
            raise HTTPException(status_code=404, detail="Предпросмотр не найден на этой реплике")
        return manifest

    async def read_data(self, manifest: PreviewManifest) -> dict:
        asset = self.cache.asset_path(manifest)
        if not asset or not asset.is_file():
            raise HTTPException(status_code=404, detail="Данные предпросмотра не найдены")
        try:
            return json.loads(await asyncio.to_thread(asset.read_text, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="Данные предпросмотра повреждены") from exc

    def to_public(self, manifest: PreviewManifest) -> PreviewPublic:
        ready = manifest.status == "ready"
        has_content = ready and manifest.renderer in {
            "proxy", "pdf", "image", "audio", "text", "html"
        }
        has_data = ready and manifest.renderer in {"spreadsheet", "archive", "email"}
        return PreviewPublic(
            id=manifest.id,
            display_name=manifest.display_name,
            status=manifest.status,
            renderer=manifest.renderer,
            detected_type=manifest.detected_type,
            mime_type=manifest.mime_type,
            error=manifest.error,
            content_url=f"/api/previews/{manifest.id}/content" if has_content else None,
            data_url=f"/api/previews/{manifest.id}/data" if has_data else None,
            download_url=f"/api/previews/{manifest.id}/download" if ready else None,
            depth=len(manifest.origin_chain),
        )

    async def materialize_for_download(self, manifest: PreviewManifest) -> tuple[Path, Path]:
        work_dir = Path(tempfile.mkdtemp(prefix=f"download-{manifest.id[:8]}-", dir=self.settings.work_dir))
        try:
            path, _ = await self._materialize(manifest, work_dir)
            return path, work_dir
        except Exception:
            await asyncio.to_thread(shutil.rmtree, work_dir, True)
            raise

    async def stop(self) -> None:
        active = [task for task in self.tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    def _schedule(self, manifest: PreviewManifest) -> None:
        task = asyncio.create_task(self._process(manifest.id), name=f"preview-{manifest.id}")
        self.tasks[manifest.id] = task
        task.add_done_callback(lambda _: self.tasks.pop(manifest.id, None))

    async def _process(self, preview_id: str) -> None:
        manifest = await self.cache.load(preview_id)
        if manifest is None:
            return
        manifest.status = "processing"
        manifest.error = None
        await self.cache.save(manifest)
        work_dir = Path(tempfile.mkdtemp(prefix=f"job-{preview_id[:8]}-", dir=self.settings.work_dir))
        try:
            async with self.conversion_slots:
                if not manifest.origin_chain:
                    prefix = await self.source.read_prefix(manifest.source_url)
                    detected = detect_prefix(prefix, manifest.display_name)
                    if detected.kind in {"pdf", "image", "audio"}:
                        manifest.renderer = detected.kind
                        manifest.detected_type = detected.label
                        manifest.mime_type = detected.mime_type
                        manifest.asset_name = None
                        manifest.status = "ready"
                        await self.cache.save(manifest)
                        return

                source_path, materialized_name = await self._materialize(manifest, work_dir)
                prefix = await asyncio.to_thread(_read_prefix, source_path)
                detected = detect_prefix(prefix, materialized_name)
                if detected.kind == "zip_container":
                    detected = await asyncio.to_thread(inspect_zip, source_path)
                elif detected.mime_type == "application/x-ole-storage":
                    detected = await asyncio.to_thread(inspect_ole, source_path, detected)
                manifest.detected_type = detected.label
                manifest.mime_type = detected.mime_type
                result = await process_materialized_file(
                    source_path,
                    detected,
                    self.cache.entry_dir(manifest.id),
                    self.settings,
                )
                manifest.renderer = result.renderer  # type: ignore[assignment]
                manifest.detected_type = result.detected_type
                manifest.mime_type = result.mime_type
                manifest.asset_name = result.asset_name
                manifest.status = "ready"
        except asyncio.CancelledError:
            manifest.status = "failed"
            manifest.error = "Обработка прервана перезапуском сервиса"
            raise
        except HTTPException as exc:
            manifest.status = "failed"
            manifest.error = str(exc.detail)
            logger.warning("Preview %s failed: %s", preview_id, exc.detail)
        except Exception as exc:  # keep worker alive and expose a stable error state
            manifest.status = "failed"
            manifest.error = "Внутренняя ошибка обработчика"
            logger.exception("Preview %s failed", preview_id, exc_info=exc)
        finally:
            await self.cache.save(manifest)
            await asyncio.to_thread(shutil.rmtree, work_dir, True)

    async def _materialize(self, manifest: PreviewManifest, work_dir: Path) -> tuple[Path, str]:
        current_name = safe_display_name(manifest.source_url.rsplit("/", 1)[-1], manifest.display_name)
        current = work_dir / f"root{Path(current_name).suffix or '.bin'}"
        await self.source.download(manifest.source_url, current, self.settings.max_source_bytes)
        for index, step in enumerate(manifest.origin_chain):
            next_path = work_dir / f"nested-{index}{Path(step.filename).suffix or '.bin'}"
            if step.container == "email":
                current_name = await asyncio.to_thread(extract_email_entry, current, step.locator, next_path)
            else:
                if zipfile_is_zip(current):
                    await asyncio.to_thread(extract_zip_entry, current, step.locator, next_path, self.settings)
                elif rarfile.is_rarfile(current):
                    await asyncio.to_thread(extract_rar_entry, current, step.locator, next_path, self.settings)
                else:
                    await extract_7z_entry(current, step.locator, next_path, self.settings)
                current_name = step.filename
            current = next_path
        return current, current_name if manifest.origin_chain else manifest.display_name


def _preview_id(*parts: str | None) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8", "surrogatepass"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


def _read_prefix(path: Path, limit: int = 262_144) -> bytes:
    with path.open("rb") as stream:
        return stream.read(limit)
