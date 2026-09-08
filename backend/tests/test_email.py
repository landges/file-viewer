from email.message import EmailMessage
from pathlib import Path

from app.container_formats import extract_email_entry, list_email
from app.processors import _sanitize_email_html


def create_email(path: Path) -> None:
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["To"] = "user@example.test"
    message["Subject"] = "Test"
    message.set_content("Plain body")
    message.add_alternative(
        '<p onclick="alert(1)">Body</p><img src="http://tracker/pixel"><script>alert(1)</script>',
        subtype="html",
    )
    message.add_attachment(b"attachment", maintype="text", subtype="plain", filename="note.txt")
    path.write_bytes(message.as_bytes())


def test_email_metadata_and_attachment_extraction(tmp_path: Path) -> None:
    path = tmp_path / "message.eml"
    create_email(path)
    data, _ = list_email(path)
    assert data["subject"] == "Test"
    assert len(data["attachments"]) == 1
    destination = tmp_path / "attachment.txt"
    name = extract_email_entry(path, data["attachments"][0]["locator"], destination)
    assert name == "note.txt"
    assert destination.read_bytes() == b"attachment"


def test_email_html_removes_scripts_events_and_remote_images() -> None:
    cleaned = _sanitize_email_html(
        '<p onclick="alert(1)">Body</p><img src="http://tracker/pixel"><script>alert(1)</script>'
    )
    assert "onclick" not in cleaned
    assert "http://tracker" not in cleaned
    assert "<script" not in cleaned

