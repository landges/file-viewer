from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import extract_msg
import olefile
import rarfile
from fastapi import HTTPException

from .config import Settings
from .security import safe_archive_path, safe_display_name


def stable_entry_id(locator: str) -> str:
    return hashlib.sha256(locator.encode("utf-8", "surrogatepass")).hexdigest()[:24]


def list_zip(path: Path, settings: Settings) -> dict:
    entries: list[dict] = []
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > settings.max_archive_entries:
                raise HTTPException(status_code=413, detail="В архиве слишком много элементов")
            for info in infos:
                locator = info.filename
                if not safe_archive_path(locator):
                    continue
                total += info.file_size
                if total > settings.max_extracted_bytes:
                    raise HTTPException(status_code=413, detail="Распакованный архив превышает лимит")
                entries.append(
                    {
                        "id": stable_entry_id(locator),
                        "path": locator,
                        "name": safe_display_name(locator),
                        "is_dir": info.is_dir(),
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "encrypted": bool(info.flag_bits & 0x1),
                    }
                )
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="ZIP-архив повреждён") from exc
    entries.sort(key=lambda item: (not item["is_dir"], item["path"].lower()))
    return {"entries": entries, "total_size": total, "format": "zip"}


def list_rar(path: Path, settings: Settings) -> dict:
    entries: list[dict] = []
    total = 0
    try:
        with rarfile.RarFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > settings.max_archive_entries:
                raise HTTPException(status_code=413, detail="В архиве слишком много элементов")
            for info in infos:
                locator = info.filename
                if not safe_archive_path(locator):
                    continue
                total += info.file_size
                if total > settings.max_extracted_bytes:
                    raise HTTPException(status_code=413, detail="Распакованный архив превышает лимит")
                entries.append(
                    {
                        "id": stable_entry_id(locator),
                        "path": locator,
                        "name": safe_display_name(locator),
                        "is_dir": info.isdir(),
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "encrypted": info.needs_password(),
                    }
                )
    except rarfile.PasswordRequired as exc:
        raise HTTPException(status_code=422, detail="Архив защищён паролем") from exc
    except rarfile.Error as exc:
        raise HTTPException(status_code=422, detail="RAR-архив не удалось прочитать") from exc
    entries.sort(key=lambda item: (not item["is_dir"], item["path"].lower()))
    return {"entries": entries, "total_size": total, "format": "rar"}


async def list_7z(path: Path, settings: Settings) -> dict:
    process = await asyncio.create_subprocess_exec(
        "7z",
        "l",
        "-slt",
        "-ba",
        "-p-",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=settings.archive_timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HTTPException(status_code=504, detail="Превышено время чтения архива") from exc
    if process.returncode not in {0, 1}:
        message = stderr.decode("utf-8", "replace").strip()
        if any(marker in message.lower() for marker in ("password", "break signaled", "headers error")):
            raise HTTPException(status_code=422, detail="Архив защищён паролем")
        raise HTTPException(status_code=422, detail=f"Архив не удалось прочитать: {message[-300:]}")

    blocks = stdout.decode("utf-8", "replace").replace("\r\n", "\n").split("\n\n")
    entries: list[dict] = []
    total = 0
    for block in blocks:
        values: dict[str, str] = {}
        for line in block.splitlines():
            key, separator, value = line.partition(" = ")
            if separator:
                values[key] = value
        locator = values.get("Path")
        if not locator or "Folder" not in values or not safe_archive_path(locator):
            continue
        size = int(values.get("Size", "0") or 0)
        total += size
        entries.append(
            {
                "id": stable_entry_id(locator),
                "path": locator,
                "name": safe_display_name(locator),
                "is_dir": values.get("Folder") == "+",
                "size": size,
                "compressed_size": int(values.get("Packed Size", "0") or 0),
                "encrypted": values.get("Encrypted") == "+",
            }
        )
        if len(entries) > settings.max_archive_entries or total > settings.max_extracted_bytes:
            raise HTTPException(status_code=413, detail="Архив превышает безопасные лимиты")
    entries.sort(key=lambda item: (not item["is_dir"], item["path"].lower()))
    return {"entries": entries, "total_size": total, "format": "7z"}


def list_email(path: Path) -> tuple[dict, object]:
    message = _load_email_message(path)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    for index, part in enumerate(message.walk()):
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        content_type = part.get_content_type()
        if disposition == "attachment" or filename:
            payload = part.get_payload(decode=True) or b""
            locator = f"part-{index}"
            attachments.append(
                {
                    "id": stable_entry_id(locator),
                    "locator": locator,
                    "name": safe_display_name(filename, f"attachment-{index}"),
                    "content_type": content_type,
                    "size": len(payload),
                }
            )
            continue
        if content_type == "text/plain":
            try:
                plain_parts.append(_clean_email_text(part.get_content()))
            except (LookupError, UnicodeError):
                plain_parts.append(_clean_email_text((part.get_payload(decode=True) or b"").decode("utf-8", "replace")))
        elif content_type == "text/html":
            try:
                html_parts.append(_clean_email_text(part.get_content()))
            except (LookupError, UnicodeError):
                html_parts.append(_clean_email_text((part.get_payload(decode=True) or b"").decode("utf-8", "replace")))
    data = {
        "from": _clean_email_text(message.get("From", "")),
        "to": _clean_email_text(message.get("To", "")),
        "cc": _clean_email_text(message.get("Cc", "")),
        "subject": _clean_email_text(message.get("Subject", "")),
        "date": _clean_email_text(message.get("Date", "")),
        "text": "\n\n".join(plain_parts),
        "html": "\n".join(html_parts),
        "attachments": attachments,
    }
    return data, message


def extract_zip_entry(archive_path: Path, locator: str, destination: Path, settings: Settings) -> None:
    if not safe_archive_path(locator):
        raise HTTPException(status_code=400, detail="Некорректный путь внутри архива")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo(locator)
            if info.is_dir():
                raise HTTPException(status_code=400, detail="Каталог нельзя открыть как файл")
            if info.flag_bits & 0x1:
                raise HTTPException(status_code=422, detail="Файл защищён паролем")
            if info.file_size > settings.max_source_bytes:
                raise HTTPException(status_code=413, detail="Вложенный файл превышает лимит")
            with archive.open(info) as source, destination.open("wb") as output:
                remaining = settings.max_source_bytes
                while chunk := source.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise HTTPException(status_code=413, detail="Вложенный файл превышает лимит")
                    output.write(chunk)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Элемент архива не найден") from exc


def extract_rar_entry(archive_path: Path, locator: str, destination: Path, settings: Settings) -> None:
    if not safe_archive_path(locator):
        raise HTTPException(status_code=400, detail="Некорректный путь внутри архива")
    try:
        with rarfile.RarFile(archive_path) as archive:
            info = archive.getinfo(locator)
            if info.isdir():
                raise HTTPException(status_code=400, detail="Каталог нельзя открыть как файл")
            if info.needs_password():
                raise HTTPException(status_code=422, detail="Файл защищён паролем")
            if info.file_size > settings.max_source_bytes:
                raise HTTPException(status_code=413, detail="Вложенный файл превышает лимит")
            with archive.open(info) as source, destination.open("wb") as output:
                remaining = settings.max_source_bytes
                while chunk := source.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise HTTPException(status_code=413, detail="Вложенный файл превышает лимит")
                    output.write(chunk)
    except rarfile.PasswordRequired as exc:
        raise HTTPException(status_code=422, detail="Файл защищён паролем") from exc
    except (rarfile.Error, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Элемент RAR-архива не найден") from exc


async def extract_7z_entry(
    archive_path: Path, locator: str, destination: Path, settings: Settings
) -> None:
    if not safe_archive_path(locator):
        raise HTTPException(status_code=400, detail="Некорректный путь внутри архива")
    process = await asyncio.create_subprocess_exec(
        "7z",
        "x",
        "-so",
        "-y",
        "-p-",
        str(archive_path),
        locator,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    written = 0
    try:
        with destination.open("wb") as output:
            assert process.stdout is not None
            while chunk := await asyncio.wait_for(process.stdout.read(1024 * 1024), timeout=settings.archive_timeout_seconds):
                written += len(chunk)
                if written > settings.max_source_bytes:
                    process.kill()
                    raise HTTPException(status_code=413, detail="Вложенный файл превышает лимит")
                output.write(chunk)
        stderr = await process.stderr.read() if process.stderr else b""
        await process.wait()
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HTTPException(status_code=504, detail="Превышено время извлечения") from exc
    if process.returncode != 0:
        raise HTTPException(status_code=422, detail=stderr.decode("utf-8", "replace")[-300:])


def extract_email_entry(email_path: Path, locator: str, destination: Path) -> str:
    message = _load_email_message(email_path)
    try:
        expected_index = int(locator.removeprefix("part-"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный идентификатор вложения") from exc
    for index, part in enumerate(message.walk()):
        if index == expected_index and (part.get_filename() or part.get_content_disposition() == "attachment"):
            destination.write_bytes(part.get_payload(decode=True) or b"")
            return safe_display_name(part.get_filename(), f"attachment-{index}")
    raise HTTPException(status_code=404, detail="Вложение не найдено")


def _load_email_message(path: Path) -> EmailMessage:
    if olefile.isOleFile(path):
        try:
            with extract_msg.openMsg(str(path)) as outlook_message:
                message = outlook_message.asEmailMessage()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Outlook MSG не удалось прочитать") from exc
        if not isinstance(message, EmailMessage):
            raise HTTPException(status_code=422, detail="Outlook MSG не содержит почтового сообщения")
        # extract-msg can return compatibility MIMEText children even when the root
        # is EmailMessage. Reparse once so MSG MIME part uses the modern API.
        return BytesParser(policy=policy.default).parsebytes(message.as_bytes(policy=policy.default))
    return BytesParser(policy=policy.default).parsebytes(path.read_bytes())


def _clean_email_text(value: object) -> str:
    return str(value or "").replace("\x00", "").strip()


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
