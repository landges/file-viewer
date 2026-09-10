import pytest

from app.config import Settings
from app.source import SourceClient


@pytest.mark.asyncio
async def test_source_client_disables_tls_verification_only_for_configured_host() -> None:
    client = SourceClient(
        Settings(
            _env_file=None,
            source_allowed_hosts=["trusted-storage.internal", "secure-storage.internal"],
            source_allowed_ports=[9001],
            source_allowed_schemes=["https"],
            source_tls_insecure_hosts=["trusted-storage.internal"],
        )
    )
    try:
        assert client.insecure_client is not None
        assert (
            client._client_for_url("https://trusted-storage.internal:9001/file.xml")
            is client.insecure_client
        )
        assert (
            client._client_for_url("https://secure-storage.internal:9001/file.xml")
            is client.client
        )
        assert client._client_for_url("http://trusted-storage.internal/file.xml") is client.client
    finally:
        await client.close()
