from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.detection import detect_prefix, inspect_zip


def test_detects_common_signatures_without_relying_on_suffix() -> None:
    assert detect_prefix(b"%PDF-1.7\n", "wrong.bin").kind == "pdf"
    assert detect_prefix(b"\x89PNG\r\n\x1a\nrest", "wrong.bin").mime_type == "image/png"
    assert detect_prefix(b"7z\xbc\xaf'\x1cdata", "wrong.bin").kind == "archive"
    assert detect_prefix(b"PK\x03\x04data", "archive.custom").kind == "zip_container"


def test_distinguishes_ooxml_from_generic_zip(tmp_path: Path) -> None:
    docx = tmp_path / "anything.custom"
    with ZipFile(docx, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    assert inspect_zip(docx).kind == "office"
    assert inspect_zip(docx).extension == ".docx"

    generic = tmp_path / "archive.payload"
    with ZipFile(generic, "w", ZIP_DEFLATED) as archive:
        archive.writestr("nested/readme.txt", "hello")
    assert inspect_zip(generic).kind == "archive"


def test_email_heuristic() -> None:
    raw = b"From: sender@example.test\r\nTo: user@example.test\r\nSubject: Hi\r\n\r\nBody"
    assert detect_prefix(raw, "unknown.data").kind == "email"

