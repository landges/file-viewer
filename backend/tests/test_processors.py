import pytest

from app.config import Settings
from app.detection import DetectedFormat
from app.processors import _decode_text, _sanitize_email_html, process_materialized_file


@pytest.mark.parametrize(
    ("encoding", "text"),
    [
        ("cp1256", "مرحبا بالعالم"),
        ("cp720", "مرحبا بالعالم"),
        ("cp1251", "Проверка русского текста"),
    ],
)
def test_decodes_unlabelled_legacy_text(encoding: str, text: str) -> None:
    assert _decode_text(text.encode(encoding)) == text


def test_xml_encoding_declaration_is_authoritative() -> None:
    source = '<?xml version="1.0" encoding="windows-1256"?><message>مرحبا</message>'
    assert _decode_text(source.encode("cp1256")) == source


def test_iso_arabic_xml_encoding_declaration_is_supported() -> None:
    source = '<?xml version="1.0" encoding="ISO-8859-6"?><message>مرحبا</message>'
    assert _decode_text(source.encode("iso8859-6")) == source


def test_decodes_utf32_bom_before_utf16_bom() -> None:
    source = '<?xml version="1.0"?><message>مرحبا</message>'
    assert _decode_text(source.encode("utf-32")) == source


@pytest.mark.asyncio
async def test_xml_uses_dedicated_inert_renderer(tmp_path) -> None:
    source = tmp_path / "source.xml"
    source.write_bytes(
        '<?xml version="1.0" encoding="windows-1256"?><message>مرحبا</message>'.encode("cp1256")
    )
    entry = tmp_path / "entry"
    detected = DetectedFormat("xml", "application/xml", ".xml", "XML")

    result = await process_materialized_file(source, detected, entry, Settings())

    assert result.renderer == "xml"
    assert result.mime_type == "text/plain; charset=utf-8"
    assert (entry / result.asset_name).read_text(encoding="utf-8").endswith(
        "<message>مرحبا</message>"
    )


@pytest.mark.asyncio
async def test_json_uses_dedicated_inert_renderer_without_changing_numbers(tmp_path) -> None:
    content = '{"id":900719925474099312345,"title":"مرحبا","enabled":true}'
    source = tmp_path / "source.json"
    source.write_text(content, encoding="utf-8")
    entry = tmp_path / "entry"
    detected = DetectedFormat("json", "application/json", ".json", "JSON")

    result = await process_materialized_file(source, detected, entry, Settings())

    assert result.renderer == "json"
    assert result.mime_type == "text/plain; charset=utf-8"
    assert (entry / result.asset_name).read_text(encoding="utf-8") == content


def test_html_sanitizer_preserves_only_safe_text_direction() -> None:
    cleaned = _sanitize_email_html('<p dir="rtl" lang="ar" data-x="bad">مرحبا</p>')
    assert 'dir="rtl"' in cleaned
    assert 'lang="ar"' in cleaned
    assert "data-x" not in cleaned
