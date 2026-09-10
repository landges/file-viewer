import pytest
from fastapi import HTTPException

from app.config import Settings
from app.security import SourceUrlPolicy, safe_archive_path, safe_display_name


def policy() -> SourceUrlPolicy:
    return SourceUrlPolicy(
        Settings(
            _env_file=None,
            source_allowed_hosts=["*.storage.internal", "source.localhost"],
            source_allowed_ports=[80, 8081],
            source_allowed_schemes=["http"],
            source_tls_insecure_hosts=["legacy.storage.internal"],
        )
    )


def test_source_allowlist() -> None:
    assert policy().validate("http://bucket.storage.internal/file.docx")
    assert policy().validate("http://source.localhost:8081/files/file.docx")


@pytest.mark.parametrize(
    "url",
    [
        "https://bucket.storage.internal/file.docx",
        "http://example.org/file.docx",
        "http://user:password@source.localhost/file.docx",
        "http://source.localhost:9000/file.docx",
    ],
)
def test_source_policy_rejects_out_of_scope_urls(url: str) -> None:
    with pytest.raises(HTTPException):
        policy().validate(url)


def test_archive_paths_and_names_are_normalized() -> None:
    assert safe_archive_path("folder/document.docx")
    assert not safe_archive_path("../../etc/passwd")
    assert not safe_archive_path("/absolute/path")
    assert safe_display_name("folder\\document.docx") == "document.docx"


def test_tls_verification_exception_is_exact_and_https_only() -> None:
    source_policy = policy()
    assert not source_policy.requires_tls_verification(
        "https://legacy.storage.internal:9001/file.xml"
    )
    assert source_policy.requires_tls_verification(
        "https://sub.legacy.storage.internal:9001/file.xml"
    )
    assert source_policy.requires_tls_verification(
        "https://bucket.storage.internal:9001/file.xml"
    )
    assert source_policy.requires_tls_verification(
        "http://legacy.storage.internal:9000/file.xml"
    )
