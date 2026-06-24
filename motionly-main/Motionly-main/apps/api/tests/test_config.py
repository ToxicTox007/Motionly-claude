import os
import pytest
from app.core.config import get_settings
from pydantic import ValidationError

def test_production_config_with_skip_api_auth_fails(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SKIP_API_AUTH", "true")
    get_settings.cache_clear()
    
    with pytest.raises(ValidationError) as exc_info:
        get_settings()
        
    assert "Security violation" in str(exc_info.value)

@pytest.mark.parametrize("truthy_value", ["1", "yes", "on", "TRUE", "Yes"])
def test_production_config_with_truthy_values_fails(monkeypatch, truthy_value) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SKIP_API_AUTH", truthy_value)
    get_settings.cache_clear()
    
    with pytest.raises(ValidationError) as exc_info:
        get_settings()
        
    assert "Security violation" in str(exc_info.value)

def test_production_config_with_normal_auth_passes(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SKIP_API_AUTH", raising=False)
    get_settings.cache_clear()
    
    # Should not raise validation error
    settings = get_settings()
    assert settings.app_env == "production"

def test_non_production_config_with_skip_api_auth_passes(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SKIP_API_AUTH", "true")
    get_settings.cache_clear()
    
    settings = get_settings()
    assert settings.app_env == "development"

def test_verify_supabase_jwt_blocks_bypass_in_production(monkeypatch) -> None:
    from app.services.auth import verify_supabase_jwt
    
    # Configure mock production settings, skip_api_auth=true
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SKIP_API_AUTH", "true")
    get_settings.cache_clear()
    
    # Should raise validation error or fail bypass validation because settings is production
    with pytest.raises(Exception):
        verify_supabase_jwt(token="mock-local-bearer", jwt_secret="test_secret")
