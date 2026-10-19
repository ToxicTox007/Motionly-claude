"""Media bytes and media metadata have separate backends.

``FORCE_LOCAL_MEDIA_STORAGE`` keeps media *bytes* on the local disk (browser
tests, the local audit). Upload metadata (object references, quota
reservations, upload sessions) lives in database tables next to
``uploaded_videos``, so it must follow the database. It used to follow the bytes
backend: the reference stayed in memory while ``uploaded_videos`` was written to
Postgres, and completing an upload failed on the
``task6_upload_source_reference_tenant_fk`` foreign key (HTTP 502).
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.main import create_app
from app.services.local_storage import LocalObjectStorage

SUPABASE = "http://127.0.0.1:54321"


def _app(monkeypatch: pytest.MonkeyPatch, *, force_local_media: bool, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-for-test")
    monkeypatch.setenv("FORCE_LOCAL_MEDIA_STORAGE", "true" if force_local_media else "false")
    monkeypatch.setenv("MEDIA_WORK_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("force_local_media", [True, False])
def test_upload_metadata_follows_the_database_not_the_media_bytes_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path, force_local_media: bool
) -> None:
    app = _app(monkeypatch, force_local_media=force_local_media, tmp_path=tmp_path)

    references = app.state.object_reference_service
    quota = app.state.storage_quota_service
    assert references._in_memory is False
    assert references._supabase_url == SUPABASE
    assert quota._in_memory is False
    assert quota._supabase_url == SUPABASE
    for service in (app.state.upload_session_service, app.state.media_upload_session_service):
        assert service._db_client is not None
        assert service._db_client.rest_url == f"{SUPABASE}/rest/v1"


def test_forced_local_media_keeps_bytes_and_links_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    app = _app(monkeypatch, force_local_media=True, tmp_path=tmp_path)

    # Bytes and the links that move them stay on the local disk.
    assert type(app.state.object_storage) is LocalObjectStorage
    assert app.state.storage_signer.supabase_url is None
    assert app.state.storage_signer.public_supabase_url is None


def test_without_a_database_upload_metadata_stays_in_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SETTINGS_ENV_FILE", "none")
    monkeypatch.setenv("MEDIA_WORK_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    assert app.state.object_reference_service._in_memory is True
    assert app.state.storage_quota_service._in_memory is True
    assert app.state.upload_session_service._db_client is None
