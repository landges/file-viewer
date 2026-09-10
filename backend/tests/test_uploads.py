import shutil
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from app.cache import LocalPreviewCache
from app.config import Settings
from app.service import PreviewService


class NoRemoteSource:
    def __getattr__(self, name: str):
        raise AssertionError(f"Uploaded files must not use remote source method {name}")


def upload_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        work_dir=tmp_path / "work",
        static_dir=tmp_path / "static",
        **overrides,
    )


@pytest.mark.asyncio
async def test_uploaded_file_is_processed_and_can_be_downloaded(tmp_path) -> None:
    settings = upload_settings(tmp_path)
    settings.prepare_directories()
    cache = LocalPreviewCache(settings)
    service = PreviewService(settings, NoRemoteSource(), cache)  # type: ignore[arg-type]
    source = b'<?xml version="1.0" encoding="UTF-8"?><message>local file</message>'

    manifest = await service.create_upload(
        UploadFile(file=BytesIO(source), filename="../../local.xml")
    )
    task = service.tasks[manifest.id]
    await task

    ready = await service.require(manifest.id)
    assert ready.status == "ready"
    assert ready.renderer == "xml"
    assert ready.display_name == "local.xml"
    assert cache.upload_path(manifest.id).read_bytes() == source

    download, work_dir = await service.materialize_for_download(ready)
    try:
        assert download.name == "root.xml"
        assert download.read_bytes() == source
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_uploaded_file_size_limit_is_enforced(tmp_path) -> None:
    settings = upload_settings(tmp_path, max_source_bytes=4)
    settings.prepare_directories()
    cache = LocalPreviewCache(settings)
    service = PreviewService(settings, NoRemoteSource(), cache)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as caught:
        await service.create_upload(UploadFile(file=BytesIO(b"12345"), filename="large.txt"))

    assert caught.value.status_code == 413
    assert list(settings.cache_dir.iterdir()) == []
