from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .models import PreviewManifest


class LocalPreviewCache:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.cache_dir.resolve()
        self._write_lock = asyncio.Lock()

    def entry_dir(self, preview_id: str) -> Path:
        if not preview_id or any(character not in "0123456789abcdef" for character in preview_id):
            raise ValueError("Invalid preview id")
        target = (self.root / preview_id).resolve()
        if target.parent != self.root:
            raise ValueError("Invalid preview path")
        return target

    def manifest_path(self, preview_id: str) -> Path:
        return self.entry_dir(preview_id) / "manifest.json"

    async def load(self, preview_id: str, touch: bool = False) -> PreviewManifest | None:
        path = self.manifest_path(preview_id)
        try:
            data = await asyncio.to_thread(path.read_text, encoding="utf-8")
            manifest = PreviewManifest.model_validate_json(data)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return None
        if touch:
            manifest.accessed_at = datetime.now(UTC)
            await self.save(manifest)
        return manifest

    async def save(self, manifest: PreviewManifest) -> None:
        entry = self.entry_dir(manifest.id)
        entry.mkdir(parents=True, exist_ok=True)
        manifest.updated_at = datetime.now(UTC)
        payload = manifest.model_dump_json(indent=2)
        temporary = entry / "manifest.json.tmp"
        target = entry / "manifest.json"
        async with self._write_lock:
            await asyncio.to_thread(temporary.write_text, payload, encoding="utf-8")
            await asyncio.to_thread(os.replace, temporary, target)

    def is_expired(self, manifest: PreviewManifest) -> bool:
        short_lived = manifest.status in {"queued", "processing", "failed"}
        ttl = self.settings.failed_ttl_seconds if short_lived else self.settings.cache_ttl_seconds
        return (datetime.now(UTC) - manifest.accessed_at).total_seconds() > ttl

    async def remove(self, preview_id: str) -> None:
        target = self.entry_dir(preview_id)
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target, True)

    def asset_path(self, manifest: PreviewManifest) -> Path | None:
        if not manifest.asset_name:
            return None
        candidate = (self.entry_dir(manifest.id) / manifest.asset_name).resolve()
        if self.entry_dir(manifest.id) not in candidate.parents:
            return None
        return candidate

    async def cleanup(self) -> int:
        now = datetime.now(UTC)
        removed = 0
        for child in list(self.root.iterdir()):
            if not child.is_dir():
                continue
            try:
                manifest = await self.load(child.name)
                if manifest is None:
                    age = now.timestamp() - child.stat().st_mtime
                    expired = age > self.settings.failed_ttl_seconds
                else:
                    expired = self.is_expired(manifest)
                if expired:
                    await asyncio.to_thread(shutil.rmtree, child, True)
                    removed += 1
            except OSError:
                continue
        return removed
