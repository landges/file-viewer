from __future__ import annotations

import asyncio
import csv
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import mkdtemp

import bleach
import rarfile
from fastapi import HTTPException
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError

from .config import Settings
from .container_formats import list_7z, list_email, list_rar, list_zip, write_json
from .detection import DetectedFormat


@dataclass(frozen=True, slots=True)
class ProcessResult:
    renderer: str
    detected_type: str
    mime_type: str
    asset_name: str | None


async def process_materialized_file(
    source_path: Path,
    detected: DetectedFormat,
    entry_dir: Path,
    settings: Settings,
) -> ProcessResult:
    entry_dir.mkdir(parents=True, exist_ok=True)
    if detected.kind == "pdf":
        target = entry_dir / "preview.pdf"
        await asyncio.to_thread(shutil.copy2, source_path, target)
        return ProcessResult("pdf", detected.label, "application/pdf", target.name)
    if detected.kind == "image":
        target = entry_dir / f"preview{detected.extension}"
        await asyncio.to_thread(shutil.copy2, source_path, target)
        return ProcessResult("image", detected.label, detected.mime_type, target.name)
    if detected.kind == "audio":
        target = entry_dir / f"preview{detected.extension}"
        await asyncio.to_thread(shutil.copy2, source_path, target)
        return ProcessResult("audio", detected.label, detected.mime_type, target.name)
    if detected.kind == "image_convert":
        return await _convert_image(source_path, entry_dir, detected)
    if detected.kind == "office":
        return await _convert_office(source_path, entry_dir, detected, settings)
    if detected.kind == "spreadsheet":
        return await _render_spreadsheet(source_path, entry_dir, detected, settings)
    if detected.kind == "delimited":
        return await _render_delimited(source_path, entry_dir, detected, settings)
    if detected.kind == "archive":
        return await _render_archive(source_path, entry_dir, detected, settings)
    if detected.kind in {"email", "outlook_email"}:
        return await _render_email(source_path, entry_dir, detected, settings)
    if detected.kind == "html":
        return await _render_html(source_path, entry_dir, detected, settings)
    if detected.kind in {"text", "xml"}:
        return await _render_text(source_path, entry_dir, detected, settings)
    return ProcessResult("unsupported", detected.label, detected.mime_type, None)


async def _convert_office(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    if source_path.stat().st_size > settings.max_office_bytes:
        raise HTTPException(status_code=413, detail="Office-файл превышает лимит")
    typed_input = source_path.parent / f"input{detected.extension}"
    await asyncio.to_thread(shutil.copy2, source_path, typed_input)
    profile = Path(mkdtemp(prefix="lo-profile-", dir=source_path.parent))
    command = [
        "soffice",
        "--headless",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        f"-env:UserInstallation={profile.as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(entry_dir),
        str(typed_input),
    ]
    await _run(command, settings.office_timeout_seconds, "Office-файл не удалось преобразовать")
    candidates = sorted(entry_dir.glob("*.pdf"))
    if not candidates:
        raise HTTPException(status_code=422, detail="LibreOffice не создал PDF")
    output = entry_dir / "preview.pdf"
    if candidates[0] != output:
        candidates[0].replace(output)
    return ProcessResult("pdf", detected.label, "application/pdf", output.name)


async def _render_spreadsheet(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    if source_path.stat().st_size > settings.max_office_bytes:
        raise HTTPException(status_code=413, detail="Таблица превышает лимит")
    workbook_path = source_path
    if detected.extension not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        typed_input = source_path.parent / f"input{detected.extension}"
        await asyncio.to_thread(shutil.copy2, source_path, typed_input)
        output_dir = source_path.parent / "converted"
        output_dir.mkdir(exist_ok=True)
        profile = Path(mkdtemp(prefix="lo-profile-", dir=source_path.parent))
        await _run(
            [
                "soffice",
                "--headless",
                "--nologo",
                "--nodefault",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "xlsx",
                "--outdir",
                str(output_dir),
                str(typed_input),
            ],
            settings.office_timeout_seconds,
            "Таблицу не удалось преобразовать",
        )
        candidates = sorted(output_dir.glob("*.xlsx"))
        if not candidates:
            raise HTTPException(status_code=422, detail="LibreOffice не создал XLSX")
        workbook_path = candidates[0]

    data = await asyncio.to_thread(_workbook_to_dict, workbook_path, settings)
    output = entry_dir / "workbook.json"
    await asyncio.to_thread(write_json, output, data)
    return ProcessResult("spreadsheet", detected.label, "application/json", output.name)


def _workbook_to_dict(path: Path, settings: Settings) -> dict:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Таблицу не удалось прочитать") from exc
    sheets: list[dict] = []
    truncated = False
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[object]] = []
            for row_index, row in enumerate(
                worksheet.iter_rows(max_col=settings.max_sheet_columns, values_only=True), start=1
            ):
                if row_index > settings.max_sheet_rows:
                    truncated = True
                    break
                values = [_json_cell(value) for value in row]
                while values and values[-1] is None:
                    values.pop()
                rows.append(values)
            sheets.append({"name": worksheet.title, "rows": rows})
    finally:
        workbook.close()
    return {"sheets": sheets, "truncated": truncated}


def _json_cell(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def _render_delimited(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    text = await asyncio.to_thread(_decode_text, source_path.read_bytes())
    delimiter = "\t" if detected.extension == ".tsv" else ","
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        pass
    rows: list[list[str]] = []
    truncated = False
    for index, row in enumerate(csv.reader(text.splitlines(), delimiter=delimiter), start=1):
        if index > settings.max_sheet_rows:
            truncated = True
            break
        rows.append(row[: settings.max_sheet_columns])
    output = entry_dir / "workbook.json"
    await asyncio.to_thread(
        write_json,
        output,
        {"sheets": [{"name": "Data", "rows": rows}], "truncated": truncated},
    )
    return ProcessResult("spreadsheet", detected.label, "application/json", output.name)


async def _render_archive(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    if source_path.stat().st_size > settings.max_archive_bytes:
        raise HTTPException(status_code=413, detail="Архив превышает лимит")
    if zipfile_is_zip(source_path):
        data = await asyncio.to_thread(list_zip, source_path, settings)
    elif rarfile.is_rarfile(source_path):
        data = await asyncio.to_thread(list_rar, source_path, settings)
    else:
        data = await list_7z(source_path, settings)
    output = entry_dir / "archive.json"
    await asyncio.to_thread(write_json, output, data)
    return ProcessResult("archive", detected.label, "application/json", output.name)


async def _render_email(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    if source_path.stat().st_size > settings.max_office_bytes:
        raise HTTPException(status_code=413, detail="Почтовый файл превышает лимит")
    data, _ = await asyncio.to_thread(list_email, source_path)
    data["html"] = _sanitize_email_html(data["html"])
    output = entry_dir / "email.json"
    await asyncio.to_thread(write_json, output, data)
    return ProcessResult("email", detected.label, "application/json", output.name)


async def _render_text(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    if source_path.stat().st_size > settings.max_text_bytes:
        raise HTTPException(status_code=413, detail="Текстовый файл превышает лимит")
    content = await asyncio.to_thread(_decode_text, source_path.read_bytes())
    output = entry_dir / "preview.txt"
    await asyncio.to_thread(output.write_text, content, encoding="utf-8")
    return ProcessResult("text", detected.label, "text/plain; charset=utf-8", output.name)


async def _render_html(
    source_path: Path, entry_dir: Path, detected: DetectedFormat, settings: Settings
) -> ProcessResult:
    if source_path.stat().st_size > settings.max_text_bytes:
        raise HTTPException(status_code=413, detail="HTML-файл превышает лимит")
    content = await asyncio.to_thread(_decode_text, source_path.read_bytes())
    sanitized = _sanitize_email_html(content)
    output = entry_dir / "preview.html"
    await asyncio.to_thread(output.write_text, sanitized, encoding="utf-8")
    return ProcessResult("html", detected.label, "text/html; charset=utf-8", output.name)


async def _convert_image(
    source_path: Path, entry_dir: Path, detected: DetectedFormat
) -> ProcessResult:
    output = entry_dir / "preview.png"

    def convert() -> None:
        try:
            with Image.open(source_path) as image:
                image.seek(0)
                image.thumbnail((12_000, 12_000))
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.save(output, "PNG", optimize=True)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=422, detail="Изображение не удалось прочитать") from exc

    await asyncio.to_thread(convert)
    return ProcessResult("image", detected.label, "image/png", output.name)


async def _run(command: list[str], timeout: int, error_prefix: str) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"{error_prefix}: обработчик не установлен") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HTTPException(status_code=504, detail=f"{error_prefix}: превышено время") from exc
    if process.returncode != 0:
        details = (stderr or stdout).decode("utf-8", "replace").strip()[-500:]
        raise HTTPException(status_code=422, detail=f"{error_prefix}: {details}")


def _decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    for encoding in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _sanitize_email_html(value: str) -> str:
    allowed_tags = {
        "a", "abbr", "b", "blockquote", "br", "caption", "code", "div", "em", "h1", "h2",
        "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "small", "span",
        "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
    }

    def allow_attribute(tag: str, name: str, attribute_value: str) -> bool:
        if name in {"colspan", "rowspan", "title", "alt"}:
            return True
        if tag == "a" and name == "href":
            return attribute_value.startswith(("http://", "https://", "mailto:"))
        if tag == "img" and name == "src":
            return attribute_value.startswith(("cid:", "data:image/"))
        return False

    return bleach.clean(
        value,
        tags=allowed_tags,
        attributes=allow_attribute,
        protocols={"http", "https", "mailto", "cid", "data"},
        strip=True,
        strip_comments=True,
    )


def zipfile_is_zip(path: Path) -> bool:
    import zipfile

    return zipfile.is_zipfile(path)
