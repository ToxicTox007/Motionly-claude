import os
import shutil
import tempfile

import pytest

# Test collection imports app.main before fixtures run. Override repository and
# inherited shell defaults so collection is hermetic and authentication fails
# closed. Individual tests can still opt in with monkeypatch after collection.
os.environ["APP_ENV"] = "local"
os.environ["SKIP_API_AUTH"] = "false"

from app.core.config import get_settings  # noqa: E402

RUN_GPU_STAGE_TESTS = os.getenv("RUN_GPU_STAGE_TESTS") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip GPU-stage tests unless the GPU stage has been explicitly enabled.

    The skip reason carries the manifest id so scripts/verify/skip_audit.py can
    prove every deferred check is declared in tests/gpu-stage.json.
    """
    for item in items:
        marker = item.get_closest_marker("gpu_stage")
        if marker is None:
            continue
        gpu_stage_id = marker.args[0] if marker.args else marker.kwargs.get("id")
        if not gpu_stage_id:
            raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.gpu_stage needs a manifest id")
        if not RUN_GPU_STAGE_TESTS:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"gpu-stage[{gpu_stage_id}]: deferred until the GPU stage is approved"
                )
            )


@pytest.fixture(scope="session", autouse=True)
def contain_temporary_files(tmp_path_factory: pytest.TempPathFactory):
    """Files tests create with ``tempfile`` go to one session directory that is
    removed afterwards, instead of accumulating in the system temp directory."""
    previous = tempfile.tempdir
    directory = tmp_path_factory.mktemp("tempfile")
    tempfile.tempdir = str(directory)
    yield
    tempfile.tempdir = previous
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(autouse=True)
def default_local_app_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
