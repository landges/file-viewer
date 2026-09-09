from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.detection import DetectedFormat, detect_prefix, inspect_ole, inspect_zip


def test_detects_common_signatures_without_relying_on_suffix() -> None:
    assert detect_prefix(b"%PDF-1.7\n", "wrong.bin").kind == "pdf"
    assert detect_prefix(b"\x89PNG\r\n\x1a\nrest", "wrong.bin").mime_type == "image/png"
    assert detect_prefix(b"7z\xbc\xaf'\x1cdata", "wrong.bin").kind == "archive"
    assert detect_prefix(b"PK\x03\x04data", "archive.custom").kind == "zip_container"
    wav = detect_prefix(b"RIFF\x24\x00\x00\x00WAVEfmt ", "sound.payload")
    assert (wav.kind, wav.mime_type) == ("audio", "audio/wav")
    mp3 = detect_prefix(b"ID3\x04\x00\x00\x00\x00\x00\x00", "sound.payload")
    assert (mp3.kind, mp3.mime_type) == ("audio", "audio/mpeg")


def test_detects_headerless_mp3_frame() -> None:
    detected = detect_prefix(b"\xff\xfb\x90\x64" + b"\x00" * 32, "recording.bin")
    assert detected.kind == "audio"
    assert detected.extension == ".mp3"


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


def test_detects_rtf_by_signature_and_suffix() -> None:
    by_signature = detect_prefix(b"\xef\xbb\xbf  {\\RTF1\\ansi Test}", "document.payload")
    assert by_signature.kind == "office"
    assert by_signature.extension == ".rtf"
    assert by_signature.mime_type == "application/rtf"

    by_suffix = detect_prefix(b"plain text accepted by office converter", "document.rtf")
    assert by_suffix.kind == "office"
    assert by_suffix.label == "RTF"


def test_detects_xml_html_and_htm() -> None:
    html = detect_prefix(b"  <!DOCTYPE HTML><html><body>ok</body></html>", "page.payload")
    assert (html.kind, html.mime_type, html.extension) == ("html", "text/html", ".html")

    xml_payload = "<?xml version='1.0'?><catalog/>".encode("utf-16")
    xml = detect_prefix(xml_payload, "catalog.payload")
    assert (xml.kind, xml.mime_type, xml.extension) == ("xml", "application/xml", ".xml")

    assert detect_prefix(b"<p>HTML fragment</p>", "fragment.htm").kind == "html"
    assert detect_prefix(b"<catalog/>", "catalog.xml").kind == "xml"


def test_detects_outlook_msg_by_suffix_and_ole_structure(tmp_path: Path, monkeypatch) -> None:
    signature = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert detect_prefix(signature, "message.msg").kind == "outlook_email"

    class FakeOleFile:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def listdir(self):
            return [["__properties_version1.0"], ["__substg1.0_0037001F"]]

    monkeypatch.setattr("app.detection.olefile.OleFileIO", lambda _path: FakeOleFile())
    fallback = DetectedFormat("office", "application/x-ole-storage", ".bin", "OLE document")
    detected = inspect_ole(tmp_path / "message.bin", fallback)
    assert detected.kind == "outlook_email"
    assert detected.mime_type == "application/vnd.ms-outlook"
