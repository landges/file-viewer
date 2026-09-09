from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def wait_ready(viewer: str, preview: dict, allow_failure: bool = False) -> dict:
    deadline = time.monotonic() + 60
    while preview["status"] in {"queued", "processing"} and time.monotonic() < deadline:
        time.sleep(0.35)
        preview = request_json(f"{viewer}/api/previews/{preview['id']}")
    if preview["status"] == "failed" and allow_failure:
        return preview
    assert preview["status"] == "ready", preview
    return preview


def create(viewer: str, source: str, filename: str, allow_failure: bool = False) -> dict:
    preview = request_json(
        f"{viewer}/api/previews",
        {"url": f"{source}/{filename}", "filename": filename},
    )
    return wait_ready(viewer, preview, allow_failure)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", default="http://localhost:8080")
    parser.add_argument("--source", default="http://source.localhost:8081/files")
    args = parser.parse_args()
    viewer = args.viewer.rstrip("/")
    source = args.source.rstrip("/")

    expected = {
        "document.docx": "pdf",
        "document.doc": "pdf",
        "document.rtf": "pdf",
        "table.xlsx": "spreadsheet",
        "table.xls": "spreadsheet",
        "presentation.pptx": "pdf",
        "presentation.ppt": "pdf",
        "document.pdf": "pdf",
        "image.png": "image",
        "audio.wav": "audio",
        "audio.mp3": "audio",
        "document.xml": "text",
        "page.html": "html",
        "page.htm": "html",
        "archive.custom-package": "archive",
        "message.eml": "email",
        "message.msg": "email",
    }
    results: list[str] = []
    previews: dict[str, dict] = {}
    for filename, renderer in expected.items():
        preview = create(viewer, source, filename)
        assert preview["renderer"] == renderer, preview
        previews[filename] = preview
        results.append(f"OK {filename}: {preview['detected_type']} / {renderer}")

    xml = previews["document.xml"]
    with urlopen(f"{viewer}{xml['content_url']}", timeout=20) as response:
        xml_content = response.read().decode("utf-8")
    assert xml["detected_type"] == "XML"
    assert "<catalog>" in xml_content and "Тестовый XML" in xml_content
    results.append("OK XML safe text preview")

    for filename in ("page.html", "page.htm"):
        html = previews[filename]
        with urlopen(f"{viewer}{html['content_url']}", timeout=20) as response:
            html_content = response.read().decode("utf-8")
            content_security_policy = response.headers["Content-Security-Policy"]
        assert "Безопасный HTML" in html_content
        assert "onclick" not in html_content
        assert "<script" not in html_content
        assert "tracking.invalid" not in html_content
        assert "sandbox" in content_security_policy and "default-src 'none'" in content_security_policy
    results.append("OK HTML/HTM sanitized sandbox preview")

    archive = previews["archive.custom-package"]
    archive_data = request_json(f"{viewer}{archive['data_url']}")
    entry = next(item for item in archive_data["entries"] if item["path"] == "docs/table.xlsx")
    child = request_json(
        f"{viewer}/api/previews/{archive['id']}/entries",
        {"entry_id": entry["id"]},
    )
    child = wait_ready(viewer, child)
    assert child["renderer"] == "spreadsheet", child
    results.append("OK archive → XLSX attachment")

    audio_entry = next(
        item for item in archive_data["entries"] if item["path"] == "media/audio.mp3"
    )
    embedded_audio = request_json(
        f"{viewer}/api/previews/{archive['id']}/entries",
        {"entry_id": audio_entry["id"]},
    )
    embedded_audio = wait_ready(viewer, embedded_audio)
    assert embedded_audio["renderer"] == "audio", embedded_audio
    range_request = Request(
        f"{viewer}{embedded_audio['content_url']}", headers={"Range": "bytes=0-15"}
    )
    with urlopen(range_request, timeout=20) as response:
        assert response.status == 206
        assert len(response.read()) == 16
    results.append("OK archive → MP3 playback range")

    email = previews["message.eml"]
    email_data = request_json(f"{viewer}{email['data_url']}")
    attachment = email_data["attachments"][0]
    child = request_json(
        f"{viewer}/api/previews/{email['id']}/entries",
        {"entry_id": attachment["id"]},
    )
    child = wait_ready(viewer, child)
    assert child["renderer"] == "pdf", child
    results.append("OK EML → DOCX attachment")

    outlook = previews["message.msg"]
    outlook_data = request_json(f"{viewer}{outlook['data_url']}")
    assert outlook_data["subject"] == "Проверка Outlook MSG и вложения"
    attachment = outlook_data["attachments"][0]
    child = request_json(
        f"{viewer}/api/previews/{outlook['id']}/entries",
        {"entry_id": attachment["id"]},
    )
    child = wait_ready(viewer, child)
    assert child["renderer"] == "pdf", child
    results.append("OK MSG → DOCX attachment")

    pdf = previews["document.pdf"]
    range_request = Request(f"{viewer}{pdf['content_url']}", headers={"Range": "bytes=0-7"})
    with urlopen(range_request, timeout=20) as response:
        assert response.status == 206
        assert len(response.read()) == 8
    results.append("OK PDF byte range")

    for filename in ("audio.wav", "audio.mp3"):
        audio = previews[filename]
        range_request = Request(
            f"{viewer}{audio['content_url']}", headers={"Range": "bytes=0-15"}
        )
        with urlopen(range_request, timeout=20) as response:
            assert response.status == 206
            assert len(response.read()) == 16
            assert response.headers["Content-Type"] == audio["mime_type"]
    results.append("OK WAV/MP3 playback range")

    protected = create(viewer, source, "protected.7z", allow_failure=True)
    assert protected["status"] == "failed"
    assert "парол" in (protected.get("error") or "").lower()
    results.append("OK protected archive refusal")

    print("\n".join(results))


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, HTTPError, OSError) as error:
        print(f"SMOKE TEST FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error
