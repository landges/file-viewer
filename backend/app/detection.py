from __future__ import annotations

import mimetypes
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import olefile


@dataclass(frozen=True, slots=True)
class DetectedFormat:
    kind: str
    mime_type: str
    extension: str
    label: str


IMAGE_SIGNATURES: list[tuple[bytes, DetectedFormat]] = [
    (b"\x89PNG\r\n\x1a\n", DetectedFormat("image", "image/png", ".png", "PNG")),
    (b"\xff\xd8\xff", DetectedFormat("image", "image/jpeg", ".jpg", "JPEG")),
    (b"GIF87a", DetectedFormat("image", "image/gif", ".gif", "GIF")),
    (b"GIF89a", DetectedFormat("image", "image/gif", ".gif", "GIF")),
    (b"BM", DetectedFormat("image_convert", "image/bmp", ".bmp", "BMP")),
    (b"II*\x00", DetectedFormat("image_convert", "image/tiff", ".tiff", "TIFF")),
    (b"MM\x00*", DetectedFormat("image_convert", "image/tiff", ".tiff", "TIFF")),
]

ARCHIVE_SIGNATURES: list[tuple[bytes, DetectedFormat]] = [
    (b"7z\xbc\xaf'\x1c", DetectedFormat("archive", "application/x-7z-compressed", ".7z", "7-Zip")),
    (b"Rar!\x1a\x07\x00", DetectedFormat("archive", "application/vnd.rar", ".rar", "RAR")),
    (b"Rar!\x1a\x07\x01\x00", DetectedFormat("archive", "application/vnd.rar", ".rar", "RAR 5")),
    (b"\x1f\x8b", DetectedFormat("archive", "application/gzip", ".gz", "GZip")),
]


def detect_prefix(prefix: bytes, filename: str = "") -> DetectedFormat:
    lower_name = filename.lower()
    if prefix.startswith(b"%PDF-"):
        return DetectedFormat("pdf", "application/pdf", ".pdf", "PDF")
    for signature, detected in IMAGE_SIGNATURES:
        if prefix.startswith(signature):
            return detected
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return DetectedFormat("image", "image/webp", ".webp", "WebP")
    if prefix.lstrip().startswith(b"<svg") or b"<svg" in prefix[:1024].lower():
        return DetectedFormat("image", "image/svg+xml", ".svg", "SVG")
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return DetectedFormat("zip_container", "application/zip", ".zip", "ZIP container")
    for signature, detected in ARCHIVE_SIGNATURES:
        if prefix.startswith(signature):
            return detected
    if len(prefix) > 265 and prefix[257:262] == b"ustar":
        return DetectedFormat("archive", "application/x-tar", ".tar", "TAR")
    if prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        extension = Path(lower_name).suffix
        mapping = {
            ".doc": DetectedFormat("office", "application/msword", ".doc", "Microsoft Word"),
            ".xls": DetectedFormat("spreadsheet", "application/vnd.ms-excel", ".xls", "Microsoft Excel"),
            ".ppt": DetectedFormat("office", "application/vnd.ms-powerpoint", ".ppt", "Microsoft PowerPoint"),
        }
        return mapping.get(extension, DetectedFormat("office", "application/x-ole-storage", extension or ".ole", "OLE document"))
    if prefix.startswith(b"{\\rtf"):
        return DetectedFormat("office", "application/rtf", ".rtf", "RTF")

    extension = Path(lower_name).suffix
    if extension == ".eml" or _looks_like_email(prefix):
        return DetectedFormat("email", "message/rfc822", ".eml", "Email")
    if extension in {".csv", ".tsv"} and _looks_text(prefix):
        return DetectedFormat("delimited", "text/csv", extension, "Delimited text")
    if _looks_text(prefix):
        guessed = mimetypes.guess_type(lower_name)[0] or "text/plain"
        return DetectedFormat("text", guessed if guessed.startswith("text/") else "text/plain", extension or ".txt", "Text")
    return DetectedFormat("unknown", "application/octet-stream", extension or ".bin", "Unknown")


def inspect_zip(path: Path) -> DetectedFormat:
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
            lowered = {name.lower() for name in names}
            if "[content_types].xml" in lowered:
                if any(name.startswith("word/") for name in lowered):
                    return DetectedFormat("office", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", "Microsoft Word")
                if any(name.startswith("xl/") for name in lowered):
                    return DetectedFormat("spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", "Microsoft Excel")
                if any(name.startswith("ppt/") for name in lowered):
                    return DetectedFormat("office", "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx", "Microsoft PowerPoint")
            if "mimetype" in lowered:
                mime = archive.read(next(name for name in names if name.lower() == "mimetype"))[:128].decode("ascii", "ignore")
                if "spreadsheet" in mime:
                    return DetectedFormat("spreadsheet", mime, ".ods", "OpenDocument Spreadsheet")
                if "text" in mime or "presentation" in mime:
                    ext = ".odp" if "presentation" in mime else ".odt"
                    return DetectedFormat("office", mime, ext, "OpenDocument")
    except (OSError, zipfile.BadZipFile, KeyError):
        return DetectedFormat("unknown", "application/octet-stream", ".bin", "Damaged ZIP container")
    return DetectedFormat("archive", "application/zip", ".zip", "ZIP archive")


def inspect_ole(path: Path, fallback: DetectedFormat) -> DetectedFormat:
    try:
        with olefile.OleFileIO(path) as document:
            streams = {"/".join(parts).lower() for parts in document.listdir()}
    except (OSError, IOError, olefile.OleFileError):
        return fallback
    if "worddocument" in streams:
        return DetectedFormat("office", "application/msword", ".doc", "Microsoft Word")
    if "workbook" in streams or "book" in streams:
        return DetectedFormat("spreadsheet", "application/vnd.ms-excel", ".xls", "Microsoft Excel")
    if "powerpoint document" in streams:
        return DetectedFormat("office", "application/vnd.ms-powerpoint", ".ppt", "Microsoft PowerPoint")
    return fallback


def _looks_like_email(prefix: bytes) -> bool:
    text = prefix[:64_000].decode("utf-8", "ignore")
    header, separator, _ = text.partition("\n\n")
    if not separator:
        header, separator, _ = text.partition("\r\n\r\n")
    matches = re.findall(r"(?im)^(from|to|subject|date|mime-version|content-type):", header[:16_000])
    return len(set(item.lower() for item in matches)) >= 2


def _looks_text(prefix: bytes) -> bool:
    if not prefix:
        return True
    if b"\x00" in prefix[:8192]:
        return False
    sample = prefix[:8192]
    printable = sum(byte in b"\n\r\t\f\b" or 32 <= byte <= 126 or byte >= 128 for byte in sample)
    return printable / len(sample) > 0.90
