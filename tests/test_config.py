import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


@pytest.mark.anyio
async def test_health_without_database_or_external_model():
    app = create_app(Settings(_env_file=None, app_env="test", ai_provider="disabled"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).json() == {"status": "UP"}
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/health/ready")).status_code == 503


@pytest.mark.anyio
async def test_production_hides_api_documentation():
    settings = Settings(
        _env_file=None,
        app_env="prod",
        ai_provider="disabled",
        ai_internal_api_key="test-only-production-key-32-characters",
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


@pytest.mark.parametrize("api_key", ["", "local-development-only", "short"])
def test_production_rejects_missing_or_development_secrets(api_key):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prod", ai_internal_api_key=api_key)


def test_production_rejects_stub_provider():
    with pytest.raises(ValidationError, match="stub provider"):
        Settings(
            _env_file=None,
            app_env="prod",
            ai_provider="stub",
            ai_internal_api_key="test-only-production-key-32-characters",
        )


def test_settings_representation_hides_api_key():
    settings = Settings(_env_file=None, ai_internal_api_key="never-print-this-secret")
    assert "never-print-this-secret" not in repr(settings)
