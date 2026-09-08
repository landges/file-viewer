from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.container_formats import extract_zip_entry, list_zip


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        work_dir=tmp_path / "work",
        max_archive_entries=5,
        max_extracted_bytes=1024,
        max_source_bytes=1024,
    )


def test_lists_custom_suffix_zip_and_ignores_unsafe_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.custom"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("folder/note.txt", "hello")
        archive.writestr("../../escape.txt", "blocked")
    result = list_zip(archive_path, settings(tmp_path))
    assert [item["path"] for item in result["entries"]] == ["folder/note.txt"]


def test_extracts_only_selected_safe_entry(tmp_path: Path) -> None:
    archive_path = tmp_path / "archive.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("folder/note.txt", "hello")
    destination = tmp_path / "result.txt"
    extract_zip_entry(archive_path, "folder/note.txt", destination, settings(tmp_path))
    assert destination.read_text() == "hello"
    with pytest.raises(HTTPException):
        extract_zip_entry(archive_path, "../note.txt", destination, settings(tmp_path))


def test_archive_expansion_limit(tmp_path: Path) -> None:
    archive_path = tmp_path / "large.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("large.txt", "x" * 2048)
    with pytest.raises(HTTPException) as error:
        list_zip(archive_path, settings(tmp_path))
    assert error.value.status_code == 413

